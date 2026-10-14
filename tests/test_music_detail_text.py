from pathlib import Path

import pytest
from sqlalchemy import Engine
from test_music import run, service

from shiros.domains.music import ObjectInput


@pytest.mark.integration
def test_music_detail_text_roundtrip_and_omitted_fields_survive_old_clients(
    db_engine: Engine, tmp_path: Path
):
    s = service(db_engine, tmp_path)
    work = run(
        s,
        "save",
        object={
            "kind": "work",
            "title": "Synthetic Song",
            "lyrics": "First line\nSecond line",
            "critical_reception": "Synthetic review with source.",
        },
    )["item"]
    saved = run(
        s,
        "save",
        id=work["id"],
        revision=work["revision"],
        object={"kind": "work", "title": "Synthetic Song renamed"},
    )["item"]
    assert saved["lyrics"] == "First line\nSecond line"
    assert saved["critical_reception"] == "Synthetic review with source."
    recording = run(
        s,
        "save",
        object={
            "kind": "recording",
            "title": "Synthetic Session",
            "recorded_on": "2000-01-01",
            "location": "Synthetic Hall",
            "recording_history": "Synthetic recording history.",
            "critical_reception": "Synthetic critic source.",
        },
    )["item"]
    assert recording["recording_history"] == "Synthetic recording history."
    release = run(
        s,
        "save",
        object={
            "kind": "release",
            "title": "Synthetic Release",
            "relations": [{"object_id": recording["id"], "role": "recording"}],
            "tracks": [
                {"title": "Synthetic Track", "position": 1, "lyrics": "Track text\nSecond line"}
            ],
        },
    )["item"]
    track = run(s, "detail", id=release["tracks"][0]["id"])["item"]
    assert track["lyrics"] == "Track text\nSecond line"
    track = run(
        s,
        "track-save",
        id=track["id"],
        revision=track["revision"],
        track={"title": track["title"], "position": 1, "relations": []},
    )["item"]
    assert track["lyrics"] == "Track text\nSecond line"
    cleared = run(
        s,
        "save",
        id=saved["id"],
        revision=saved["revision"],
        object={"kind": "work", "title": saved["title"], "lyrics": ""},
    )["item"]
    assert cleared["lyrics"] == ""
    export = run(s, "export")
    assert any(
        x.get("data", {}).get("recording_history") == "Synthetic recording history."
        for x in export["tables"]["music_profiles"]
    )


def test_lyrics_are_for_individual_works_and_tracks():
    for kind, form in (
        ("recording", ""),
        ("release", ""),
        ("person", ""),
        ("work", "album"),
        ("work", "collection"),
    ):
        with pytest.raises(ValueError, match="music.lyrics_work_only"):
            ObjectInput(kind=kind, title="Synthetic", work_form=form, lyrics="Text")
