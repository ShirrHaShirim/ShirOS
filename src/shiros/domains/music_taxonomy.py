"""Scoped music-only genre and private-tag trees; never infer from memory labels."""

import json
from typing import Any
from uuid import UUID, uuid4, uuid5

from sqlalchemy import Connection, text

from shiros.identity import audit
from shiros.tags import _name

PRESETS = {
    "古典": [
        "早期音乐",
        "巴洛克",
        "古典主义",
        "浪漫主义",
        "印象主义",
        "现代主义",
        "当代古典",
        "极简主义",
        "电声音乐",
    ],
    "摇滚": [
        "朋克",
        "后朋克",
        "前卫摇滚",
        "艺术摇滚",
        "实验摇滚",
        "迷幻摇滚",
        "硬摇滚",
        "布鲁斯摇滚",
        "民谣摇滚",
        "爵士摇滚",
        "另类摇滚",
        "独立摇滚",
        "后摇滚",
        "车库摇滚",
        "噪音摇滚",
        "Shoegaze",
        "金属",
    ],
    "爵士": [
        "比博普",
        "自由爵士",
        "摇摆乐",
        "冷爵士",
        "硬博普",
        "后博普",
        "模态爵士",
        "融合爵士",
        "拉丁爵士",
        "灵魂爵士",
    ],
    "电子": [
        "Ambient",
        "Techno",
        "House",
        "IDM",
        "Downtempo",
        "Trip-Hop",
        "Drum and Bass",
        "Electro",
        "Industrial",
        "Drone",
    ],
    "流行": [
        "艺术流行",
        "合成器流行",
        "独立流行",
        "梦幻流行",
        "巴洛克流行",
        "流行摇滚",
        "Disco",
        "Soul",
        "R&B",
        "Funk",
    ],
    "Hip-Hop": [
        "East Coast Hip-Hop",
        "West Coast Hip-Hop",
        "Alternative Hip-Hop",
        "Jazz Rap",
        "Conscious Hip-Hop",
        "Experimental Hip-Hop",
        "Trap",
    ],
    "世界音乐": [
        "传统民谣",
        "Flamenco",
        "Tango",
        "Bossa Nova",
        "Afrobeat",
        "Reggae",
        "Dub",
        "印度古典音乐",
    ],
}


def nodes(c: Connection, scope: UUID) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in c.execute(
            text(
                "SELECT * FROM music_taxonomy WHERE scope_id=:s AND NOT deleted "
                "ORDER BY namespace,lower(name),id"
            ),
            {"s": scope},
        ).mappings()
    ]


