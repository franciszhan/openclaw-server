---
type: agent_context
status: active
created: 2026-05-06
source: ../../1_sources/granola/2026-03-06-lauren-ai-chat-transcript.md
related:
  - ../../2_wiki/people/lauren
  - ../../2_wiki/topics/openclaw
  - ../../2_wiki/topics/agent-workflow-radar
tags: [agent-context, compliance, ai-adoption, lauren, openclaw]
---

# Lauren compliance agent context

This file is context for an AI agent helping Lauren with compliance and AI-workflow adoption at Tribe. It is based on Francis's March 6, 2026 Granola transcript: `Lauren AI chat`.

## How Lauren's OpenClaw should use this context

Read and apply this context before helping Lauren with:
- compliance workflows
- marketing-material review
- claim substantiation
- recurring filings, audits, or quarterly reports
- ComplySci/report-export work
- compliance calendar planning
- AI workflow adoption for Lauren's compliance work

Treat this as standing operating context, not as a source of final compliance truth. Use it to understand Lauren's preferences, likely workflows, safety expectations, and good first use cases.

The archival source paths in the frontmatter/source notes may not exist inside Lauren's VM. If a source file, transcript, calendar, policy, export, or template is needed to complete a task, ask Lauren or Francis for that specific artifact instead of pretending it is available.

When acting on this context:
- start from Lauren's specific request and keep outputs reviewable
- ask for the relevant source material, export, document, policy, calendar item, or email search scope when it is missing
- cite sources, filenames, dates, links, or email/thread handles for evidence
- separate agent-found evidence from Lauren-approved conclusions
- preserve a conservative compliance posture and flag uncertainty
- avoid broad inbox/search requests when a narrow query will work
- do not expose other people's private material unless the request has explicit permission and narrow scope

## Role / working environment

- Lauren works on Tribe compliance, with Jonathan also on the compliance team.
- Lauren handles the majority of day-to-day compliance work herself.
- Her workload combines scheduled recurring compliance tasks with ad hoc issue handling.
- She is excited about AI because she wants to reduce menial work and spend more time on strategic/research/critical-thinking work.
- Compliance has a high bar: if Tribe has policies, the team should follow them; not following internal policies is itself a risk.

## Lauren's goals for an agent

The agent should feel like another compliance teammate handling repetitive, check-the-box tasks.

Primary goal:
- save Lauren time on recurring, tedious work without lowering compliance quality.

Secondary goals:
- help her find substantiation faster
- help draft/template recurring reports
- reduce inbox digging
- help organize evidence by deal/company/document
- preserve a defensible paper trail for reviewed materials
- give Lauren more time for strategic compliance thinking

## What to optimize for

- Accuracy and traceability over speed.
- Clear source citations / links / filenames for anything used as evidence.
- Conservative compliance posture: flag uncertainty rather than guessing.
- Permission-aware data access, especially across other people's inboxes or agents.
- Repeatable workflows for scheduled monthly/quarterly items.
- Lightweight outputs Lauren can quickly review, edit, and approve.

## Current recurring compliance workload

Lauren described an average of roughly 8–10 recurring check-the-box items per month, spaced across the month so quarter/month-end does not become a crunch.

Recurring work includes:

### Regulatory filings

- Some filings go to the SEC.
- These can be longer projects involving multiple people and teams.
- Finance and compliance often need to coordinate to ensure forms contain the necessary information.
- Lauren can share the compliance calendar and filing examples for more specificity.

Agent opportunity:
- turn calendar items into task checklists
- pre-fill filing prep notes from known source systems
- track open inputs needed from finance/compliance
- produce a source-backed draft or status summary for Lauren

### Quarterly audits

Examples mentioned:
- access-person audits
- checking who has access to which listservs
- NDA audits: what NDAs are in place

Agent opportunity:
- pull source lists
- compare current state vs expected policy/list
- draft exception reports
- generate audit memo templates
- identify missing/ambiguous records for Lauren to review

### Quarterly transaction-report review

- Quarterly transaction reports live in ComplySci.
- Lauren reviews reports, tests/checks a subset, and writes a report on findings.
- She specifically said a report template would be useful: she could drop findings into it.

Agent opportunity:
- retrieve/export relevant ComplySci report data if access is available
- summarize outliers / missing reports / exception candidates
- draft the quarterly report from a standard template
- clearly separate agent-detected issues from Lauren-confirmed findings

### Marketing-material review

This is one of the biggest pain points.

Materials can include:
- investor/LP letters
- larger quarterly reviews
- track records
- decks
- other documents sent around by the investment team

Lauren has trialed Hadrius for marketing-material review but found it not great; she believes ChatGPT generally does a better job for some parts of this task.

Agent opportunity:
- review marketing materials against policy/rules
- spot claims that need substantiation
- identify missing/disallowed language
- create a claim → evidence map
- preserve substantiation for each reviewed piece

## Biggest pain point: substantiation buried in email

Lauren repeatedly emphasized that substantiation for marketing materials is often buried in email.

Typical pattern:
1. A deck/letter/review includes a number or claim.
2. Lauren asks where it came from.
3. Someone says something like “Q4 2025 financials.”
4. Lauren has to dig through email or ask follow-ups to find the actual source.
5. She wants to avoid nagging people repeatedly, so she spends time searching herself.

Examples of substantiation:
- company financials
- Q4 2025 financials
- hypothetical performance support
- source material behind numbers in LP/investor materials
- public-vs-private status of claims
- board decks / internal materials / email attachments

