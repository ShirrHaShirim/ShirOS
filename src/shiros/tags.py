"""Scoped organization metadata; tags never promote draft text into facts."""

import unicodedata
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine, text

from shiros.adapters.database.records import lock_writes, row_data
from shiros.core.memory import Memory
from shiros.core.privacy import PrivacyInput, RuleBasedPrivacyPolicy
from shiros.identity import DatabasePermissions
from shiros.memory_metadata import details
from shiros.visibility import visible_memory


def _name(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).strip()
    if not 1 <= len(value) <= 64 or any(unicodedata.category(c).startswith("C") for c in value):
        raise ValueError("tag.invalid_name")
    decision = RuleBasedPrivacyPolicy().evaluate(PrivacyInput(text=value, reviewed=True))
    if not decision.persistence_allowed or decision.safe_text != value:
        raise PermissionError("privacy.unsafe_tag")
    return value


def _write(connection: Connection, actor: UUID, scope: UUID, additive: bool) -> None:
    lock_writes(connection)
    permissions = DatabasePermissions(connection)
    permissions.require(actor, scope, "read")
    if not permissions.has(actor, scope, "review") and not (
        additive and permissions.has(actor, scope, "propose")
    ):
        raise PermissionError("permission.denied")


def _tag(connection: Connection, scope: UUID, identifier: UUID) -> dict[str, Any]:
    row = (
        connection.execute(
            text("SELECT * FROM memory_tags WHERE scope_id=:s AND id=:id AND NOT deleted"),
            {"s": scope, "id": identifier},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise ValueError("tag.not_found")
    return {
        k: str(v) if isinstance(v, UUID) else v.isoformat() if hasattr(v, "isoformat") else v
        for k, v in row.items()
    }


def _audit(connection: Connection, actor: UUID, scope: UUID, subject: UUID, action: str) -> None:
    connection.execute(
        text(
            "INSERT INTO tag_audit(scope_id,actor_id,subject_id,action) VALUES (:s,:a,:id,:action)"
        ),
        {"s": scope, "a": actor, "id": subject, "action": action},
    )


def _visible(connection: Connection, scope: UUID, memory_id: UUID) -> None:
    found = connection.execute(
        text(
            "SELECT 1 FROM memories m WHERE m.scope_id=:s "
            "AND m.memory_id=:id AND NOT EXISTS "
            "(SELECT 1 FROM memories n WHERE n.supersedes_id=m.id) AND " + visible_memory()
        ),
        {"s": scope, "id": memory_id},
    ).first()
    if found is None:
        raise ValueError("memory.not_found")


def list_tags(engine: Engine, actor: UUID, scope: UUID) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        DatabasePermissions(connection).require(actor, scope, "read")
        ids: list[UUID] = list(
            connection.execute(
                text(
                    "SELECT id FROM memory_tags WHERE scope_id=:s AND NOT deleted "
                    "ORDER BY lower(name),id"
                ),
                {"s": scope},
            )
            .scalars()
            .all()
        )
        return [_tag(connection, scope, identifier) for identifier in ids]


def _validate_parent(
    connection: Connection, scope: UUID, parent: UUID | None, identifier: UUID
) -> None:
    visited = {str(identifier)}
    while parent is not None:
        if str(parent) in visited:
            raise ValueError("tag.cycle")
        visited.add(str(parent))
        if len(visited) > 64:
            raise ValueError("tag.depth_limit")
        row = _tag(connection, scope, parent)
        parent = UUID(row["parent_id"]) if row["parent_id"] else None


def _unique(
    connection: Connection, scope: UUID, name: str, parent: UUID | None, identifier: UUID
) -> None:
    if connection.execute(
        text(
            "SELECT 1 FROM memory_tags WHERE scope_id=:s AND NOT deleted "
            "AND parent_id IS NOT DISTINCT FROM CAST(:p AS uuid) "
            "AND lower(name)=lower(:name) AND id<>:id"
        ),
        {"s": scope, "p": parent, "name": name, "id": identifier},
    ).first():
        raise ValueError("tag.duplicate_name")


def create_tag(
    engine: Engine, actor: UUID, scope: UUID, name: str, parent_id: UUID | None = None
) -> dict[str, Any]:
    name, identifier = _name(name), uuid4()
    with engine.begin() as connection:
        _write(connection, actor, scope, True)
        _validate_parent(connection, scope, parent_id, identifier)
        existing = connection.scalar(
            text(
                "SELECT id FROM memory_tags WHERE scope_id=:s AND NOT deleted "
                "AND parent_id IS NOT DISTINCT FROM CAST(:p AS uuid) AND lower(name)=lower(:name)"
            ),
            {"s": scope, "p": parent_id, "name": name},
        )
        if existing is not None:
            return _tag(connection, scope, existing)
        connection.execute(
            text(
                "INSERT INTO memory_tags(id,scope_id,name,parent_id,created_by) "
                "VALUES (:id,:s,:name,:p,:a)"
            ),
            {"id": identifier, "s": scope, "name": name, "p": parent_id, "a": actor},
        )
        _audit(connection, actor, scope, identifier, "create")
        return _tag(connection, scope, identifier)


def update_tag(
    engine: Engine, actor: UUID, scope: UUID, id: UUID, name: str, parent_id: UUID | None = None
) -> dict[str, Any]:
    name = _name(name)
    with engine.begin() as connection:
        _write(connection, actor, scope, False)
        _tag(connection, scope, id)
        _validate_parent(connection, scope, parent_id, id)
        _unique(connection, scope, name, parent_id, id)
        connection.execute(
            text("UPDATE memory_tags SET name=:name,parent_id=:p WHERE id=:id AND scope_id=:s"),
            {"id": id, "s": scope, "name": name, "p": parent_id},
        )
        _audit(connection, actor, scope, id, "update")
        return _tag(connection, scope, id)


def _memory_tags(connection: Connection, scope: UUID, memory_id: UUID) -> list[dict[str, Any]]:
    ids: list[UUID] = list(
        connection.execute(
            text(
                "SELECT t.id FROM memory_tags t JOIN memory_tag_links l "
                "ON l.tag_id=t.id AND l.scope_id=t.scope_id "
                "WHERE l.scope_id=:s AND l.memory_id=:id AND NOT t.deleted "
                "ORDER BY lower(t.name),t.id"
            ),
            {"s": scope, "id": memory_id},
        )
        .scalars()
        .all()
    )
    return [_tag(connection, scope, identifier) for identifier in ids]


def memory_tags(engine: Engine, actor: UUID, scope: UUID, memory_id: UUID) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        DatabasePermissions(connection).require(actor, scope, "read")
        _visible(connection, scope, memory_id)
        return _memory_tags(connection, scope, memory_id)


def set_memory_tags(
    engine: Engine,
    actor: UUID,
    scope: UUID,
    memory_id: UUID,
    tag_ids: list[UUID],
    add_only: bool = False,
) -> list[dict[str, Any]]:
    ids = set(tag_ids)
    if len(ids) > 30:
        raise ValueError("tag.too_many")
    with engine.begin() as connection:
        _write(connection, actor, scope, add_only)
        _visible(connection, scope, memory_id)
        for identifier in ids:
            _tag(connection, scope, identifier)
        current = {UUID(t["id"]) for t in _memory_tags(connection, scope, memory_id)}
        if len(current | ids) > 30 and add_only:
            raise ValueError("tag.too_many")
        if not add_only:
            connection.execute(
                text("DELETE FROM memory_tag_links WHERE scope_id=:s AND memory_id=:id"),
                {"s": scope, "id": memory_id},
            )
        for identifier in ids:
            connection.execute(
                text(
                    "INSERT INTO memory_tag_links(scope_id,memory_id,tag_id,created_by) "
                    "VALUES (:s,:id,:tag,:a) ON CONFLICT DO NOTHING"
                ),
                {"s": scope, "id": memory_id, "tag": identifier, "a": actor},
            )
        _audit(connection, actor, scope, memory_id, "add" if add_only else "replace")
        return _memory_tags(connection, scope, memory_id)


def _manage(connection: Connection, actor: UUID, scope: UUID) -> None:
    lock_writes(connection)
    permissions = DatabasePermissions(connection)
    permissions.require(actor, scope, "read")
    if not (permissions.has(actor, scope, "tag_manage") or permissions.has(actor, scope, "admin")):
        raise PermissionError("permission.denied")


def delete_tag(engine: Engine, actor: UUID, scope: UUID, tag_id: UUID) -> dict[str, Any]:
    with engine.begin() as connection:
        _manage(connection, actor, scope)
        deleted = connection.scalar(
            text("SELECT deleted FROM memory_tags WHERE id=:id AND scope_id=:s"),
            {"id": tag_id, "s": scope},
        )
        if deleted is None:
            raise ValueError("tag.not_found")
        if not deleted:
            has_children = connection.scalar(
                text(
                    "SELECT 1 FROM memory_tags WHERE parent_id=:id AND scope_id=:s "
                    "AND NOT deleted LIMIT 1"
                ),
                {"id": tag_id, "s": scope},
            )
            if has_children:
                raise ValueError("tag.has_children")
            connection.execute(
                text("UPDATE memory_tags SET deleted=true WHERE id=:id AND scope_id=:s"),
                {"id": tag_id, "s": scope},
            )
            _audit(connection, actor, scope, tag_id, "delete")
        return {"tag_id": str(tag_id), "deleted": True, "permanent": False}


def remove_memory_tags(
    engine: Engine,
    actor: UUID,
    scope: UUID,
    memory_id: UUID,
    tag_ids: list[UUID],
) -> list[dict[str, Any]]:
    ids = set(tag_ids)
    if not 1 <= len(ids) <= 30:
        raise ValueError("tag.invalid_count")
    with engine.begin() as connection:
        _manage(connection, actor, scope)
        _visible(connection, scope, memory_id)
        for identifier in ids:
            # Tombstoned tags can also have their historical association removed explicitly.
            if not connection.scalar(
                text("SELECT 1 FROM memory_tags WHERE id=:id AND scope_id=:s"),
                {"id": identifier, "s": scope},
            ):
                raise ValueError("tag.not_found")
        connection.execute(
            text(
                "DELETE FROM memory_tag_links WHERE scope_id=:s AND memory_id=:id "
                "AND tag_id=ANY(CAST(:tags AS uuid[]))"
            ),
            {"s": scope, "id": memory_id, "tags": list(ids)},
        )
        _audit(connection, actor, scope, memory_id, "remove")
        return _memory_tags(connection, scope, memory_id)


def library(
    engine: Engine,
    actor: UUID,
    scope: UUID,
    tag_id: UUID | None = None,
    offset: int = 0,
    limit: int = 100,
    untagged: bool = False,
) -> dict[str, Any]:
    if offset < 0 or not 1 <= limit <= 100:
        raise ValueError("request.invalid")
    with engine.connect() as connection:
        DatabasePermissions(connection).require(actor, scope, "read")
        if tag_id is not None:
            _tag(connection, scope, tag_id)
        condition = (
            "m.scope_id=:s AND NOT EXISTS "
            "(SELECT 1 FROM memories n WHERE n.supersedes_id=m.id) AND " + visible_memory()
        )
        if untagged:
            if tag_id is not None:
                raise ValueError("request.invalid")
            condition += (
                " AND NOT EXISTS (SELECT 1 FROM memory_tag_links l "
                "JOIN memory_tags t ON t.id=l.tag_id WHERE l.scope_id=m.scope_id "
                "AND l.memory_id=m.memory_id AND NOT t.deleted)"
            )
        if tag_id is not None:
            condition += (
                " AND EXISTS (SELECT 1 FROM memory_tag_links l "
                "WHERE l.scope_id=m.scope_id AND l.memory_id=m.memory_id AND l.tag_id IN ("
                "WITH RECURSIVE subtree AS ("
                "SELECT id FROM memory_tags WHERE scope_id=:s AND id=:tag AND NOT deleted "
                "UNION SELECT t.id FROM memory_tags t JOIN subtree p ON t.parent_id=p.id "
                "WHERE t.scope_id=:s AND NOT t.deleted) SELECT id FROM subtree))"
            )
        params = {"s": scope, "tag": tag_id, "offset": offset, "limit": limit}
        total = connection.scalar(
            text("SELECT count(*) FROM memories m WHERE " + condition), params
        )
        rows = (
            connection.execute(
                text(
                    "SELECT m.*, (SELECT min(first.created_at) FROM memories first "
                    "WHERE first.scope_id=m.scope_id AND first.memory_id=m.memory_id) AS added_at "
                    "FROM memories m WHERE "
                    + condition
                    + " ORDER BY added_at DESC,m.memory_id LIMIT :limit OFFSET :offset"
                ),
                params,
            )
            .mappings()
            .all()
        )
        items = []
        for row in rows:
            data = dict(row)
            added = data.pop("added_at")
            memory = Memory.model_validate(row_data(data)).model_dump(mode="json")
            metadata = details(connection, Memory.model_validate(row_data(data)))
            memory.update(
                **metadata,
                added_at=added.isoformat(),
                tags=_memory_tags(connection, scope, row["memory_id"]),
            )
            items.append(memory)
        return {"items": items, "total": total, "offset": offset, "limit": limit}
