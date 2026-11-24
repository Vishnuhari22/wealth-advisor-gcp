"""Unified ADK Agent Runner — In-Process (default) + Future Remote Agent Engine.

Reads MEETING_PREP_AGENT_MODE ('in_process' | 'remote', default 'in_process').
In-process mode runs step-scoped LlmAgents directly via ADK InMemoryRunner to
avoid remote HTTP/session overhead; remote mode invokes a deployed Vertex AI
Agent Engine resource via stream_query().
"""

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

_SA_KEY = Path(__file__).resolve().parent.parent / "catalog" / "agentic-wealthadvisor-9e4f503e15c0.json"
if _SA_KEY.exists():
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(_SA_KEY)

_COMBINED_CA = Path(__file__).resolve().parent.parent / "catalog" / "combined_ca.pem"
if _COMBINED_CA.exists():
    os.environ["REQUESTS_CA_BUNDLE"] = str(_COMBINED_CA)
    os.environ["SSL_CERT_FILE"] = str(_COMBINED_CA)

logger = logging.getLogger(__name__)

def _require_env(name: str) -> str:
    val = os.getenv(name, "").strip()
    if not val:
        raise RuntimeError(
            f"Missing required environment variable: {name}. Set it in .env or runtime environment."
        )
    return val


PROJECT = _require_env("GCP_PROJECT_ID")
LOCATION = _require_env("GCP_LOCATION")
AGENT_MODE = _require_env("MEETING_PREP_AGENT_MODE").lower()

_ENGINE: Any = None


def _try_parse_dict(text: str) -> Optional[dict]:
    try:
        val = json.loads(text, strict=False)
    except Exception:
        return None
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        try:
            inner = json.loads(val, strict=False)
            if isinstance(inner, dict):
                return inner
        except Exception:
            return None
    return None


def _strip_json(raw: str) -> dict:
    clean = (raw or "").strip()
    if not clean:
        return {}

    parsed = _try_parse_dict(clean)
    if parsed is not None:
        return parsed

    for m in reversed(re.findall(r"```(?:json)?\s*([\s\S]*?)\s*```", clean)):
        parsed = _try_parse_dict(m.strip())
        if parsed is not None:
            return parsed

    decoder = json.JSONDecoder(strict=False)
    idx = clean.find("{")
    while idx != -1:
        try:
            obj, _ = decoder.raw_decode(clean, idx)
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
        idx = clean.find("{", idx + 1)

    logger.error("Agent returned non-JSON output: %s", clean[:500])
    return {}


async def _run_in_process_async(step: str, prompt: str, user_id: str = "mcp-server") -> dict:
    from google.adk.runners import InMemoryRunner
    from google.genai.types import Content, Part

    from agents_meeting_prep.agent import build_step_agent

    agent = build_step_agent(step)
    runner = InMemoryRunner(agent=agent, app_name="meeting_prep")
    session = await runner.session_service.create_session(app_name="meeting_prep", user_id=user_id)

    event_texts: list[str] = []
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session.id,
        new_message=Content(role="user", parts=[Part(text=prompt)]),
    ):
        content = getattr(event, "content", None)
        if content and getattr(content, "parts", None):
            parts_text = []
            for p in content.parts:
                if getattr(p, "thought", False):
                    continue
                text = getattr(p, "text", None)
                if text:
                    parts_text.append(text)
            if parts_text:
                event_texts.append("".join(parts_text))

    for evt_text in reversed(event_texts):
        parsed = _strip_json(evt_text)
        if parsed:
            return parsed
    return _strip_json("".join(event_texts)) if event_texts else {}


def _invoke_in_process_step(step: str, prompt: str, user_id: str = "mcp-server") -> dict:
    try:
        asyncio.get_running_loop()
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, _run_in_process_async(step, prompt, user_id)).result()
    except RuntimeError:
        return asyncio.run(_run_in_process_async(step, prompt, user_id))


