"""Offline verification tests for wa_gcp/agents_meeting_prep (13 MCP tools + caching + naming)."""

import asyncio
import json
import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

for _k, _v in {
    "GCP_PROJECT_ID": "agentic-wealthadvisor",
    "BQ_DATASET": "wealth_advisor",
    "BQ_MP_DATASET": "wealth_meeting_prep",
    "BQ_LOG_DATASET": "wealth_logs_and_cache",
    "GCP_LOCATION": "us-west1",
    "CONTEXT_SET_ID": "wealth-advisor-cs-test",
    "MEETING_PREP_AGENT_MODE": "in_process",
    "PORT": "8080",
}.items():
    os.environ.setdefault(_k, _v)

_WA_GCP = Path(__file__).resolve().parents[1]
if str(_WA_GCP) not in sys.path:
    sys.path.insert(0, str(_WA_GCP))


def _stub(name: str, attrs: dict | None = None):
    if name in sys.modules:
        return
    m = types.ModuleType(name)
    for k, v in (attrs or {}).items():
        setattr(m, k, v)
    sys.modules[name] = m


_stub("google")
_stub("google.auth", {"default": MagicMock(return_value=(MagicMock(), "agentic-wealthadvisor"))})
_stub("google.auth.transport")
_stub("google.auth.transport.requests", {"Request": MagicMock()})
_stub("google.cloud")
_stub("google.cloud.bigquery", {"Client": MagicMock(), "QueryJobConfig": MagicMock(), "ScalarQueryParameter": MagicMock(), "ArrayQueryParameter": MagicMock()})
_stub("google.cloud.dataplex_v1", {"CatalogServiceClient": MagicMock(), "EntryView": MagicMock(), "GetEntryRequest": MagicMock(), "ListEntriesRequest": MagicMock()})
_stub("google.cloud.firestore", {"Client": MagicMock(), "SERVER_TIMESTAMP": "SERVER_TIMESTAMP"})
_stub("google.adk")
_stub("google.adk.agents", {"LlmAgent": MagicMock()})
_stub("google.adk.models", {"Gemini": type("Gemini", (), {"__init__": lambda self, **kw: None})})
_stub("google.adk.runners", {"InMemoryRunner": MagicMock()})
_stub("google.adk.tools", {"BaseTool": MagicMock()})
_stub("google.adk.tools.tool_context", {"ToolContext": MagicMock()})
_stub("google.genai", {"Client": MagicMock()})
_stub("google.genai.types", {
    "Content": MagicMock(),
    "Part": MagicMock(),
    "GenerateContentConfig": MagicMock(),
    "ThinkingConfig": MagicMock(),
})
_stub("vertexai", {"init": MagicMock()})
_stub("vertexai.agent_engines", {"get": MagicMock()})

import agents_meeting_prep.agent_runner as agent_runner
import agents_meeting_prep.mcp_server as mcp_server
import agents_meeting_prep.orchestrator as orchestrator
import agents_meeting_prep.tools as mp_tools

SAMPLE_CLIENTS = [
    {"client_id": "C022", "name": "Nathan Kerrigan"},
    {"client_id": "C005", "name": "David Thompson"},
]

SAMPLE_PRE_FLIGHT = {
    "signal_facts": [
        {"signal_type": "security_concentration", "ticker": "MMKT", "policy_status": "breach"},
        {"signal_type": "excess_cash", "cash_balance": 340000.0, "policy_status": "breach"},
    ],
    "scenario_fires": {"CASH_DEPLOYMENT": True, "CONCENTRATION_MANAGEMENT": True},
}

SAMPLE_SNAPSHOT = {
    "holdings": {
        "totalValue": 1005829.0,
        "returns": {"ytd": 4.55, "1y": 6.26},
        "performance": [{"ticker": "VFIAX", "y2025": 11.26, "trend": "Decelerating"}],
        "keyInsight": ["VFIAX returned 11.26% YTD."],
    }
}

