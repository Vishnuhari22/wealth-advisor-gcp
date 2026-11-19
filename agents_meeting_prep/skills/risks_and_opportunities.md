---
name: risks-and-opportunities
description: Orchestrates the 5 MVP specialist sub-skills in parallel, collects their normalized signals, and produces the risk metrics panel. Does not generate Discussion Angles.
version: 1.0.0
data_catalog_sections: [1, 2, 3, 4, 5, 6, 7, 8, 9]
source_skills:
  - sub/portfolio_performance_insight.md
  - sub/allocation_drift_policy_insight.md
  - sub/concentration_risk_insight.md
  - sub/liquidity_goal_insight.md
  - sub/market_investment_insight.md
---

# Risks & Opportunities Skill

## Purpose

Run all 5 MVP specialist sub-skills against the client data. Collect their normalized signals. Produce the `risk` section of the brief including the risk metrics panel. Do not generate Discussion Angles — that is DiscussionAnglesSkill's job.

## Pre-Computed Signal Facts

The prompt may contain a `PRE-COMPUTED SIGNAL FACTS` block. These are computed from exact
arithmetic against graph data before this step runs. They are authoritative:
- Each fact MUST appear as a signal in your output
- Use the `policy_status` and `materiality` provided — do not downgrade or override them
- You may enrich with narrative, `evidence_refs`, and `limitations`
- You cannot drop, contradict, or re-evaluate the threshold decision

Narration rule for allocation breach signals: when writing the `observation.summary` for an
`allocation_drift`, `portfolio_overweight`, or `portfolio_underweight` signal, the breach
statement MUST reference the IPS maximum (or minimum) band, not the target.
- WRONG: "Cash is 13.8pp above the 20.0% target" — 20% is the target, not the limit
- RIGHT: "Cash is 3.8pp above the 30.0% IPS maximum" — 30% is the band limit
The pre-computed fact contains both `ips_max` and `ips_target` — use `ips_max` (or `ips_min`
for underweight) as the breach reference. Use `ips_target` only as secondary context.

For signals NOT covered by pre-computed facts (performance, sentiment, market conditions),
apply your normal analysis.

## Data Fetching

Before analysis, fetch all required data using the knowledge catalog sections declared above:
- Section 1 (client_profile) — `graph_query`
- Section 2 (ips_targets) — `graph_query`
- Section 3 (holdings_list) — `graph_query`; traverse Client→Account→Holding→Instrument
- Section 4 (fund_returns_live) — `yfinance_price` for all tickers (skip CASH, MMKT). Returns a **dict keyed by ticker** e.g. `{"VFIAX": {"return_ytd": 13.4, "return_1y": 15.8, ...}}`
- Section 5 (goals) — `graph_query`; traverse Client→InvestmentObjective
- Section 6 (interactions) — `graph_query`; last 6 Interaction+Decision nodes
- Section 7 (open_risks) — `graph_query`; OPEN Risk nodes linked to client
- Section 8 (fund_news) — call `finnhub_news_with_benchmarks(client_id)` — pass only the `client_id`. The tool fetches significant holdings and their benchmarks from the graph internally and returns `{ticker: {recent_headlines, article_count, sourced_from}}`.
- Section 9 (market_indicators) — `get_market_indicators`
- Portfolio beta — call `get_portfolio_beta` with a list of `{ticker, weight}` for all holdings where ticker is NOT CASH or MMKT. This call is **required** — do not skip it. The result drives the Volatility Reactivity metric. If the call returns `error: null` and `portfolio_beta` is a number, use that number. Only set Volatility Reactivity to `Unknown` if `portfolio_beta` is null or `error` is non-null.
- Section 9 (market_indicators) — `get_market_indicators`
- Portfolio beta — call `get_portfolio_beta` with a list of `{ticker, weight}` for all holdings where ticker is NOT CASH or MMKT. This call is **required** — do not skip it. The result drives the Volatility Reactivity metric. If the call returns `error: null` and `portfolio_beta` is a number, use that number. Only set Volatility Reactivity to `Unknown` if `portfolio_beta` is null or `error` is non-null.

Construct all Cypher queries adhoc using `$client_id` as the parameter.

## Sub-Skill Invocation

Run these 5 sub-skills. Each produces normalized signals in the canonical contract from `master_meeting_prep.md §3`.

| Sub-skill | File | Signals produced |
|---|---|---|
| Portfolio Performance Insight | `sub/portfolio_performance_insight.md` | performance_change, benchmark_gap, holding_trend |
| Allocation Drift & Policy Insight | `sub/allocation_drift_policy_insight.md` | allocation_drift, portfolio_overweight, portfolio_underweight, policy_deviation |
| Concentration Risk Insight | `sub/concentration_risk_insight.md` | security_concentration, issuer_concentration, sector_concentration |
| Liquidity & Goal Insight | `sub/liquidity_goal_insight.md` | excess_cash, liquidity_need, goal_approaching, goal_funding_gap, retirement_horizon_approaching, retirement_income_need |
| Market & Investment Insight | `sub/market_investment_insight.md` | market_condition_change, investment_review_trigger, market_evidence |

Each sub-skill receives only the data it needs (see its own skill file for inputs). Do not pass the full data package to every sub-skill.

## Grounding Sub-Skills to Graph Data

