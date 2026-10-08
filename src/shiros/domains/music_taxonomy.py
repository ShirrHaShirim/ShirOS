"""Scoped music-only genre and private-tag trees; never infer from memory labels."""

import json
from typing import Any
from uuid import UUID, uuid4, uuid5

from sqlalchemy import Connection, text

from shiros.identity import audit
from shiros.tags import _name

PRESETS = {
    "古典": ["早期音乐", "当代古典"],
    "摇滚": ["朋克", "后朋克", "前卫摇滚"],
    "爵士": ["比博普", "自由爵士"],
    "电子": ["Ambient", "Techno"],
    "流行": [],
    "Hip-Hop": [],
    "世界音乐": [],
}


def nodes(c: Connection, scope: UUID) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in c.execute(
            text(
                "SELECT * FROM music_taxonomy WHERE scope_id=:s ORDER BY namespace,lower(name),id"
            ),
            {"s": scope},
        ).mappings()
    ]


def get_node(c: Connection, scope: UUID, identifier: UUID) -> dict[str, Any]:
    row = (
        c.execute(
            text("SELECT * FROM music_taxonomy WHERE scope_id=:s AND id=:id"),
            {"s": scope, "id": identifier},
        )
        .mappings()
        .first()
    )
    if not row:
        raise ValueError("music.taxonomy_not_found")
    return dict(row)


def save_node(
    c: Connection,
    actor: UUID,
    scope: UUID,
    namespace: str,
    name: str,
    parent: UUID | None,
    identifier: UUID | None = None,
    revision: int | None = None,
) -> dict[str, Any]:
    name = _name(name)
    identifier = identifier or uuid4()
    old = (
        c.execute(
            text("SELECT * FROM music_taxonomy WHERE id=:id AND scope_id=:s"),
            {"s": scope, "id": identifier},
        )
        .mappings()
        .first()
    )
    if old is None and c.scalar(
        text("SELECT 1 FROM music_taxonomy WHERE id=:id"), {"id": identifier}
    ):
        raise ValueError("music.taxonomy_not_found")
    if old and (old["revision"] != revision or old["namespace"] != namespace):
        raise ValueError("music.revision_conflict")
    visited = {identifier}
    current = parent
    while current:
        if current in visited or len(visited) > 64:
            raise ValueError("music.taxonomy_cycle")
        visited.add(current)
        node = get_node(c, scope, current)
        if node["namespace"] != namespace:
            raise ValueError("music.taxonomy_namespace")
        current = node["parent_id"]
    duplicate = c.scalar(
        text(
            "SELECT 1 FROM music_taxonomy WHERE scope_id=:s AND namespace=:ns "
            "AND lower(name)=lower(:n) "
            "AND parent_id IS NOT DISTINCT FROM CAST(:p AS uuid) AND id<>:id"
        ),
        {"s": scope, "ns": namespace, "n": name, "p": parent, "id": identifier},
    )
    if duplicate:
        raise ValueError("music.taxonomy_duplicate")
    c.execute(
        text(
            "INSERT INTO music_taxonomy(id,scope_id,namespace,name,parent_id,created_by) "
            "VALUES (:id,:s,:ns,:n,:p,:a) ON CONFLICT(id) DO UPDATE SET "
            "name=:n,parent_id=:p,revision=music_taxonomy.revision+1"
        ),
        {"id": identifier, "s": scope, "ns": namespace, "n": name, "p": parent, "a": actor},
    )
    node = get_node(c, scope, identifier)
    c.execute(
        text(
            "INSERT INTO music_taxonomy_history(id,node_id,scope_id,revision,snapshot,created_by) "
            "VALUES (:id,:node,:s,:r,CAST(:snapshot AS jsonb),:a)"
        ),
        {
            "id": uuid4(),
            "node": identifier,
            "s": scope,
            "r": node["revision"],
            "snapshot": json.dumps(node, default=str),
            "a": actor,
        },
    )
    audit(c, actor, scope, identifier, "edit")
    return node


def seed(c: Connection, actor: UUID, scope: UUID) -> None:
    if c.scalar(text("SELECT 1 FROM music_taxonomy_bootstrap WHERE scope_id=:s"), {"s": scope}):
        return
    for name, children in PRESETS.items():
        parent = c.scalar(
            text(
                "SELECT id FROM music_taxonomy WHERE scope_id=:s "
                "AND namespace='genre' AND parent_id IS NULL AND name=:n"
            ),
            {"s": scope, "n": name},
        )
        if not parent:
            parent = uuid5(scope, "music-genre:" + name)
            save_node(c, actor, scope, "genre", name, None, parent)
        for child in children:
            exists = c.scalar(
                text(
                    "SELECT 1 FROM music_taxonomy WHERE scope_id=:s "
                    "AND namespace='genre' AND parent_id=:p AND name=:n"
                ),
                {"s": scope, "p": parent, "n": child},
            )
            if not exists:
                identifier = uuid5(scope, "music-genre:" + name + "/" + child)
                save_node(c, actor, scope, "genre", child, parent, identifier)
    c.execute(text("INSERT INTO music_taxonomy_bootstrap VALUES (:s)"), {"s": scope})


def assign(c: Connection, scope: UUID, identifier: UUID, namespace: str, ids: list[UUID]) -> None:
    for node_id in set(ids):
        if get_node(c, scope, node_id)["namespace"] != namespace:
            raise ValueError("music.taxonomy_namespace")
    c.execute(
        text("DELETE FROM music_taxonomy_links WHERE object_id=:id AND namespace=:ns"),
        {"id": identifier, "ns": namespace},
    )
    for node_id in set(ids):
        c.execute(
            text("INSERT INTO music_taxonomy_links VALUES (:id,:s,:ns,:node)"),
            {"id": identifier, "s": scope, "ns": namespace, "node": node_id},
        )


def assigned(c: Connection, scope: UUID, identifier: UUID, namespace: str) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in c.execute(
            text(
                "SELECT n.* FROM music_taxonomy n JOIN music_taxonomy_links l ON l.node_id=n.id "
                "WHERE l.object_id=:id AND l.scope_id=:s AND l.namespace=:ns ORDER BY n.name"
            ),
            {"id": identifier, "s": scope, "ns": namespace},
        ).mappings()
    ]
