"""Meeting Prep — GCP FastMCP Server (Cloud Run Edition).

Exposes 13 read-only MCP tools over stateless Streamable HTTP at /mcp:

5 Parent Step Tools:
  1. get_portfolio_snapshot
  2. get_risks_and_opportunities
  3. get_discussion_angles
  4. get_recent_activity
  5. get_meeting_brief

8 Granular Sub-Tools (slice warm parent caches with 0 LLM calls, or compute & cache):
  6.  get_holding_trends
  7.  get_portfolio_performance_insights
  8.  get_allocation_drift_and_policy_insights
  9.  get_concentration_risk_insights
  10. get_liquidity_and_goal_insights
  11. get_market_and_investment_insights
  12. get_next_best_actions
  13. get_fund_news
"""

import asyncio
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv

load_dotenv(_ROOT / ".env")

from mcp.server.fastmcp import FastMCP

from agents_meeting_prep.agent_runner import (
    run_discussion_angles,
    run_fund_news,
    run_holding_trends,
    run_portfolio_snapshot,
    run_recent_activity,
    run_risks_and_opportunities,
    run_specialist_insight,
)
from agents_meeting_prep.tools import (
    compute_pre_flight,
    ensure_meeting_context,
    find_client,
    get_cached,
    put_cached,
)

logger = logging.getLogger(__name__)

_port_raw = (os.getenv("PORT") or os.getenv("MEETING_PREP_MCP_PORT") or "").strip()
if not _port_raw:
    raise RuntimeError(
        "Missing required environment variable: PORT or MEETING_PREP_MCP_PORT. Set it in .env or runtime environment."
    )
try:
    _mcp_port = int(_port_raw)
except ValueError as exc:
    raise RuntimeError(f"Invalid MCP port value {_port_raw!r}: must be an integer.") from exc

mcp = FastMCP(
    "meeting_prep",
    stateless_http=True,
    json_response=True,
    host="0.0.0.0",
    port=_mcp_port,
)

_SUB_INSIGHT_CACHE_KEYS = (
    "sub_perf_insights",
    "sub_allocation_insights",
    "sub_concentration_insights",
    "sub_liquidity_goal_insights",
    "sub_market_insights",
)


# ── Client Resolution & Formatting Helpers ───────────────────────────────────

def _resolve_client_from_task(
    task: str,
    explicit_client_id: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str], Optional[str], str]:
    clients = find_client()
    if clients is None:
        return None, None, "Could not reach BigQuery via Context Set to resolve the client.", "error"
    if not clients:
        return None, None, "No client records were found in the database.", "error"

    if explicit_client_id and explicit_client_id.strip():
        clean_id = explicit_client_id.strip()
        match = next((c for c in clients if str(c.get("client_id", "")).upper() == clean_id.upper()), None)
        if match:
            return match["client_id"], match.get("name") or match["client_id"], None, "ok"
        return None, None, f"Client ID '{clean_id}' was not found. Which client would you like me to run this for?", "needs_input"

    task_clean = (task or "").strip()
    if not task_clean:
        return None, None, "Which client would you like me to run this for?", "needs_input"

    task_lower = task_clean.lower()

    for c in clients:
        cid = str(c.get("client_id", "")).strip()
        if cid and re.search(rf"\b{re.escape(cid.lower())}\b", task_lower):
            return cid, c.get("name") or cid, None, "ok"

    full_matches = []
    partial_matches = []
    for c in clients:
        c_name = (c.get("name") or "").strip()
        if not c_name:
            continue
        c_name_lower = c_name.lower()
        if c_name_lower in task_lower:
            full_matches.append((len(c_name), c["client_id"], c_name))
            continue
        c_words = [w for w in re.findall(r"\w+", c_name_lower) if len(w) > 1]
        if not c_words:
            continue
        found_words = sum(1 for w in c_words if re.search(rf"\b{re.escape(w)}\b", task_lower))
        if found_words == len(c_words):
            full_matches.append((len(c_name), c["client_id"], c_name))
        elif found_words > 0:
            partial_matches.append((found_words, c["client_id"], c_name))

    if full_matches:
        full_matches.sort(key=lambda x: x[0], reverse=True)
        top_score = full_matches[0][0]
        tied = [m for m in full_matches if m[0] == top_score]
        if len(tied) > 1:
            names = ", ".join(m[2] for m in tied)
            return None, None, f"Multiple clients match your request ({names}). Which client did you mean?", "needs_input"
        return full_matches[0][1], full_matches[0][2], None, "ok"

    if len(partial_matches) == 1:
        return partial_matches[0][1], partial_matches[0][2], None, "ok"
    if len(partial_matches) > 1:
        names = ", ".join(m[2] for m in partial_matches[:5])
        return None, None, f"Multiple clients partially match your request ({names}). Please specify the full client name.", "needs_input"

    return None, None, "Which client would you like me to run this for?", "needs_input"


