---
name: memory-governance
description: Adds a lightweight memory governance layer on top of agent-memory, self-improvement, and rag-engineer. Use when capturing user preferences, decisions, constraints, milestones, corrections, and cross-session context; when reconciling conflicting memory; or when summarizing what the system should remember before/after major actions.
---

# Memory Governance

Provide a strict, low-noise memory policy that complements existing memory skills.

This skill does not replace:
- `agent-memory` (long-term memory workflow)
- `self-improvement` (failure/correction learning)
- `rag-engineer` (retrieval architecture)

It adds:
- structure (what to remember)
- consistency (how to resolve conflicts)
- checkpoints (when to capture and review memory)
- ontology-style entity links
- proactive ask/proceed gates
- query-vs-curate operation split
- append-only local event logging

## Ontology Memory Schema

Model memory as linked entities:
1. `Project`
2. `Task`
3. `Decision`
4. `Constraint`
5. `Preference`
6. `Milestone`
7. `Incident`
8. `Artifact`
9. `OpenQuestion`

Use these link types:
- `belongs_to` (Task -> Project)
- `constrains` (Constraint -> Project/Task/Decision)
- `prefers` (Preference -> Project/Task)
- `decides` (Decision -> Task/Project)
- `blocks` (OpenQuestion/Incident -> Task/Milestone)
- `produces` (Task -> Artifact)
- `supersedes` (new Decision/Constraint/Preference -> old one)

For each event, store:
- `what`
- `why`
- `scope` (global/project/task)
- `source` (user explicit, tool output, inference)
- `confidence` (high/medium/low)
- `timestamp`

## Append-Only Local Log (WAL-style)

Keep one append-only local event log for raw facts, then curate into structured memory.

- File example: `.openclaw/memory/events.log`
- One event per line (or block), never rewrite history in this file.
- Curated memory is the cleaned/indexed layer; log is the forensic source-of-truth.

Minimum event fields:
- `event_id`
- `event_type`
- `entity_type`
- `entity_id`
- `summary`
- `source`
- `timestamp`

## Query vs Curate

Always separate two modes:

1. Query mode:
- retrieve only memory relevant to the current request
- return concise context: preferences + constraints + latest decisions + open questions
- include linked entities when they materially change action

2. Curate mode:
- write only high-signal events
- deduplicate semantically similar entries
- merge repeated facts instead of appending noise
- update link graph and mark old entries as `superseded` instead of deleting

## Conflict Resolution Policy

When memory conflicts:
1. latest explicit user statement wins
2. explicit user statement beats inferred memory
3. project-scoped rule beats global rule inside that project
4. if tie remains, ask one concise clarifying question

Never silently keep both conflicting rules without marking one as superseded.

## Ask vs Proceed Policy (Proactive Gate)

Use this gate before execution:

Proceed without asking when:
1. user intent is clear and constraints are known
2. operation is reversible and low-risk
3. same preference/decision was explicitly confirmed before

Ask one concise question when:
1. two or more plausible actions materially differ
2. missing constraint could cause wrong or destructive output
3. confidence is low and no prior user preference resolves it

Stop and request explicit confirmation when:
1. destructive/irreversible actions
2. external side effects (publish, delete, billing-impacting operations)
3. policy/security constraints are uncertain

## Checkpoint Triggers

Run a memory checkpoint at these moments:
1. after project creation
2. after plan generation/approval
3. after major tool failure or user correction
4. before destructive or irreversible actions
5. at milestone completion

Checkpoint output should include:
- new facts captured
- conflicts resolved
- still-open questions
- execution gate result (`proceed`, `ask`, or `confirm_required`)

## Integration Guidance

Use this skill as a coordinator with existing skills:

- With `agent-memory`:
  - keep durable memory concise and structured
  - maintain a short "working buffer" summary for active context

- With `self-improvement`:
  - convert incidents/corrections into reusable learnings
  - promote recurring failures to high-priority prevention notes

- With `rag-engineer`:
  - mark high-value artifacts and decisions as retrieval candidates
  - include metadata tags for better retrieval filtering

## Memory Hygiene Rules

1. Prefer updates over duplicates.
2. Prefer concrete facts over narrative.
3. Keep unresolved items visible until closed.
4. Mark stale assumptions and replace quickly.
5. Keep user-facing replies natural; do not expose internal schemas unless asked.
6. Keep append-only logs immutable; curate in separate files/views.

## Minimal Memory Summary Template

Use this internal structure when summarizing memory for action:

- Project/Task:
- Preferences:
- Constraints:
- Latest Decisions:
- Linked Dependencies:
- Active Milestone:
- Open Questions:
- Risks/Incidents:
- Gate Decision: `proceed | ask | confirm_required`

Keep it short and actionable.