SAMPLE_RISKS = {
    "signals": [
        {
            "signal_id": "SIG-CONC-1",
            "producer_skill": "concentration-risk-insight-mvp",
            "assessment": {"policy_status": "breach"},
            "observation": {"summary": "MMKT 33.8% > 20%"},
        },
        {
            "signal_id": "SIG-LIQ-1",
            "producer_skill": "liquidity-goal-insight-mvp",
            "assessment": {"policy_status": "breach"},
            "observation": {"summary": "Excess cash $340,000"},
        },
    ],
    "risk": {"riskMetrics": [{"label": "IPS Alignment", "value": "Breach", "status": "red"}]},
}

SAMPLE_ANGLES = {
    "discussionAngles": [{"title": "Cash Deployment Review", "decision": "Protect reserve?"}],
    "nextBestActions": [{"label": "Quantify protected liquidity reserve", "resolution_owner": "client_led"}],
}

SAMPLE_ACTIVITY = {
    "activity": [{"date": "10 Dec 2025", "channel": "meeting", "summary": "Annual review", "sentiment": "Concerned"}]
}


@pytest.fixture
def fake_gcp(monkeypatch):
    cache = {}
    calls = {"pre_flight": 0, "snapshot": 0, "risks": 0, "angles": 0, "activity": 0, "assemble": 0, "sub": 0}

    def fake_get(k, dt, uid=""):
        return cache.get((f"{uid}#{k}" if uid else k, dt))

    def fake_put(k, dt, data, uid=""):
        cache[(f"{uid}#{k}" if uid else k, dt)] = data

    def fake_pf(cid, dt=None):
        calls["pre_flight"] += 1
        return SAMPLE_PRE_FLIGHT

    def fake_snap(cid, mid, dt):
        calls["snapshot"] += 1
        return SAMPLE_SNAPSHOT

    def fake_risk(cid, mid, dt, sf=None, cached_sub=None):
        calls["risks"] += 1
        return SAMPLE_RISKS

    def fake_ang(cid, mid, dt, sigs, sf=None):
        calls["angles"] += 1
        return SAMPLE_ANGLES

    def fake_act(cid, mid, dt):
        calls["activity"] += 1
        return SAMPLE_ACTIVITY

    def fake_asm(cid, mid, dt, prior):
        calls["assemble"] += 1
        return {
            "client": {"clientId": cid, "name": "Nathan Kerrigan", "aum": "$1,005,829"},
            "holdings": SAMPLE_SNAPSHOT,
            "risk": SAMPLE_RISKS["risk"],
            "discussionAngles": SAMPLE_ANGLES["discussionAngles"],
            "nextBestActions": SAMPLE_ANGLES["nextBestActions"],
            "activity": SAMPLE_ACTIVITY["activity"],
            "fundNews": [{"ticker": "VFIAX", "weight_pct": 21.2, "summary": "Up 11.26% YTD"}],
        }

    def fake_sub_insight(ikey, cid, mid, dt, sf=None):
        calls["sub"] += 1
        return {"signals": [s for s in SAMPLE_RISKS["signals"] if ikey in s["producer_skill"] or ikey == "concentration" and "concentration" in s["producer_skill"]]}

    monkeypatch.setattr(mcp_server, "find_client", lambda: SAMPLE_CLIENTS)
    monkeypatch.setattr(mcp_server, "ensure_meeting_context", lambda cid, cname, mid=None: (mid or f"direct_{cid}", "2026-11-19"))
    monkeypatch.setattr(mcp_server, "compute_pre_flight", fake_pf)
    monkeypatch.setattr(orchestrator, "compute_pre_flight", fake_pf)
    monkeypatch.setattr(mcp_server, "get_cached", fake_get)
    monkeypatch.setattr(mcp_server, "put_cached", fake_put)
    monkeypatch.setattr(orchestrator, "get_cached", fake_get)
    monkeypatch.setattr(orchestrator, "put_cached", fake_put)
    monkeypatch.setattr(mcp_server, "run_portfolio_snapshot", fake_snap)
    monkeypatch.setattr(mcp_server, "run_risks_and_opportunities", fake_risk)
    monkeypatch.setattr(mcp_server, "run_discussion_angles", fake_ang)
    monkeypatch.setattr(mcp_server, "run_recent_activity", fake_act)
    monkeypatch.setattr(mcp_server, "run_specialist_insight", fake_sub_insight)
    monkeypatch.setattr(mcp_server, "run_holding_trends", lambda cid, mid, dt: (calls.__setitem__("sub", calls["sub"] + 1) or {"performance": SAMPLE_SNAPSHOT["holdings"]["performance"], "keyInsight": []}))
    monkeypatch.setattr(mcp_server, "run_fund_news", lambda cid, mid, dt: (calls.__setitem__("sub", calls["sub"] + 1) or {"fundNews": [{"ticker": "VFIAX", "summary": "Up 11.26%"}]}))
    monkeypatch.setattr(orchestrator, "run_portfolio_snapshot", fake_snap)
    monkeypatch.setattr(orchestrator, "run_risks_and_opportunities", fake_risk)
    monkeypatch.setattr(orchestrator, "run_discussion_angles", fake_ang)
    monkeypatch.setattr(orchestrator, "run_recent_activity", fake_act)
    monkeypatch.setattr(orchestrator, "assemble_meeting_brief", fake_asm)
    return {"cache": cache, "calls": calls}