def _fmt_currency(val: Any) -> str:
    if isinstance(val, (int, float)):
        return f"${val:,.2f}"
    if val is None:
        return "N/A"
    return str(val)


def _format_envelope(
    status: str,
    reply: str,
    client_id: Optional[str] = None,
    meeting_id: Optional[str] = None,
    result_data: Optional[Dict[str, Any]] = None,
    confidence: float = 1.0,
    evidence: Optional[List[str]] = None,
    question: Optional[str] = None,
    error_code: Optional[str] = None,
) -> Dict[str, Any]:
    if status == "needs_input":
        return {
            "status": "needs_input",
            "data": {},
            "question": question or "Which client would you like me to run this for?",
        }

    if status == "error":
        return {
            "status": "error",
            "error_code": error_code or "internal_error",
            "data": {"message": reply, "client_id": client_id, "meeting_id": meeting_id},
        }

    data_payload: Dict[str, Any] = {}
    if client_id is not None:
        data_payload["client_id"] = client_id
    if meeting_id is not None:
        data_payload["meeting_id"] = meeting_id
    data_payload["reply"] = reply
    if result_data is not None:
        data_payload["result"] = result_data

    return {
        "status": status,
        "data": data_payload,
        "confidence": confidence,
        "evidence": evidence or ["meeting_prep"],
    }


def _resolve_or_error(task: str, client_id: Optional[str]) -> Tuple[Optional[str], Optional[str], Optional[Dict[str, Any]]]:
    cid, cname, msg, res_status = _resolve_client_from_task(task, client_id)
    if cid:
        return cid, cname, None
    if res_status == "error":
        return None, None, _format_envelope(status="error", reply=msg or "Database error", error_code="database_unavailable")
    return None, None, _format_envelope(status="needs_input", reply="", question=msg)


# ── Step & Sub-Step Cache Helpers ────────────────────────────────────────────

async def _get_or_compute_pre_flight(client_id: str, dt: str) -> dict:
    key = f"pre_flight#{client_id}"
    cached = get_cached(key, dt)
    if cached:
        return cached
    computed = await asyncio.to_thread(compute_pre_flight, client_id, dt)
    if computed:
        put_cached(key, dt, computed)
    return computed or {"signal_facts": [], "scenario_fires": {}}


async def _get_or_compute_snapshot(client_id: str, meeting_id: str, dt: str) -> dict:
    key = f"step_snapshot#{client_id}"
    cached = get_cached(key, dt)
    if cached:
        return cached
    res = await asyncio.to_thread(run_portfolio_snapshot, client_id, meeting_id, dt)
    if res:
        put_cached(key, dt, res)
    return res or {}


async def _get_or_compute_risks(client_id: str, meeting_id: str, dt: str) -> dict:
    key = f"step_risks#{client_id}"
    cached = get_cached(key, dt)
    if cached:
        return cached
    pre_flight = await _get_or_compute_pre_flight(client_id, dt)
    cached_sub_signals: list = []
    for skey in _SUB_INSIGHT_CACHE_KEYS:
        sub = get_cached(f"{skey}#{client_id}", dt)
        if sub and isinstance(sub.get("signals"), list):
            cached_sub_signals.extend(sub["signals"])
    res = await asyncio.to_thread(
        run_risks_and_opportunities,
        client_id,
        meeting_id,
        dt,
        pre_flight.get("signal_facts", []),
        cached_sub_signals or None,
    )
    if res:
        put_cached(key, dt, res)
    return res or {}


