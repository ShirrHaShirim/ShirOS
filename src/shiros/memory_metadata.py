"""Memory-specific labels/references; immutable original sources remain intact."""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection, text

from shiros.core.memory import Memory
from shiros.core.schemas import UtcTimestamp


class SourceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    id: UUID | None = None
    kind: Literal["note", "conversation", "document", "report", "import", "other"] = "note"
    title: str = Field(min_length=1, max_length=250)
    text: str = Field(min_length=1, max_length=20000)
    locator: str | None = Field(default=None, max_length=2000)
    observed_at: UtcTimestamp | None = None


def details(connection: Connection, memory: Memory) -> dict[str, Any]:
    row = (
        connection.execute(
            text(
                "SELECT title,source_records FROM memory_revision_metadata "
                "WHERE revision_id=:id AND scope_id=:s"
            ),
            {"id": memory.id, "s": memory.scope_id},
        )
        .mappings()
        .first()
    )
    if row:
        return {"title": row["title"], "source_records": row["source_records"]}
    title = connection.scalar(
        text("SELECT title FROM sources WHERE id=:id AND scope_id=:s"),
        {"id": memory.provenance.source_id, "s": memory.scope_id},
    )
    return {"title": title or "", "source_records": []}
