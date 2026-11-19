"""Meeting Prep — GCP Orchestrator.

Coordinates deterministic pre_compute, step-level BigQuery caching
(wealth_meeting_prep.meeting_prep_cache), and in-process / remote ADK execution
via agent_runner.py. Reuses any already-cached sub-skill insights automatically.
"""

import asyncio
import logging
from datetime import date
from typing import Callable, Optional

from agents_meeting_prep.agent_runner import (
    assemble_meeting_brief,
    run_discussion_angles,
    run_portfolio_snapshot,
    run_recent_activity,
    run_risks_and_opportunities,
)
from agents_meeting_prep.tools import compute_pre_flight, get_cached, put_cached

logger = logging.getLogger(__name__)

_SUB_INSIGHT_KEYS = (
    "sub_perf_insights",
    "sub_allocation_insights",
    "sub_concentration_insights",
    "sub_liquidity_goal_insights",
    "sub_market_insights",
)


async def run_meeting_prep(
    client_id: str,
    meeting_id: str,
    meeting_date: str,
    user_id: str = "",
    task_id: Optional[str] = None,
    progress_fn: Optional[Callable] = None,
    force_refresh: bool = False,
    cache_key: Optional[str] = None,
) -> dict:
    """Run the full meeting prep pipeline using BigQuery cache + agent_runner."""

    def _emit(message: str, pct: int, done: bool = False, result: dict = None):
        if progress_fn and task_id:
            progress_fn(task_id, message, pct, done, result)

    today = meeting_date or date.today().isoformat()

    _emit("Checking cache", 10)
    _cache_key = cache_key or f"assist#{client_id}"
    if not force_refresh:
        cached = get_cached(_cache_key, today, user_id)
        if cached:
            logger.info("BQ Cache hit for cache_key=%s", _cache_key)
            _emit(
                "Loaded from cache",
                100,
                done=True,
                result={"type": "meeting_prep", "data": cached, "meeting_id": meeting_id},
            )
            return cached

    # Pre-compute — deterministic checks via Context Set tools
    pre_flight_key = f"pre_flight#{client_id}"
    pre_flight = None if force_refresh else get_cached(pre_flight_key, today, user_id)
    if not pre_flight:
        pre_flight = await asyncio.to_thread(compute_pre_flight, client_id, today)
        if pre_flight:
            put_cached(pre_flight_key, today, pre_flight, user_id)
    pre_flight = pre_flight or {"signal_facts": [], "scenario_fires": {}}

    # Step 1 — Portfolio Snapshot
    _emit("Building portfolio snapshot", 35)
    step1_key = f"step_snapshot#{client_id}"
    holdings_section = None if force_refresh else get_cached(step1_key, today, user_id)
    if not holdings_section:
        holdings_section = await asyncio.to_thread(run_portfolio_snapshot, client_id, meeting_id, today)
        if holdings_section:
            put_cached(step1_key, today, holdings_section, user_id)

    # Step 2 — Risks & Opportunities (reuses any pre-cached sub-skill signals)
    _emit("Analysing risks and opportunities", 55)
    step2_key = f"step_risks#{client_id}"
    risks_output = None if force_refresh else get_cached(step2_key, today, user_id)
    if not risks_output:
        cached_sub_signals: list = []
        if not force_refresh:
            for skey in _SUB_INSIGHT_KEYS:
                sub = get_cached(f"{skey}#{client_id}", today, user_id)
                if sub and isinstance(sub.get("signals"), list):
                    cached_sub_signals.extend(sub["signals"])
        risks_output = await asyncio.to_thread(
            run_risks_and_opportunities,
            client_id,
            meeting_id,
            today,
            pre_flight.get("signal_facts", []),
            cached_sub_signals or None,
        )
        if risks_output:
            put_cached(step2_key, today, risks_output, user_id)
    signals = (risks_output or {}).get("signals", [])
    risk_section = (risks_output or {}).get("risk", {"riskMetrics": []})

    # Steps 3 + 4 — Parallel
    _emit("Generating discussion angles and activity", 70)
    step3_key = f"step_angles#{client_id}"
    step4_key = f"step_activity#{client_id}"
    angles_output = None if force_refresh else get_cached(step3_key, today, user_id)
    activity_output = None if force_refresh else get_cached(step4_key, today, user_id)

    tasks_to_run = []
    run_angles_idx = None
    run_activity_idx = None

    if not angles_output:
        run_angles_idx = len(tasks_to_run)
        tasks_to_run.append(
            asyncio.to_thread(
                run_discussion_angles,
                client_id,
                meeting_id,
                today,
                signals,
                pre_flight.get("scenario_fires", {}),
            )
        )
    if not activity_output:
        run_activity_idx = len(tasks_to_run)
        tasks_to_run.append(asyncio.to_thread(run_recent_activity, client_id, meeting_id, today))

    if tasks_to_run:
        results = await asyncio.gather(*tasks_to_run)
        if run_angles_idx is not None:
            angles_output = results[run_angles_idx]
            if angles_output:
                put_cached(step3_key, today, angles_output, user_id)
        if run_activity_idx is not None:
            activity_output = results[run_activity_idx]
            if activity_output:
                put_cached(step4_key, today, activity_output, user_id)

    angles_output = angles_output or {}
    activity_output = activity_output or {}

    # Step 5 — Brief Assembler
    _emit("Assembling meeting brief", 88)
    brief = await asyncio.to_thread(
        assemble_meeting_brief,
        client_id,
        meeting_id,
        today,
        {
            "holdings_section": holdings_section,
            "signals": signals,
            "risk_section": risk_section,
            "discussion_angles": angles_output.get("discussionAngles", []),
            "next_best_actions": angles_output.get("nextBestActions", []),
            "activity": activity_output.get("activity", []),
        },
    )

    if not brief:
        raise RuntimeError(f"BriefAssembler returned empty result for client_id={client_id}")

    try:
        from agents_meeting_prep.pptx_utils import html_to_pptx

        await asyncio.to_thread(html_to_pptx, brief, client_id, meeting_id)
    except Exception as e:
        logger.warning("PPTX generation failed: %s", e)

    put_cached(_cache_key, today, brief, user_id)
    _emit(
        "Complete",
        100,
        done=True,
        result={"type": "meeting_prep", "data": brief, "meeting_id": meeting_id},
    )
    return brief