def get_node(c: Connection, scope: UUID, identifier: UUID) -> dict[str, Any]:
    row = (
        c.execute(
            text("SELECT * FROM music_taxonomy WHERE scope_id=:s AND id=:id AND NOT deleted"),
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
    derived_only: bool = False,
    music_class: str = "",
) -> dict[str, Any]:
    name = _name(name)
    from shiros.domains.music_classes import CLASSES
    from shiros.domains.music_profiles import DERIVED_NAMES

    if namespace == "genre" and parent is None and not music_class:
        music_class = next((key for key, label in CLASSES.items() if label == name), "")

    if namespace == "identity" and name in DERIVED_NAMES and not derived_only:
        raise ValueError("music.identity_from_relations")
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
    if old and old["deleted"]:
        raise ValueError("music.taxonomy_not_found")
    if old and old["derived_only"] and not derived_only:
        raise ValueError("music.identity_from_relations")
    if namespace == "genre" and parent:
        from shiros.domains.music_classes import genre_class

        required = genre_class(c, scope, parent)
        own_class = old["music_class"] if old else music_class
        if required and own_class and required != own_class:
            raise ValueError("music.class_genre_mismatch")
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
            "SELECT 1 FROM music_taxonomy WHERE scope_id=:s AND namespace=:ns AND NOT deleted "
            "AND lower(name)=lower(:n) "
            "AND parent_id IS NOT DISTINCT FROM CAST(:p AS uuid) AND id<>:id"
        ),
        {"s": scope, "ns": namespace, "n": name, "p": parent, "id": identifier},
    )
    if duplicate:
        raise ValueError("music.taxonomy_duplicate")
    c.execute(
        text(
            "INSERT INTO music_taxonomy(id,scope_id,namespace,name,parent_id,created_by,"
            "derived_only,music_class) VALUES (:id,:s,:ns,:n,:p,:a,:derived,:class) "
            "ON CONFLICT(id) DO UPDATE SET "
            "name=:n,parent_id=:p,revision=music_taxonomy.revision+1"
        ),
        {
            "id": identifier,
            "s": scope,
            "ns": namespace,
            "n": name,
            "p": parent,
            "a": actor,
            "derived": derived_only,
            "class": music_class,
        },
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


def delete_node(
    c: Connection, actor: UUID, scope: UUID, identifier: UUID, revision: int | None
) -> dict[str, Any]:
    node = get_node(c, scope, identifier)
    if node["derived_only"]:
        raise ValueError("music.identity_from_relations")
    if node["revision"] != revision:
        raise ValueError("music.revision_conflict")
    if c.scalar(
        text(
            "SELECT 1 FROM music_taxonomy WHERE parent_id=:id "
            "AND scope_id=:s AND NOT deleted LIMIT 1"
        ),
        {"id": identifier, "s": scope},
    ):
        raise ValueError("music.taxonomy_has_children")
    c.execute(
        text(
            "UPDATE music_taxonomy SET deleted=true,revision=revision+1 "
            "WHERE id=:id AND scope_id=:s"
        ),
        {"id": identifier, "s": scope},
    )
    node.update(deleted=True, revision=node["revision"] + 1)
    c.execute(
        text(
            "INSERT INTO music_taxonomy_history "
            "(id,node_id,scope_id,revision,snapshot,created_by) "
            "VALUES (:id,:node,:s,:rev,CAST(:data AS jsonb),:actor)"
        ),
        {
            "id": uuid4(),
            "node": identifier,
            "s": scope,
            "rev": node["revision"],
            "data": json.dumps(node, default=str),
            "actor": actor,
        },
    )
    audit(c, actor, scope, identifier, "edit")
    return {"id": identifier, "deleted": True, "permanent": False}


def seed(c: Connection, actor: UUID, scope: UUID) -> None:
    from shiros.domains.music_classes import CLASSES
    from shiros.domains.music_identity_catalogue import GROUPS
    from shiros.domains.music_profiles import (
        DERIVED_NAMES,
        IDENTITIES,
        PERFORMANCE_ROLES,
        identity_id,
    )

    hierarchy: list[tuple[str, str | None]] = [(name, None) for name in IDENTITIES]
    hierarchy += [(child, name) for name, children in IDENTITIES.items() for child in children]
    hierarchy += [
        (group, "团体" if group == "团体编制" else "音乐家" if group == "声部分工" else "演奏家")
        for group in GROUPS
    ]
    hierarchy += [(part, group) for group, parts in GROUPS.items() for part in parts]
    for label, above in hierarchy:
        identifier = identity_id(scope, label)
        parent = identity_id(scope, above) if above else None
        existing = c.execute(
            text("SELECT parent_id,deleted,revision FROM music_taxonomy WHERE id=:id"),
            {"id": identifier},
        ).first()
        if not existing:
            save_node(
                c,
                actor,
                scope,
                "identity",
                label,
                parent,
                identifier,
                derived_only=label in DERIVED_NAMES,
            )
        elif (
            label in PERFORMANCE_ROLES
            and not existing.deleted
            and existing.parent_id == identity_id(scope, "音乐家")
        ):
            # Move only the previous factory default; preserve owner-customized hierarchies.
            save_node(
                c,
                actor,
                scope,
                "identity",
                label,
                parent,
                identifier,
                revision=existing.revision,
                derived_only=True,
            )
    bootstrapped = c.scalar(
        text("SELECT 1 FROM music_taxonomy_bootstrap WHERE scope_id=:s"), {"s": scope}
    )
    if bootstrapped:
        for key, name in CLASSES.items():
            c.execute(
                text(
                    "UPDATE music_taxonomy SET music_class=:class WHERE scope_id=:s "
                    "AND namespace='genre' AND parent_id IS NULL AND name=:name "
                    "AND music_class=''"
                ),
                {"class": key, "s": scope, "name": name},
            )
        # Supplement missing presets in established scopes without renaming,
        # moving or resurrecting classifications the owner has edited/deleted.
    for name, children in PRESETS.items():
        parent = c.scalar(
            text(
                "SELECT id FROM music_taxonomy WHERE scope_id=:s "
                "AND namespace='genre' AND parent_id IS NULL AND name=:n"
            ),
            {"s": scope, "n": name},
        )
        seeded_parent = (
            c.execute(
                text("SELECT id,deleted FROM music_taxonomy WHERE id=:id AND scope_id=:s"),
                {"id": uuid5(scope, "music-genre:" + name), "s": scope},
            )
            .mappings()
            .first()
        )
        if seeded_parent:
            if seeded_parent["deleted"]:
                continue
            parent = seeded_parent["id"]
        if not parent:
            if bootstrapped:
                continue
            parent = uuid5(scope, "music-genre:" + name)
            category = next((key for key, label in CLASSES.items() if label == name), "")
            save_node(c, actor, scope, "genre", name, None, parent, music_class=category)
        if c.scalar(text("SELECT deleted FROM music_taxonomy WHERE id=:id"), {"id": parent}):
            continue
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
                if c.scalar(text("SELECT 1 FROM music_taxonomy WHERE id=:id"), {"id": identifier}):
                    continue
                save_node(c, actor, scope, "genre", child, parent, identifier)
    c.execute(
        text("INSERT INTO music_taxonomy_bootstrap VALUES (:s) ON CONFLICT DO NOTHING"),
        {"s": scope},
    )


def assign(c: Connection, scope: UUID, identifier: UUID, namespace: str, ids: list[UUID]) -> None:
    for node_id in set(ids):
        node = get_node(c, scope, node_id)
        if node["namespace"] != namespace:
            raise ValueError("music.taxonomy_namespace")
        if node["derived_only"]:
            raise ValueError("music.identity_from_relations")
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
                "WHERE l.object_id=:id AND l.scope_id=:s AND l.namespace=:ns "
                "AND NOT n.deleted ORDER BY n.name"
            ),
            {"id": identifier, "s": scope, "ns": namespace},
        ).mappings()
    ]
