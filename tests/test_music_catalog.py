"""Catalogue facts, scope isolation, manual precedence and audited legacy compatibility."""

from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from test_music import service
from test_review_workflow import setup

from shiros.apps.workbench import create_workbench
from shiros.domains.music import MusicRequest, MusicService
from shiros.domains.music_catalog import natural_key
from shiros.domains.music_catalog_migration import apply_plan
from shiros.memory_gateway import MemoryGateway


def run(s: MusicService, action: str, **fields: Any) -> dict[str, Any]:
    return s.run(action, MusicRequest.model_validate(fields))


def work(
    s: MusicService, title: str, composers: Sequence[dict[str, Any]] = (), **fields: Any
) -> dict[str, Any]:
    result = run(
        s,
        "save",
        object={
            "kind": "work",
            "title": title,
            "music_classes": ["classical"],
            "relations": [{"object_id": p["id"], "role": "composer"} for p in composers],
            **fields,
        },
    )["item"]
    return cast(dict[str, Any], result)


def test_natural_catalogue_order() -> None:
    assert sorted(["No. 10", "No. 2", "No. 1"], key=natural_key) == ["No. 1", "No. 2", "No. 10"]


@pytest.mark.integration
def test_personal_cover_groups_nest_without_duplicating_works(
    db_engine: Engine, tmp_path: Path
) -> None:
    s = service(db_engine, tmp_path)
    root = work(s, "Composer: Chamber Music", work_form="folder")
    subgroup = work(
        s,
        "Composer: Early String Quartets",
        work_form="folder",
        relations=[{"object_id": root["id"], "role": "parent_work"}],
    )
    leaf = work(
        s,
        "Synthetic Quartet No. 1",
        relations=[{"object_id": subgroup["id"], "role": "parent_work"}],
    )
    loose = work(s, "New standalone work")
    assert [item["id"] for item in run(s, "list", kind="work", root_works=True)["items"]] == [
        root["id"],
        loose["id"],
    ]
    assert (
        run(s, "list", kind="work", root_works=True, query="Synthetic Quartet")["items"][0]["id"]
        == root["id"]
    )
    assert (
        run(s, "list", kind="work", parent_work_id=root["id"], sort="work_order")["items"][0][
            "work_form"
        ]
        == "folder"
    )
    assert run(s, "detail", id=subgroup["id"])["item"]["child_works"][0]["id"] == leaf["id"]
    with pytest.raises(ValueError, match="music.work_cycle"):
        run(
            s,
            "save",
            id=root["id"],
            revision=root["revision"],
            object={
                "kind": "work",
                "title": root["title"],
                "work_form": "folder",
                "relations": [{"object_id": leaf["id"], "role": "parent_work"}],
            },
        )


@pytest.mark.integration
def test_classification_uses_explicit_genre_and_manual_survives_old_client(
    db_engine: Engine, tmp_path: Path
) -> None:
    s = service(db_engine, tmp_path)
    root = run(s, "taxonomy-save", namespace="genre", name="古典")["node"]
    sym = run(s, "taxonomy-save", namespace="genre", name="交响曲", parent_id=root["id"])["node"]
    song = run(s, "taxonomy-save", namespace="genre", name="艺术歌曲", parent_id=root["id"])["node"]
    misleading = work(s, "Not a Symphony: title is not evidence")
    assert misleading["catalog_assignments"][0]["category_code"] == "unclassified"
    actual = work(s, "Synthetic No. 2", genre_ids=[sym["id"]])
    assert actual["catalog_assignments"][0]["category_code"] == "orchestral.symphonies"
    ambiguous = work(s, "Synthetic multi-genre", genre_ids=[sym["id"], song["id"]])
    assert ambiguous["catalog_assignments"][0]["category_code"] == "unclassified"
    assert set(ambiguous["catalog_candidates"]) == {"orchestral.symphonies", "vocal.art-songs"}
    manual = work(
        s,
        "Manual selection",
        genre_ids=[sym["id"]],
        catalog_primary="concertante.other",
        catalog_auxiliary=["orchestral.other"],
        catalog_source="Synthetic user correction",
    )
    updated = run(
        s,
        "save",
        id=manual["id"],
        revision=manual["revision"],
        object={
            "kind": "work",
            "title": manual["title"],
            "music_classes": ["classical"],
            "genre_ids": [song["id"]],
        },
    )["item"]
    assert updated["catalog_assignments"] == manual["catalog_assignments"]
    assert updated["catalog_assignments"][0]["origin"] == "manual"
    with pytest.raises(ValueError, match="catalog_invalid_category"):
        work(s, "Bad category", catalog_primary="missing")
    with pytest.raises(ValueError, match="catalog_classical_only"):
        run(
            s,
            "save",
            object={
                "kind": "work",
                "title": "Rock",
                "music_classes": ["rock"],
                "catalog_primary": "solo.piano",
            },
        )


