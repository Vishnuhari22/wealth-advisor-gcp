---
name: discussion-angles
description: Runs AdvisoryReasoningMapSkill against validated signals to produce discussion angles and next best actions. Prerequisite: RisksAndOpportunitiesSkill must be complete.
version: 1.0.0
data_catalog_sections: [1, 5]
source_skills:
  - sub/advisory_reasoning_map.yaml
---

# Discussion Angles Skill

## Purpose

Produce `discussionAngles[]` and `nextBestActions[]` for the meeting brief. You receive validated signals from RisksAndOpportunitiesSkill and the meeting objective. You apply the Advisory Reasoning Map to select which scenarios apply and generate angles only for those that fire.

## Data Fetching

Signals and meeting_objective are passed directly from prior steps — no re-fetching needed for those.
Fetch only what is not already available:
- Section 1 (client_profile) — `graph_query` if client properties not already in context
- Section 5 (goals) — `graph_query` if goals not already in context

Construct Cypher adhoc using `$client_id`.

## Pre-Computed Scenario Firing Decisions

The prompt may contain a `PRE-COMPUTED SCENARIO FIRING DECISIONS` block. These are computed
from exact arithmetic before this step runs. They are authoritative:
- A scenario marked `true` MUST be processed — skip the firing test, go directly to angle/NBA generation
- A scenario marked `false` MUST NOT fire — do not generate an angle or NBA for it regardless of signals
- Do not re-evaluate firing conditions for any scenario in this block

For scenarios not in the block (none currently), apply normal firing logic.

## Advisory Reasoning Map Application

Load `sub/advisory_reasoning_map.yaml`. For each of the 5 MVP scenarios, test whether it applies:

1. **CASH_DEPLOYMENT** — fires if any `excess_cash` or `liquidity_need` signal is present
2. **CONCENTRATION_MANAGEMENT** — fires if any `security_concentration`, `issuer_concentration`, or `sector_concentration` signal is present
3. **RETIREMENT_TRANSITION** — fires if any `retirement_horizon_approaching` or `retirement_income_need` signal is present, OR if client has a retirement goal with target_date within 5 years
4. **CHANGING_MARKET_CONDITIONS** — fires if any `market_condition_change` or `investment_review_trigger` signal is present with materiality ≥ medium
5. **GOAL_FUNDING** — fires if any `goal_approaching` or `goal_funding_gap` signal is present

Before fusing any signal as contextual evidence into another scenario, check whether its owning scenario has a `minimum_standalone_threshold` defined in the map. If the threshold condition is met, that scenario **must** fire as a standalone angle — it cannot be absorbed as context into another scenario.

For GOAL_FUNDING specifically: if a `goal_funding_gap` signal is present and `observation.current_value >= 20` (gap ≥ 20%), fire GOAL_FUNDING as a standalone angle even if CASH_DEPLOYMENT or another scenario also fired.

For each scenario that fires:
1. Read `resolution_owner` from the map — `client_led` or `advisor_led`
2. Collect the direct + contextual signals that map to it
3. Test fusion compatibility per the map's `fusion_rule`
4. Activate tensions only when both sides have evidence
5. Retrieve permitted pathways from the map
6. If `client_led` → generate one discussion angle + lightweight NBA
7. If `advisor_led` → skip discussion angle entirely, generate specific NBA only

## Angle Generation Rules (from v3.0 §8 Discussion Angles)

For each fired scenario, produce one angle with:
- `title` — short label (e.g. "Cash Deployment Review")
- `desc` — 2–3 sentences: decision question + why now + what the advisor should explore
- `decision` — the scenario's `decision_question` from the map
- `why_now` — grounded in the specific signal values (e.g. "Cash is 28% of net worth vs 15% IPS target")
- `tension` — must always be populated. If both sides have strong evidence, state the real conflict. If the client-side is weak, use a fallback: "client may prefer to retain current positioning" or "implementation constraints may outweigh the signal." Never leave tension null.
- `advisor_question` — one open question for the advisor to ask the client
- `permitted_pathways` — from the map
- `assumptions_to_validate` — 1–2 items

Hard gates before including any angle:
- Evidence sufficiency — signal must have confidence > 0 and freshness = current
- Non-speculation — do not activate a tension without evidence for both sides
- Meeting-purpose fit — angle must be relevant to the meeting_objective
- Deduplication — before finalising the angle list, check all pairs of angles for overlap. If two angles share more than 60% of their `supporting_signal_refs`, merge them into one richer angle: keep the higher `relevance_score`, combine the `desc` and `assumptions_to_validate`, and union the `supporting_signal_refs`. Never emit two angles that would lead to the same advisor conversation.