async def _get_or_compute_angles(client_id: str, meeting_id: str, dt: str) -> dict:
    key = f"step_angles#{client_id}"
    cached = get_cached(key, dt)
    if cached:
        return cached
    pre_flight = await _get_or_compute_pre_flight(client_id, dt)
    risks = await _get_or_compute_risks(client_id, meeting_id, dt)
    res = await asyncio.to_thread(
        run_discussion_angles,
        client_id,
        meeting_id,
        dt,
        risks.get("signals", []),
        pre_flight.get("scenario_fires", {}),
    )
    if res:
        put_cached(key, dt, res)
    return res or {}


async def _get_or_compute_activity(client_id: str, meeting_id: str, dt: str) -> dict:
    key = f"step_activity#{client_id}"
    cached = get_cached(key, dt)
    if cached:
        return cached
    res = await asyncio.to_thread(run_recent_activity, client_id, meeting_id, dt)
    if res:
        put_cached(key, dt, res)
    return res or {}


async def _run_specialist_subtool(
    task: str,
    client_id: Optional[str],
    meeting_id: Optional[str],
    cache_prefix: str,
    insight_key: str,
    producer_skills: Tuple[str, ...],
    fact_types: Tuple[str, ...],
    title: str,
) -> dict:
    cid, cname, err = _resolve_or_error(task, client_id)
    if err:
        return err

    mid, dt = ensure_meeting_context(cid, cname, meeting_id)

    # 1. Reuse parent step_risks cache if already warm (0 LLM calls)
    parent_risks = get_cached(f"step_risks#{cid}", dt)
    if parent_risks and isinstance(parent_risks.get("signals"), list):
        filtered = [
            s for s in parent_risks["signals"]
            if s.get("producer_skill") in producer_skills
        ]
        return _format_envelope(
            status="ok",
            reply=f"{title} for {cname} (ID: {cid}): {len(filtered)} signal(s) from cached risk analysis.",
            client_id=cid,
            meeting_id=mid,
            result_data={"signals": filtered},
            confidence=0.94,
            evidence=[f"sub-skill:{insight_key}", f"Client:{cid}", "BigQuery:cache"],
        )

    # 2. Check sub-tool cache or compute via ADK runner
    sub_key = f"{cache_prefix}#{cid}"
    cached_sub = get_cached(sub_key, dt)
    if not cached_sub:
        try:
            pre_flight = await _get_or_compute_pre_flight(cid, dt)
            relevant_facts = [
                f for f in pre_flight.get("signal_facts", [])
                if f.get("signal_type") in fact_types
            ]
            cached_sub = await asyncio.to_thread(
                run_specialist_insight, insight_key, cid, mid, dt, relevant_facts or None
            )
            if cached_sub:
                put_cached(sub_key, dt, cached_sub)
        except Exception as e:
            logger.error("%s failed for %s: %s", cache_prefix, cid, e, exc_info=True)
            return _format_envelope(
                status="error",
                reply=f"Failed to compute {title}: {e}",
                client_id=cid,
                meeting_id=mid,
                error_code="step_execution_failed",
            )

    signals = (cached_sub or {}).get("signals", [])
    return _format_envelope(
        status="ok",
        reply=f"{title} for {cname} (ID: {cid}): {len(signals)} signal(s) identified.",
        client_id=cid,
        meeting_id=mid,
        result_data={"signals": signals},
        confidence=0.93,
        evidence=[f"sub-skill:{insight_key}", f"Client:{cid}", "BigQuery:wealth-advisor-cs"],
    )


# ── The 5 Parent MCP Tools ───────────────────────────────────────────────────

