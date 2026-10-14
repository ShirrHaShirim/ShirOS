"""Explicit work categories constrain genre trees and performance credits."""

from typing import Literal
from uuid import UUID

from sqlalchemy import Connection, text

from shiros.domains.music_identity_catalogue import PART_CLASSES as PART_CLASSES

MusicClass = Literal[
    "", "classical", "rock", "pop", "jazz", "electronic", "hiphop", "world", "other"
]
CLASSES = {
    "classical": "古典",
    "rock": "摇滚",
    "pop": "流行",
    "jazz": "爵士",
    "electronic": "电子",
    "hiphop": "Hip-Hop",
    "world": "世界音乐",
    "other": "其他",
}


def effective(c: Connection, scope: UUID, identifiers: list[UUID]) -> set[str]:
    if not identifiers:
        return set()
    return {
        row[0]
        for row in c.execute(
            text(
                "SELECT DISTINCT category FROM music_work_paths path "
                "JOIN music_profiles p ON p.id=path.work_id AND p.scope_id=path.scope_id "
                "CROSS JOIN LATERAL jsonb_array_elements_text("
                "COALESCE(p.data->'music_classes','[]'::jsonb) "
                "|| CASE WHEN COALESCE(p.data->>'music_class','')<>'' THEN "
                "jsonb_build_array(p.data->>'music_class') ELSE '[]'::jsonb END) category "
                "WHERE path.scope_id=:s AND path.entity_id=ANY(:ids) "
                "AND category<>''"
            ),
            {"s": scope, "ids": identifiers},
        )
    }


def genre_class(c: Connection, scope: UUID, identifier: UUID) -> str:
    rows = c.execute(
        text(
            "WITH RECURSIVE parents AS (SELECT * FROM music_taxonomy WHERE id=:id AND scope_id=:s "
            "UNION SELECT n.* FROM music_taxonomy n JOIN parents p ON n.id=p.parent_id "
            "WHERE n.scope_id=:s) SELECT music_class FROM parents WHERE music_class<>'' LIMIT 1"
        ),
        {"s": scope, "id": identifier},
    ).all()
    return rows[0][0] if rows else ""