def test_thirteen_tools_registered():
    names = sorted(t.name for t in asyncio.run(mcp_server.mcp.list_tools()))
    expected = sorted([
        "get_portfolio_snapshot",
        "get_risks_and_opportunities",
        "get_discussion_angles",
        "get_recent_activity",
        "get_meeting_brief",
        "get_holding_trends",
        "get_portfolio_performance_insights",
        "get_allocation_drift_and_policy_insights",
        "get_concentration_risk_insights",
        "get_liquidity_and_goal_insights",
        "get_market_and_investment_insights",
        "get_next_best_actions",
        "get_fund_news",
    ])
    assert names == expected


def test_business_context_aliases():
    import agents_meeting_prep.agent as agent_mod

    tool_names = [fn.__name__ for fn in agent_mod._AGENT_TOOLS]
    assert len(tool_names) == len(set(tool_names)), f"Duplicate tool names: {tool_names}"
    assert mp_tools.fetch_all_clients_via_cs is mp_tools.find_client
    assert mp_tools.get_meeting_context.__name__ == "get_meeting_context"
    assert mp_tools.yfinance_price.__name__ == "yfinance_price"
    assert mp_tools.finnhub_news_with_benchmarks.__name__ == "finnhub_news_with_benchmarks"
    assert agent_runner.run_snapshot is agent_runner.run_portfolio_snapshot
    assert agent_runner.run_risks is agent_runner.run_risks_and_opportunities
    assert agent_runner.run_angles is agent_runner.run_discussion_angles
    assert agent_runner.run_activity is agent_runner.run_recent_activity
    assert agent_runner.run_assemble is agent_runner.assemble_meeting_brief


def test_subtool_slices_warm_parent_cache(fake_gcp):
    calls = fake_gcp["calls"]
    asyncio.run(mcp_server.get_risks_and_opportunities("Risks for Nathan Kerrigan"))
    assert calls["risks"] == 1

    # Sub-tool must slice from step_risks#C022 with 0 new agent calls
    conc = asyncio.run(mcp_server.get_concentration_risk_insights("Concentration for C022"))
    assert conc["status"] == "ok"
    assert len(conc["data"]["result"]["signals"]) == 1
    assert calls["sub"] == 0 and calls["risks"] == 1


