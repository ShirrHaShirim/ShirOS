# Domain boundary

Music v0.4.0 owns explicit relational tables and a bound service in `music.py`.
It shares PostgreSQL, entities, sources, permissions, tags, audit and event ordering
with Shared Memory Core. Music records are not automatically promoted to memory.
Papers, Knowledge, People and Projects remain future modules.
