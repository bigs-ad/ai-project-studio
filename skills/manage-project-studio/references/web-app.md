# Web Application Profile

Optimize for a user completing a valuable end-to-end job safely and reliably.

## Phase focus

- discovery: define target user, real problem, primary journey, success signal, constraints, and explicit non-goals.
- validation: test the primary journey with a prototype or thin end-to-end slice. Validate value before building broad infrastructure.
- build: deliver complete user-value slices including data, permissions, loading, empty, error, and recovery states.
- release: verify accessibility, security, privacy, migrations, observability, deployment, and rollback.
- operate: use production behavior and feedback to choose the next risk or outcome.

## Ordering rules

Do not start with a generic design system, broad platform layer, or speculative scalability. Establish the critical user journey and data or permission boundaries first. Treat authentication, payments, personal data, destructive actions, external messages, and production migrations as explicit approval boundaries.

## Review questions

- Can the target user complete the primary job end to end?
- Are loading, empty, validation, error, permission-denied, and recovery states covered?
- Are authorization checks enforced server-side where applicable?
- Are data ownership, retention, migration, and deletion behaviors explicit?
- Is keyboard use and accessible naming supported?
- Are deployment health, monitoring, and rollback observable?
