# ADR-006: Immutable Memory and Ordered Transactions

Status: Accepted, 2026-10-06.

Problem: A checkpoint can skip an event if sequence allocation is mistaken for commit order. Partial content/event writes and destructive edits also break traceability.

Decision: Immutable Memory revisions, constrained supersession chains, append-only content tables, and one transaction-scoped advisory lock for writes. A transactional row counter allocates event order. State, vector, event and receipt commit together. Incremental per-revision summaries, snapshots, derived events and checkpoint share one transaction.

Consequences: Retried requests are idempotent; failed batches leave no partial derived state. A global lock limits throughput. Remote providers will require a durable job/outbox design rather than long network calls inside this transaction. Explicit data erasure is deferred and must be designed separately from normal immutable updates.

