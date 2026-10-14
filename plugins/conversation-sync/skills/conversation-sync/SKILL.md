---
name: conversation-sync
description: Configure or inspect ShirOS conversation capture with explicit per-conversation permission.
---

Open ShirOS Settings, then Plugins and conversation sync. Create a grant for the exact
conversation, and obtain its local pairing information. Never copy the pairing token into
chat, a public repository, or a log. Save it locally at `~/.config/ShirOS/capture.json`, or
the user's `SHIROS_CAPTURE_CONFIG` path. Multiple pairings use `{"pairings":[...]}`.

Installing the plugin is not consent to collect all conversations. Codex must separately
trust its hook definition. The hook reads only a paired session. The native file adapter
reads only the selected transcript. Revoking a ShirOS grant stops ingestion and does not
delete existing memories or files. Do not approve sensitive memories automatically.

Use the source project's `docs/conversation-sync-v073.md` for installation, supported
formats, limitations, and verification. Never claim inaccessible attachments or unloaded
messages were captured. Test with synthetic content before collecting user conversations.
