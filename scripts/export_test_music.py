"""Export only the owner's explicitly authorized music catalogue for UI testing."""

import argparse
import json
from pathlib import Path

from sqlalchemy import text

from shiros.adapters.database.connection import make_engine
from shiros.config import Settings
from shiros.identity import LocalIdentity
from shiros.music_seed import export_music_seed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--catalogue-only",
        action="store_true",
        help="Omit the owner's reviews, library state and saved playback links",
    )
    args = parser.parse_args()
    engine = make_engine(Settings().database_url())
    try:
        identity = LocalIdentity(engine)
        with engine.connect() as c:
            actor = identity.current(c)
            scope = c.scalar(
                text(
                    "SELECT scope_id FROM local_grants WHERE actor_id=:a AND role='owner' LIMIT 1"
                ),
                {"a": actor},
            )
        path = Path("build/desktop-test/music-seed.json.gz")
        counts = export_music_seed(
            engine, scope, path, include_personal=not args.catalogue_only
        )
        print(json.dumps({"path": str(path), "counts": counts, "bytes": path.stat().st_size}))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
