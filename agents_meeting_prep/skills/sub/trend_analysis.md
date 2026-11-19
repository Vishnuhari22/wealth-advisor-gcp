---
name: trend-analysis
description: Per-fund trend label, remarks, and portfolio-level key insight bullets. Called by PortfolioSnapshotSkill for each holding.
version: 1.0.0
---

# Trend Analysis Sub-Skill

## Purpose

For each fund in the portfolio, produce a trend label, a one-line advisory remark, and — after processing all funds — 1–2 portfolio-level key insight bullets.

## Inputs (per fund)

```
ticker        — Instrument.instrument_id
asset_class   — Instrument.asset_class
weight        — Holding.actual_weight (%)
y2024         — live_prices[ticker].return_2024 (full-year Jan–Dec 2024, may be null)
y2025         — live_prices[ticker].return_ytd (2025 YTD, may be null for any ticker including CASH and MMKT)
```

## Trend Label Rules

Evaluate `y2026` and `y2025` to assign one label per fund:

| Label | Condition |
|---|---|
| `Growing` | y2025 > 5% AND y2025 > y2024 |
| `Improving` | y2025 > 0% AND y2025 ≤ 5% AND y2025 > y2024 |
| `Declining` | y2025 < 0% |
| `Decelerating` | y2025 > 0% AND y2025 < y2024 AND (y2024 − y2025) > 5pp |
| `Stable` | abs(y2025) ≤ 5% AND abs(y2025 − y2024) ≤ 5pp |
| `Unknown` | y2024 is null OR y2025 is null |

Priority order when multiple conditions could apply: Growing → Improving → Declining → Decelerating → Stable → Unknown.

## Remarks Rules

One short advisory note per fund based on `asset_class` + trend label:

| asset_class | trend | remarks |
|---|---|---|
| Equity | Growing | "Active Growth Core" or "Strong Momentum" |
| Equity | Improving | "Recovery Trend" |
| Equity | Stable | "Holding Steady" |
| Equity | Declining | "Underperforming — Monitor" |
| International / Global | Growing | "Intl Outperformer" |
| International / Global | Declining | "Intl Headwinds" |
| Bond / Fixed Income | Growing | "Bond Rally" |
| Bond / Fixed Income | Declining | "Rate Pressure" or "Duration Risk" |
| Cash / Money Market | any | "Liquidity Reserve" |
| Balanced / Multi-Asset | Growing | "Balanced Outperformer" |
| Balanced / Multi-Asset | Declining | "Balanced Drag" |
| any | Decelerating | "Momentum Fading" |
| any | Unknown | "Insufficient data" |

## Key Insight Bullets

After processing all funds, produce 1–2 bullets summarizing the most material portfolio-level observations. Focus on:
- The highest-weight funds and their trend direction
- Any notable divergence (e.g. equity growing while bonds declining)
- Any fund with Declining trend and weight > 10%

Examples:
- "Equity holdings outperforming YTD; VFIAX and XLI both in Growing trend"
- "Bond allocation under pressure — ABNDX down 3.2% YTD, Declining trend"
- "Portfolio broadly stable; no fund above 5% YTD gain or loss"

## Output Contract

```json
{
  "per_fund": [
    {
      "ticker": "string",
      "trend": "Growing | Improving | Decelerating | Stable | Declining | Unknown",
      "remarks": "string"
    }
  ],
  "keyInsight": ["string"]
}
```

## Guardrails

- Do not invent y2024 or y2025 values not supplied in inputs.
- **Universal fallback:** if either y2024 or y2025 is null for any ticker (including CASH and MMKT), set trend = "Unknown", remarks = "Insufficient data". No exceptions.
- CASH and MMKT get trend = "Stable", remarks = "Liquidity Reserve" only when both y2024 and y2025 are non-null.
- If y2025 is non-null for MMKT, show the value in the 2025 column as yield (not return).
- A fund with y2024 > 0% but significantly below y2024 (gap > 5pp) is Decelerating, not Stable — do not default to Stable when momentum has clearly dropped.
- Do not recommend buying or selling based on trend alone.
- Key insight bullets must reference specific tickers and values — no generic statements.
