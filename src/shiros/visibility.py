"""SQL policy shared by every normal memory retrieval and derivation path."""

from uuid import UUID

from sqlalchemy import Connection, text


def visible_memory(alias: str = "m") -> str:
    if alias not in ("m", "memories"):
        raise ValueError("visibility.invalid_alias")
    return (
        f"NOT EXISTS (SELECT 1 FROM revocations rv WHERE rv.scope_id={alias}.scope_id "
        f"AND ((rv.kind='source' AND rv.target_id={alias}.source_id) "
        f"OR (rv.kind='memory' AND rv.target_id={alias}.memory_id))) "
        f"AND NOT coalesce((SELECT hidden FROM memory_visibility mv "
        f"WHERE mv.memory_id={alias}.memory_id AND mv.scope_id={alias}.scope_id "
        "ORDER BY mv.id DESC LIMIT 1),false)"
    )


def memory_visible(connection: Connection, revision_id: UUID) -> bool:
    return bool(
        connection.execute(
            text("SELECT 1 FROM memories m WHERE m.id=:id AND " + visible_memory()),
            {"id": revision_id},
        ).first()
    )
