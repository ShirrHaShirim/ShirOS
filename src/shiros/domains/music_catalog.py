"""Virtual, composer-independent catalogue. Titles are never classification evidence."""

import json
import re
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, text

# Stable codes, shared by every composer; unused nodes stay out of browse trees.
GROUPS = [
    (
        "orchestral",
        "管弦乐",
        "Orchestral Music",
        [
            ("symphonies", "交响曲", "Symphonies"),
            ("symphonic-poems", "交响诗", "Symphonic Poems"),
            ("overtures", "序曲", "Overtures"),
            ("other", "其他管弦乐作品", "Other Orchestral Works"),
        ],
    ),
    (
        "concertante",
        "协奏性作品",
        "Concertante Music",
        [
            ("piano", "钢琴协奏曲", "Piano Concertos"),
            ("violin", "小提琴协奏曲", "Violin Concertos"),
            ("wind", "管乐协奏曲", "Wind Concertos"),
            ("multiple", "多独奏者协奏曲", "Multiple-Soloist Concertos"),
            ("other", "其他协奏性作品", "Other Concertante Works"),
        ],
    ),
    (
        "chamber",
        "室内乐",
        "Chamber Music",
        [
            ("string-quartets", "弦乐四重奏", "String Quartets"),
            ("string-quintets", "弦乐五重奏", "String Quintets"),
            ("piano-trios", "钢琴三重奏", "Piano Trios"),
            ("piano-quartets", "钢琴四重奏", "Piano Quartets"),
            ("piano-quintets", "钢琴五重奏", "Piano Quintets"),
            ("instrumental-sonatas", "器乐奏鸣曲", "Instrumental Sonatas"),
            ("quartet-related", "弦乐四重奏相关独立作品", "Related Independent Quartet Works"),
            ("other", "其他室内乐", "Other Chamber Music"),
        ],
    ),
    (
        "solo",
        "独奏器乐",
        "Solo Instrumental Music",
        [
            ("piano", "钢琴", "Piano"),
            ("violin", "小提琴", "Violin"),
            ("cello", "大提琴", "Cello"),
            ("organ", "管风琴", "Organ"),
            ("other", "其他乐器", "Other Instruments"),
        ],
    ),
    (
        "vocal",
        "声乐与合唱",
        "Vocal & Choral Music",
        [
            ("art-songs", "艺术歌曲", "Art Songs"),
            ("cantatas", "康塔塔", "Cantatas"),
            ("passions", "受难曲", "Passions"),
            ("masses", "弥撒", "Masses"),
            ("oratorios", "清唱剧", "Oratorios"),
            ("other", "其他声乐作品", "Other Vocal Works"),
        ],
    ),
    (
        "stage",
        "舞台音乐",
        "Stage Music",
        [
            ("operas", "歌剧", "Operas"),
            ("ballets", "芭蕾", "Ballets"),
            ("incidental", "戏剧配乐", "Incidental Music"),
        ],
    ),
    ("series", "作品系列", "Work Series", []),
    ("unclassified", "其他 / 未分类", "Other / Unclassified", []),
]
GENRE_MAP = {
    "交响曲": "orchestral.symphonies",
    "交响诗": "orchestral.symphonic-poems",
    "管弦乐": "orchestral",
    "序曲": "orchestral.overtures",
    "室内乐": "chamber",
    "弦乐四重奏": "chamber.string-quartets",
    "弦乐五重奏": "chamber.string-quintets",
    "钢琴三重奏": "chamber.piano-trios",
    "钢琴四重奏": "chamber.piano-quartets",
    "钢琴五重奏": "chamber.piano-quintets",
    "钢琴协奏曲": "concertante.piano",
    "小提琴协奏曲": "concertante.violin",
    "管乐协奏曲": "concertante.wind",
    "无伴奏小提琴": "solo.violin",
    "无伴奏大提琴": "solo.cello",
    "钢琴独奏": "solo.piano",
    "管风琴": "solo.organ",
    "艺术歌曲": "vocal.art-songs",
    "康塔塔": "vocal.cantatas",
    "受难曲": "vocal.passions",
    "弥撒": "vocal.masses",
    "清唱剧": "vocal.oratorios",
    "歌剧": "stage.operas",
    "芭蕾": "stage.ballets",
}


def seed_categories(c: Connection) -> None:
    for position, (code, zh, en, children) in enumerate(GROUPS, 1):
        rows = [(code, zh, en, None, position * 10)] + [
            (code + "." + key, name, english, code, index * 10)
            for index, (key, name, english) in enumerate(children, 1)
        ]
        for key, name, english, parent, order in rows:
            c.execute(
                text(
                    "INSERT INTO music_catalog_categories VALUES (:code,:zh,:en,:parent,:pos) "
                    "ON CONFLICT(code) DO NOTHING"
                ),
                {"code": key, "zh": name, "en": english, "parent": parent, "pos": order},
            )


