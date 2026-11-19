---
name: market-investment-insight-mvp
description: Produces evidence-bound changed-market-condition and investment-review signals for Meeting Preparation MVP.
version: 1.0.0-mvp
owner: Wealth Advisor Intelligence
risk_classification: advisory-decision-support
client_brain_compatibility: compatible_not_dependent
human_approval_required: true
---

# Market & Investment Insight: MVP Specialist Skill

## 1. Purpose

Identify material, current market or investment evidence that may warrant reviewing existing portfolio positioning, while remaining client-independent and non-recommendatory.

## 2. Scope and Boundary

This skill produces specialist facts and atomic signals. It is Client-Brain-compatible but not Client-Brain-dependent. Client context may change downstream meeting relevance, prioritization and framing, but must not alter specialist calculations, confidence or source evidence.

The skill does not generate client-specific advice, Discussion Angles, executable actions or trade instructions.

## 3. Inputs
- approved market and security data
- approved internal/external research
- relevant benchmarks or historical references
- covered asset classes, funds or securities
- approved investment universe
- as-of date and expiry/reassessment policy

## 4. Processing Approach
1. Resolve approved coverage, entitlements and evidence scope.
2. Detect a material change using approved market/research thresholds.
3. Describe the observation and investment implication at market or security level.
4. Preserve supporting and counter-evidence.
5. Assess time relevance, evidence strength, confidence and expiry.
6. Do not assert client relevance inside the specialist skill.
7. Emit normalized market signals for downstream portfolio/client reasoning.

## 5. Signals Produced

```yaml
producer_skill: market-investment-insight-mvp
signal_types:
  - market_condition_change
  - investment_review_trigger
  - market_evidence
```

## 6. Freshness and Materiality

### Freshness

Freshness follows signal type, market volatility, publication date and expiry policy. Reassess after a material market move, new research, superseding event or expiry.

### Materiality

Materiality may consider magnitude, persistence, benchmark-relative change, evidence strength and relevance to covered exposures. Popularity or repeated commentary does not establish materiality.

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
- Do not make client-specific recommendations or trade instructions.
- Do not infer suitability or client appetite.
- Do not suppress credible counter-evidence.
- Do not turn generic commentary into a signal without material evidence.
- Do not guarantee returns or invent forecasts.
- Do not invent missing values, thresholds, dates, classifications or client context.
- Do not expose unauthorized client, household, account or research data.
- Do not allow downstream Client Brain enrichment to overwrite the sealed specialist result.

## 10. Evaluation Cases
1. Material approved evidence creates a current market-change signal.
2. Below-threshold change does not force a signal.
3. Stale or expired evidence is qualified or rejected.
4. Conflicting research is retained as counter-evidence.
5. Client relevance remains false until downstream enrichment.
6. Market signal can support monitor/no-action.
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