def _get_resource_name() -> str:
    env_res = os.getenv("MEETING_PREP_AGENT_RESOURCE", "").strip()
    if env_res:
        return env_res
    res_file = Path(__file__).resolve().parent / ".deployed_resource_name"
    if res_file.exists():
        return res_file.read_text(encoding="utf-8").strip()
    raise RuntimeError(
        "MEETING_PREP_AGENT_RESOURCE is not set and .deployed_resource_name was not found."
    )


def _get_engine():
    global _ENGINE
    if _ENGINE is None:
        import vertexai
        from vertexai import agent_engines

        vertexai.init(
            project=PROJECT,
            location=LOCATION,
            staging_bucket=f"gs://{PROJECT}-agent-engine",
        )
        _ENGINE = agent_engines.get(_get_resource_name())
    return _ENGINE


def _invoke_remote_step(step: str, prompt: str, user_id: str = "mcp-server") -> dict:
    engine = _get_engine()
    session = engine.create_session(user_id=user_id or "mcp-server")
    event_texts: list[str] = []
    for event in engine.stream_query(
        session_id=session["id"],
        message=prompt,
        user_id=user_id or "mcp-server",
    ):
        parts_text = []
        for p in event.get("content", {}).get("parts", []):
            if isinstance(p, dict) and p.get("thought"):
                continue
            text = p.get("text") if isinstance(p, dict) else None
            if text:
                parts_text.append(text)
        if parts_text:
            event_texts.append("".join(parts_text))
    for evt_text in reversed(event_texts):
        parsed = _strip_json(evt_text)
        if parsed:
            return parsed
    return _strip_json("".join(event_texts)) if event_texts else {}


def _invoke_step(step: str, prompt: str, user_id: str = "mcp-server") -> dict:
    mode = os.getenv("MEETING_PREP_AGENT_MODE", AGENT_MODE).strip().lower()
    if mode == "remote":
        return _invoke_remote_step(step, prompt, user_id=user_id)
    return _invoke_in_process_step(step, prompt, user_id=user_id)


# ── Business-Context Step Runners ────────────────────────────────────────────

def run_portfolio_snapshot(client_id: str, meeting_id: str, meeting_date: str) -> dict:
    prompt = (
        f"Run PortfolioSnapshotSkill for client_id={client_id}.\n"
        f"meeting_id={meeting_id} meeting_date={meeting_date}\n\n"
        "Use get_client_profile, get_ips, get_holdings, and get_benchmark_returns (Context Set backed) "
        "and output ONLY valid JSON ({holdings: {totalValue, costBasis, unrealizedGL, performance, returns, keyInsight}})."
    )
    return _invoke_step("snapshot", prompt)


def run_risks_and_opportunities(
    client_id: str,
    meeting_id: str,
    meeting_date: str,
    signal_facts: Optional[list] = None,
    cached_sub_signals: Optional[list] = None,
) -> dict:
    facts_block = (
        f"\n\nPRE-COMPUTED SIGNAL FACTS (authoritative — include all):\n{json.dumps(signal_facts, default=str)}\n"
        if signal_facts else ""
    )
    cached_block = (
        f"\n\nALREADY COMPUTED SUB-SIGNALS (reuse directly):\n{json.dumps(cached_sub_signals, default=str)}\n"
        if cached_sub_signals else ""
    )
    prompt = (
        f"Run RisksAndOpportunitiesSkill (all 5 specialist insight sub-skills) for client_id={client_id}.\n"
        f"meeting_id={meeting_id} meeting_date={meeting_date}"
        f"{facts_block}{cached_block}\n"
        "Use the Context Set tools and live market/news tools (get_benchmark_returns, get_approved_commentary, "
        "get_market_indicators, get_portfolio_beta) and output ONLY valid JSON ({signals: [...], risk: {riskMetrics: [...]}})."
    )
    return _invoke_step("risks", prompt)


def run_discussion_angles(
    client_id: str,
    meeting_id: str,
    meeting_date: str,
    signals: list,
    scenario_fires: Optional[dict] = None,
) -> dict:
    fires_block = (
        f"\n\nPRE-COMPUTED SCENARIO FIRING DECISIONS:\n{json.dumps(scenario_fires, default=str)}\n"
        if scenario_fires else ""
    )
    prompt = (
        f"Run DiscussionAnglesSkill for client_id={client_id}.\n"
        f"meeting_id={meeting_id} meeting_date={meeting_date}\n\n"
        f"VALIDATED SIGNALS:\n{json.dumps(signals, default=str)}"
        f"{fires_block}\n"
        "Apply advisory_reasoning_map.yaml and output ONLY valid JSON "
        "({discussionAngles: [...], nextBestActions: [...]})."
    )
    return _invoke_step("angles", prompt)


