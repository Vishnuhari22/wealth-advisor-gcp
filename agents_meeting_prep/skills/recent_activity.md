---
name: recent-activity
description: Reads Interaction + Decision nodes from graph and LLM-frames each into a structured activity entry. Does not invent — only frames what exists.
version: 1.0.0
data_catalog_sections: [6]
---

# Recent Activity Skill

## Purpose

Produce `activity[]` for the meeting brief from the last 6 Interaction nodes and their linked Decision nodes. Frame each interaction into a structured entry. Do not invent facts — only frame what is in the data.

## Data Fetching

Fetch using the knowledge catalog section declared above:
- Section 6 (interactions) — `graph_query`; fetch last 6 Interaction nodes with OPTIONAL linked Decision nodes, ordered by date DESC

Construct Cypher adhoc using `$client_id`.

## Framing Rules

For each interaction, produce one activity entry:

**date** — `Interaction.date` formatted as "DD MMM YYYY"

**summary** — Use `Interaction.summary` directly. If summary is null or empty, frame from `Interaction.topic` as: "Discussion on {topic}."

**decision** — If a Decision node is linked:
  - Use `Decision.description` if present
  - Otherwise frame as: "{initiated_by}-initiated {type} decision"
  - If no Decision node: null

**sentiment** — Map `Interaction.sentiment` to display label:
  - `positive` / `satisfied` / `confident` → "Positive"
  - `neutral` → "Neutral"
  - `anxious` / `worried` / `risk_averse` / `frustrated` → "Concerned"
  - `excited` → "Enthusiastic"
  - anything else → use raw value

**outcome** — Use `Interaction.outcome` if present; otherwise null

**channel** — Use `Interaction.channel` directly (e.g. "Phone", "In-person", "Email")

## Output Contract

```json
{
  "activity": [
    {
      "date": "string",
      "summary": "string",
      "decision": "string | null",
      "sentiment": "string",
      "outcome": "string | null",
      "channel": "string"
    }
  ]
}
```

Entries are ordered most recent first (same order as input).

## Guardrails

- Do not invent decisions, outcomes, or sentiment not in the data.
- Do not infer what the client was feeling beyond the recorded sentiment label.
- If an interaction has no summary and no topic, produce: `"summary": "No details recorded"`.
- Maximum 6 entries — use the 6 most recent interactions.
