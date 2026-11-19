"""Meeting Prep ADK Agent — In-Process & Future-Ready Vertex AI Agent Engine.

Provides:
  - build_step_agent(step): constructs a lightweight step-scoped LlmAgent
    loading only master_meeting_prep.md + the active step/sub-step skills
    for fast in-process execution via InMemoryRunner.
  - root_agent: full-skill LlmAgent exported for future Vertex AI Agent Engine
    deployment via scripts/deploy_meeting_prep_agent.py.
"""

from functools import cached_property
from pathlib import Path

from google.adk.agents import LlmAgent
from google.adk.models import Gemini
from google.adk.tools import BaseTool
from google.adk.tools.tool_context import ToolContext
from google.genai import Client

from agents_meeting_prep.tools import (
    execute_bq_query,
    find_client,
    finnhub_news_with_benchmarks,
    get_approved_commentary,
    get_benchmark_returns,
    get_client_profile,
    get_crm_interactions,
    get_fund_news_summary,
    get_holdings,
    get_ips,
    get_market_indicators,
    get_meeting,
    get_meeting_context,
    get_portfolio_alerts,
    get_portfolio_beta,
    get_prior_plan,
    get_valuations,
    list_catalog_nodes,
    lookup_catalog_rule,
    lookup_context,
    lookup_schema,
    set_agent_context,
    yfinance_price,
)

_SKILLS_DIR = Path(__file__).resolve().parent / "skills"

_STEP_SKILLS: dict[str, list[str]] = {
    "snapshot": ["portfolio_snapshot.md", "sub/trend_analysis.md"],
    "risks": [
        "risks_and_opportunities.md",
        "sub/portfolio_performance_insight.md",
        "sub/allocation_drift_policy_insight.md",
        "sub/concentration_risk_insight.md",
        "sub/liquidity_goal_insight.md",
        "sub/market_investment_insight.md",
    ],
    "angles": ["discussion_angles.md", "sub/advisory_reasoning_map.yaml"],
    "activity": ["recent_activity.md"],
    "assemble": ["brief_assembler.md"],
    # Granular sub-step skill bundles
    "sub_trends": ["portfolio_snapshot.md", "sub/trend_analysis.md"],
    "sub_perf_insights": ["sub/portfolio_performance_insight.md"],
    "sub_allocation_insights": ["sub/allocation_drift_policy_insight.md"],
    "sub_concentration_insights": ["sub/concentration_risk_insight.md"],
    "sub_liquidity_goal_insights": ["sub/liquidity_goal_insight.md"],
    "sub_market_insights": ["sub/market_investment_insight.md"],
    "sub_fund_news": ["brief_assembler.md"],
}

_ALL_SKILLS_ORDER: list[str] = [
    "portfolio_snapshot.md",
    "sub/trend_analysis.md",
    "risks_and_opportunities.md",
    "sub/portfolio_performance_insight.md",
    "sub/allocation_drift_policy_insight.md",
    "sub/concentration_risk_insight.md",
    "sub/liquidity_goal_insight.md",
    "sub/market_investment_insight.md",
    "discussion_angles.md",
    "sub/advisory_reasoning_map.yaml",
    "recent_activity.md",
    "brief_assembler.md",
]


def _load_skills(rel_paths: list[str]) -> str:
    parts = []
    master = _SKILLS_DIR / "master_meeting_prep.md"
    if master.exists():
        parts.append(f"## Skill: master_meeting_prep.md\n\n{master.read_text(encoding='utf-8')}")
    for rel in rel_paths:
        p = _SKILLS_DIR / rel
        if p.exists():
            parts.append(f"## Skill: {rel}\n\n{p.read_text(encoding='utf-8')}")
    return "\n\n---\n\n".join(parts)


class GlobalGemini(Gemini):
    @cached_property
    def api_client(self) -> Client:
        return Client(vertexai=True, location="global")