@pytest.mark.integration
def test_shared_tree_multi_author_series_search_counts_and_scope(
    db_engine: Engine, tmp_path: Path
) -> None:
    s = service(db_engine, tmp_path)
    authors = [
        run(s, "save", object={"kind": "person", "title": title})["item"]
        for title in ["Synthetic Composer A", "Synthetic Composer B"]
    ]
    series = work(
        s,
        "Synthetic historical group",
        authors,
        work_form="collection",
        catalog_primary="solo.violin",
    )
    children = []
    for number in [10, 2, 1]:
        children.append(
            work(
                s,
                f"Synthetic No. {number}",
                authors,
                catalog_primary="solo.violin",
                relations=[
                    *[{"object_id": p["id"], "role": "composer"} for p in authors],
                    {"object_id": series["id"], "role": "parent_work"},
                ],
            )
        )
    root = run(s, "catalog-tree")
    assert len(root["composers"]) == 2
    for composer in root["composers"]:
        assert len(composer["nodes"]) == 2
        assert composer["nodes"][1]["total"] == 3
        assert composer["nodes"][1]["collections"] == 1
    grouped = run(
        s,
        "list",
        kind="work",
        root_works=True,
        composer_id=authors[0]["id"],
        catalog_category="solo.violin",
        query="No. 2",
    )
    assert [row["id"] for row in grouped["items"]] == [series["id"]]
    direct = run(
        s,
        "list",
        kind="work",
        composer_id=authors[0]["id"],
        catalog_category="solo.violin",
        sort="catalog_order",
    )
    assert [row["title"] for row in direct["items"] if row["id"] != series["id"]] == [
        "Synthetic No. 1",
        "Synthetic No. 2",
        "Synthetic No. 10",
    ]
    other = service(db_engine, tmp_path / "other")
    assert run(other, "catalog-tree")["composers"] == []
    with pytest.raises(ValueError, match="music.not_found"):
        run(other, "catalog-tree", composer_id=authors[0]["id"])
    with pytest.raises(ValueError, match="invalid_filter"):
        run(s, "list", kind="release", catalog_category="solo")
    export = run(s, "export")["tables"]
    assert len(export["music_catalog_assignments"]) == 4
    assert export["music_catalog_history"]


