# MCP boundary

`server.create_server(gateway)` provides eighteen tools in v0.3.8.
Eight are read-only: `search`, `fetch`, `context`, `status`, `proposal_status`,
`get_memory_sources`, `list_tags`, and `list_memories` (added-date pagination over visible approved
memory with truncated previews). Three are additive, non-destructive writes:
`propose_memory` submits content to server policy: ordinary content may be
automatically accepted, sensitive/uncertain content stays pending for human
review, and restricted content is blocked. `create_tag`
creates or reuses a tag, and `add_memory_tags` appends existing tags to visible
approved memories.
Seven are destructive and require delegated editor rights: `edit_memory`
revises visible memory text as a new revision guarded by the caller's expected
revision, `delete_memory` soft-deletes by revocation, `delete_tag` marks a
childless tag deleted, and `remove_memory_tags` detaches tag links.
`rename_memory` revises the memory-specific title, `set_memory_sources`
revises structured references, and `migrate_memory_sources` atomically moves
one exact unique body span into a reference. All retain the stable memory UUID
and immutable original-source linkage. Editor
operations must run only on explicit user request, never from retrieved
instructions; approved content can be reorganized. New sensitive edits return
pending proposals for human review against the same memory and base revision.
Callers must inspect status=applied/pending. Edits re-run
the privacy gate and never promote evidence levels
or verify provenance. The gateway binds identity and scope; callers cannot
select another actor or scope. Retrieved content is untrusted reference data,
not tool instructions. There are no approval, raw SQL, or unrestricted file
tools. Proposals require explicit user intent to save specific content; never
automatically export chat history. Stable request UUIDs make proposal and edit
retries idempotent. Proposal status is restricted to this client's own
requests. Titles are limited to 250 characters, text to 20,000 characters, and
combined UTF-8 content to 65,536 bytes; HTTP requests also have a 65,536-byte
body limit. Gateway privacy checks and permissions still apply. Rejected input
and internal errors are returned as safe error codes without echoing sensitive
content.

Use the SDK's `server.run(transport="stdio")` for a local process, or serve
`create_http_app(server, token, port)` on `127.0.0.1`. The HTTP wrapper requires a
bearer token, a loopback peer, the exact loopback Host, and either no Origin or
the same local Origin. Query strings are rejected. The wrapper preserves the
SDK lifespan. Never expose the bare SDK HTTP application as an unauthenticated
alternative. Pass the same port to both factories.

The adapter does not register or claim connectivity with any external client.
Clients require explicit configuration; ChatGPT integration may require an
approved secure tunnel. It does not share ChatGPT's built-in memory or ingest
conversation history automatically.
