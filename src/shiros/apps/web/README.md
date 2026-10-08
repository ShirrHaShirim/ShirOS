# Local Workbench 0.5.0

Dependency-free browser adapter for existing review and retrieval services. Start
with Start-ShirOS.cmd, or use `uv run shiros web --port 8001` and `uv run shiros open`.

Database access remains on the Python service side. Browser clients cannot choose
actor or scope. No CDN, remote models, analytics, automatic imports or persistent
browser copy of source text. sessionStorage contains only the ephemeral browser
capability; localStorage contains language and color-theme preferences. Music image payloads
are validated and stored on the server; authenticated reads use temporary blob URLs.
Music Genre and private-tag trees are independent from memory tags.

