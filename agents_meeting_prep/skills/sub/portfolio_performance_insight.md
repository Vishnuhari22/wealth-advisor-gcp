---
name: portfolio-performance-insight-mvp
description: Produces evidence-bound portfolio, account and holding performance signals for Meeting Preparation MVP.
version: 1.0.0-mvp
owner: Wealth Advisor Intelligence
risk_classification: advisory-decision-support
client_brain_compatibility: compatible_not_dependent
human_approval_required: true
---

# Portfolio Performance Insight: MVP Specialist Skill

## 1. Purpose

Identify material changes in portfolio, account and holding performance relative to approved benchmarks and prior periods, while preserving calculation method, period, coverage and evidence.

## 2. Scope and Boundary

This skill produces specialist facts and atomic signals. It is Client-Brain-compatible but not Client-Brain-dependent. Client context may change downstream meeting relevance, prioritization and framing, but must not alter specialist calculations, confidence or source evidence.

The skill does not generate client-specific advice, Discussion Angles, executable actions or trade instructions.

## 3. Inputs
- portfolio/account identifiers
- authorized holdings and valuations
- cash flows and transactions required by the approved return method
- approved benchmark identifiers
- measurement period and comparison point
- security classifications and portfolio weights

## 4. Processing Approach
1. Resolve authorized portfolio scope and return methodology.
2. Retrieve approved valuations, cash flows, transactions and benchmark observations.
3. Calculate or retrieve authoritative returns using the approved performance engine.
4. Compare current return with benchmark and selected prior period.
5. Detect material portfolio, account or holding changes using versioned thresholds.
6. Bind calculations and explanatory research separately to evidence.
7. Emit normalized signals with period, coverage, confidence and limitations.

## 5. Signals Produced

```yaml
producer_skill: portfolio-performance-insight-mvp
signal_types:
  - performance_change
  - benchmark_gap
  - holding_trend
```

## 6. Freshness and Materiality

### Freshness

Freshness follows the portfolio valuation cycle, benchmark availability and corrections. Reassess after a valuation correction, material transaction, benchmark revision or newer approved period close.

### Materiality

Materiality may consider absolute return, benchmark gap, holding weight, contribution to portfolio result, persistence and approved review thresholds. Strong performance alone is not an opportunity or recommendation.

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
- Do not let the language model calculate authoritative returns.
- Do not compare incompatible periods, currencies, benchmarks or methodologies.
- Do not attribute performance causality without approved evidence.
- Do not recommend replacing or increasing an investment solely from performance.
- Do not invent missing values, thresholds, dates, classifications or client context.
- Do not expose unauthorized client, household, account or research data.
- Do not allow downstream Client Brain enrichment to overwrite the sealed specialist result.

## 10. Evaluation Cases
1. Portfolio return reconciles to the approved performance engine.
2. Benchmark gap uses the same period and compatible methodology.
3. A small change below threshold does not force a signal.
4. Material holding trend includes portfolio weight and evidence.
5. Missing benchmark is shown as unavailable rather than invented.
6. Corrected valuations trigger reassessment and supersession.
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
