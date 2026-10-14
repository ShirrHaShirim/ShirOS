"""Profile facts, image attribution history and additive genre catalogue upgrades."""

from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine
from test_music import service
from test_music_v041 import bitmap

from shiros.domains.music import MusicRequest, ObjectInput, _safe, _safe_profile


def test_catalogue_identifiers_do_not_hide_contacts_or_secrets() -> None:
    url = "https://musicbrainz.org/artist/66742cdb-d1c2-4389-8452-3ff36db12da9"
    assert _safe(url) == url
    _safe_profile({"official_url": "https://example.org/artists/106023346"})
    _safe_profile(
        {
            "image_attributions": {
                "a": {"source_url": "https://example.org/photo/106023346", "credit": "Artist"}
            }
        }
    )
    for value in [url + " phone: +86 13800138000", "password=secret-value", "5555-1234"]:
        with pytest.raises(PermissionError):
            _safe(value)
    for profile in [
        {"official_url": "https://example.org/?token=secret-value"},
        {"description": "phone: +86 13800138000"},
        {"image_attributions": {"a": {"credit": "phone: +86 13800138000"}}},
    ]:
        with pytest.raises(PermissionError):
            _safe_profile(profile)


def test_profile_fields_validate_entity_and_urls() -> None:
    ObjectInput(kind="person", title="Synthetic person", born_on="1900-01-01")
    ObjectInput(kind="organization", title="Synthetic label", founded_on="1970")
    for values in [
        {"kind": "work", "country": "Synthetic"},
        {"kind": "group", "born_on": "1900-01-01"},
        {"kind": "person", "founded_on": "1970"},
        {"kind": "person", "born_on": "2000-01-01", "died_on": "1900-01-01"},
        {"kind": "person", "official_url": "javascript:alert(1)"},
        {"kind": "person", "official_url": "https://user:secret@example.org"},
    ]:
        with pytest.raises((ValidationError, ValueError)):
            ObjectInput(title="Synthetic invalid", **values)


@pytest.mark.integration
def test_profile_and_image_credits_survive_edits_and_replacement(
    db_engine: Engine, tmp_path: Path
) -> None:
    s = service(db_engine, tmp_path)
    obj = {"kind": "person", "title": "Synthetic", "country": "Synthetic region"}
    person = s.run("save", MusicRequest(object=obj))["item"]
    args = dict(id=person["id"], revision=person["revision"], image_data=bitmap())
    with pytest.raises(ValueError, match="music.invalid_url"):
        s.run("image-upload", MusicRequest(**args, image_source_url="javascript:alert(1)"))
    person = s.run(
        "image-upload",
        MusicRequest(
            **args, image_source_url="https://example.org/portrait", image_credit="Artist"
        ),
    )["item"]
    old_image = str(person["image_id"])
    assert person["image_info"]["credit"] == "Artist"
    person = s.run("save", MusicRequest(id=person["id"], revision=person["revision"], object=obj))[
        "item"
    ]
    assert person["country"] == "Synthetic region"
    assert person["image_info"]["source_url"] == "https://example.org/portrait"
    person = s.run(
        "image-upload",
        MusicRequest(id=person["id"], revision=person["revision"], image_data=bitmap("blue")),
    )["item"]
    assert person["image_info"]["credit"] == ""
    assert person["image_attributions"][old_image]["credit"] == "Artist"
    person = s.run("image-remove", MusicRequest(id=person["id"], revision=person["revision"]))[
        "item"
    ]
    assert person["image_info"] == {}
    assert person["image_attributions"][old_image]["credit"] == "Artist"


@pytest.mark.integration
def test_seed_preserves_renamed_and_deleted_genres(db_engine: Engine, tmp_path: Path) -> None:
    s = service(db_engine, tmp_path)
    s.run("taxonomy-seed", MusicRequest())
    nodes = s.run("taxonomy", MusicRequest())["items"]
    root = next(n for n in nodes if n["name"] == "摇滚")
    leaf = next(n for n in nodes if n["name"] == "硬摇滚")
    s.run(
        "taxonomy-save",
        MusicRequest(id=root["id"], revision=root["revision"], name="My rock", namespace="genre"),
    )
    s.run("taxonomy-delete", MusicRequest(id=leaf["id"], revision=leaf["revision"]))
    s.run("taxonomy-seed", MusicRequest())
    final = s.run("taxonomy", MusicRequest())["items"]
    assert len(final) == len(nodes) - 1
    assert any(n["id"] == root["id"] and n["name"] == "My rock" for n in final)
    assert not any(n["id"] == leaf["id"] for n in final)
