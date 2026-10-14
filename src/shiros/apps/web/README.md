# Local Workbench — 0.7.18

Native HTML/CSS/JavaScript adapter served by `apps/workbench.py`. Start with
`uv run shiros web --port 8001` and open the authorized session with `uv run shiros open`.
Desktop embeds the same service in pywebview.

## State and authentication

The server binds actor and scope. Browser clients do not access SQL or choose
another identity. The ephemeral capability stays in sessionStorage; localStorage
contains language and theme preferences. Page caches are transient. No CDN,
analytics or separate browser copy of the personal knowledge database is required.
Images are validated on upload and read through authenticated temporary blob URLs.

## Music navigation

Work roots and children use cover cards with explicit `parent_work` nesting and
independent images. Genre and personal tags remain separate from browsing folders.
Complete recordings may cover multiple Works; explicit child recordings are loaded
lazily and can be reused across Releases. Child recordings omit rating controls.

Work → Recording → Release carries a transient range. The compact inline range
shows title, visible/total count and Show all. Recording → Album Work initially
shows performed songs. Independent search, sorting, filters and mutations clear
inherited ranges; Back restores them. Canonical detail and exports remain complete.
The server validates scope and resolves ordered track IDs through `browse-context`.

Library search uses associated names and profile suggestions. In-memory page caches
last up to 90 seconds and invalidate after successful changes. Facts render before
slower artwork; profile sections can load independently. UI language changes do not
translate stored names, notes, reviews or private tags.

## Capture and knowledge

Conversation capture requires explicit per-session authorization, a separate
pairing token and authorization rechecks. Attachment originals need separate
permission. Sensitive candidates remain pending. Knowledge drafts and formal
publication are separate, including additional L0 confirmation.

See [architecture](../../../../docs/architecture.md),
[music rules](../../../../docs/music-entry-rules.md) and
[capture protocol](../../../../docs/conversation-sync-v073.md).
