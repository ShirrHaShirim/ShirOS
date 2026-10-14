# ADR-005: Open Workspace Projection

Status: Accepted, 2026-10-06.

Context: Knowledge must remain readable by Obsidian, VSCode, Git and ordinary file tools.
Decision: stable UUID-based Markdown/YAML projections, with database ownership of system fields.
Consequence: external changes need whitelist validation and conflict handling. Step One uses an in-memory mock; the manually exported work report is not a working synchronizer.