@mcp.tool(
    description=(
        "Get a client's full portfolio snapshot: holdings table, total value, cost basis, "
        "unrealized gain/loss, YTD/1Y returns, and per-fund trends. Use for overall holdings "
        "or valuation queries; use get_holding_trends if only fund trend classifications are needed. "
        "Pass the advisor request as task (with client ID or name), and optional client_id and meeting_id."
    )
)
async def get_portfolio_snapshot(
    task: str,
    client_id: Optional[str] = None,
    meeting_id: Optional[str] = None,
) -> dict:
    cid, cname, err = _resolve_or_error(task, client_id)
    if err:
        return err

    mid, dt = ensure_meeting_context(cid, cname, meeting_id)
    try:
        snapshot = await _get_or_compute_snapshot(cid, mid, dt)
        if not snapshot or "holdings" not in snapshot:
            return _format_envelope(
                status="error",
                reply=f"Portfolio Snapshot agent returned empty output for {cname} ({cid}).",
                client_id=cid,
                meeting_id=mid,
                error_code="empty_step_output",
            )
    except Exception as e:
        logger.error("get_portfolio_snapshot failed for %s: %s", cid, e, exc_info=True)
        return _format_envelope(
            status="error",
            reply=f"Failed to generate portfolio snapshot: {e}",
            client_id=cid,
            meeting_id=mid,
            error_code="step_execution_failed",
        )

    holdings = snapshot.get("holdings", {})
    total_val = _fmt_currency(holdings.get("totalValue"))
    ytd = holdings.get("returns", {}).get("ytd")
    ytd_str = f"{ytd}%" if ytd is not None else "N/A"
    insights = holdings.get("keyInsight", [])
    insight_summary = insights[0] if insights else "Holdings snapshot compiled."
    reply = f"Portfolio snapshot for {cname} (ID: {cid}): Total Value {total_val}, YTD Return: {ytd_str}. {insight_summary}"

    perf_list = holdings.get("performance", [])
    has_missing = any(h.get("y2025") is None and h.get("ticker") not in ("CASH", "MMKT") for h in perf_list)
    status = "partial" if (has_missing or ytd is None) else "ok"

    return _format_envelope(
        status=status,
        reply=reply,
        client_id=cid,
        meeting_id=mid,
        result_data=snapshot,
        confidence=0.95 if status == "ok" else 0.85,
        evidence=["portfolio-snapshot-agent", f"Client:{cid}", "BigQuery:wealth-advisor-cs", "yfinance"],
    )


@mcp.tool(
    description=(
        "Run a comprehensive multi-domain risk and opportunity assessment across performance, "
        "IPS allocation drift, concentration, liquidity/goal gaps, market triggers, and risk metrics. "
        "Use for an overall risk review; use a targeted insight sub-tool (e.g. get_concentration_risk_insights) "
        "when only one risk domain is requested. Pass the advisor request as task (with client ID or name), "
        "and optional client_id and meeting_id."
    )
)
async def get_risks_and_opportunities(
    task: str,
    client_id: Optional[str] = None,
    meeting_id: Optional[str] = None,
) -> dict:
    cid, cname, err = _resolve_or_error(task, client_id)
    if err:
        return err

    mid, dt = ensure_meeting_context(cid, cname, meeting_id)
    try:
        risks = await _get_or_compute_risks(cid, mid, dt)
        if not risks or ("signals" not in risks and "risk" not in risks):
            return _format_envelope(
                status="error",
                reply=f"Risks and Opportunities agent returned empty output for {cname} ({cid}).",
                client_id=cid,
                meeting_id=mid,
                error_code="empty_step_output",
            )
    except Exception as e:
        logger.error("get_risks_and_opportunities failed for %s: %s", cid, e, exc_info=True)
        return _format_envelope(
            status="error",
            reply=f"Failed to analyze risks and opportunities: {e}",
            client_id=cid,
            meeting_id=mid,
            error_code="step_execution_failed",
        )

    signals = risks.get("signals", [])
    metrics = risks.get("risk", {}).get("riskMetrics", [])
    breaches = [s.get("observation", {}).get("summary", "") for s in signals if s.get("assessment", {}).get("policy_status") == "breach"]
    summary_lead = f"Found {len(signals)} risk signals ({len(breaches)} policy breaches)."
    if breaches:
        summary_lead += f" Primary breach: {breaches[0]}"

    reply = f"Risk & opportunity analysis for {cname} (ID: {cid}): {summary_lead}"
    has_unknown_metric = any(m.get("value") == "Unknown" for m in metrics)
    status = "partial" if has_unknown_metric else "ok"

    return _format_envelope(
        status=status,
        reply=reply,
        client_id=cid,
        meeting_id=mid,
        result_data=risks,
        confidence=0.94 if status == "ok" else 0.85,
        evidence=["risks-and-opportunities-agent", f"Client:{cid}", "BigQuery:wealth-advisor-cs", "Finnhub"],
    )