def run_recent_activity(client_id: str, meeting_id: str, meeting_date: str) -> dict:
    prompt = (
        f"Run RecentActivitySkill for client_id={client_id}.\n"
        f"meeting_id={meeting_id} meeting_date={meeting_date}\n\n"
        "Use get_crm_interactions (Context Set backed) and output ONLY valid JSON ({activity: [...]})."
    )
    return _invoke_step("activity", prompt)


def assemble_meeting_brief(client_id: str, meeting_id: str, meeting_date: str, prior_outputs: dict) -> dict:
    prompt = (
        f"Run BriefAssemblerSkill for client_id={client_id}.\n"
        f"meeting_id={meeting_id} meeting_date={meeting_date}\n\n"
        f"PRIOR STEP OUTPUTS:\n{json.dumps(prior_outputs, default=str)}\n\n"
        "Fetch meeting metadata via get_meeting and client profile via get_client_profile, "
        "then assemble and output ONLY the final 11-key meeting brief JSON."
    )
    return _invoke_step("assemble", prompt)


# ── Granular Sub-Step Runners (for cold-cache MCP sub-tools) ─────────────────

def run_holding_trends(client_id: str, meeting_id: str, meeting_date: str) -> dict:
    prompt = (
        f"Run TrendAnalysisSubSkill for client_id={client_id} (meeting_id={meeting_id}, date={meeting_date}).\n"
        "Fetch holdings via get_holdings and live returns via get_benchmark_returns, "
        "then output ONLY valid JSON: {performance: [{ticker, type, weight, y2024, y2025, trend, remarks}], keyInsight: [...]}."
    )
    return _invoke_step("sub_trends", prompt)


_SPECIALIST_PROMPTS: dict[str, tuple[str, str]] = {
    "perf": ("sub_perf_insights", "PortfolioPerformanceInsightSkill"),
    "allocation": ("sub_allocation_insights", "AllocationDriftPolicyInsightSkill"),
    "concentration": ("sub_concentration_insights", "ConcentrationRiskInsightSkill"),
    "liquidity_goal": ("sub_liquidity_goal_insights", "LiquidityGoalInsightSkill"),
    "market": ("sub_market_insights", "MarketInvestmentInsightSkill"),
}


def run_specialist_insight(
    insight_key: str,
    client_id: str,
    meeting_id: str,
    meeting_date: str,
    signal_facts: Optional[list] = None,
) -> dict:
    step_name, skill_title = _SPECIALIST_PROMPTS[insight_key]
    facts_block = (
        f"\n\nPRE-COMPUTED FACTS:\n{json.dumps(signal_facts, default=str)}\n"
        if signal_facts else ""
    )
    prompt = (
        f"Run ONLY {skill_title} for client_id={client_id} (meeting_id={meeting_id}, date={meeting_date})."
        f"{facts_block}\n"
        "Output ONLY valid JSON: {signals: [...]}."
    )
    return _invoke_step(step_name, prompt)


def run_fund_news(client_id: str, meeting_id: str, meeting_date: str) -> dict:
    prompt = (
        f"Generate ONLY the fundNews[] section for client_id={client_id} (meeting_id={meeting_id}, date={meeting_date}).\n"
        "Fetch holdings via get_holdings, live returns via get_benchmark_returns, and news via get_approved_commentary. "
        "Output ONLY valid JSON: {fundNews: [{ticker, fund_name, weight_pct, summary}]}."
    )
    return _invoke_step("sub_fund_news", prompt)


# Backward-compatible aliases
run_snapshot = run_portfolio_snapshot
run_risks = run_risks_and_opportunities
run_angles = run_discussion_angles
run_activity = run_recent_activity
run_assemble = assemble_meeting_brief
