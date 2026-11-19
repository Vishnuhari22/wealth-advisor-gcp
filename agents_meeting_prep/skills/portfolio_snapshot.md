---
name: portfolio-snapshot
description: Assembles the holdings table and portfolio summary from graph data + live yfinance prices. Delegates per-fund trend labelling to TrendAnalysisSubSkill.
version: 1.0.0
data_catalog_sections: [1, 2, 3, 4]
---

# Portfolio Snapshot Skill

## Purpose

Produce the `holdings` section of the meeting brief: the performance table, portfolio totals, returns strip, and 1–2 key insight bullets.

## Data Fetching

Before analysis, fetch all required data using the knowledge catalog sections declared above:
- Section 1 (client_profile) — fetch via `graph_query`
- Section 2 (ips_targets) — fetch via `graph_query`
- Section 3 (holdings_list) — fetch via `graph_query`; construct Cypher to traverse Client→Account→Holding→Instrument
- Section 4 (fund_returns_live) — fetch via `yfinance_price` for **all** tickers from Section 3 (skip CASH only). MMKT is included. Returns a **dict keyed by ticker** e.g. `{"VFIAX": {"return_ytd": 13.4, "return_2024": 18.2, "return_1y": 15.8, ...}}`

Construct all Cypher queries adhoc using `$client_id` as the parameter. Do not hardcode client IDs.

## Column Production Rules

For each holding, produce one row in `holdings.performance[]`:

| Output field | Source | Rule |
|---|---|---|
| `ticker` | `Instrument.instrument_id` | Direct |
| `type` | `Instrument.category` | Direct |
| `weight` | `Holding.actual_weight` | Direct, as % |
| `y2024` | `live_prices[ticker]["return_2024"]` | Full-year 2024 from yfinance (Jan–Dec 2024) |
| `y2025` | `live_prices[ticker]["return_ytd"]` | Live YTD 2025 from yfinance dict |
| `trend` | TrendAnalysisSubSkill | Computed |
| `remarks` | TrendAnalysisSubSkill | Computed |

## Portfolio Totals

```
totalValue    = sum(Holding.market_value) across all holdings
costBasis     = sum(Holding.cost_basis) across all holdings
unrealizedGL  = totalValue - costBasis
```

Use graph values only. Do not recalculate from prices.

## Returns Strip

`holdings.returns[]` — weighted portfolio returns using live_prices.

CASH has no live price. MMKT has a YTD yield value but must still be excluded from portfolio return calculations — exclude both from the numerator and the denominator.
Normalize weights to the investable subset only:

```
investable_holdings = holdings where ticker NOT IN (CASH, MMKT) AND live_prices[ticker] exists
investable_weight_sum = sum(h.actual_weight for h in investable_holdings)

ytd = sum(h.actual_weight × live_prices[h.ticker]["return_ytd"] for h in investable_holdings)
      / investable_weight_sum

1y  = sum(h.actual_weight × live_prices[h.ticker]["return_1y"]  for h in investable_holdings)
      / investable_weight_sum
```

If `investable_weight_sum` is 0 or no live prices are available, set both to null.
Do not leave `1y` null just because CASH/MMKT have no return — they are excluded by design.

## Key Insights

TrendAnalysisSubSkill produces 1–2 `keyInsight` bullets after processing all funds. Pass these through directly to the output contract.

## Output Contract

```json
{
  "holdings": {
    "totalValue": 0.0,
    "costBasis": 0.0,
    "unrealizedGL": 0.0,
    "performance": [
      {
        "ticker": "string",
        "type": "string",
        "weight": 0.0,
        "y2024": "number | null",
        "y2025": "number | null",
        "trend": "Growing | Improving | Stable | Declining | Unknown",
        "remarks": "string"
      }
    ],
    "returns": {
      "ytd": 0.0,
      "1y": 0.0
    },
    "keyInsight": ["string"]
  }
}
```

## TrendAnalysisSubSkill

Delegate per-fund trend labelling and key insight generation to `sub/trend_analysis.md`.

For each holding, pass: `{ticker, asset_class, weight, y2024, y2025}`.
Receive back: `{trend, remarks}` per fund and `keyInsight[]` across all funds.

CASH has no yfinance ticker — pass y2024 = null, y2025 = null. MMKT now has a live y2025 YTD yield value from yfinance_price — pass it through, or null if fetch failed. TrendAnalysisSubSkill applies the universal fallback: if either y2024 or y2025 is null for any ticker including CASH and MMKT, set trend="Unknown", remarks="Insufficient data". Only if both values are present does CASH/MMKT get trend="Stable", remarks="Liquidity Reserve".

## Guardrails

- Do not invent prices or returns not returned by live_prices.
- Do not calculate totalValue from prices — use Holding.market_value from graph.
- If live_prices is missing for a ticker, pass y2025=null to TrendAnalysisSubSkill.
- Do not recommend buying or selling based on trend alone.
