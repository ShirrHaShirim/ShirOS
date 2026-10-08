"""Synthetic tag hierarchy, permissions and visible date library coverage."""

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine
from test_candidate_submission import actor, workflow

from shiros.candidate_submission import submit
from shiros.tags import (
    create_tag,
    delete_tag,
    library,
    list_tags,
    memory_tags,
    remove_memory_tags,
    set_memory_tags,
    update_tag,
)


@pytest.mark.integration
def test_tag_tree_privacy_and_permissions(db_engine: Engine) -> None:
    scope = uuid4()
    owner = actor(db_engine, scope, "owner")
    proposer = actor(db_engine, scope, "proposer")
    reader = actor(db_engine, scope, "reader")
    root = create_tag(db_engine, owner, scope, "Projects")
    child = create_tag(db_engine, proposer, scope, "Synthetic", UUID(root["id"]))
    assert len(list_tags(db_engine, reader, scope)) == 2
    assert create_tag(db_engine, owner, scope, "projects")["id"] == root["id"]
    with pytest.raises(ValueError, match="cycle"):
        update_tag(db_engine, owner, scope, UUID(root["id"]), "Projects", UUID(child["id"]))
    with pytest.raises(PermissionError):
        update_tag(db_engine, proposer, scope, UUID(child["id"]), "Changed")
    with pytest.raises(PermissionError):
        create_tag(db_engine, reader, scope, "Unauthorized")
    for name in ("password=synthetic", "person@example.com"):
        with pytest.raises(PermissionError):
            create_tag(db_engine, owner, scope, name)
    for name in ("", "x" * 65, "a\nb"):
        with pytest.raises(ValueError):
            create_tag(db_engine, owner, scope, name)
    foreign_scope = uuid4()
    foreign = create_tag(
        db_engine, actor(db_engine, foreign_scope, "owner"), foreign_scope, "Foreign"
    )
    with pytest.raises(ValueError, match="not_found"):
        create_tag(db_engine, owner, scope, "Invalid", UUID(foreign["id"]))
    result = update_tag(db_engine, owner, scope, UUID(child["id"]), "Renamed")
    assert result["name"] == "Renamed" and result["parent_id"] is None


@pytest.mark.integration
def test_library_multitag_visibility_and_pagination(db_engine: Engine, tmp_path: Path) -> None:
    scope = uuid4()
    owner = actor(db_engine, scope, "owner")
    proposer = actor(db_engine, scope, "proposer")
    review = workflow(db_engine, scope, tmp_path)
    first = submit(db_engine, proposer, scope, "First", "First synthetic tag note", uuid4())
    memory = review.approve(scope, UUID(first["candidate_id"]))
    second = submit(db_engine, proposer, scope, "Second", "Second synthetic tag note", uuid4())
    other = review.approve(scope, UUID(second["candidate_id"]))
    one = UUID(create_tag(db_engine, owner, scope, "One")["id"])
    two = UUID(create_tag(db_engine, proposer, scope, "Two")["id"])
    assert len(set_memory_tags(db_engine, owner, scope, memory.memory_id, [one])) == 1
    assert len(set_memory_tags(db_engine, proposer, scope, memory.memory_id, [two], True)) == 2
    assert len(set_memory_tags(db_engine, proposer, scope, memory.memory_id, [two], True)) == 2
    with pytest.raises(PermissionError):
        set_memory_tags(db_engine, proposer, scope, memory.memory_id, [])
    page = library(db_engine, owner, scope, limit=1)
    assert page["total"] == 2 and page["items"][0]["memory_id"] == str(other.memory_id)
    assert page["items"][0]["title"] == "Second"
    assert library(db_engine, owner, scope, untagged=True)["total"] == 1
    assert library(db_engine, owner, scope, untagged=True)["items"][0]["memory_id"] == str(
        other.memory_id
    )
    with pytest.raises(ValueError):
        library(db_engine, owner, scope, one, untagged=True)
    assert library(db_engine, owner, scope, offset=1, limit=1)["items"][0]["memory_id"] == str(
        memory.memory_id
    )
    assert library(db_engine, owner, scope, one)["total"] == 1
    parent = UUID(create_tag(db_engine, owner, scope, "Folder")["id"])
    update_tag(db_engine, owner, scope, one, "One", parent)
    update_tag(db_engine, owner, scope, two, "Two", parent)
    # Two descendant links still represent one memory, not duplicate rows.
    assert library(db_engine, owner, scope, parent)["total"] == 1
    assert len(library(db_engine, owner, scope, parent)["items"]) == 1
    assert len(memory_tags(db_engine, owner, scope, memory.memory_id)) == 2
    with pytest.raises(ValueError):
        set_memory_tags(db_engine, owner, scope, memory.memory_id, [uuid4()])
    assert len(memory_tags(db_engine, owner, scope, memory.memory_id)) == 2
    set_memory_tags(db_engine, owner, scope, memory.memory_id, [two])
    assert library(db_engine, owner, scope, one)["total"] == 0
    review.hide(scope, memory.memory_id, True)
    assert library(db_engine, owner, scope, two)["total"] == 0
    with pytest.raises(ValueError, match="not_found"):
        memory_tags(db_engine, owner, scope, memory.memory_id)
    with pytest.raises(ValueError, match="not_found"):
        set_memory_tags(db_engine, proposer, scope, memory.memory_id, [one], True)
    review.hide(scope, memory.memory_id, False)
    review.revoke(scope, UUID(first["source_id"]))
    assert library(db_engine, owner, scope)["total"] == 1
    with pytest.raises(PermissionError):
        library(db_engine, proposer, uuid4())


