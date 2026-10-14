"""Deleting a classification hides it while preserving object and link history."""

from pathlib import Path

import pytest
from sqlalchemy import Engine, text
from test_music import release_fixture, service
from test_music_v070 import run


@pytest.mark.integration
def test_music_tag_soft_delete(db_engine: Engine, tmp_path: Path) -> None:
    s = service(db_engine, tmp_path)
    root = run(s, "taxonomy-save", name="Root")["node"]
    child = run(s, "taxonomy-save", name="Child", parent_id=root["id"])["node"]
    obj = run(
        s, "save", object=release_fixture(s, title="Synthetic release", tag_ids=[child["id"]])
    )["item"]
    with pytest.raises(ValueError, match="music.taxonomy_has_children"):
        run(s, "taxonomy-delete", id=root["id"], revision=root["revision"])
    with pytest.raises(ValueError, match="music.revision_conflict"):
        run(s, "taxonomy-delete", id=child["id"], revision=9)
    assert run(s, "taxonomy-delete", id=child["id"], revision=1)["deleted"]
    assert len(run(s, "taxonomy")["items"]) == 1
    assert run(s, "detail", id=obj["id"])["item"]["tags"] == []
    assert run(s, "list", untagged=True)["total"] == 1
    assert run(s, "detail", id=obj["id"])["item"]["revision"] == obj["revision"]
    with db_engine.connect() as c:
        assert (
            c.scalar(
                text("SELECT count(*) FROM music_taxonomy_links WHERE node_id=:id"),
                {"id": child["id"]},
            )
            == 1
        )
        assert (
            c.scalar(
                text("SELECT count(*) FROM music_taxonomy_history WHERE node_id=:id"),
                {"id": child["id"]},
            )
            == 2
        )
    assert run(s, "taxonomy-save", name="Child", parent_id=root["id"])["node"]["id"] != child["id"]
