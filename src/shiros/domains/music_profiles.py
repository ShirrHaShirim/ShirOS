"""Actual profile credits drive libraries and automatically derived identity labels."""

from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import Connection, text

from shiros.domains.music_identity_catalogue import GROUPS, PART_ROLES, PARTS

IDENTITIES = {
    "音乐家": ["作曲家", "改编者", "指挥家", "演奏家", "歌唱家", "歌手"],
    "团体": ["乐团", "乐队"],
    "机构": ["厂牌", "出版商"],
}
IDENTITY_ROLES = {
    "作曲家": ("composer", "work"),
    "改编者": ("arranger", None),
    "指挥家": ("conductor", "recording"),
    "演奏家": ("performer", "recording"),
    "歌唱家": ("singer", "recording"),
    "歌手": ("singer", "recording"),
    "厂牌": ("label", "release"),
    "出版商": ("publisher", None),
}
PERFORMANCE_ROLES = PARTS
DERIVED_NAMES = {
    "作曲家",
    "改编者",
    "指挥家",
    "演奏家",
    "歌唱家",
    "歌手",
    *PERFORMANCE_ROLES,
    *GROUPS,
}


def vocal_class_condition(classical: bool) -> str:
    """Classify only actual vocal credits, using linked Works in the same scope."""
    categories = (
        "SELECT category FROM music_work_paths path JOIN music_profiles profile "
        "ON profile.id=path.work_id AND profile.scope_id=path.scope_id "
        "CROSS JOIN LATERAL jsonb_array_elements_text("
        "COALESCE(profile.data->'music_classes','[]'::jsonb) "
        "|| CASE WHEN COALESCE(profile.data->>'music_class','')<>'' THEN "
        "jsonb_build_array(profile.data->>'music_class') ELSE '[]'::jsonb END) category "
        "WHERE path.entity_id=credit.subject_id AND path.scope_id=credit.scope_id "
    )
    known_classical = f"EXISTS({categories} AND category='classical')"
    if classical:
        return known_classical
    return (
        f"(NOT {known_classical} OR EXISTS({categories} "
        "AND category<>'classical' AND category<>''))"
    )


def identity_id(scope: UUID, name: str) -> UUID:
    return uuid5(scope, "music-identity:" + name)


def identity_condition(scope: UUID, node_ids: list[UUID], alias: str = "o") -> str:
    """Only fixed preset IDs have inferred meaning; arbitrary tag names do not."""
    selected = set(node_ids)
    conditions = []
    for name, kind in (("音乐家", "person"), ("团体", "group"), ("机构", "organization")):
        if identity_id(scope, name) in selected:
            conditions.append(f"{alias}.kind='{kind}'")
    for name, (role, subject_kind) in IDENTITY_ROLES.items():
        if identity_id(scope, name) in selected:
            type_clause = f" AND subject.kind='{subject_kind}'" if subject_kind else ""
            if role == "singer":
                type_clause = " AND subject.kind IN ('recording','track')"
                type_clause += " AND " + vocal_class_condition(name == "歌唱家")
            conditions.append(
                "EXISTS(SELECT 1 FROM music_relations credit JOIN music_objects subject "
                "ON subject.id=credit.subject_id AND subject.scope_id=credit.scope_id "
                f"WHERE credit.object_id={alias}.id AND credit.scope_id={alias}.scope_id "
                f"AND credit.role='{role}'{type_clause})"
            )
    for name in PERFORMANCE_ROLES:
        if identity_id(scope, name) in selected:
            conditions.append(
                "EXISTS(SELECT 1 FROM music_relations credit JOIN music_relation_details d "
                "USING(subject_id,object_id,role) JOIN music_objects subject "
                "ON subject.id=credit.subject_id AND subject.scope_id=credit.scope_id "
                f"WHERE credit.object_id={alias}.id AND credit.scope_id={alias}.scope_id "
                f"AND credit.role='{PART_ROLES[name]}' AND subject.kind IN ('recording','track') "
                f"AND d.performance_role='{name}')"
            )
    return " OR ".join(conditions) or "false"