@mcp.tool(
    description=(
        "Generate full meeting agenda framing: evidence-backed talking points, decision questions, "
        "portfolio tensions, and next best actions. Use for meeting preparation talking points; "
        "use get_next_best_actions if only prioritized action items are needed. "
        "Pass the advisor request as task (with client ID or name), and optional client_id and meeting_id."
    )
)
async def get_discussion_angles(
    task: str,
    client_id: Optional[str] = None,
    meeting_id: Optional[str] = None,
) -> dict:
    cid, cname, err = _resolve_or_error(task, client_id)
    if err:
        return err

    mid, dt = ensure_meeting_context(cid, cname, meeting_id)
    try:
        angles_out = await _get_or_compute_angles(cid, mid, dt)
        if not angles_out:
            return _format_envelope(
                status="error",
                reply=f"Discussion Angles agent returned empty output for {cname} ({cid}).",
                client_id=cid,
                meeting_id=mid,
                error_code="empty_step_output",
            )
    except Exception as e:
        logger.error("get_discussion_angles failed for %s: %s", cid, e, exc_info=True)
        return _format_envelope(
            status="error",
            reply=f"Failed to generate discussion angles: {e}",
            client_id=cid,
            meeting_id=mid,
            error_code="step_execution_failed",
        )

    angles = angles_out.get("discussionAngles", [])
    nbas = angles_out.get("nextBestActions", [])
    if angles:
        top_angle = angles[0]
        reply = (
            f"Discussion angles for {cname} (ID: {cid}): {len(angles)} angle(s) and {len(nbas)} recommended action(s). "
            f"Top priority: '{top_angle.get('title')}' — {top_angle.get('decision')}"
        )
    else:
        reply = f"No critical discussion angles identified for {cname} (ID: {cid}); portfolio remains aligned with policy."

    return _format_envelope(
        status="ok",
        reply=reply,
        client_id=cid,
        meeting_id=mid,
        result_data=angles_out,
        confidence=0.92,
        evidence=["discussion-angles-agent", f"Client:{cid}", "advisory_reasoning_map"],
    )


@mcp.tool(
    description=(
        "Retrieve a client's recent CRM interaction timeline: last 6 advisor interactions, "
        "decisions made, communication channels, and sentiment history. "
        "Pass the advisor request as task (with client ID or name), and optional client_id and meeting_id."
    )
)
async def get_recent_activity(
    task: str,
    client_id: Optional[str] = None,
    meeting_id: Optional[str] = None,
) -> dict:
    cid, cname, err = _resolve_or_error(task, client_id)
    if err:
        return err

    mid, dt = ensure_meeting_context(cid, cname, meeting_id)
    try:
        activity_out = await _get_or_compute_activity(cid, mid, dt)
        if not activity_out or "activity" not in activity_out:
            return _format_envelope(
                status="error",
                reply=f"Recent Activity agent returned empty output for {cname} ({cid}).",
                client_id=cid,
                meeting_id=mid,
                error_code="empty_step_output",
            )
    except Exception as e:
        logger.error("get_recent_activity failed for %s: %s", cid, e, exc_info=True)
        return _format_envelope(
            status="error",
            reply=f"Failed to retrieve recent activity: {e}",
            client_id=cid,
            meeting_id=mid,
            error_code="step_execution_failed",
        )

    activities = activity_out.get("activity", [])
    if activities:
        latest = activities[0]
        reply = (
            f"Recent activity for {cname} (ID: {cid}): {len(activities)} interaction(s) on record. "
            f"Most recent on {latest.get('date')} ({latest.get('channel')}): {latest.get('summary')} [Sentiment: {latest.get('sentiment')}]."
        )
    else:
        reply = f"No recent interactions recorded for {cname} (ID: {cid})."

    return _format_envelope(
        status="ok",
        reply=reply,
        client_id=cid,
        meeting_id=mid,
        result_data=activity_out,
        confidence=0.96,
        evidence=["recent-activity-agent", f"Client:{cid}", "BigQuery:interaction"],
    )