## Next Best Actions

NBA generation differs by `resolution_owner`.

**For `client_led` scenarios:**
Derive a next best action by reasoning over the signals and meeting objective. Ask: given what this client's data shows and the purpose of this meeting, what is the most useful thing the advisor should do or prepare?

For CASH_DEPLOYMENT specifically: if the client's required liquidity reserve is not quantified in the IPS or client profile, the NBA MUST flag this explicitly — e.g. "the required reserve is not yet quantified; identify the information needed before modelling deployment". Do not write the NBA as if the reserve is already known.

Examples:
- Excess cash + approaching goal → advisor should model deployment options before the meeting
- Retirement horizon + allocation drift → advisor should prepare a transition scenario to explore with the client

**For `advisor_led` scenarios:**
The IPS or portfolio data already defines the constraint — the client's answer is not needed. Generate a specific advisor preparation task. The NBA must say:
- What the advisor should model, prepare, or review before or after the meeting
- Which specific holding, allocation, or position it applies to
- What the output of that preparation should be (a scenario, a model, a review note)

Examples:
- Concentration breach → "Prepare a rebalancing scenario showing the impact of reducing [fund] from [X]% to the IPS [Y]% limit — bring to next meeting for discussion"
- Market conditions → "Review whether [position] still fits IPS given [market signal] — model the alternative allocation and note the trade-off"

Each next best action must be grounded in a specific signal — not generic advice.

For CONCENTRATION_MANAGEMENT specifically: if any `security_concentration` signal is present with materiality ≥ medium, an NBA MUST be emitted. It must name the specific fund(s), their actual weight(s), the IPS single-fund maximum, and instruct the advisor to prepare a review note showing retain/reduce/phase scenarios. Do not omit this NBA even if other NBAs are present.

## NBA Ordering

Order `nextBestActions[]` by urgency — highest first:
1. Any NBA that is a prerequisite for other NBAs comes first (e.g. quantifying the liquidity reserve must precede any deployment modelling)
2. `client_led` NBAs that are time-sensitive or block other actions
3. `advisor_led` NBAs (preparation tasks — important but not blocking)
4. Remaining `client_led` NBAs

Never put an `advisor_led` NBA before a `client_led` NBA that it depends on or that is more urgent.

## Relevance Scoring

For each angle, assign a `relevance_score` from 1–10 before writing the output. This score determines the display order — highest first.

Score on these four factors (sum them):

| Factor | Max | Criteria |
|---|---|---|
| Signal materiality | 4 | critical=4, high=3, medium=2, low=1 — use the highest materiality signal supporting this angle |
| Meeting purpose fit | 3 | Directly addresses the meeting_objective.statement=3, related=2, tangential=1 |
| Time sensitivity | 2 | Action needed before next meeting=2, within quarter=1, no urgency=0 |
| Evidence strength | 1 | Multiple corroborating signals=1, single signal=0 |

Round to nearest integer. Do not normalise or cap — a score of 10 is valid if all four factors are at maximum.

After scoring all angles, **sort the `discussionAngles` array by `relevance_score` descending** before writing the output.

## Output Contract

```json
{
  "discussionAngles": [
    {
      "title": "string",
      "desc": "string",
      "decision": "string",
      "why_now": "string",
      "tension": "string | null",
      "advisor_question": "string",
      "permitted_pathways": ["string"],
      "assumptions_to_validate": ["string"],
      "supporting_signal_refs": ["string"],
      "confidence": "low | medium | high",
      "relevance_score": "number (1–10)"
    }
  ],
  "nextBestActions": [
    {
      "label": "string",
      "rationale": "string",
      "signal_ref": "string",
      "resolution_owner": "client_led | advisor_led"
    }
  ]
}
```

## Guardrails

- Only generate angles for scenarios that fired — do not generate angles for all 5 by default.
- Do not activate a tension without evidence for both sides.
- Do not convert a market signal directly into a recommendation.
- Monitor and no-action are valid outcomes — include them in permitted_pathways.
- Maximum 4 discussion angles per brief. `advisor_led` scenarios do not count toward this cap — they contribute to `nextBestActions[]` only.
- tension must never be null — see Angle Generation Rules above.