The specialist sub-skills were written for a generic data model. Ground them to our graph as follows:

**Portfolio Performance Insight:**
- "authorized holdings and valuations" = Holding.market_value, Holding.cost_basis from graph
- "approved benchmark" = Instrument.benchmark_index from graph
- "returns" = live_prices[ticker].return_ytd and return_1y
- A `holding_trend` signal MUST be produced for every holding with weight > 8% (excluding CASH and MMKT). Do not omit a significant holding from Portfolio Performance because its performance is positive or unremarkable — weight alone makes it material.

**Allocation Drift & Policy Insight:**
- "approved strategic target allocation" = IPS.equity_target, bond_target, cash_target
- "allowed ranges" = IPS.equity_min/max, bond_min/max, cash_min/max
- "current weights" = compute from Holding.market_value grouped by Instrument.asset_class

**Concentration Risk Insight:**
- "authorized holdings" = Holding.actual_weight from graph
- "approved concentration reference" = IPS.max_single_fund_weight
- "sector classifications" = Instrument.asset_class (proxy for sector in our data model)

**Liquidity & Goal Insight:**
- "cash and account balances" = Client.cash_balance from graph
- "verified goal records" = InvestmentObjective.target_date, target_amount, type from graph
- "known liquidity requirements" = Client.liquidity_needs from graph

**Market & Investment Insight:**
- "approved market data" = market_indicators (VIX, S&P500 YTD) + live_prices
- "fund news" = Finnhub headlines (passed in data package)
- "covered securities" = tickers from holdings

## Risk Metrics Panel

After collecting all signals, produce `risk.riskMetrics[]` from the following mappings.

**CRITICAL: `value` must be a single word or short phrase (≤ 4 words). Never put evidence sentences in `value`. Evidence belongs in the signal body below the panel.**

| Metric | Value format | Status rule |
|---|---|---|
| Override Score | `{n} / {total}` e.g. `2 / 2` | green = 0, amber = 1, red ≥ 2 client-initiated |
| Volatility Reactivity | `Low` / `Moderate` / `High` / `Unknown` | Classify from `get_portfolio_beta` result: green = Low (beta ≤ 0.75), amber = Moderate (0.75 < beta ≤ 1.0), red = High (beta > 1.0), amber = Unknown (only when portfolio_beta is null or error is non-null) |
| Behavioral Risk | `Low` / `Medium` / `High` | green = Low, amber = Medium, red = High (all recent interactions negative = High) |
| Sentiment | Last Interaction.sentiment value, title-cased e.g. `Risk-Averse` | green = positive/neutral, amber = cautious, red = anxious/risk_averse/worried/frustrated |
| IPS Alignment | `Within Band` / `Warning` / `Breach` | green = within, amber = warning, red = breach |

Also surface any OPEN Risk nodes from the graph (from client monitor) as pre-existing signals — label them with `source: "client_monitor"`.
For client_monitor Risk nodes that do not map to a sub-skill signal type (e.g. `interaction_silence`,
`sentiment_trend_negative`), assign `producer_skill: "client_monitor"` and place them in the
`Liquidity & Goals` section (`producer_skill: "liquidity-goal-insight-mvp"` is wrong for these —
use `"client_monitor"` so the assembler places them correctly). These MUST appear in the output
if their status is OPEN — they are pre-existing confirmed risks, not LLM inferences.

## Signal Deduplication

After collecting all signals from the 5 sub-skills and client monitor Risk nodes, run a deduplication pass before writing the output:

1. Group signals by `signal_type`
2. Within each group, if two signals have `observation.current_value` within 5% of each other (or are the same string value), they represent the same underlying fact
3. Keep the signal with the higher `assessment.materiality` (critical > high > medium > low). If equal, keep the one from the specialist sub-skill over `client_monitor`
4. Merge the dropped signal's `evidence_refs` into the kept signal's `evidence_refs`
5. Never emit two signals for the same underlying fact

## Output Contract

```json
{
  "signals": [
    {
      "signal_id": "string",
      "producer_skill": "string",
      "signal_type": "string",
      "subject": {"entity_type": "string", "entity_ref": "string"},
      "observation": {
        "summary": "string",
        "current_value": "number | string | null",
        "previous_value": "number | string | null",
        "target_value": "number | string | null",
        "unit": "string | null",
        "as_of": "ISO-8601"
      },
      "assessment": {
        "materiality": "low | medium | high | critical",
        "urgency": "low | medium | high",
        "policy_status": "within | warning | breach | blocked | unknown",
        "confidence": 0.0
      },
      "freshness": {"status": "current | aging | stale | expired"},
      "evidence_refs": ["string"],
      "limitations": ["string"]
    }
  ],
  "risk": {
    "riskMetrics": [
      {
        "label": "string",
        "value": "string",
        "status": "green | amber | red"
      }
    ]
  }
}
```

## Guardrails (inherited from specialist sub-skills)

- Do not recalculate values already in the graph — use graph values as authoritative.
- Do not assume every drift requires rebalancing.
- Do not equate portfolio weight with risk contribution.
- Do not infer goals, priorities or life events not in the data.
- A market condition signal alone remains research/monitoring unless it intersects materially with portfolio position.
- Preserve counter-evidence and limitations from each sub-skill.
