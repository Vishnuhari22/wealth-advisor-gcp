"""Live smoke-test runner for all 13 MCP tools against http://localhost:8080/mcp.

Usage (in Cloud Shell Tab 2 while mcp_server.py is running in Tab 1):
    python scripts/test_live_tools.py
"""

import json
import sys
import time
import urllib.request

URL = "http://localhost:8080/mcp"

TOOLS = [
    ("get_portfolio_snapshot", "C022"),
    ("get_holding_trends", "C022"),
    ("get_portfolio_performance_insights", "C022"),
    ("get_allocation_drift_and_policy_insights", "C022"),
    ("get_concentration_risk_insights", "C022"),
    ("get_liquidity_and_goal_insights", "C022"),
    ("get_market_and_investment_insights", "C022"),
    ("get_risks_and_opportunities", "C022"),
    ("get_discussion_angles", "C022"),
    ("get_next_best_actions", "C022"),
    ("get_recent_activity", "C022"),
    ("get_fund_news", "C022"),
    ("get_meeting_brief", "Nathan Kerrigan"),
]


def call_tool(name: str, task: str) -> dict:
    body = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": {"task": task}},
    }).encode("utf-8")
    req = urllib.request.Request(
        URL,
        data=body,
        headers={
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        rpc = json.loads(resp.read().decode("utf-8"))
    text = rpc["result"]["content"][0]["text"]
    return json.loads(text)


def main() -> None:
    targets = sys.argv[1:]
    to_run = [t for t in TOOLS if not targets or t[0] in targets]
    passed = 0

    for name, task in to_run:
        t0 = time.time()
        try:
            res = call_tool(name, task)
            dt = round(time.time() - t0, 1)
            status = res.get("status")
            conf = res.get("confidence")
            data = res.get("data", {})
            reply = data.get("reply", "")
            rkeys = list((data.get("result") or {}).keys())
            ok = status in ("ok", "partial")
            if ok:
                passed += 1
            print(f"[{'PASS' if ok else 'FAIL'}] {name} ({dt}s) | status={status} conf={conf} | keys={rkeys}")
            print(f"       reply: {reply}")
            if len(to_run) == 1:
                print(json.dumps(res, indent=2))
            elif not ok:
                print(f"       error: {res}")
            if name == "get_meeting_brief" and ok:
                with open("c022_gcp_brief.json", "w", encoding="utf-8") as f:
                    json.dump(res, f, indent=2)
                print("       saved full brief to c022_gcp_brief.json")
        except Exception as e:
            print(f"[FAIL] {name}: {e}")

    print(f"\nSummary: {passed}/{len(to_run)} tools passed.")


if __name__ == "__main__":
    main()
