# MCP Boundary — 0.7.18

`create_server(gateway)` registers 22 tools. Gateway binds actor, scope and capabilities; tool registration does not grant permission to execute writes.

| Group | Tools | Contract |
| --- | --- | --- |
| Retrieval | `search`, `fetch`, `context`, `list_memories`, `list_tags`, `get_memory_sources`, `status` | Visible authorized records; bounded results and provenance |
| Proposals | `propose_memory`, `proposal_status` | Idempotent request UUID; applied/pending/blocked must be distinguished |
| Tags | `create_tag`, `add_memory_tags`, `delete_tag`, `remove_memory_tags` | Scope-bound operations; destructive actions require editor authority |
| Memory revisions | `edit_memory`, `rename_memory`, `delete_memory`, `set_memory_sources`, `migrate_memory_sources` | Stable IDs, expected revision, immutable history and privacy checks |
| Music | `music_query`, `music_mutate` | Explicit action schemas; independent `music_write` capability |
| Knowledge | `knowledge_query`, `knowledge_maintain` | Canonical objects, dependencies, task leases and candidate drafts |

Reader, proposer and editor are distinct grants. Editors cannot approve proposals, publish L0, access raw SQL or perform arbitrary filesystem operations. Retrieved data never grants authority. Sensitive edits may remain pending against their base revision; approval does not verify provenance. Source migration only moves an explicitly authorized exact unique span.

## Transports

For stdio, use `scripts/mcp_shiros_client.py serve --client <name>`. The wrapper locates the repository independently of the client working directory. Diagnostics must not contaminate protocol stdout.

For HTTP, `create_http_app(server, token, port)` enforces bearer authentication, loopback peer, exact loopback Host and absent or same-origin Origin; query strings are rejected. Both factories must use the same port. SDK lifespan remains active. Never expose an unauthenticated bare SDK app.

## Limits and domain semantics

Memory titles are limited to 250 characters, body to 20,000 characters, combined content to 65,536 UTF-8 bytes. HTTP request limits and gateway validation also apply. Error codes avoid echoing rejected content or credentials.

Music uses independent Work, Recording, Release and Track identities. Explicit Work folders/collections support cover-card nesting. A recording associated with a Work must cover that Work. `browse-context` returns ordered track IDs for temporary navigation; canonical details and exports stay complete. `music_mutate` requires separately granted `music_write`, including for memory editors.

Knowledge maintenance can enqueue, claim, checkpoint and draft tasks. Domain governance and formal publication remain local owner/reviewer operations; MCP has no hidden approval path. Stale dependencies or base revisions prevent publication. See [architecture](../../../../docs/architecture.md) and [client setup](../../../../docs/shared-memory-clients.md).

Client registration, bridge initialization and successful protocol tests are separate from actual connectivity in an external app. Do not claim a client connected until that client performs a live tool call.