def identities(c: Connection, scope: UUID, item: dict[str, Any]) -> list[dict[str, Any]]:
    if item["kind"] not in ("person", "group", "organization"):
        return []
    from shiros.domains.music_taxonomy import assigned

    labels = assigned(c, scope, item["id"], "identity")
    for row in labels:
        row["derived"] = False
    inferred = {{"person": "音乐家", "group": "团体", "organization": "机构"}[item["kind"]]}
    credits = c.execute(
        text(
            "SELECT credit.role,subject.kind,COALESCE(d.performance_role,'') AS part, "
            "CASE WHEN credit.role='singer' THEN "
            + vocal_class_condition(True)
            + " ELSE false END AS classical_voice, CASE WHEN credit.role='singer' THEN "
            + vocal_class_condition(False)
            + " ELSE false END AS other_voice "
            "FROM music_relations credit JOIN music_objects subject "
            "ON subject.id=credit.subject_id AND subject.scope_id=credit.scope_id "
            "LEFT JOIN music_relation_details d ON d.subject_id=credit.subject_id "
            "AND d.object_id=credit.object_id AND d.role=credit.role "
            "AND d.scope_id=credit.scope_id "
            "WHERE credit.object_id=:id AND credit.scope_id=:s"
        ),
        {"id": item["id"], "s": scope},
    ).mappings()
    for credit in credits:
        for name, (role, kind) in IDENTITY_ROLES.items():
            if credit["role"] != role:
                continue
            if role == "singer":
                if (
                    credit["kind"] in ("recording", "track")
                    and credit["classical_voice" if name == "歌唱家" else "other_voice"]
                ):
                    inferred.add(name)
            elif kind is None or credit["kind"] == kind:
                inferred.add(name)
        part = credit["part"]
        if (
            part in PART_ROLES
            and credit["role"] == PART_ROLES[part]
            and credit["kind"] in ("recording", "track")
        ):
            inferred.add(part)
    nodes = c.execute(
        text("SELECT * FROM music_taxonomy WHERE scope_id=:s AND NOT deleted AND id=ANY(:ids)"),
        {"s": scope, "ids": [identity_id(scope, name) for name in inferred]},
    ).mappings()
    existing = {row["id"] for row in labels}
    for node in sorted(nodes, key=lambda row: row["name"]):
        if node["id"] not in existing:
            labels.append({**node, "derived": True})
    return labels


# Each column follows its own credits. Authorship or publication of a Work
# never implies participation in any recording or release of it.
PROFILE_CTE = """
WITH credits AS MATERIALIZED (
 SELECT subject.id,subject.kind,r.role FROM music_relations r JOIN music_objects subject
 ON subject.id=r.subject_id AND subject.scope_id=r.scope_id
 WHERE r.scope_id=:s AND r.object_id=:profile
 AND r.role IN ('composer','arranger','conductor','performer','singer','ensemble',
                'label','publisher')
), authored AS (
 SELECT DISTINCT id FROM credits WHERE kind='work' AND role IN ('composer','arranger','publisher')
), performed AS (
 SELECT id FROM credits WHERE kind='recording'
 AND role IN ('composer','arranger','conductor','performer','singer','ensemble')
 UNION SELECT contents.object_id FROM credits credit JOIN music_relations contents
 ON contents.subject_id=credit.id AND contents.scope_id=:s
 WHERE credit.kind='track' AND contents.role='recording'
 AND credit.role IN ('composer','arranger','conductor','performer','singer','ensemble')
), issued AS (
 SELECT id FROM credits WHERE kind='release'
 UNION SELECT track.release_id FROM credits credit JOIN music_tracks track
 ON track.id=credit.id AND track.scope_id=:s WHERE credit.kind='track'
 UNION SELECT subject.id FROM performed p JOIN music_relations contents
 ON contents.object_id=p.id AND contents.scope_id=:s AND contents.role='recording'
 JOIN music_objects subject ON subject.id=contents.subject_id AND subject.scope_id=:s
 WHERE subject.kind='release'
 UNION SELECT track.release_id FROM performed p JOIN music_relations contents
 ON contents.object_id=p.id AND contents.scope_id=:s AND contents.role='recording'
 JOIN music_tracks track ON track.id=contents.subject_id AND track.scope_id=:s
), profile_items AS (
 SELECT id,'work' AS section FROM authored
 UNION SELECT id,'recording' FROM performed
 UNION SELECT id,'release' FROM issued
 UNION SELECT r.object_id,'members' FROM music_relations r WHERE r.subject_id=:profile
 AND r.scope_id=:s AND r.role IN ('member','former_member','chief_conductor','suborganization')
)
"""


def sections(
    c: Connection, scope: UUID, identifier: UUID, root_works: bool = False
) -> list[dict[str, Any]]:
    items = "profile_items"
    prefix = PROFILE_CTE
    if root_works:
        prefix = (
            prefix.rstrip()
            + """, grouped_items AS (
 SELECT DISTINCT p.work_id AS id,'work' AS section FROM music_work_paths p
 JOIN profile_items i ON i.id=p.entity_id WHERE p.scope_id=:s AND i.section='work'
 AND NOT EXISTS(SELECT 1 FROM music_relations r WHERE r.subject_id=p.work_id
 AND r.scope_id=:s AND r.role='parent_work')
 UNION SELECT id,section FROM profile_items WHERE section<>'work'
) """
        )
        items = "grouped_items"
    return [
        dict(row)
        for row in c.execute(
            text(prefix + f"SELECT section,count(*) AS total FROM {items} GROUP BY section"),
            {"s": scope, "profile": identifier},
        ).mappings()
    ]