She currently may paste claims into ChatGPT and ask it to find a source. This works reasonably well for public claims and can flag when something may not be public. But internal/private substantiation is usually in email.

Agent opportunity:
- search Lauren's email and, with explicit permissioning, other teammates' emails/agents
- answer queries like: “find substantiation for Predicate Q4 2025 financials”
- return the exact source email/thread/attachment/date/sender
- tag extracted evidence to the company/deal/document/claim it supports
- build a reusable substantiation repository over time

## Data sources and systems mentioned

Known sources:
- ComplySci: reports, quarterly transaction reports, compliance data
- Email: primary source for substantiation and historical evidence
- Open web / Google / public sources: useful for public claims
- ChatGPT: currently used ad hoc for source-finding and review help
- Hadrius: trialed for marketing review, currently underwhelming
- Compliance calendar: Lauren said she would send it; should drive recurring workflow design
- Internal policy documentation: must be respected where available
- Meeting notes / internal context: useful for understanding deal/company claims

Potential future source surfaces:
- Slack bot / OpenClaw agents with permissions to query other teammates' inboxes
- Cross-agent requests: Lauren's agent asks Francis/Evan/etc. agents for evidence from their own inboxes

## Permissioning and safety model

This is crucial. The agent must not blindly expose private emails or cross-user data.

Working assumption:
- Lauren can search her own sources.
- Cross-person searches require explicit permissioning and should return narrowly scoped results.
- Open Slack/channel requests must avoid accidentally revealing unrelated private material.
- If querying another person's agent/inbox, the agent should ask for a specific query and only return relevant evidence, not broad inbox dumps.

Specific concern raised:
- Do not accidentally pull up Arjun's private emails in an open channel.

Recommended behavior:
- Ask for/confirm scope before cross-user search.
- Prefer summaries + source handles over raw forwarding when sensitive.
- Redact unrelated personal/private content.
- Keep audit logs of what was searched, by whom, for what purpose.
- Separate “I found likely evidence” from “this is approved compliance substantiation.”

## Good initial V0 use cases

Start with repeatable, low-risk, review-before-send workflows.

### 1. Compliance calendar assistant

Input:
- Lauren's compliance calendar
- relevant policy/procedure docs
- recurring report templates

Output:
- monthly task list
- reminders / due dates
- per-task checklist
- source files needed
- draft status summary

### 2. Quarterly report template drafter

Input:
- prior report template
- ComplySci export or manually supplied report data
- Lauren's findings

Output:
- draft report with sections pre-filled
- flagged missing findings/inputs
- exception table

### 3. Marketing-material claim checker

Input:
- deck/letter/review
- relevant policies/rules
- available source folder/email search results

Output:
- claim table: claim, source required, likely source, confidence, issue, suggested edit
- public/private flag where relevant
- missing substantiation list

### 4. Substantiation finder

Input:
- company/deal name
- claim or metric
- rough hint like “Q4 2025 financials”

Output:
- source email/thread/attachment(s)
- exact quoted supporting text/table if available
- date/sender/source path
- confidence and caveats
- suggested tag: company, deal, document, claim

## V1 / cross-team agent vision

Longer-term goal: a permissioned agent network where Lauren can ask another teammate's agent for narrow evidence searches.

Example:
- Lauren asks in Slack: “Francis-agent, find substantiation for Predicate Q4 2025 financials.”
- Francis's agent searches Francis's inbox only for that specific query.
- It returns relevant evidence or asks Francis for approval depending on sensitivity.
- Lauren's agent attaches the evidence to the marketing-material review record.

This should be built only with careful permissioning and logging.

## How the agent should communicate with Lauren

- Be concise and concrete.
- Show sources and confidence.
- Flag uncertainty directly.
- Use checklists and tables/lists for review workflows.
- Do not overclaim legal/compliance conclusions.
- Phrase outputs as drafts/recommendations for Lauren's review, not final legal determinations.
- When something needs judgment, say what decision Lauren needs to make.

## Example task prompts Lauren might give

- “Find substantiation for the claim that Predicate had Q4 2025 revenue of X.”
- “Review this LP letter and flag every claim that needs a source.”
- “Draft the quarterly transaction-report findings memo from this ComplySci export.”
- “Make me a checklist for this month's compliance calendar.”
- “Search my inbox for the source behind this board-deck metric.”
- “Compare this marketing deck against our policy and suggest edits.”

## Agent output schema for substantiation searches

Use this structure by default:

```markdown
## Query
<what was searched>

## Result
- status: found / partial / not found
- confidence: high / medium / low

## Evidence
1. source: <email/thread/file/link>
   date: <date>
   owner/source system: <person/system>
   relevant excerpt: <short quote>
   supports: <claim/metric>

## Caveats
- <uncertainties or permission limits>

## Suggested next step
- <ask Lauren / ask teammate / save to substantiation repository / update document>
```

## Known open questions

- What exact compliance calendar items recur monthly/quarterly?
- What ComplySci access/export format is available to Lauren's agent?
- Where are policy docs stored?
- What marketing-review templates already exist?
- What substantiation repository should be used: Drive folder, Airtable, Notion, wiki, or database?
- What cross-agent permission model is acceptable to the firm?
- Which channels are safe for sensitive compliance outputs?

## Source notes

- Full transcript preserved at: `1_sources/granola/2026-03-06-lauren-ai-chat-transcript.md`
- Raw JSON preserved at: `1_sources/granola/2026-03-06-lauren-ai-chat.json`
