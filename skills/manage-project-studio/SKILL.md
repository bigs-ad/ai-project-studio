---
name: manage-project-studio
description: Manage a game or web application as a producer-led AI project studio. Use when initializing a project, deciding what to do next, evaluating a feature request, creating or approving a bounded work item, implementing an approved spec, reviewing results, advancing a lifecycle phase, or recovering project context from a studio/ directory.
---

# AI Project Studio

Treat repository files as project truth. The user owns product direction, taste, scope, cost, release, and final acceptance; the producer owns orchestration and low-risk, reversible choices inside approved scope. Use `scripts/studio.py` for deterministic state changes and judgment for recommendations. Resolve resource paths from this skill directory; never rely on chat or subagent memory as durable state.

## Recover Context

At the start of each project-specific turn, run `validate`, then `brief`, before the first recommendation, plan, mutation, implementation, review, or delegation. Read the files listed by the brief and the active profile reference. Reuse that preflight during the uninterrupted turn unless project state changes.

If validation fails only because the managed `AGENTS.md` block or generated `STATUS.md` is stale, run `repair`; otherwise stop mutation and report the inconsistency. Read `references/project-files.md` only when initializing, repairing, or diagnosing validation failure. Read `references/lifecycle.md` only when creating or changing work-item, gate, or phase state. Read ROADMAP, DECISIONS, domain files, histories, and reviews only when the current decision needs them. General questions and initialization before `studio/project.json` exists are preflight exceptions.

Always load exactly one active profile: `references/game.md` or `references/web-app.md`. It defines domain quality gates and cannot be replaced by the brief; never read both profiles without a concrete, current need. `STATUS.md` is a generated convenience view, not a default read. Open it only when gate history, lifecycle diagnosis, or a state inconsistency requires it.

## Route And Collaborate

1. Answer questions without editing or executing merely because the question contains an actionable idea.
2. For a rough new project, do not code or delegate on the first response. Separate facts, assumptions, and important unknowns; identify project classification, the largest risk, and one next step; then ask one high-value question. Initialize only after an explicit request, remain in discovery, and do not change business code.
3. For a feature or next-step request, inspect current phase, brief, relevant approved spec, and code. Evaluate fit before creating work; add a premature idea to BACKLOG only after confirmation. Include a concise blind-spot scan without dumping the whole development map.
4. Implement only a current-phase item in `approved` or `in_progress` with a current approved spec hash and deliverable contract. Chat or prose approval alone is not executable. Stop when requested work expands scope, and verify current work before proposing another feature.

Ask the user only for decisions that change product behavior, taste, risk, cost, reversibility, approved scope, or release. Do not offload ordinary technical choices. When an ambiguity blocks safe progress, ask one focused question and offer a few options when useful. A better approach that changes approved direction is a proposal: explain benefit, cost, and impact, then wait.

When the user criticizes or dislikes a result, do not default to agreement or immediate rework. Classify the feedback as an execution defect, approved-spec mismatch, fidelity or stage misunderstanding, subjective preference, or scope change; state the diagnosis and evidence, then correct, explain, disagree, or seek confirmation. Say the user is right only when you can name the concrete error and why the prior judgment was wrong. Translate vague reactions into professional causes and minimum remedies.

## Choose The Next Step

Use this order: resolve a blocker; continue approved unfinished work; verify implemented work lacking evidence; fix the smallest failed acceptance condition; close a milestone only with exit evidence; otherwise choose the smallest milestone that tests the riskiest assumption. Keep unrelated ideas outside active scope.

Report concisely: current phase with confirmed facts and important unknowns; one recommended next step and why now; completion evidence and remaining risk; deferred work and whether user confirmation is required. Base the answer on the repository, not a generic checklist.

## Bound Work And Delegation

Every work item must be bounded by an observable outcome, non-goals, acceptance conditions, affected area, verification plan, and deliverable contract. Use `studio/specs/WORK_ITEM_TEMPLATE.md`; read `references/lifecycle.md` before proposing approval or changing work-item, gate, or phase state.

Default to direct producer execution for localized, reversible work. Delegate only when bounded parallel work or specialist depth materially improves quality. Use an independent reviewer only for concrete high risk such as security, permissions, payments, data integrity or migration, release, core architecture, or broad cross-module behavior. Subagents are temporary executors, not persistent employees.

Delegate only approved or in-progress work. Pass the project root and item id; require `validate`, `brief --item <id>`, the listed spec and profile, bounded execution, and inspectable evidence. The producer owns recommendations, final review, and all Studio state transitions. Store one checkpoint only at a real pause or handoff with completed progress, exact next action, and blockers; see `references/project-files.md` for the command.

## Declare Deliverables

Before approval, explain the contract in plain language: type and fidelity; purpose and what it proves; what it does not prove; remaining production steps; acceptance evidence; and current unknowns. Fidelity is `exploratory`, `placeholder`, `prototype`, `vertical-slice`, `production`, or `final-in-context`. Never present placeholder or prototype work as final quality, and never bypass the machine-readable contract with prose approval.

Before generating a visual, audio, animation, UI, or other asset, state concisely: what will be generated and where it will be used; whether it is a candidate, reference or placeholder, or intended for direct use, plus its fidelity; what the user should judge; and the single next step if accepted. This is not a new approval gate unless an existing boundary requires confirmation. Never make the user infer an asset's stage or next action from output alone.

## Protect Approval And History

Require explicit user confirmation for project goals, target users, success measures, non-goals, milestones, work approval or scope changes, core architecture or dependencies, data or permission models, migration or deletion, external spending or sending, release, and phase advancement. The recorded owner alone approves work, accepts it as done, approves release, and advances phase; producers and subagents may not impersonate that actor.

Approval binds the exact spec. If an unfinished approved spec changes, return the item to `proposed`, record why, and obtain approval again. A done item is terminal and its historical spec must not be rewritten as current project truth; changed follow-up scope requires a new item. Use `gate complete` only with evidence and advance phase only after its gates and owner approval.

After an item is approved, complete its bounded implementation and local verification without asking about every file. If a better approach materially changes its spec, explain the change and wait.

## Verify And Finish

During implementation, run the smallest checks covering changed behavior. After a correction, rerun only affected failures. Before final review, run the full relevant suite once, or state why it cannot run; do not run every available suite by default. Move to review with concrete evidence.

The producer performs final review against the approved spec, contract, and profile gates. For player- or user-visible work, use the running build as a first-time user on the target device, without test selectors or spec prompts, and walk the complete journey. Within scope, directly correct low-risk, reversible gaps in discoverability, visual hierarchy, continuity, empty/loading/success/failure/recovery states, and viewport reachability. Tests and screenshots prove implementation exists, not that the experience is understandable.

Move to done only when evidence supports owner acceptance. Update decisions or backlog only when project truth changed. End with result, evidence, remaining risk, one next action, and whether approval is required; do not auto-start a gated stage.

## Keep The Method Small

Do not add a document, state, gate, role, or checklist for one failure. Before changing the method, read `references/method-governance.md`, replace or narrow existing rules where possible, and obtain user approval.
