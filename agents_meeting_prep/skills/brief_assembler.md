---
name: brief-assembler
description: Pure mapping — no reasoning. Assembles all sub-skill outputs into the final meeting brief JSON. Sets meeting metadata from wa_meeting_context.
version: 1.0.0
data_catalog_sections: [1, 8, 10]
---

# Brief Assembler Skill

## Purpose

Map all sub-skill outputs into the final meeting brief JSON. You do no reasoning. You do no analysis. You assemble fields from the inputs you receive.

## Data Fetching

All analysis data comes from prior step outputs passed to you directly — no graph queries needed.
Fetch three things:
- Section 1 (client_profile) — `graph_query` for the Client node using `$client_id` to get `relationship_bio`, `tier`, `segment`, `stated_risk_tolerance`, `primary_objective`, and all other client fields
- Section 10 (meeting_context) — `dynamo_query` on `wa_meeting_context` table using `meeting_id` and `meeting_date`
- Fund news — call `get_fund_news_summary(client_id)` to get structured advisor-facing news items spread across significant holdings

## Field Mapping

### client section
```
client.clientId         ← client.client_id
client.name             ← client.name
client.customerId       ← client.client_id  (same — single identifier)
client.householdId      ← client.household_id
client.accountId        ← client.account_id
client.segment          ← client.segment
client.tier             ← client.tier
client.ageBand          ← client.age_band
client.advicePosture    ← client.advice_posture
client.adviceRelationshipType ← client.advice_relationship_type
client.lifecycleStage   ← client.lifecycle_stage
client.primaryObjective ← client.primary_objective
client.statedRiskTolerance ← client.stated_risk_tolerance
client.investmentHorizon ← client.investment_horizon_years (as string, e.g. "10 years")
client.taxStatus        ← client.tax_status
client.liquidityNeeds   ← client.liquidity_needs
client.regBiOptIn       ← client.reg_bi_opt_in
client.behavioralProfile ← client.behavioral_profile
client.aum              ← holdings.totalValue (the portfolio market value — this is the AUM figure shown in the header)
client.sentiment        ← last Interaction.sentiment mapped to display label using the same mapping as RecentActivitySkill:
                              positive/satisfied/confident → "Positive"
                              neutral → "Neutral"
                              anxious/worried/risk_averse/frustrated → "Concerned"
                              excited → "Enthusiastic"
                              anything else → raw value
client.meetingGoal      ← derive a short, human-readable meeting purpose using this priority order:
                          1. If meeting_context.meeting_objective.statement is a meaningful purpose (not just the client
                             name alone, not empty, not a generic placeholder) — use it directly, first sentence only,
                             trimmed to ≤ 10 words.
                          2. If meeting_context.source == "google_calendar" AND meeting_objective.statement is just the
                             client name (or blank) — the calendar event had no subject. Use client.primary_objective
                             directly as the meeting goal, trimmed to ≤ 10 words. Do NOT prepend "Review:" or any label.
                             Example: primary_objective = "Cash deployment review and behavioral alignment"
                             → meetingGoal = "Cash deployment review and behavioral alignment"
                          3. If meeting_context.source == "assist_chat" — use meeting_objective.statement as-is
                             (advisor provided it explicitly), trimmed to ≤ 10 words.
                          Never output the client name alone. Never output just "Review" or any single word.
client.meetingTime      ← if meeting_context.source == "google_calendar":
                              "{meeting_context.time} · {meeting_context.meeting_type}"
                          else:
                              "On demand · {today}"
```

