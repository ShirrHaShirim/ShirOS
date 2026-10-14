"""Detach an explicitly reviewed collection-cover copy while retaining every original image."""

import argparse
import hashlib
import json
from pathlib import Path

from sqlalchemy import text

from shiros.adapters.database.connection import make_engine
from shiros.adapters.database.records import lock_writes
from shiros.config import Settings
from shiros.domains.music import MusicRequest, MusicService
from shiros.identity import DatabasePermissions, LocalIdentity


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--isolated-result", type=Path)
    args = parser.parse_args()
    path = Path("docs/music-catalog-image-supplement-v0717.json")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    plan = json.loads(path.read_text(encoding="utf-8"))
    if not args.database.endswith("_test"):
        assert args.isolated_result
        proof = json.loads(args.isolated_result.read_text(encoding="utf-8"))
        assert proof["verified"] and proof["plan_sha256"] == digest
    engine = make_engine(Settings().database_url().set(database=args.database))
    with engine.begin() as c:
        actor = LocalIdentity(engine).current(c)
        scope = c.scalar(
            text("SELECT scope_id FROM local_grants WHERE actor_id=:id AND role='owner'"),
            {"id": actor},
        )
        assert scope
        DatabasePermissions(c).require(actor, scope, "music_write")
        lock_writes(c)
        before = c.scalar(text("SELECT count(*) FROM music_images WHERE scope_id=:s"), {"s": scope})
        service = MusicService(engine, actor, scope)
        changed = 0
        for image in plan["images"]:
            item = service._run(c, "detail", MusicRequest(id=image["id"]))["item"]
            if not item["image_id"]:
                continue
            assert (
                str(item["image_id"]) == image["image_id"] and item["revision"] == image["revision"]
            )
            old_profile = c.scalar(
                text("SELECT data FROM music_profiles WHERE id=:id"), {"id": item["id"]}
            )
            updated = service._run(
                c, "image-remove", MusicRequest(id=item["id"], revision=item["revision"])
            )["item"]
            assert updated["image_id"] is None
            assert old_profile == c.scalar(
                text("SELECT data FROM music_profiles WHERE id=:id"), {"id": item["id"]}
            )
            changed += 1
        assert before == c.scalar(
            text("SELECT count(*) FROM music_images WHERE scope_id=:s"), {"s": scope}
        )
    result = {
        "verified": True,
        "plan_sha256": digest,
        "images_detached": changed,
        "database": args.database,
    }
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
