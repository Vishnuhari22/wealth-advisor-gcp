---
name: allocation-drift-policy-insight-mvp
description: Produces evidence-bound asset-allocation drift and policy-status signals for Meeting Preparation MVP.
version: 1.0.0-mvp
owner: Wealth Advisor Intelligence
risk_classification: advisory-decision-support
client_brain_compatibility: compatible_not_dependent
human_approval_required: true
---

# Allocation Drift & Policy Insight: MVP Specialist Skill

## 1. Purpose

Identify material divergence between current portfolio allocation and approved strategic targets or ranges, including persistent drift and policy warnings or breaches.

## 2. Scope and Boundary

This skill produces specialist facts and atomic signals. It is Client-Brain-compatible but not Client-Brain-dependent. Client context may change downstream meeting relevance, prioritization and framing, but must not alter specialist calculations, confidence or source evidence.

The skill does not generate client-specific advice, Discussion Angles, executable actions or trade instructions.

## 3. Inputs
- authorized holdings and market values
- canonical asset-class classifications
- approved strategic target allocation
- allowed ranges or IPS bands
- portfolio/account scope
- comparison point and policy effective date

## 4. Processing Approach
1. Resolve portfolio scope and current approved policy profile.
2. Normalize holdings to canonical asset classes without double counting.
3. Calculate current weights using authoritative market values.
4. Compare weights with targets and allowed ranges.
5. Measure direction, magnitude and persistence of drift.
6. Classify within range, warning or breach using versioned rules.
7. Emit normalized drift signals with policy and calculation evidence.

## 5. Signals Produced

```yaml
producer_skill: allocation-drift-policy-insight-mvp
signal_types:
  - allocation_drift
  - portfolio_overweight
  - portfolio_underweight
  - policy_deviation
```

## 6. Freshness and Materiality

### Freshness

Freshness depends on current holdings, prices, classifications and the effective policy version. Reassess after a material transaction, price movement, policy update or classification correction.

### Materiality

Materiality may consider percentage-point deviation, range breach, market-value impact, duration of the drift and policy significance. Policy breach is a hard status, not merely a score.

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
- Do not assume every drift requires rebalancing.
- Do not treat a target as current without effective-date validation.
- Do not silently blend conflicting classifications.
- Do not treat cash above target as fully investable without liquidity evidence.
- Do not invent missing values, thresholds, dates, classifications or client context.
- Do not expose unauthorized client, household, account or research data.
- Do not allow downstream Client Brain enrichment to overwrite the sealed specialist result.

## 10. Evaluation Cases
1. Current allocation reconciles to holdings and market value.
2. Drift uses the currently effective target and range.
3. Within-range exposure produces no breach signal.
4. Persistent drift is distinguished from temporary drift.
5. Missing target produces qualified output.
6. Cash drift does not become a deployment recommendation.
7. Output validates against the Meeting Preparation MVP normalized signal contract.
8. Every output is reconstructable from skill version, source records, methods and configuration.

## 11. Observability

Log request ID, skill version, data sources, as-of times, permissions, method/configuration versions, coverage, freshness decision, materiality decision, signal IDs, evidence references, limitations and downstream disposition where available.

## 12. Release Gates

- 100% pass on fabrication, permission, evidence, sensitive-inference and advice-boundary tests.
- Approved quantitative/reference-case tolerances pass.
- Freshness, expiry and reassessment behavior passes.
- Signal schema compatibility with Meeting Preparation MVP v3.0 passes.
- Client Brain invariance passes: downstream client context does not change specialist facts or confidence.