@mcp.tool(
    description=(
        "Assemble the complete end-to-end advisor meeting brief combining client profile, "
        "portfolio snapshot, risk signals, discussion angles, next best actions, activity timeline, "
        "and fund news. Use only when a full meeting brief is requested, not for single-section questions. "
        "Pass the advisor request as task (with client ID or name), and optional client_id and meeting_id."
    )
)
async def get_meeting_brief(
    task: str,
    client_id: Optional[str] = None,
    meeting_id: Optional[str] = None,
) -> dict:
    cid, cname, err = _resolve_or_error(task, client_id)
    if err:
        return err

    mid, dt = ensure_meeting_context(cid, cname, meeting_id)
    try:
        from agents_meeting_prep.orchestrator import run_meeting_prep

        brief = await run_meeting_prep(
            client_id=cid,
            meeting_id=mid,
            meeting_date=dt,
            force_refresh=False,
        )
    except Exception as e:
        logger.error("get_meeting_brief failed for %s: %s", cid, e, exc_info=True)
        return _format_envelope(
            status="error",
            reply=f"Failed to assemble full meeting brief: {e}",
            client_id=cid,
            meeting_id=mid,
            error_code="brief_assembly_failed",
        )

    client_profile = brief.get("client", {})
    aum_str = _fmt_currency(client_profile.get("aum"))
    angles = brief.get("discussionAngles", [])
    top_title = angles[0].get("title") if angles else "Standard Review"

    perf_list = brief.get("holdings", {}).get("holdings", {}).get("performance", [])
    has_missing = any(h.get("y2025") is None and h.get("ticker") not in ("CASH", "MMKT") for h in perf_list)
    status = "partial" if has_missing else "ok"

    reply = (
        f"Full meeting brief prepared for {cname} (ID: {cid}). Total AUM: {aum_str}. "
        f"Top agenda focus: '{top_title}'. PowerPoint brief generated."
    )

    return _format_envelope(
        status=status,
        reply=reply,
        client_id=cid,
        meeting_id=mid,
        result_data=brief,
        confidence=0.95 if status == "ok" else 0.88,
        evidence=["meeting-brief-agent", f"Client:{cid}", "BigQuery:wealth-advisor-cs"],
    )


# ── The 8 Granular MCP Sub-Tools ─────────────────────────────────────────────

@mcp.tool(
    description=(
        "Get per-fund trend classifications (Growing, Stable, Decelerating, Declining) and advisory "
        "remarks comparing YTD vs 2024 returns without generating the full portfolio snapshot. "
        "Pass the advisor request as task (with client ID or name), and optional client_id and meeting_id."
    )
)
async def get_holding_trends(
    task: str,
    client_id: Optional[str] = None,
    meeting_id: Optional[str] = None,
) -> dict:
    cid, cname, err = _resolve_or_error(task, client_id)
    if err:
        return err

    mid, dt = ensure_meeting_context(cid, cname, meeting_id)
    parent = get_cached(f"step_snapshot#{cid}", dt)
    if parent and "holdings" in parent:
        h = parent["holdings"]
        trends = {"performance": h.get("performance", []), "keyInsight": h.get("keyInsight", [])}
        return _format_envelope(
            status="ok",
            reply=f"Holding trends for {cname} (ID: {cid}): {len(trends['performance'])} fund(s) from cached snapshot.",
            client_id=cid,
            meeting_id=mid,
            result_data=trends,
            confidence=0.95,
            evidence=["sub-skill:trend-analysis", f"Client:{cid}", "BigQuery:cache"],
        )

    sub_key = f"sub_trends#{cid}"
    trends = get_cached(sub_key, dt)
    if not trends:
        try:
            trends = await asyncio.to_thread(run_holding_trends, cid, mid, dt)
            if trends:
                put_cached(sub_key, dt, trends)
        except Exception as e:
            return _format_envelope(status="error", reply=f"Failed to get holding trends: {e}", client_id=cid, meeting_id=mid)

    perf = (trends or {}).get("performance", [])
    return _format_envelope(
        status="ok",
        reply=f"Holding trends for {cname} (ID: {cid}): {len(perf)} fund(s) analyzed.",
        client_id=cid,
        meeting_id=mid,
        result_data=trends or {"performance": [], "keyInsight": []},
        confidence=0.94,
        evidence=["sub-skill:trend-analysis", f"Client:{cid}", "yfinance"],
    )