@pytest.mark.integration
def test_tag_deletion_detachment_and_recreation(db_engine: Engine, tmp_path: Path) -> None:
    scope = uuid4()
    owner = actor(db_engine, scope, "owner")
    proposer = actor(db_engine, scope, "proposer")
    editor = actor(db_engine, scope, "editor")
    review = workflow(db_engine, scope, tmp_path)
    parent = create_tag(db_engine, owner, scope, "Synthetic parent")
    child = create_tag(db_engine, owner, scope, "Synthetic child", UUID(parent["id"]))
    with pytest.raises(ValueError, match="has_children"):
        delete_tag(db_engine, owner, scope, UUID(parent["id"]))
    with pytest.raises(PermissionError):
        delete_tag(db_engine, proposer, scope, UUID(child["id"]))
    with pytest.raises(ValueError, match="not_found"):
        delete_tag(db_engine, editor, scope, uuid4())
    assert delete_tag(db_engine, editor, scope, UUID(child["id"]))["deleted"] is True
    assert delete_tag(db_engine, editor, scope, UUID(child["id"]))["deleted"] is True
    assert [UUID(item["id"]) for item in list_tags(db_engine, owner, scope)] == [UUID(parent["id"])]
    recreated = create_tag(db_engine, owner, scope, "Synthetic child", UUID(parent["id"]))
    assert recreated["id"] != child["id"]
    first = submit(db_engine, proposer, scope, "Tagged", "Tagged synthetic note", uuid4())
    memory = review.approve(scope, UUID(first["candidate_id"]))
    linked = set_memory_tags(db_engine, owner, scope, memory.memory_id, [UUID(recreated["id"])])
    assert len(linked) == 1
    delete_tag(db_engine, editor, scope, UUID(recreated["id"]))
    with pytest.raises(ValueError, match="not_found"):
        set_memory_tags(
            db_engine, owner, scope, memory.memory_id, [UUID(recreated["id"])], add_only=True
        )
    with pytest.raises(PermissionError):
        remove_memory_tags(db_engine, proposer, scope, memory.memory_id, [UUID(recreated["id"])])
    with pytest.raises(ValueError, match="not_found"):
        remove_memory_tags(db_engine, editor, scope, memory.memory_id, [uuid4()])
    assert (
        remove_memory_tags(db_engine, editor, scope, memory.memory_id, [UUID(recreated["id"])])
        == []
    )
    assert delete_tag(db_engine, owner, scope, UUID(parent["id"]))["deleted"] is True
    assert list_tags(db_engine, owner, scope) == []
