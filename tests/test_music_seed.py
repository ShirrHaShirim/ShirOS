"""Music test snapshots never transfer private memory or the original identity."""

import base64
import gzip
import io
import json
from pathlib import Path
from uuid import uuid5

import pytest
from PIL import Image
from sqlalchemy import Engine, text
from test_music import run, service

from shiros.music_seed import clean, export_music_seed, import_music_seed


def test_seed_preserves_public_urls_and_removes_local_paths() -> None:
    assert clean("https://example.org/music") == "https://example.org/music"
    assert clean("http://example.org/music") == "http://example.org/music"
    assert clean("C:/Users/private/photo.png") == "[local file]"


@pytest.mark.integration
def test_seed_reviews_identity_relations_and_idempotency(db_engine: Engine, tmp_path: Path) -> None:
    original = service(db_engine, tmp_path / "original")
    person = run(original, "save", object={"kind": "person", "title": "Synthetic composer"})["item"]
    work = run(
        original,
        "save",
        object={
            "kind": "recording",
            "title": "Synthetic recording",
            "relations": [{"object_id": str(person["id"]), "role": "composer"}],
        },
    )["item"]
    run(
        original,
        "review",
        id=str(work["id"]),
        revision=work["revision"],
        rating=6,
        rating_max=7,
        comment="Synthetic seven-star review",
    )
    updated = run(original, "detail", id=str(work["id"]))["item"]
    image = io.BytesIO()
    Image.new("RGB", (16, 16), "red").save(image, format="PNG")
    updated = run(
        original,
        "image-upload",
        id=str(work["id"]),
        revision=updated["revision"],
        image_data=base64.b64encode(image.getvalue()).decode(),
        filename="synthetic.png",
        image_source_url="https://example.org/portrait",
        image_credit="Synthetic fixture",
    )["item"]
    run(
        original,
        "image-frame",
        id=str(work["id"]),
        revision=updated["revision"],
        image_frame={"x": 20, "y": 30, "zoom": 2},
    )
    updated = run(original, "detail", id=str(work["id"]))["item"]
    run(
        original,
        "review",
        id=str(work["id"]),
        revision=updated["revision"],
        rating=7,
        rating_max=7,
        comment="Newest synthetic review",
    )
    seed = tmp_path / "music.json.gz"
    counts = export_music_seed(db_engine, original.scope, seed)
    with gzip.open(seed, "rt", encoding="utf-8") as f:
        payload = json.load(f)
    serialized = json.dumps(payload)
    assert str(original.actor) not in serialized and str(original.scope) not in serialized
    assert "sources" not in payload["tables"] and "memories" not in payload["tables"]
    assert counts["music_reviews"] == 2
    recipient = service(db_engine, tmp_path / "recipient")
    result = import_music_seed(db_engine, recipient.actor, recipient.scope, seed)
    assert result["status"] == "imported"
    imported_id = uuid5(recipient.scope, "test-seed:" + str(work["id"]))
    detail = run(recipient, "detail", id=str(imported_id))["item"]
    assert detail["rating"] == 7
    assert detail["reviews"][0]["comment"] == "Newest synthetic review"
    assert detail["relations"][0]["role"] == "composer"
    assert (
        detail["image_attributions"][str(detail["image_id"])]["source_url"]
        == "https://example.org/portrait"
    )
    assert detail["image_frames"][str(detail["image_id"])]["x"] == 20
    assert (
        import_music_seed(db_engine, recipient.actor, recipient.scope, seed)["status"]
        == "already_initialized"
    )
    with db_engine.connect() as c:
        for table in (
            "memories",
            "memory_candidates",
            "library_files",
            "capture_grants",
            "memory_tags",
        ):
            assert (
                c.scalar(
                    text(f"SELECT count(*) FROM {table} WHERE scope_id=:s"), {"s": recipient.scope}
                )
                == 0
            )
        assert (
            c.scalar(
                text("SELECT count(*) FROM music_reviews WHERE scope_id=:s"), {"s": recipient.scope}
            )
            == 2
        )
        assert c.scalar(
            text("SELECT count(*) FROM music_taxonomy WHERE scope_id=:s AND namespace='identity'"),
            {"s": recipient.scope},
        ) == counts["music_taxonomy"] - sum(
            r["namespace"] != "identity" for r in payload["tables"]["music_taxonomy"]
        )


@pytest.mark.integration
def test_catalogue_only_seed_omits_owner_reviews_state_and_links(
    db_engine: Engine, tmp_path: Path
) -> None:
    original = service(db_engine, tmp_path / "original")
    recording = run(
        original, "save", object={"kind": "recording", "title": "Synthetic catalogue audio"}
    )["item"]
    release = run(
        original,
        "save",
        object={
            "kind": "release",
            "title": "Synthetic catalogue release",
            "relations": [{"object_id": str(recording["id"]), "role": "recording"}],
        },
    )["item"]
    release = run(
        original,
        "review",
        id=str(release["id"]),
        revision=release["revision"],
        rating=6,
        rating_max=7,
        comment="Synthetic private review",
    )["item"]
    release = run(
        original,
        "membership",
        id=str(release["id"]),
        revision=release["revision"],
        library="frequent",
    )["item"]
    run(
        original,
        "source",
        id=str(release["id"]),
        revision=release["revision"],
        url="https://example.org/private-link",
        platform="external",
    )
    seed = tmp_path / "catalogue.json.gz"
    counts = export_music_seed(db_engine, original.scope, seed, include_personal=False)
    with gzip.open(seed, "rt", encoding="utf-8") as f:
        payload = json.load(f)
    for name in ("music_reviews", "music_memberships", "music_playback_sources"):
        assert counts[name] == 0 and payload["tables"][name] == []
    recipient = service(db_engine, tmp_path / "recipient")
    result = import_music_seed(db_engine, recipient.actor, recipient.scope, seed)
    assert result["status"] == "imported"
    imported = run(
        recipient, "detail", id=str(uuid5(recipient.scope, "test-seed:" + str(release["id"])))
    )["item"]
    assert imported["title"] == "Synthetic catalogue release"
    assert imported["reviews"] == []
    with db_engine.connect() as c:
        for table in ("music_reviews", "music_memberships", "music_playback_sources"):
            count = c.scalar(
                text(f"SELECT count(*) FROM {table} WHERE scope_id=:s"),
                {"s": recipient.scope},
            )
            assert count == 0


@pytest.mark.integration
def test_seed_preserves_existing_catalogue(db_engine: Engine, tmp_path: Path) -> None:
    source = service(db_engine, tmp_path / "source")
    run(source, "save", object={"kind": "work", "title": "Synthetic seed"})
    seed = tmp_path / "music.json.gz"
    export_music_seed(db_engine, source.scope, seed)
    recipient = service(db_engine, tmp_path / "recipient")
    run(recipient, "save", object={"kind": "work", "title": "Existing local work"})
    assert (
        import_music_seed(db_engine, recipient.actor, recipient.scope, seed)["status"]
        == "existing_catalogue_preserved"
    )
    assert run(recipient, "list", kind="work")["items"][0]["title"] == "Existing local work"


def test_seed_rejects_private_table(tmp_path: Path) -> None:
    seed = tmp_path / "bad.json.gz"
    with gzip.open(seed, "wt", encoding="utf-8") as f:
        json.dump({"format": "shiros-test-music-1", "tables": {"memories": []}}, f)
    with pytest.raises(ValueError, match="invalid_music_seed"):
        import_music_seed(None, None, None, seed)  # Validation precedes DB access.
