"""Specific credits round-trip, derive identities, and participate in tree filters."""

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine, text
from test_music import service

from shiros.domains.music import MusicRequest, RelationInput
from shiros.domains.music_identity_catalogue import PARTS


@pytest.mark.integration
def test_specific_identity_catalogue(db_engine: Engine, tmp_path: Path) -> None:
    s = service(db_engine, tmp_path)

    def run(action: str, **data: Any) -> dict[str, Any]:
        return s.run(action, MusicRequest.model_validate(data))

    run("taxonomy-seed")
    context = run("relation-context")["identity_catalogue"]
    assert set(context["parts"]) == set(PARTS)
    person = run("save", object={"kind": "person", "title": "Specific musician"})["item"]
    work = run(
        "save",
        object={"kind": "work", "title": "Synthetic composition", "music_classes": ["classical"]},
    )["item"]
    recording = run(
        "save",
        object={
            "kind": "recording",
            "title": "Synthetic performance",
            "relations": [
                {"object_id": work["id"], "role": "work"},
                {"object_id": person["id"], "role": "performer", "performance_role": "羽管键琴"},
            ],
        },
    )["item"]
    assert (
        next(r for r in recording["relations"] if r["role"] == "performer")["performance_role"]
        == "羽管键琴"
    )
    detail = run("detail", id=person["id"])["item"]
    names = {identity["name"] for identity in detail["identities"]}
    assert "羽管键琴" in names and "演奏家" in names
    tree = run("taxonomy")["items"]
    violin = next(
        node for node in tree if node["namespace"] == "identity" and node["name"] == "小提琴"
    )
    root = next(
        node for node in tree if node["namespace"] == "identity" and node["name"] == "音乐家"
    )
    with db_engine.begin() as c:
        c.execute(
            text("UPDATE music_taxonomy SET parent_id=:parent WHERE id=:id"),
            {"parent": root["id"], "id": violin["id"]},
        )
    run("taxonomy-seed")
    upgraded = next(node for node in run("taxonomy")["items"] if node["id"] == violin["id"])
    assert upgraded["parent_id"] != root["id"]
    assert upgraded["revision"] == violin["revision"] + 1
    run("taxonomy-seed")
    assert (
        next(node for node in run("taxonomy")["items"] if node["id"] == violin["id"])["revision"]
        == upgraded["revision"]
    )
    family = next(
        node for node in tree if node["namespace"] == "identity" and node["name"] == "键盘乐器"
    )
    filtered = run("list", kind="person", identity_id=family["id"])
    assert person["id"] in {item["id"] for item in filtered["items"]}
    vocalist = run("save", object={"kind": "person", "title": "Specific vocalist"})["item"]
    run(
        "save",
        object={
            "kind": "recording",
            "title": "Synthetic vocal performance",
            "relations": [
                {"object_id": work["id"], "role": "work"},
                {"object_id": vocalist["id"], "role": "singer", "performance_role": "女高音"},
            ],
        },
    )
    assert "女高音" in {
        node["name"] for node in run("detail", id=vocalist["id"])["item"]["identities"]
    }
    with pytest.raises(ValueError, match="music.invalid_relation"):
        run(
            "save",
            object={
                "kind": "recording",
                "title": "Wrong role",
                "relations": [
                    {"object_id": work["id"], "role": "work"},
                    {"object_id": person["id"], "role": "singer", "performance_role": "钢琴"},
                ],
            },
        )
    with pytest.raises(ValidationError, match="music.invalid_performance_role"):
        RelationInput(object_id=person["id"], role="performer", performance_role="invented")


@pytest.mark.integration
def test_multiple_ensemble_identities(db_engine: Engine, tmp_path: Path) -> None:
    s = service(db_engine, tmp_path)

    def run(action: str, **data: Any) -> dict[str, Any]:
        return s.run(action, MusicRequest.model_validate(data))

    run("taxonomy-seed")
    group = run("save", object={"kind": "group", "title": "Synthetic flexible ensemble"})["item"]
    work = run(
        "save", object={"kind": "work", "title": "Synthetic work", "music_classes": ["classical"]}
    )["item"]
    recordings = []
    for part in ["室内乐团", "弦乐四重奏"]:
        recording = run(
            "save",
            object={
                "kind": "recording",
                "title": part + " synthetic",
                "relations": [
                    {"object_id": work["id"], "role": "work"},
                    {"object_id": group["id"], "role": "ensemble", "performance_role": part},
                ],
            },
        )["item"]
        recordings.append(recording)
    detail = run("detail", id=group["id"])["item"]
    assert {"室内乐团", "弦乐四重奏"} <= {node["name"] for node in detail["identities"]}
    selected = next(node for node in detail["identities"] if node["name"] == "弦乐四重奏")
    assert group["id"] in {
        item["id"] for item in run("list", kind="group", identity_id=selected["id"])["items"]
    }
    recording = recordings[1]
    run(
        "save",
        id=recording["id"],
        revision=recording["revision"],
        object={
            "kind": "recording",
            "title": recording["title"],
            "relations": [{"object_id": work["id"], "role": "work"}],
        },
    )
    labels = {node["name"] for node in run("detail", id=group["id"])["item"]["identities"]}
    assert "室内乐团" in labels and "弦乐四重奏" not in labels
