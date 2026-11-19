---
name: concentration-risk-insight-mvp
description: Produces evidence-bound security, issuer and sector concentration signals for Meeting Preparation MVP.
version: 1.0.0-mvp
owner: Wealth Advisor Intelligence
risk_classification: advisory-decision-support
client_brain_compatibility: compatible_not_dependent
human_approval_required: true
---

# Concentration Risk Insight: MVP Specialist Skill

## 1. Purpose

Identify material concentration at security, issuer and sector levels and provide a traceable portfolio-risk observation without prescribing diversification action.

## 2. Scope and Boundary

This skill produces specialist facts and atomic signals. It is Client-Brain-compatible but not Client-Brain-dependent. Client context may change downstream meeting relevance, prioritization and framing, but must not alter specialist calculations, confidence or source evidence.

The skill does not generate client-specific advice, Discussion Angles, executable actions or trade instructions.

## 3. Inputs
- authorized holdings and market values
- security, issuer, parent and sector classifications
- approved concentration reference or threshold
- portfolio/account scope
- available fund look-through data
- comparison point

## 4. Processing Approach
1. Resolve portfolio scope, securities, issuers, parents and sectors.
2. Apply approved fund look-through where available and prevent double counting.
3. Calculate weights at security, issuer and sector levels.
4. Compare exposures with approved references or review thresholds.
5. Detect material increases and identify principal contributors.
6. Disclose look-through and unresolved classification coverage.
7. Emit normalized concentration signals with evidence and limitations.

## 5. Signals Produced

```yaml
producer_skill: concentration-risk-insight-mvp
signal_types:
  - security_concentration
  - issuer_concentration
  - sector_concentration
```

## 6. Freshness and Materiality

### Freshness

Freshness depends on holdings, prices, corporate hierarchy, classifications and fund look-through dates. Reassess after material price/position change, corporate action or updated look-through.

### Materiality

Materiality may consider portfolio weight, change from prior state, threshold proximity/breach, exposure coverage and persistence. The MVP does not claim full risk contribution unless supplied by an approved engine.

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
- Do not equate portfolio weight with risk contribution.
- Do not double count direct and look-through exposure.
- Do not assume concentration should be reduced.
- Do not invent issuer/sector classification or tax consequences.
- Do not invent missing values, thresholds, dates, classifications or client context.
- Do not expose unauthorized client, household, account or research data.
- Do not allow downstream Client Brain enrichment to overwrite the sealed specialist result.

## 10. Evaluation Cases
1. Single-security concentration is calculated correctly.
2. Multiple securities aggregate to the same issuer where applicable.
3. Direct and fund exposure are not double counted.
4. Missing look-through lowers confidence.
5. Conflicting classifications are surfaced.
6. Concentration signal remains an observation, not a recommendation.
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
