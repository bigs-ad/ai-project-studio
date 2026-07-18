# Project Files

The initialized studio/ directory is project-local and must be version controlled.

## Machine-owned files

- project.json: profile, active lifecycle phase, phase runs, gate evidence, and transition history.
- work-items.json: bounded units of work, approved spec hashes, and transition history.
- STATUS.md: generated human-readable summary. Do not edit it manually.

Change machine-owned state only through scripts/studio.py so validation and history remain consistent.
Approved spec paths are repository-relative POSIX paths. Approval hashes normalize text newlines, so Git line-ending conversion alone does not invalidate approval.

## Human and AI maintained files

- PROJECT.md: confirmed outcome, users, measures, constraints, non-goals, assumptions, and unknowns.
- ROADMAP.md: approved milestones, not speculative feature inventories.
- DECISIONS.md: accepted decisions and reasons. Never record an unapproved proposal as fact.
- BACKLOG.md: deferred ideas with a reason for deferral.
- specs/: bounded work specifications. New items use WORK_ITEM_TEMPLATE.md and its machine-readable deliverable contract.
- reviews/: durable acceptance and review evidence.

Game adds GAME_DESIGN.md and PLAYTEST.md. Web-app adds PRODUCT.md, USER_FLOWS.md, and RELEASE_CHECKLIST.md.

Keep facts, assumptions, unknowns, proposals, and approved decisions visibly distinct. Chat history is context, not project truth.

## Initialization

Choose `game` only for a playable interactive product whose primary success measure is player experience. Choose `web-app` for SaaS, dashboards, portals, internal tools, and transactional sites. Ask the user when the classification is materially ambiguous.

Run:

```text
python3 scripts/studio.py init <project-root> --profile <game|web-app> --name "Project Name" --owner "User" --idea "User's initial description"
```

Initialization may add `studio/` and a managed block to `AGENTS.md`; it must preserve existing instructions, leave business code unchanged, and remain in Discovery. Run `validate` after initialization.

The CLI stores a normalized deliverable-contract snapshot on new work items when they become proposed. It refuses approval when the spec omits or invalidates the contract. Legacy work items remain readable and are reported as warnings rather than silently rewritten.

Use `studio.py brief <project-root>` at the start of a new thread or after context loss. It prints a compact recovery map from existing state without creating another project file. Use `--item <id>` for a subagent handoff and `--json` for structured output. The brief identifies the active phase, pending gates, focus work item, deliverable contract, and minimum files to read.

For unfinished work, `studio.py work checkpoint` stores only the latest progress summary, next action, blockers, author, and timestamp on the work item. Use it at pause and handoff boundaries; it is not an activity log:

```text
python3 scripts/studio.py work checkpoint <project-root> <item-id> --summary "..." --next "..." --blocker "..." --by "AI Producer"
```

Use `studio.py repair <project-root>` only to refresh the managed AGENTS.md block and generated STATUS.md after a plugin policy update. It refuses to hide unrelated state corruption.

Legacy projects without an owner remain readable with a validation warning. After user confirmation, `studio.py project set-owner <project-root> --owner "..."` records the owner once; changing an existing owner requires an explicit migration rather than silent replacement.
