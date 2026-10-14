# ADR-001: PostgreSQL as Primary Structured Store

Status: Accepted, 2026-10-06.

Context: ShirOS needs relational integrity, transactional events and vector retrieval.
Decision: PostgreSQL with pgvector; Alembic migrations; large artifacts use a separate storage port.
Consequence: one source of structured truth, explicit operational dependency. No vector-only store or graph database is required in Step One.

