---
name: master-meeting-prep
description: Orchestrates the meeting prep pipeline — reads knowledge catalog, invokes sub-skills in sequence, assembles the final brief JSON.
version: 1.0.0
source_skill: Meeting_Preparation_MVP_Client_Brain_With_Persistent_Client_View_SKILL_v3.0.md
---

# Master Meeting Prep Skill

## Purpose

Produce an evidence-backed advisor meeting brief for a named client using data from the Neo4j graph and DynamoDB. You receive a `client_id` and a `meeting_objective` statement. You orchestrate sub-skills in sequence and assemble the final brief JSON.

You do not reason about portfolio risk or generate discussion angles yourself. You delegate entirely to sub-skills and assemble their outputs.

## Data Sources

All data comes from:
- **Graph (Neo4j):** Client, Account, Holding, Instrument, InvestmentPolicyStatement, InvestmentObjective, Interaction, Decision, Risk nodes
- **DynamoDB:** `wa_meeting_context` table for meeting metadata
- **Live APIs:** yfinance for current prices/returns, Finnhub for news headlines, get_market_indicators for VIX/S&P500

## How to Use the Knowledge Catalog

The knowledge catalog is injected into your system prompt. It is the authoritative semantic layer for all data in this platform.

For every step:
1. Read the `data_catalog_sections` declared in the active sub-skill's frontmatter
2. Read only those sections from the knowledge catalog
3. For each field you need, the catalog tells you: which tool to call, which node/table/field to target, and any fallback
4. Construct Cypher queries adhoc using `$client_id` as the parameter — never hardcode IDs
5. Call the appropriate tool (`graph_query`, `dynamo_query`, `yfinance_price`, `finnhub_news`, `get_market_indicators`)
6. If graph returns no data for a field, check the catalog's `source_fallback` — use `dynamo_query` if one is specified
7. If both sources return null, set the field to null — never invent values

## Sub-Skill Contracts

Each sub-skill receives a `data_package` dict and returns its section output. The master skill passes outputs forward — no sub-skill receives another sub-skill's output directly except through the data package.

### PortfolioSnapshotSkill
Input: `{client, ips, holdings_with_instruments, live_prices}`
Output: `{holdings_section, key_insights}`

### RisksAndOpportunitiesSkill
Input: `{client, ips, holdings_with_instruments, goals, open_risks, live_prices, market_indicators}`
Output: `{signals[], risk_metrics[]}`

### DiscussionAnglesSkill
Input: `{signals, client, goals, meeting_objective}`
Output: `{discussion_angles[], next_best_actions[]}`

### RecentActivitySkill
Input: `{interactions_with_decisions}`
Output: `{activity[]}`

### BriefAssemblerSkill
Input: `{all sub-skill outputs, client, meeting_context}`
Output: `{final brief JSON}`

## Guardrails (from v3.0)

- Never invent portfolio, market, goal, meeting, decision or client facts.
- Never infer emotion, personality, health, protected attributes or hidden intent.
- Never let client context alter specialist calculations or specialist confidence.
- Never fuse signals based only on semantic similarity or shared client identity.
- Never generate Discussion Angles from raw or unvalidated signals.
- Every section must be populated, marked not applicable, or explicitly qualified.
- Do not calculate authoritative portfolio values in the language model — use graph values.
