# Method Governance

The workflow is infrastructure, not a place to accumulate advice.

## Change threshold

Propose a permanent method change only when at least two independent project incidents show the same failure class, or when one incident exposes an irreversible safety risk. Keep incident evidence in the work item's review; do not create a separate process log.

## Required proposal

A method change proposal must state:

- the observed incidents and shared cause;
- the smallest rule or deterministic check that would prevent recurrence;
- expected benefit and recurring context or execution cost;
- the existing rule it replaces, narrows, or makes redundant;
- how the change will be forward-tested and rolled back.

The user must approve the proposal. AI suggestions are not approval.

## Implementation preference

Use prose for judgment, references for domain knowledge, and scripts for fragile state transitions. Prefer tightening an existing rule over adding a new stage, role, file, or gate. Never use an arbitrary rule such as deleting two steps whenever one is added; remove only demonstrably redundant process.