@mcp.tool(
    description=(
        "Get performance-risk signals only: holding-level and portfolio-level return changes "
        "and benchmark gaps using live market returns (not full holdings tables). "
        "Pass the advisor request as task (with client ID or name), and optional client_id and meeting_id."
    )
)
async def get_portfolio_performance_insights(
    task: str,
    client_id: Optional[str] = None,
    meeting_id: Optional[str] = None,
) -> dict:
    return await _run_specialist_subtool(
        task, client_id, meeting_id,
        cache_prefix="sub_perf_insights",
        insight_key="perf",
        producer_skills=("portfolio-performance-insight-mvp",),
        fact_types=(),
        title="Portfolio performance insights",
    )


@mcp.tool(
    description=(
        "Get allocation-drift signals only: check asset-class allocation (Equity, Fixed Income, Cash) "
        "against IPS targets and min/max ranges to surface drift warnings and policy breaches. "
        "Pass the advisor request as task (with client ID or name), and optional client_id and meeting_id."
    )
)
async def get_allocation_drift_and_policy_insights(
    task: str,
    client_id: Optional[str] = None,
    meeting_id: Optional[str] = None,
) -> dict:
    return await _run_specialist_subtool(
        task, client_id, meeting_id,
        cache_prefix="sub_allocation_insights",
        insight_key="allocation",
        producer_skills=("allocation-drift-policy-insight-mvp",),
        fact_types=("allocation_drift", "portfolio_underweight", "portfolio_overweight"),
        title="Allocation drift & policy insights",
    )


@mcp.tool(
    description=(
        "Get concentration-risk signals only: evaluate single-fund, issuer, and sector concentration "
        "against the client's IPS concentration limits. "
        "Pass the advisor request as task (with client ID or name), and optional client_id and meeting_id."
    )
)
async def get_concentration_risk_insights(
    task: str,
    client_id: Optional[str] = None,
    meeting_id: Optional[str] = None,
) -> dict:
    return await _run_specialist_subtool(
        task, client_id, meeting_id,
        cache_prefix="sub_concentration_insights",
        insight_key="concentration",
        producer_skills=("concentration-risk-insight-mvp",),
        fact_types=("security_concentration",),
        title="Concentration risk insights",
    )


@mcp.tool(
    description=(
        "Get liquidity and goal-gap signals only: evaluate idle cash vs net worth, short-term liquidity needs, "
        "goal funding gaps, approaching retirement horizons, and behavioral/interaction alerts. "
        "Pass the advisor request as task (with client ID or name), and optional client_id and meeting_id."
    )
)
async def get_liquidity_and_goal_insights(
    task: str,
    client_id: Optional[str] = None,
    meeting_id: Optional[str] = None,
) -> dict:
    return await _run_specialist_subtool(
        task, client_id, meeting_id,
        cache_prefix="sub_liquidity_goal_insights",
        insight_key="liquidity_goal",
        producer_skills=("liquidity-goal-insight-mvp", "client_monitor"),
        fact_types=("excess_cash", "goal_funding_gap", "retirement_horizon_approaching", "sentiment_trend_negative", "interaction_silence"),
        title="Liquidity & goal insights",
    )


