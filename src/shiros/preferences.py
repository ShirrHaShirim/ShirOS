"""Appearance is an actor preference, independent of browser lifetime and port."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Engine, text

from shiros.identity import DatabasePermissions

Theme = Literal["white", "ivory", "forest", "lapis", "plum", "graphite"]


class ThemeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    theme: Theme | None = None


def preference(
    engine: Engine, actor: UUID, scope: UUID, value: ThemeRequest
) -> dict[str, str | bool]:
    with engine.begin() as c:
        DatabasePermissions(c).require(actor, scope, "read")
        if value.theme is not None:
            c.execute(
                text(
                    "INSERT INTO workspace_preferences(scope_id,actor_id,theme) VALUES (:s,:a,:t) "
                    "ON CONFLICT(scope_id,actor_id) DO UPDATE SET theme=:t,updated_at=now()"
                ),
                {"s": scope, "a": actor, "t": value.theme},
            )
        saved = c.scalar(
            text("SELECT theme FROM workspace_preferences WHERE scope_id=:s AND actor_id=:a"),
            {"s": scope, "a": actor},
        )
        return {"theme": saved or "ivory", "saved": saved is not None}
