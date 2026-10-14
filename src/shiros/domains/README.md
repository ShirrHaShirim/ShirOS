# Domain Ownership — 0.7.18

`music.py` and related `music_*.py` modules own the Music Library. Explicit tables distinguish Works, Recordings, Releases, Tracks, profiles, relationships, taxonomy, reviews and images. Music shares PostgreSQL, sources, permissions, scope and auditing with Shared Memory, but music objects do not automatically become Memory records.

Knowledge adapters reference canonical Memory/Music IDs and revisions instead of creating independently editable fact copies. Files and capture are separately governed services outside this package. Papers, projects and other planned domains are not fully implemented.

See [module boundaries](../../../docs/module-boundaries.md) and [music specification](../../../docs/music-library.md).