@mcp.tool(
    description=(
        "Get market-regime signals only: retrieve broad market indicators (VIX, SPY, QQQ) and relevant "
        "fund/benchmark commentary to identify market-driven review triggers. "
        "Pass the advisor request as task (with client ID or name), and optional client_id and meeting_id."
    )
)
async def get_market_and_investment_insights(
    task: str,
    client_id: Optional[str] = None,
    meeting_id: Optional[str] = None,
) -> dict:
    return await _run_specialist_subtool(
        task, client_id, meeting_id,
        cache_prefix="sub_market_insights",
        insight_key="market",
        producer_skills=("market-investment-insight-mvp",),
        fact_types=("market_condition_change",),
        title="Market & investment insights",
    )


@mcp.tool(
    description=(
        "Extract prioritized, actionable advisor preparation tasks and follow-ups (nextBestActions) "
        "for a client without returning full discussion-angle framing. "
        "Pass the advisor request as task (with client ID or name), and optional client_id and meeting_id."
    )
)
async def get_next_best_actions(
    task: str,
    client_id: Optional[str] = None,
    meeting_id: Optional[str] = None,
) -> dict:
    cid, cname, err = _resolve_or_error(task, client_id)
    if err:
        return err

    mid, dt = ensure_meeting_context(cid, cname, meeting_id)
    try:
        angles_out = await _get_or_compute_angles(cid, mid, dt)
    except Exception as e:
        return _format_envelope(status="error", reply=f"Failed to compute next best actions: {e}", client_id=cid, meeting_id=mid)

    nbas = (angles_out or {}).get("nextBestActions", [])
    lead = nbas[0].get("label") if nbas else "No immediate actions required."
    return _format_envelope(
        status="ok",
        reply=f"Next best actions for {cname} (ID: {cid}): {len(nbas)} action(s). Top priority: {lead}",
        client_id=cid,
        meeting_id=mid,
        result_data={"nextBestActions": nbas},
        confidence=0.92,
        evidence=["sub-skill:next-best-actions", f"Client:{cid}", "advisory_reasoning_map"],
    )


@mcp.tool(
    description=(
        "Generate concise live news and return summaries only for the client's significant holdings "
        "(>5% portfolio weight) without assembling the full meeting brief. "
        "Pass the advisor request as task (with client ID or name), and optional client_id and meeting_id."
    )
)
async def get_fund_news(
    task: str,
    client_id: Optional[str] = None,
    meeting_id: Optional[str] = None,
) -> dict:
    cid, cname, err = _resolve_or_error(task, client_id)
    if err:
        return err

    mid, dt = ensure_meeting_context(cid, cname, meeting_id)
    parent = get_cached(f"assist#{cid}", dt)
    if parent and "fundNews" in parent:
        news_list = parent.get("fundNews", [])
        return _format_envelope(
            status="ok",
            reply=f"Fund news for {cname} (ID: {cid}): {len(news_list)} fund summary/summaries from cached brief.",
            client_id=cid,
            meeting_id=mid,
            result_data={"fundNews": news_list},
            confidence=0.95,
            evidence=["sub-skill:fund-news", f"Client:{cid}", "BigQuery:cache"],
        )

    sub_key = f"sub_fund_news#{cid}"
    news_out = get_cached(sub_key, dt)
    if not news_out:
        try:
            news_out = await asyncio.to_thread(run_fund_news, cid, mid, dt)
            if news_out:
                put_cached(sub_key, dt, news_out)
        except Exception as e:
            return _format_envelope(status="error", reply=f"Failed to get fund news: {e}", client_id=cid, meeting_id=mid)

    news_list = (news_out or {}).get("fundNews", [])
    return _format_envelope(
        status="ok",
        reply=f"Fund news for {cname} (ID: {cid}): {len(news_list)} significant holding(s) summarized.",
        client_id=cid,
        meeting_id=mid,
        result_data={"fundNews": news_list},
        confidence=0.93,
        evidence=["sub-skill:fund-news", f"Client:{cid}", "Finnhub", "yfinance"],
    )


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
