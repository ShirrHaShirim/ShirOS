import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import Engine
from test_music import run, service


@pytest.mark.integration
def test_collection_status_is_opt_in_and_metadata_preserves_relations(
    db_engine: Engine, tmp_path: Path
) -> None:
    s = service(db_engine, tmp_path)
    rows = [
        dict(
            douban_id="98765432",
            title="Synthetic collection",
            status="wish",
            rating=4,
            marked_at="2025/07/23",
        )
    ]
    run(s, "import", content=json.dumps(rows))
    item = run(s, "list")["items"][0]
    detail = run(s, "detail", id=item["id"])["item"]
    assert (
        detail["reviews"][0]["original_at"].astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
        == "2025-07-23"
    )
    assert detail["reviews"][0]["rating_max"] == 5
    rows[0]["status"] = "listened"
    run(s, "import", content=json.dumps(rows))
    assert run(s, "detail", id=item["id"])["item"]["status"] == "wishlist"
    run(s, "import", content=json.dumps(rows), sync_status=True)
    detail = run(s, "detail", id=item["id"])["item"]
    assert detail["status"] == "listened"
    updated = run(
        s,
        "release-metadata",
        id=item["id"],
        revision=detail["revision"],
        metadata={"barcode": "4011790409825", "medium": "Audio CD"},
    )["item"]
    assert updated["barcode"] == "4011790409825"
    assert updated["relations"] == detail["relations"]
    with pytest.raises(ValueError, match="invalid_release_metadata"):
        run(
            s,
            "release-metadata",
            id=item["id"],
            revision=updated["revision"],
            metadata={"title": "overwrite"},
        )
    assert (
        run(s, "archive", object_ids=[item["id"]], reason="Remove synthetic fixture")["archived"]
        == 1
    )
    assert run(s, "list")["total"] == 0
    with pytest.raises(ValueError, match="music.not_found"):
        run(s, "detail", id=item["id"])
    exported = run(s, "export")
    assert exported


@pytest.mark.integration
def test_archive_protects_live_relations_and_retains_history(
    db_engine: Engine, tmp_path: Path
) -> None:
    s = service(db_engine, tmp_path)
    recording = run(s, "save", object={"kind": "recording", "title": "Synthetic archive audio"})[
        "item"
    ]
    release = run(
        s,
        "save",
        object={
            "title": "Synthetic archive release",
            "tracks": [
                {
                    "title": "Synthetic archive track",
                    "position": 1,
                    "relations": [{"object_id": recording["id"], "role": "recording"}],
                }
            ],
        },
    )["item"]
    with pytest.raises(ValueError, match="music.archive_in_use"):
        run(s, "archive", object_ids=[recording["id"]], reason="Synthetic fixture removal")
    assert (
        run(s, "archive", object_ids=[release["id"]], reason="Synthetic fixture removal")[
            "archived"
        ]
        == 2
    )
    assert run(s, "list", kind="track")["total"] == 0
    assert run(s, "detail", id=recording["id"])["item"]["id"] == recording["id"]
    data = run(s, "export")["tables"]
    assert len(data["music_object_records"]) == 3
    assert len(data["music_objects"]) == 1
    assert any(row["action"] == "music.archived" for row in data["music_history"])
    reader = service(db_engine, tmp_path / "reader", role="reader")
    with pytest.raises(PermissionError):
        run(reader, "archive", object_ids=[recording["id"]], reason="Synthetic fixture removal")
