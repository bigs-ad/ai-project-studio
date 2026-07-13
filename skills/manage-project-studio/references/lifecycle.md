# Lifecycle And Work Items

## Project lifecycle

Use one shared lifecycle for both profiles:

1. discovery: define outcome, user, success, constraints, and non-goals.
2. validation: test the riskiest assumption with the smallest credible artifact.
3. build: deliver approved vertical slices and verify each slice.
4. release: harden, prepare rollback, obtain approval, and distribute.
5. operate: observe real use, learn, and deliberately return to build when needed.

Do not advance because documents exist. Advance because the required evidence exists and the user approves the gate.
Each phase entry creates a new phase run. Re-entering build after operate starts with fresh gates; prior evidence remains archived but cannot satisfy the new run.

## Work-item lifecycle

Use these transitions:

    draft -> proposed -> approved -> in_progress -> review -> done
                |            |             |          |
                v            v             v          v
             rejected     cancelled     cancelled  in_progress

A rejected item may return to draft. Any unfinished item may be cancelled. Only approved work may enter in_progress. Review may return to in_progress.

A work item is bounded when it has an outcome, non-goals, acceptance conditions, affected area, verification plan, and deliverable contract. The contract declares fidelity, purpose, limits, remaining production steps, evidence, and current unknowns. Prefer one vertical user or player outcome over separate frontend, backend, or infrastructure batches. Keep at most one item in progress or review at a time.

The recorded project owner approves proposed work and accepts review work as done. Producers and subagents may supply implementation and review evidence but may not impersonate user approval. Store a checkpoint before pausing unfinished work so a later thread can recover progress without replaying chat history.

## Change requests

Stop implementation when new work changes an accepted requirement, an explicit non-goal, architecture, external cost, data or permission behavior, release risk, or the promised outcome. Present the smallest revised option and wait for confirmation. Do not silently enlarge the item.
When an unfinished approved spec changes, move its work item back to proposed, record the reason, and obtain a new approval. The CLI binds approval to the spec SHA-256. A done item remains terminal; changed follow-up scope becomes a new work item, while mismatch with its historical approval is reported as a warning.

## Evidence

Evidence must identify an inspectable result: test command and result, build path, screenshot, playtest observation, reviewed diff, deployed preview, or documented decision. Statements such as "looks good" or "should work" are not evidence.