### profileIntro
Use `client.relationship_bio` fetched from the graph (Section 1) as the basis, but rewrite it in a neutral, professional tone suitable for an advisor briefing document.
Remove any judgemental, diagnostic, or clinical language (e.g. "hoarding", "slow to act", "pattern", "anomaly"). Describe the same facts — portfolio behaviour, risk profile, tendencies — using observational, non-judgemental phrasing.
Examples of tone correction:
- "cash-hoarding pattern" → "tendency to hold higher cash balances"
- "slow to act on advisor recommendations" → "takes a considered approach to implementing changes"
- "risk-averse and reluctant" → "prefers a cautious, measured approach"
Keep the same factual content and length. Do not add new facts.
Critical: behavioural tendencies (repeated patterns) must be framed as tendencies, not as one-time current facts.
- WRONG: "currently holds 34% in money market" — reads as a snapshot, loses the pattern
- RIGHT: "tendency to hold higher cash balances. Holds 34% in money market" — pattern first, evidence second
If the bio describes a recurring behaviour, preserve the tendency framing. Do not collapse it into "currently X".
If `relationship_bio` is null or empty after fetching, construct this exact fallback:
`"{client.name} is a {client.segment} client with a {client.stated_risk_tolerance} risk profile and a {client.primary_objective} objective."`
Never write "unavailable", "not available", "N/A", "is a client", or any other placeholder — always produce a full descriptive string.

### meetingObjective
```
meetingObjective.statement  ← meeting_context.meeting_objective.statement
meetingObjective.source     ← meeting_context.meeting_objective.source
```

### holdings section
Direct pass-through from PortfolioSnapshotSkill output.

### risk section

The UI renders Risks & Opportunities as **named skill sections**, not as flat risks/opportunities arrays. Map as follows:

```
risk.riskMetrics    ← from RisksAndOpportunitiesSkill (pass through directly)

risk.skillSections  ← signals[] grouped by producer_skill, in this order:
  1. "Portfolio Performance"    ← signals where producer_skill = "portfolio-performance-insight-mvp"
  2. "Allocation & Policy"      ← signals where producer_skill = "allocation-drift-policy-insight-mvp"
  3. "Concentration Risk"       ← signals where producer_skill = "concentration-risk-insight-mvp"
  4. "Liquidity & Goals"        ← signals where producer_skill = "liquidity-goal-insight-mvp"
                                   ALSO include signals where producer_skill = "client_monitor"
                                   (e.g. interaction_silence, sentiment_trend_negative)
  5. "Market & Investment"      ← signals where producer_skill = "market-investment-insight-mvp"
```

There must be exactly ONE section with label "Liquidity & Goals".
Do NOT create a second section for client_monitor signals — they belong inside the same
"Liquidity & Goals" entry as the liquidity-goal-insight-mvp signals.

Before adding client_monitor signals to the section, deduplicate:
- Drop any client_monitor signal whose signal_type duplicates or overlaps an existing
  liquidity-goal-insight-mvp signal for the same underlying fact.
  Examples of duplicates to drop: severe_cash_drag / cash_drag when excess_cash is already present.
- Only add client_monitor signals that cover facts not already in the section.
  Examples to keep: interaction_silence, sentiment_trend_negative (no sub-skill produces these).

Each entry in `risk.skillSections[]`:
```
{
  "label": "string",           ← display name from the mapping above
  "producer_skill": "string",  ← raw producer_skill value from signal
  "signals": [...]             ← all signals for this skill, full signal objects
}
```

Do NOT populate `risk.risks[]` or `risk.opportunities[]` — these are replaced by `risk.skillSections[]`.
If a skill produced no signals, include its section with `"signals": []` — do not omit the section.

### discussionAngles
Direct pass-through from DiscussionAnglesSkill output.

### nextBestActions
Direct pass-through from DiscussionAnglesSkill output.

### activity
Direct pass-through from RecentActivitySkill output.

### fundNews
Call `get_fund_news_summary(client_id)`. The tool returns raw performance data per fund:
`{ticker, fund_name, asset_class, weight_pct, current_price, return_ytd, return_1y}`

For each item, write a single advisor-facing prose sentence as the `summary` field.
The sentence should read like a quick market update an advisor would say aloud before a meeting.
Use the performance numbers to frame the narrative — do not just list them.

Examples of the tone:
- "VFIAX is the portfolio's largest position at 21% and has delivered a solid 13% gain this year, tracking the S&P 500 closely."
- "ABNDX, the bond allocation at 11%, is slightly underwater for the year — down 2% YTD — reflecting the rate environment."
- "FSPGX has lagged this year with only a 3% YTD gain despite its large-cap growth mandate, worth noting given its 17% weight."

