# ADR-007: Trusted Review at the Persistence Boundary

Status: Accepted, 2026-10-06.

Problem: A client-supplied reviewed or persistence_allowed flag is not evidence of review, and derived text can leak prohibited source data.

Decision: Request schemas reject trust flags. A privileged local ReviewAuthority issues random, expiring approvals bound to actor and the complete input. Persistence checks permissions, approval, original/normalized privacy, provenance and idempotency before committing. Derived text is gated again; event payloads contain only identifiers, codes and structured provenance. Original denied content and approval fingerprints are not persisted.

Consequences: No anonymous write/review HTTP endpoint is added. Approval state is ephemeral, identity and scope grants come from the trusted caller, and rules remain an MVP detector. Production authentication, authorization management, revocation and comprehensive privacy classification remain future work.

