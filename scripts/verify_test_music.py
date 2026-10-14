"""Inspect only a disposable packaged-test database; never print connection secrets."""

import argparse
import gzip
import json
from pathlib import Path
from uuid import uuid5

from sqlalchemy import URL, text

from shiros.adapters.database.connection import make_engine


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--analyze", action="store_true")
    parser.add_argument("--data-dir", type=Path, default=Path(".local/test-package"))
    args = parser.parse_args()
    data = args.data_dir.resolve()
    data.relative_to((Path(__file__).resolve().parents[1] / ".local").resolve())
    if not data.name.startswith("test-package-"):
        raise ValueError("Use a disposable package test directory")
    config = json.loads((data / "database.json").read_text(encoding="utf-8"))
    engine = make_engine(
        URL.create(
            "postgresql+psycopg",
            host="127.0.0.1",
            port=config["port"],
            username="shiros",
            password=config["password"],
            database="shiros",
        )
    )
    try:
        with engine.begin() as c:
            activity = [
                dict(r)
                for r in c.execute(
                    text(
                        "SELECT pid,wait_event_type,wait_event,"
                        "extract(epoch from now()-query_start)::integer AS seconds "
                        "FROM pg_stat_activity WHERE state='active' AND pid<>pg_backend_pid()"
                    )
                ).mappings()
            ]
            print(json.dumps({"activity": activity}))
            if args.analyze:
                c.execute(text("ANALYZE"))
                print("Updated query statistics in disposable test database")
        with gzip.open("build/desktop-test/music-seed.json.gz", "rt", encoding="utf-8") as f:
            seed = json.load(f)
        with engine.connect() as c:
            scope = c.scalar(text("SELECT scope_id FROM desktop_seed_receipts LIMIT 1"))
            counts = {
                name: c.scalar(text("SELECT count(*) FROM " + name)) for name in seed["counts"]
            }
            assert counts == seed["counts"]
            # Imported UUIDs belong to the new recipient scope. Musical data stays exact.
            checked = 0
            for original in seed["tables"]["music_reviews"]:
                identifier = uuid5(scope, "test-seed:" + original["id"])
                actual = (
                    c.execute(
                        text(
                            "SELECT rating,rating_max,comment,original_at,created_at "
                            "FROM music_reviews WHERE id=:id"
                        ),
                        {"id": identifier},
                    )
                    .mappings()
                    .one()
                )
                for key in actual:
                    value = (
                        str(actual[key])
                        if key.endswith("_at") and actual[key] is not None
                        else actual[key]
                    )
                    assert value == original[key]
                checked += 1
            images = {
                str(row.id): row.sha256
                for row in c.execute(text("SELECT id,sha256 FROM music_images"))
            }
            assert all(
                images[str(uuid5(scope, "test-seed:" + row["id"]))] == row["sha256"]
                for row in seed["tables"]["music_images"]
            )
            frames = 0
            for original in seed["tables"]["music_profiles"]:
                if not original["data"].get("image_frames"):
                    continue
                actual = c.scalar(
                    text("SELECT data FROM music_profiles WHERE id=:id"),
                    {"id": uuid5(scope, "test-seed:" + original["id"])},
                )
                for image_id, frame in original["data"]["image_frames"].items():
                    # Replaced images are omitted. Their historical framing remains unused.
                    remapped = (
                        str(uuid5(scope, "test-seed:" + image_id))
                        if image_id in {r["id"] for r in seed["tables"]["music_images"]}
                        else image_id
                    )
                    assert actual["image_frames"][remapped] == frame
                    frames += 1
            result = {
                "counts": counts,
                "reviews_exact": checked,
                "image_hashes_exact": len(images),
                "frames_exact": frames,
            }
            Path(".local/test-package-checks").mkdir(exist_ok=True)
            Path(".local/test-package-checks/data-result.json").write_text(
                json.dumps(result, indent=2), encoding="utf-8"
            )
            print(json.dumps(result))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