Rules:
- One sentence per fund. No bullet points. No colons. No labels like "YTD:".
- Reference the weight and at least one return figure naturally in the sentence.
- If return data is missing, write a brief factual sentence about the fund's role in the portfolio.
- Output shape per item: `{ticker, fund_name, weight_pct, summary}`

### Highlights Synthesis

This runs last, after all prior outputs are assembled. You have the full picture:
signals[], riskMetrics[], discussionAngles[], nextBestActions[], activity[], holdings.
Use all of them together.

**Do not echo the raw `liquidity_needs` string from the client profile.** If liquidity needs
are flagged as unquantified in the client profile, use the signals and IPS cash bands from
Step 2 to produce a quantified range instead. Example: if cash is 33.8% vs IPS max 30%,
and no reserve amount is specified, state the deployable range using IPS scenarios
(e.g. "deployable cash ranges from $38k to $239k depending on reserve assumption").

**Do not repeat what is already visible** in riskMetrics or the first discussion angle.
The highlights should add synthesis — the "so what" — not restate individual signals.

### nbaTrigger
One sentence synthesising the single most urgent action across all NBAs and signals.
Do not copy or paraphrase `nextBestActions[0].label` or any NBA label verbatim.
Reason across the signals and meeting objective to produce an advisor-facing sentence that
captures the strategic picture — the *why* behind the action, not the task description.
The sentence should read as a prompt to the advisor, not as a task instruction.
Avoid internal task language like "quantify", "model", "prepare", "build" as the opening word.
Example of wrong: "Quantify Nathan's protected short-term liquidity reserve before modelling any deployment."
Example of right: "Nathan's cash position is above IPS limits and his reserve requirement is unknown — establish what must stay liquid before any deployment conversation can proceed."
If nextBestActions is empty: `"No immediate action required."`

### nbcScript
Two sentences for the advisor's opening. Synthesise across the meeting objective,
the highest-materiality signal, and the most relevant discussion angle.
Do not use a fixed template. The sentences should read naturally, as if the advisor
is orienting themselves before walking into the room.
Example: "Nathan has held 40% of his portfolio in cash for over a year, consistently
deferring deployment — today's meeting is the moment to understand what's driving that
and whether the retirement goal gap changes his thinking."
Avoid: generic openers like "Today's meeting focuses on...", restating the meeting
objective verbatim, or referencing signals the advisor already knows from the riskMetrics panel.

## Output Contract

The final JSON must conform to the existing meeting prep output schema used by `pptx_utils.py` and the frontend `MeetingPrepChatSummary` component. Do not add or remove top-level keys.

```json
{
  "client": {
    "clientId": "string",
    "name": "string",
    "customerId": "string",
    "householdId": "string",
    "accountId": "string",
    "segment": "string",
    "tier": "string",
    "ageBand": "string",
    "advicePosture": "string",
    "adviceRelationshipType": "string",
    "lifecycleStage": "string",
    "primaryObjective": "string",
    "statedRiskTolerance": "string",
    "investmentHorizon": "string",
    "taxStatus": "string",
    "liquidityNeeds": "string",
    "regBiOptIn": "boolean",
    "behavioralProfile": "string",
    "aum": "string",
    "sentiment": "string",
    "meetingGoal": "string",
    "meetingTime": "string",
    "nbaTrigger": "string",
    "nbcScript": "string"
  },
  "profileIntro": "string",
  "meetingObjective": { "statement": "string", "source": "string" },
  "holdings": { ... },
  "risk": {
    "riskMetrics": [...],
    "skillSections": [
      { "label": "string", "producer_skill": "string", "signals": [...] }
    ]
  },
  "discussionAngles": [...],
  "nextBestActions": [...],
  "activity": [...],
  "fundNews": [...],
  "nbaTrigger": "string",
  "nbcScript": "string"
}
```

## Guardrails

- Do not invent any field value — every field maps to a specific input source above.
- If a sub-skill output is missing or empty, set the corresponding section to `[]` or `null` — do not fabricate content.
- Do not reorder or rename top-level keys — the frontend depends on this exact schema.