def test_cold_subtool_caches_and_parent_brief_slices(fake_gcp):
    calls = fake_gcp["calls"]
    conc = asyncio.run(mcp_server.get_concentration_risk_insights("Concentration for C022"))
    assert conc["status"] == "ok" and calls["sub"] == 1
    assert ("sub_concentration_insights#C022", "2026-11-19") in fake_gcp["cache"]

    # Full brief populates assist#C022, then get_fund_news slices from assist#C022 (0 new sub calls)
    asyncio.run(mcp_server.get_meeting_brief("Brief for Nathan Kerrigan"))
    news = asyncio.run(mcp_server.get_fund_news("Fund news for C022"))
    assert news["status"] == "ok" and len(news["data"]["result"]["fundNews"]) == 1
    assert calls["sub"] == 1


def test_agent_runner_mode_routing(monkeypatch):
    seen = []
    monkeypatch.setattr(agent_runner, "_invoke_in_process_step", lambda s, p, user_id="mcp-server": seen.append(("in_process", s)) or {"ok": True})
    monkeypatch.setattr(agent_runner, "_invoke_remote_step", lambda s, p, user_id="mcp-server": seen.append(("remote", s)) or {"ok": True})

    monkeypatch.setenv("MEETING_PREP_AGENT_MODE", "in_process")
    agent_runner.run_portfolio_snapshot("C022", "m1", "2026-11-19")
    monkeypatch.setenv("MEETING_PREP_AGENT_MODE", "remote")
    agent_runner.run_portfolio_snapshot("C022", "m1", "2026-11-19")
    assert seen == [("in_process", "snapshot"), ("remote", "snapshot")]


def test_stateless_http_mcp_endpoint(fake_gcp):
    async def _call():
        app = mcp_server.mcp.streamable_http_app()
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
                h = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
                r1 = await c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
                r2 = await c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "get_next_best_actions", "arguments": {"task": "NBAs for Nathan Kerrigan"}}})
                return r1, r2

    r1, r2 = asyncio.run(_call())
    assert r1.status_code == 200 and len(r1.json()["result"]["tools"]) == 13
    assert r2.status_code == 200
    payload = json.loads(r2.json()["result"]["content"][0]["text"])
    assert payload["status"] == "ok" and len(payload["data"]["result"]["nextBestActions"]) == 1


def test_meeting_brief_progress_tracking(fake_gcp, monkeypatch):
    writes = []

    class _FakeDoc:
        def __init__(self, doc_id):
            self.doc_id = doc_id

        def set(self, payload):
            writes.append((self.doc_id, payload))

    class _FakeCol:
        def document(self, doc_id):
            return _FakeDoc(doc_id)

    class _FakeFS:
        def collection(self, name):
            assert name == "meeting_prep_progress"
            return _FakeCol()

    monkeypatch.setattr(mcp_server, "_FS_CLIENT", _FakeFS())

    # Cold run with task_id -> checkpoints 10, 30, 50, 70, 85, 95, 100
    res_with_id = asyncio.run(mcp_server.get_meeting_brief("Brief for Nathan Kerrigan", task_id="abc123xyz"))
    assert res_with_id["status"] == "ok"
    assert [w[1]["pct"] for w in writes] == [10, 30, 50, 70, 85, 95, 100]
    assert all(w[0] == "abc123xyz" for w in writes)
    assert writes[-1][1]["done"] is True
    assert writes[-1][1]["result"] == res_with_id

    # Without task_id -> 0 Firestore writes
    writes.clear()
    res_no_id = asyncio.run(mcp_server.get_meeting_brief("Brief for Nathan Kerrigan"))
    assert res_no_id["status"] == "ok"
    assert writes == []

    # Warm cache hit with task_id -> 10, 100 (done=True, result present)
    res_warm = asyncio.run(mcp_server.get_meeting_brief("Brief for Nathan Kerrigan", task_id="warm123"))
    assert res_warm["status"] == "ok"
    assert [w[1]["pct"] for w in writes] == [10, 100]
    assert writes[-1][1]["done"] is True
    assert writes[-1][1]["result"] == res_warm