def categories(c: Connection) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in c.execute(
            text(
                "SELECT n.* FROM music_catalog_categories n LEFT JOIN music_catalog_categories p "
                "ON p.code=n.parent_code ORDER BY coalesce(p.position,n.position),"
                "n.parent_code NULLS FIRST,n.position,n.code"
            )
        ).mappings()
    ]


def seed_genre_map(c: Connection, scope: UUID) -> None:
    # Only exact, controlled genre labels under an explicitly classical branch.
    from shiros.domains.music_classes import genre_class

    for node in c.execute(
        text(
            "SELECT id,name FROM music_taxonomy WHERE scope_id=:s "
            "AND namespace='genre' AND NOT deleted"
        ),
        {"s": scope},
    ).mappings():
        code = GENRE_MAP.get(node["name"])
        if code and genre_class(c, scope, node["id"]) == "classical":
            c.execute(
                text(
                    "INSERT INTO "
                    "music_catalog_genre_map(scope_id,node_id,category_code,source_text) "
                    "VALUES (:s,:id,:code,'Controlled classical genre mapping v1') "
                    "ON CONFLICT(node_id) DO NOTHING"
                ),
                {"s": scope, "id": node["id"], "code": code},
            )


def assignment(c: Connection, scope: UUID, work: UUID) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in c.execute(
            text(
                "SELECT a.*,n.name_zh,n.name_en,n.parent_code FROM music_catalog_assignments a "
                "JOIN music_catalog_categories n ON n.code=a.category_code "
                "WHERE a.scope_id=:s AND a.work_id=:id ORDER BY is_primary DESC,n.position,n.code"
            ),
            {"s": scope, "id": work},
        ).mappings()
    ]


def candidates(c: Connection, scope: UUID, work: UUID) -> list[str]:
    codes: set[str] = set(
        c.execute(
            text(
                "SELECT m.category_code FROM music_taxonomy_links l JOIN music_catalog_genre_map m "
                "ON m.node_id=l.node_id AND m.scope_id=l.scope_id WHERE l.scope_id=:s "
                "AND l.object_id=:id AND l.namespace='genre'"
            ),
            {"s": scope, "id": work},
        ).scalars()
    )
    return sorted(
        code for code in codes if not any(other.startswith(code + ".") for other in codes)
    )


def proposed(c: Connection, scope: UUID, genres: list[UUID]) -> list[str]:
    from shiros.domains.music_classes import genre_class
    from shiros.domains.music_taxonomy import get_node

    codes = set()
    for identifier in genres:
        node = get_node(c, scope, identifier)
        if node["namespace"] != "genre":
            raise ValueError("music.catalog_invalid_category")
        if genre_class(c, scope, identifier) != "classical":
            continue
        mapped = c.scalar(
            text(
                "SELECT category_code FROM music_catalog_genre_map "
                "WHERE scope_id=:s AND node_id=:id"
            ),
            {"s": scope, "id": identifier},
        )
        code = mapped or GENRE_MAP.get(node["name"])
        if code:
            codes.add(str(code))
    return sorted(
        code for code in codes if not any(other.startswith(code + ".") for other in codes)
    )


def assign(
    c: Connection,
    actor: UUID,
    scope: UUID,
    work: UUID,
    primary: str,
    auxiliary: list[str],
    origin: str,
    source: str,
    source_id: UUID,
) -> bool:
    active = (
        c.execute(
            text(
                "SELECT o.kind,p.data FROM music_objects o LEFT JOIN music_profiles p "
                "ON p.id=o.id WHERE o.scope_id=:s AND o.id=:id"
            ),
            {"s": scope, "id": work},
        )
        .mappings()
        .first()
    )
    if not active or active["kind"] != "work":
        raise ValueError("music.catalog_work_only")
    data = active["data"] or {}
    if "classical" not in [data.get("music_class"), *data.get("music_classes", [])]:
        raise ValueError("music.catalog_classical_only")
    valid = {row["code"] for row in categories(c)}
    if primary not in valid or any(code not in valid for code in auxiliary) or primary in auxiliary:
        raise ValueError("music.catalog_invalid_category")
    if len(set(auxiliary)) != len(auxiliary):
        raise ValueError("music.catalog_duplicate_category")
    old = assignment(c, scope, work)
    if origin != "manual" and any(row["origin"] == "manual" for row in old):
        return False
    proposed = {(primary, True), *((code, False) for code in auxiliary)}
    if {(r["category_code"], r["is_primary"]) for r in old} == proposed and all(
        r["origin"] == origin and r["source_text"] == source for r in old
    ):
        return False
    revision = 1 + int(
        c.scalar(
            text(
                "SELECT coalesce(max(revision),0) FROM music_catalog_history "
                "WHERE work_id=:id AND scope_id=:s"
            ),
            {"id": work, "s": scope},
        )
    )
    c.execute(
        text("DELETE FROM music_catalog_assignments WHERE scope_id=:s AND work_id=:id"),
        {"s": scope, "id": work},
    )
    for code, main in sorted(proposed):
        c.execute(
            text(
                "INSERT INTO music_catalog_assignments VALUES "
                "(:s,:id,:code,:main,:origin,:source,:rev,:actor,now())"
            ),
            {
                "s": scope,
                "id": work,
                "code": code,
                "main": main,
                "origin": origin,
                "source": source,
                "rev": revision,
                "actor": actor,
            },
        )
    snapshot = assignment(c, scope, work)
    c.execute(
        text(
            "INSERT INTO "
            "music_catalog_history(id,scope_id,work_id,revision,snapshot,source_id,created_by) "
            "VALUES (:id,:s,:w,:r,CAST(:snapshot AS jsonb),:source,:actor)"
        ),
        {
            "id": uuid4(),
            "s": scope,
            "w": work,
            "r": revision,
            "snapshot": json.dumps({"before": old, "after": snapshot}, default=str),
            "source": source_id,
            "actor": actor,
        },
    )
    return True


