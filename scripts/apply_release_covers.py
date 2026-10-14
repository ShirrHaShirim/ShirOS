"""Attach reviewed, source-matched artwork to Releases that still lack a cover.

Uses the normal music permission, revision, image validation and history path.
The default is a read-only check. No metadata, reviews or relations are replaced.
"""

import argparse
import base64
import hashlib
import json
from pathlib import Path
from uuid import UUID

from sqlalchemy import text

from shiros.adapters.database.connection import make_engine
from shiros.config import Settings
from shiros.domains.music import MusicRequest, MusicService
from shiros.identity import LocalIdentity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--database", help="Optional isolated database name")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    url = Settings().database_url()
    if args.database:
        url = url.set(database=args.database)
    engine = make_engine(url)
    rows = json.loads(args.manifest.read_text(encoding="utf-8"))
    if len({row["id"] for row in rows}) != len(rows):
        raise ValueError("duplicate_release_id")
    args.log.parent.mkdir(parents=True, exist_ok=True)
    with engine.connect() as c:
        actor = LocalIdentity(engine).current(c)
    results = []
    with args.log.open("a", encoding="utf-8") as log:
        for row in rows:
            result = {"id": row["id"], "title": row["title"]}
            try:
                if row.get("status") != "downloaded" or not row.get("reviewed"):
                    raise ValueError("unreviewed_asset")
                data = Path(row["path"]).read_bytes()
                if hashlib.sha256(data).hexdigest() != row["sha256"]:
                    raise ValueError("asset_hash_mismatch")
                with engine.connect() as c:
                    before = (
                        c.execute(
                            text(
                                "SELECT o.*,r.douban_id,p.data FROM music_objects o "
                                "JOIN music_releases r ON r.id=o.id "
                                "LEFT JOIN music_profiles p ON p.id=o.id WHERE o.id=:id"
                            ),
                            {"id": UUID(row["id"])},
                        )
                        .mappings()
                        .one()
                    )
                if before["kind"] != "release" or before["archived"]:
                    raise ValueError("not_active_release")
                if before["image_id"]:
                    result["status"] = "existing_cover_preserved"
                else:
                    if before["title"] != row["title"]:
                        raise ValueError("release_title_changed")
                    if before["revision"] != row["revision"]:
                        raise ValueError("release_revision_changed")
                    if row.get("douban_id") and before["douban_id"] != row["douban_id"]:
                        raise ValueError("source_identity_changed")
                    result["status"] = "validated"
                    if args.apply:
                        item = MusicService(engine, actor, before["scope_id"]).run(
                            "image-upload",
                            MusicRequest(
                                id=UUID(row["id"]),
                                revision=before["revision"],
                                filename="Release front cover",
                                image_data=base64.b64encode(data).decode(),
                                image_source_url=row["source_url"],
                                image_credit="发行封面；已核对原始来源与发行版本。",
                                image_license="来源未明确图片授权；保留原始来源供核查。",
                            ),
                        )["item"]
                        with engine.connect() as c:
                            after = (
                                c.scalar(
                                    text("SELECT data FROM music_profiles WHERE id=:id"),
                                    {"id": UUID(row["id"])},
                                )
                                or {}
                            )
                        original = before["data"] or {}
                        # Preserve every pre-existing profile field and attribution.
                        for key, value in original.items():
                            if key == "image_attributions":
                                assert all(after[key].get(k) == v for k, v in value.items())
                            else:
                                assert after.get(key) == value, key
                        assert item["image_id"]
                        result.update(status="cover_added", image_id=item["image_id"])
            except Exception as exc:
                result.update(status="failed", reason=str(exc)[:160])
            log.write(json.dumps(result, ensure_ascii=False, default=str) + "\n")
            log.flush()
            results.append(result)
            if len(results) % 20 == 0 or result["status"] == "failed":
                print(
                    json.dumps({"checked": len(results), "last_status": result["status"]}),
                    flush=True,
                )
    print(
        json.dumps(
            {
                "checked": len(results),
                "added": sum(r["status"] == "cover_added" for r in results),
                "preserved": sum(r["status"] == "existing_cover_preserved" for r in results),
                "failed": sum(r["status"] == "failed" for r in results),
            }
        )
    )


if __name__ == "__main__":
    main()