@pytest.mark.integration
def test_retirement_keeps_ids_references_history_and_unknown_audio(
    db_engine: Engine, tmp_path: Path
) -> None:
    s = service(db_engine, tmp_path)
    author = run(s, "save", object={"kind": "person", "title": "Synthetic composer"})["item"]
    group = work(s, "Synthetic virtual aggregate", [author], work_form="collection")
    child = work(
        s,
        "Synthetic independent work",
        [author],
        relations=[
            {"object_id": author["id"], "role": "composer"},
            {"object_id": group["id"], "role": "parent_work"},
        ],
    )
    audio = run(
        s,
        "save",
        object={
            "kind": "recording",
            "title": "Synthetic unverified audio",
            "relations": [{"object_id": group["id"], "role": "work"}],
        },
    )["item"]
    plan = {
        "source": "Synthetic audited plan",
        "assign": {
            str(child["id"]): {"code": "orchestral.symphonies", "revision": child["revision"]}
        },
        "retire": [
            {
                "id": str(group["id"]),
                "revision": group["revision"],
                "composer_id": str(author["id"]),
                "category": "orchestral.symphonies",
                "children": [str(child["id"])],
            }
        ],
        "retain": [],
        "images": [],
    }
    with db_engine.begin() as c:
        result = apply_plan(c, s.actor, s.scope, plan)
        assert len(result["pending"]) == 1
        assert apply_plan(c, s.actor, s.scope, plan)["idempotent"]
        assert c.scalar(
            text("SELECT archived FROM music_object_records WHERE id=:id"), {"id": group["id"]}
        )
        assert (
            c.scalar(
                text("SELECT count(*) FROM music_catalog_redirects WHERE scope_id=:s"),
                {"s": s.scope},
            )
            == 1
        )
    old = run(s, "detail", id=group["id"])["item"]
    assert old["id"] == group["id"] and old["catalog_redirect"]["composer_id"] == author["id"]
    assert old["history"] and old["image_id"] == group["image_id"]
    updated = run(s, "detail", id=audio["id"])["item"]
    assert updated["works"] == []  # Never expand aggregate into supposed performances.
    assert updated["legacy_catalog_relations"][0]["object_id"] == group["id"]
    assert [x["id"] for x in run(s, "list", kind="work", root_works=True)["items"]] == [child["id"]]
    assert not any(
        r["role"] == "parent_work" for r in run(s, "detail", id=child["id"])["item"]["relations"]
    )
    with pytest.raises(ValueError, match="music.not_found"):
        run(
            s,
            "save",
            id=group["id"],
            revision=old["revision"],
            object={"kind": "work", "title": old["title"]},
        )


@pytest.mark.integration
def test_reader_catalogue_access_cannot_write(db_engine: Engine, tmp_path: Path) -> None:
    s = service(db_engine, tmp_path, role="reader")
    assert run(s, "catalog-categories")["items"]
    assert run(s, "catalog-tree")["composers"] == []
    with pytest.raises(PermissionError):
        work(s, "Denied", catalog_primary="solo.piano")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_catalogue_browser_api_mcp_and_candidate_scope(
    db_engine: Engine, tmp_path: Path
) -> None:
    workflow, scope = setup(db_engine, tmp_path)
    with db_engine.connect() as c:
        actor = workflow.identity.current(c)
    s = MusicService(db_engine, actor, scope)
    root = run(s, "taxonomy-save", namespace="genre", name="古典")["node"]
    genre = run(s, "taxonomy-save", namespace="genre", name="交响曲", parent_id=root["id"])["node"]
    app = create_workbench(db_engine, workflow.identity, scope, token="synthetic-token")
    with TestClient(app, base_url="http://127.0.0.1:8001") as client:
        assert client.post("/ui-api/music/catalog-tree", json={}).status_code == 403
        client.headers["X-Shiros-Token"] = "synthetic-token"
        assert (
            client.post("/ui-api/music/catalog-tree", json={"scope_id": str(scope)}).status_code
            == 422
        )
        response = client.post(
            "/ui-api/music/catalog-candidates", json={"catalog_genre_ids": [str(genre["id"])]}
        )
        assert response.status_code == 200
        assert response.json()["codes"] == ["orchestral.symphonies"]
        value = client.post(
            "/ui-api/music/save",
            json={
                "object": {
                    "kind": "work",
                    "title": "Synthetic API work",
                    "music_classes": ["classical"],
                    "genre_ids": [str(genre["id"])],
                }
            },
        ).json()["item"]
        assert value["catalog_assignments"][0]["category_code"] == "orchestral.symphonies"
        assert (
            client.post("/ui-api/music/catalog-tree", json={}).json()["composers"][0]["id"] is None
        )
    gateway = MemoryGateway(db_engine, actor, scope, "synthetic-catalogue-client")
    result = await gateway.music_query("catalog-tree", {})
    assert result["content_is_untrusted_data"]
    assert result["composers"][0]["nodes"][1]["total"] == 1
    detail = await gateway.music_query("detail", {"id": str(value["id"])})
    assert detail["item"]["catalog_assignments"][0]["category_code"] == "orchestral.symphonies"