def maintain(
    c: Connection,
    actor: UUID,
    scope: UUID,
    work: UUID,
    primary: str | None,
    auxiliary: list[str] | None,
    source: str,
    source_id: UUID,
) -> None:
    if primary is not None:
        assign(
            c,
            actor,
            scope,
            work,
            primary or "unclassified",
            auxiliary or [],
            "manual",
            source or "User catalogue selection",
            source_id,
        )
        return
    old = assignment(c, scope, work)
    if any(row["origin"] in ("manual", "migration") for row in old):
        return
    choices = candidates(c, scope, work)
    assign(
        c,
        actor,
        scope,
        work,
        choices[0] if len(choices) == 1 else "unclassified",
        [],
        "genre",
        "Explicit mapped genre" if len(choices) == 1 else "Insufficient or ambiguous metadata",
        source_id,
    )


def natural_key(value: str) -> tuple[Any, ...]:
    return tuple(
        (1, int(part)) if part.isdigit() else (0, part.casefold())
        for part in re.split(r"(\d+)", value)
    )


def tree(c: Connection, scope: UUID, composer: UUID | None = None) -> dict[str, Any]:
    rows = c.execute(
        text("""SELECT DISTINCT w.id,w.title,coalesce(p.data->>'work_form','') AS work_form,
      a.category_code,person.id AS composer_id,person.title AS composer_title
      FROM music_objects w JOIN music_profiles p ON p.id=w.id AND p.scope_id=w.scope_id
      LEFT JOIN music_catalog_assignments a ON a.work_id=w.id AND a.scope_id=w.scope_id AND
      a.is_primary
      LEFT JOIN music_relations r ON r.subject_id=w.id AND r.scope_id=w.scope_id AND
      r.role='composer'
      LEFT JOIN music_objects person ON person.id=r.object_id AND person.scope_id=r.scope_id
      WHERE w.scope_id=:s AND w.kind='work'
      AND (p.data->>'music_class'='classical' OR p.data->'music_classes' ? 'classical')
      AND (CAST(:composer AS uuid) IS NULL OR r.object_id=CAST(:composer AS uuid))
      """),
        {"s": scope, "composer": composer},
    ).mappings()
    by_composer: dict[str, dict[str, Any]] = {}
    nodes = categories(c)
    for row in rows:
        key = str(row["composer_id"] or "unknown")
        group = by_composer.setdefault(
            key, {"id": row["composer_id"], "title": row["composer_title"], "counts": {}}
        )
        code = row["category_code"] or "unclassified"
        for target in (code, code.split(".")[0]) if "." in code else (code,):
            counts = group["counts"].setdefault(target, {"works": set(), "collections": set()})
            counts["collections" if row["work_form"] == "collection" else "works"].add(row["id"])
    groups = []
    for group in sorted(by_composer.values(), key=lambda g: natural_key(g["title"] or "")):
        group["nodes"] = [
            {
                **node,
                "total": len(group["counts"][node["code"]]["works"]),
                "collections": len(group["counts"][node["code"]]["collections"]),
            }
            for node in nodes
            if node["code"] in group["counts"]
        ]
        group.pop("counts")
        groups.append(group)
    return {"composers": groups, "categories": nodes}


def redirect(c: Connection, scope: UUID, identifier: UUID) -> dict[str, Any] | None:
    row = (
        c.execute(
            text(
                "SELECT r.*,p.title AS composer_title FROM music_catalog_redirects r "
                "JOIN music_object_records old ON old.id=r.work_id AND old.scope_id=r.scope_id "
                "JOIN music_object_records p ON p.id=r.composer_id AND p.scope_id=r.scope_id "
                "WHERE r.work_id=:id AND r.scope_id=:s AND old.archived"
            ),
            {"id": identifier, "s": scope},
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None
