---
name: liquidity-goal-insight-mvp
description: Produces evidence-bound liquidity, cash and goal-horizon signals for Meeting Preparation MVP.
version: 1.0.0-mvp
owner: Wealth Advisor Intelligence
risk_classification: advisory-decision-support
client_brain_compatibility: compatible_not_dependent
human_approval_required: true
---

# Liquidity & Goal Insight: MVP Specialist Skill

## 1. Purpose

Identify excess cash, liquidity needs, approaching goals, funding gaps and retirement-related cash-flow requirements using verified planning and financial records.

## 2. Scope and Boundary

This skill produces specialist facts and atomic signals. It is Client-Brain-compatible but not Client-Brain-dependent. Client context may change downstream meeting relevance, prioritization and framing, but must not alter specialist calculations, confidence or source evidence.

The skill does not generate client-specific advice, Discussion Angles, executable actions or trade instructions.

## 3. Inputs
- authorized cash and account balances
- verified goal records and target dates
- funding requirement and current funding source
- known withdrawals, liabilities and reserves
- retirement horizon and approved income need where applicable
- comparison point

## 4. Processing Approach
1. Resolve client/household, accounts and verified goals.
2. Retrieve current cash, known liquidity requirements and approved planning values.
3. **Separate protected liquidity from potentially unallocated cash.**
   - If `liquidity_needs` specifies a numeric reserve (e.g. "$50,000 emergency fund"), use that as the protected amount.
   - If `liquidity_needs` is qualitative (e.g. "High short-term") or unquantified, **do not invent a reserve figure**. Instead, compute the `excess_cash` signal under multiple protection scenarios and present all of them. Use the IPS `cash_target` and `cash_max` as reference points, not as the reserve.
   - **Phased-deployment scenarios to compute when reserve is unquantified:**
     - Scenario A — IPS cash target protected: reserve = `cash_target %` of portfolio; deployable = cash above that
     - Scenario B — IPS cash max protected: reserve = `cash_max %` of portfolio; deployable = cash above that
     - Scenario C — Conservative reserve (50% of cash protected): reserve = 50% of cash balance; deployable = remaining 50%
     - Scenario D — Minimal reserve (IPS cash min protected): reserve = `cash_min %` of portfolio; deployable = cash above that
   - Present all scenarios in the signal's `observation.summary` as a table. Do not select one scenario as the answer.
   - Label each scenario with its assumption so the advisor can validate the right reserve with the client.
4. **Calculate goal funding gap for each verified InvestmentObjective.**
   - gap_pct = (target_amount − net_worth) / target_amount × 100
   - Emit a `goal_funding_gap` signal when: target_amount > net_worth AND gap_pct > 20%.
   - `observation.current_value` = net_worth (number), `observation.target_value` = target_amount (number), `observation.unit` = "USD".
   - `observation.summary` must state the gap amount in dollars and as a percentage, the goal type, and the target date.
   - Materiality: gap_pct 20–40% → medium; 40–60% → high; > 60% → critical.
   - Urgency: if target_date is within 24 months → high; within 60 months → medium; otherwise → low.
   - `policy_status`: gap_pct > 20% → "warning"; gap_pct > 40% → "breach".
   - If target_amount or net_worth is missing, emit the signal with `policy_status: "unknown"` and note the missing field in `limitations`.
   - Do not treat the gap as a portfolio-funding instruction. Frame it as a planning fact requiring advisor validation of funding sources, contribution expectations and return assumptions.
5. Detect approaching goal dates and retirement income needs.
   - Emit `goal_approaching` when months_remaining between today and target_date is ≥ 0 and ≤ 12.
   - Emit `retirement_horizon_approaching` and `retirement_income_need` only when verified retirement goal data is present — do not infer from age.
6. Preserve assumptions, verification state and planning method.
7. Emit normalized signals with client-context references and limitations.

## 5. Signals Produced

```yaml
producer_skill: liquidity-goal-insight-mvp
signal_types:
  - excess_cash
  - liquidity_need
  - goal_approaching
  - goal_funding_gap
  - retirement_horizon_approaching
  - retirement_income_need
```

## 6. Freshness and Materiality

### Freshness

Freshness varies by field: cash follows the latest account cycle; goal dates and priorities remain valid until changed but require reconfirmation under policy; withdrawals and life events can supersede prior values immediately.

### Materiality

Materiality may consider funding-gap amount, time to goal, liquidity coverage, share of portfolio affected, remediation flexibility and explicit priority. Client relevance and urgency remain separate from specialist materiality.

## 7. Meeting Preparation Handoff Contract

```json
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
    "as_of": "ISO-8601 timestamp"
  },
  "assessment": {
    "materiality": "low | medium | high | critical",
    "urgency": "low | medium | high",
    "policy_status": "within | warning | breach | blocked | unknown",
    "confidence": 0.0
  },
  "freshness": {
    "status": "current | aging | stale | expired",
    "expires_at": "ISO-8601 timestamp | null",
    "reassessment_triggers": ["string"]
  },
  "evidence_refs": ["string"],
  "native_result_ref": "string",
  "limitations": ["string"]
}
```

Meeting Preparation may interpret the signal as a standalone risk, opportunity for evaluation, material change or monitor item. The Advisory Reasoning Map may propose fusion with other compatible signals. The specialist skill does not generate the final Discussion Angle.

## 8. Evidence and Confidence

Every material claim and metric must reference the authoritative record or approved calculation. Confidence is based on source quality, freshness, coverage, method validity and consistency. Narrative fluency never increases confidence. Missing or conflicting evidence must lower confidence, qualify the signal or trigger abstention.

## 9. Guardrails
- Do not infer goals, priorities, life events or income needs.
- Do not treat all cash as investable.
- **Do not compute a single excess_cash figure when the liquidity reserve is unquantified — present scenarios instead.**
- **Do not present deployment as a recommendation — frame it as a subject for evaluation and advisor/client approval.**
- **Do not suppress `goal_funding_gap` because `excess_cash` is also present — they are independent signals and must both be emitted when their conditions are met.**
- Do not present planning projections as guaranteed outcomes.
- Do not invent missing values, thresholds, dates, classifications or client context.
- Do not expose unauthorized client, household, account or research data.
- Do not allow downstream Client Brain enrichment to overwrite the sealed specialist result.

## 10. Evaluation Cases
1. Protected liquidity is separated from unallocated cash.
2. When reserve is unquantified, all four IPS-anchored scenarios are computed and presented — no single scenario is selected.
3. Approaching goal uses the verified target date.
4. `goal_funding_gap` signal is emitted when gap_pct > 20% — not suppressed because excess_cash is also present.
4a. Missing target_amount or net_worth produces a signal with `policy_status: "unknown"` and a limitations note — not a silent omission.
5. Retirement income need is not inferred from age or portfolio.
6. Updated goal supersedes older record with lineage preserved.
7. Signal includes assumptions and planning-method reference.
8. Output validates against the Meeting Preparation MVP normalized signal contract.
9. Every output is reconstructable from skill version, source records, methods and configuration.

## 11. Observability

Log request ID, skill version, data sources, as-of times, permissions, method/configuration versions, coverage, freshness decision, materiality decision, signal IDs, evidence references, limitations and downstream disposition where available.

## 12. Release Gates

- 100% pass on fabrication, permission, evidence, sensitive-inference and advice-boundary tests.
- Approved quantitative/reference-case tolerances pass.
- Freshness, expiry and reassessment behavior passes.
- Signal schema compatibility with Meeting Preparation MVP v3.0 passes.
- Client Brain invariance passes: downstream client context does not change specialist facts or confidence.
