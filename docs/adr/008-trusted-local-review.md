# ADR 008: Trusted Local Review and Revocation

- Status: Accepted for local MVP
- Date: 2026-10-06

## Decision

Use the existing Python services and a bilingual interactive CLI instead of adding a second web authentication surface. Bind reviewers to the operating-system process identity (Windows SID or Unix UID), with persisted scope roles and default deny. Client inputs cannot assert an actor, approval, or persistence permission.

Separate transient file preview, explicitly consented sanitized staging, and human approval. Candidates and source context are never normal retrieval inputs. Preserve sanitized original candidates and append review states. Approval and core memory ingestion share one PostgreSQL transaction and the existing global write lock.

Use append-only source/memory revocations and reversible visibility states. Filter every normal exact, source, keyword, semantic, hybrid, layer and context path at the underlying Memory. Revocations apply to stable memory roots to prevent revision resurrection. Retain physical history and derived rows for audit; no erase policy is enabled.

Use PostgreSQL custom-format dumps plus a checksum, schema revision and server version manifest. Restore only into a new isolated `shiros_restore_*_test` database. Scope ownership alone does not authorize full-database export: require the initial bootstrap owner. Archives must be locally generated and trusted; a dump can contain executable SQL. Hashes detect corruption, not malicious modification.

## Consequences

- Local review has no network listener and no external-model dependency.
- Users sharing one OS account share an identity; this is not production authentication.
- Direct database access and modification of trusted Python code remain outside the application permission boundary.
- Rule-based privacy needs human inspection and does not guarantee detection of all sensitive content.
- Backups contain permitted personal data, old revisions and revoked content, but exclude project files and development secrets. They are not encrypted.
- Physical erasure, backup retention/encryption, independent off-device recovery and broad disaster recovery remain deferred.