def _before_tool(tool: BaseTool, args: dict, ctx: ToolContext) -> None:
    try:
        session_id = ctx.session.id if ctx.session else ""
        advisor_input = ""
        if ctx.user_content and hasattr(ctx.user_content, "parts"):
            advisor_input = " ".join(
                p.text for p in ctx.user_content.parts if hasattr(p, "text")
            ).strip()
        set_agent_context(session_id=session_id, advisor_input=advisor_input)
    except Exception:
        pass


_CATALOG_INSTRUCTION = """\
# Meeting Prep Agent (GCP Context Set Edition)

## Data Discovery — Always Context Set First
Never hardcode table names, column names, JOINs, or risk thresholds.
The skill files below may reference `graph_query` (Neo4j) or `dynamo_query` (DynamoDB) from the prior stack — on GCP, fulfill those data needs using the business-context Context Set tools and Dataplex `wealth-advisor-cs` tools:
1. Business-context tools (all backed by `wealth-advisor-cs`): `find_client`, `get_client_profile`, `get_holdings`, `get_valuations`, `get_ips`, `get_prior_plan`, `get_crm_interactions`, `get_portfolio_alerts`.
2. Direct Context Set discovery + SQL: `list_catalog_nodes()`, `lookup_schema()`, `lookup_context()`, `lookup_catalog_rule()`, and `execute_bq_query(sql)` (read-only BigQuery `SELECT`).
3. Meeting context (Section 10): `get_meeting(meeting_id, meeting_date)` (or `get_meeting_context`).
4. Market & News tools: `get_benchmark_returns` (`yfinance_price`), `get_approved_commentary` (`finnhub_news_with_benchmarks`), `get_fund_news_summary`, `get_portfolio_beta`, `get_market_indicators`.

## Output Contract
Obey all guardrails in `master_meeting_prep.md` and output ONLY valid JSON matching the requested skill's output schema (no prose, no markdown fences).
"""

_AGENT_TOOLS = [
    # Business-context domain tools
    find_client,
    get_client_profile,
    get_holdings,
    get_valuations,
    get_ips,
    get_prior_plan,
    get_crm_interactions,
    get_portfolio_alerts,
    get_meeting,
    get_benchmark_returns,
    get_approved_commentary,
    get_fund_news_summary,
    get_portfolio_beta,
    get_market_indicators,
    # Context Set & BigQuery SQL discovery tools
    list_catalog_nodes,
    lookup_schema,
    lookup_context,
    lookup_catalog_rule,
    execute_bq_query,
    # Backward-compatible aliases for skill prompts
    get_meeting_context,
    yfinance_price,
    finnhub_news_with_benchmarks,
]


def build_step_agent(step: str) -> LlmAgent:
    """Build a lightweight step-scoped LlmAgent for in-process execution."""
    skills = _STEP_SKILLS.get(step, _ALL_SKILLS_ORDER)
    return LlmAgent(
        name=f"meeting_prep_{step}",
        model=GlobalGemini(model="gemini-3.8-flash"),
        before_tool_callback=_before_tool,
        description=f"Wealth Advisor Meeting Prep Agent ({step} step).",
        instruction=_CATALOG_INSTRUCTION + "\n\n---\n\n" + _load_skills(skills),
        tools=_AGENT_TOOLS,
        sub_agents=[],
    )


root_agent = LlmAgent(
    name="meeting_prep_agent",
    model=GlobalGemini(model="gemini-3.8-flash"),
    before_tool_callback=_before_tool,
    description=(
        "Wealth Advisor Meeting Prep Agent that discovers schemas, JOINs, and "
        "risk rules from the wealth-advisor-cs Context Set, queries BigQuery "
        "and live market feeds, and generates structured meeting-prep step JSON."
    ),
    instruction=_CATALOG_INSTRUCTION + "\n\n---\n\n" + _load_skills(_ALL_SKILLS_ORDER),
    tools=_AGENT_TOOLS,
    sub_agents=[],
)
