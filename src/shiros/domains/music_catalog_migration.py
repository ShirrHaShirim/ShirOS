"""Apply an audited, explicit catalogue plan without guessing from work names."""

import json
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, text

from shiros.adapters.database.records import lock_writes
from shiros.domains import music_catalog
from shiros.identity import DatabasePermissions


def apply_plan(c: Connection, actor: UUID, scope: UUID, plan: dict[str, Any]) -> dict[str, Any]:
    from shiros.domains.music import (
        ObjectInput,
        RelationInput,
        _detail,
        _provenance,
        _relations,
        _save,
        _snapshot,
    )

    DatabasePermissions(c).require(actor, scope, "music_write")
    lock_writes(c)
    retired = [
        entry for entry in plan["retire"] if not music_catalog.redirect(c, scope, UUID(entry["id"]))
    ]
    if not retired and plan["retire"]:
        return {"changed": False, "retired": [], "pending": [], "idempotent": True}
    # Reject a stale audit before creating sources or changing anything.
    for key, entry in plan["assign"].items():
        current = c.scalar(
            text("SELECT revision FROM music_objects WHERE id=:id AND scope_id=:s"),
            {"id": UUID(key), "s": scope},
        )
        if current != entry["revision"]:
            raise ValueError("music.catalog_stale_plan")
    for entry in retired:
        if _detail(c, scope, UUID(entry["id"]))["revision"] != entry["revision"]:
            raise ValueError("music.catalog_stale_plan")
    for entry in plan["retain"]:
        item = _detail(c, scope, UUID(entry["id"]))
        if item.get("work_form") != "collection" or {
            str(w["id"]) for w in item["child_works"]
        } != set(entry["members"]):
            raise ValueError("music.catalog_collection_changed")
    p, privacy = _provenance(c, actor, scope, "Work catalogue migration", plan["source"])
    changed: set[UUID] = set()
    pending = list(plan.get("pending", []))
    results = []
    program_results = []
    verified_programs = {
        UUID(entry["recording_id"]) for entry in plan.get("recording_programs", [])
    }
    music_catalog.seed_genre_map(c, scope)
    for key, entry in plan["assign"].items():
        identifier = UUID(key)
        if music_catalog.assign(
            c, actor, scope, identifier, entry["code"], [], "migration", plan["source"], p.source_id
        ):
            changed.add(identifier)
    # Catalogue the remaining explicitly classical Works from mapped genres, or leave unclassified.
    ids: Sequence[UUID] = (
        c.execute(
            text(
                "SELECT o.id FROM music_objects o JOIN music_profiles p ON p.id=o.id "
                "WHERE o.scope_id=:s AND o.kind='work' AND "
                "(p.data->>'music_class'='classical' OR p.data->'music_classes' ? 'classical')"
            ),
            {"s": scope},
        )
        .scalars()
        .all()
    )
    retirement_ids = {UUID(e["id"]) for e in retired}
    for identifier in ids:
        if (
            identifier not in changed
            and identifier not in retirement_ids
            and not music_catalog.assignment(c, scope, identifier)
        ):
            music_catalog.maintain(c, actor, scope, identifier, None, None, "", p.source_id)
            changed.add(identifier)
    for entry in retired:
        identifier = UUID(entry["id"])
        item = _detail(c, scope, identifier)
        incoming = [
            dict(row)
            for row in c.execute(
                text(
                    "SELECT r.*,d.started_on,d.ended_on,d.source_text,d.performance_role "
                    "FROM music_relations r "
                    "LEFT JOIN music_relation_details d USING(subject_id,object_id,role) "
                    "WHERE r.scope_id=:s AND r.object_id=:id"
                ),
                {"s": scope, "id": identifier},
            ).mappings()
        ]
        children = {UUID(key) for key in entry["children"]}
        if {w["id"] for w in item["child_works"]} != children:
            raise ValueError("music.catalog_stale_plan")
        detached = 0
        redundant = 0
        for relation in incoming:
            subject = relation["subject_id"]
            if relation["role"] == "parent_work" and subject in children:
                c.execute(
                    text(
                        "DELETE FROM music_relations WHERE scope_id=:s AND subject_id=:child "
                        "AND object_id=:id AND role='parent_work'"
                    ),
                    {"s": scope, "child": subject, "id": identifier},
                )
                detached += 1
                changed.add(subject)
            elif relation["role"] == "work":
                direct: set[UUID] = set(
                    c.execute(
                        text(
                            "SELECT r.object_id FROM music_relations r JOIN music_objects o "
                            "ON o.id=r.object_id WHERE r.scope_id=:s AND r.subject_id=:id "
                            "AND r.role='work' AND o.kind='work' AND o.id<>:old"
                        ),
                        {"s": scope, "id": subject, "old": identifier},
                    ).scalars()
                )
                if children and children <= direct:
                    c.execute(
                        text(
                            "DELETE FROM music_relations WHERE scope_id=:s AND subject_id=:subject "
                            "AND object_id=:id AND role='work'"
                        ),
                        {"s": scope, "subject": subject, "id": identifier},
                    )
                    redundant += 1
                    changed.add(subject)
                elif subject not in verified_programs:
                    pending.append(
                        {
                            "subject_id": str(subject),
                            "legacy_work_id": str(identifier),
                            "reason": "Unverified legacy aggregate link retained "
                            "as historical reference",
                        }
                    )
        # Preserve all original inbound credits, including cascaded relation details.
        c.execute(
            text(
                "INSERT INTO "
                "music_catalog_redirects(work_id,scope_id,composer_id,category_code,"
                "source_id,snapshot) "
                "VALUES (:id,:s,:composer,:code,:source,CAST(:snapshot AS jsonb))"
            ),
            {
                "id": identifier,
                "s": scope,
                "composer": UUID(entry["composer_id"]),
                "code": entry["category"],
                "source": p.source_id,
                "snapshot": json.dumps({"item": item, "incoming": incoming}, default=str),
            },
        )
        _snapshot(c, actor, scope, identifier, "catalog.retired_to_virtual_directory", p, privacy)
        c.execute(
            text("UPDATE music_object_records SET archived=true WHERE id=:id AND scope_id=:s"),
            {"id": identifier, "s": scope},
        )
        results.append(
            {
                "id": str(identifier),
                "title": item["title"],
                "category": entry["category"],
                "children_detached": detached,
                "redundant_links_removed": redundant,
            }
        )
    for entry in plan.get("images", []):
        identifier = UUID(entry["id"])
        image = c.scalar(
            text("SELECT image_id FROM music_objects WHERE id=:id AND scope_id=:s"),
            {"id": identifier, "s": scope},
        )
        if image != UUID(entry["image_id"]):
            raise ValueError("music.catalog_stale_image")
        c.execute(
            text("UPDATE music_objects SET image_id=NULL WHERE id=:id AND scope_id=:s"),
            {"id": identifier, "s": scope},
        )
        changed.add(identifier)
    for program in plan.get("recording_programs", []):
        recording_id = UUID(program["recording_id"])
        item = _detail(c, scope, recording_id)
        if item["revision"] != program["revision"]:
            raise ValueError("music.catalog_stale_program")
        evidence, evidence_privacy = _provenance(
            c, actor, scope, "SWR19042CD programme", program["source"]
        )
        work_ids = []
        created = 0
        for work in program["works"]:
            if work["existing_id"]:
                identifier = UUID(work["existing_id"])
                existing = _detail(c, scope, identifier)
                if existing["kind"] != "work" or not any(
                    str(r["object_id"]) == program["composer_id"] and r["role"] == "composer"
                    for r in existing["relations"]
                ):
                    raise ValueError("music.catalog_program_identity_mismatch")
            else:
                identifier = _save(
                    c,
                    actor,
                    scope,
                    ObjectInput(
                        kind="work",
                        title=work["title"],
                        music_classes=["classical"],
                        source_notes=program["source"],
                        relations=[
                            RelationInput(
                                object_id=UUID(program["composer_id"]),
                                role="composer",
                                source_text=program["source"],
                            )
                        ],
                    ),
                    evidence,
                    evidence_privacy,
                )
                created += 1
            music_catalog.assign(
                c,
                actor,
                scope,
                identifier,
                work["category"],
                [],
                "migration",
                program["source"],
                evidence.source_id,
            )
            changed.add(identifier)
            work_ids.append(identifier)
        _relations(
            c,
            scope,
            recording_id,
            [
                RelationInput(object_id=key, role="work", source_text=program["source"])
                for key in work_ids
            ],
        )
        c.execute(
            text(
                "DELETE FROM music_relations WHERE scope_id=:s AND subject_id=:id "
                "AND object_id=:old AND role='work'"
            ),
            {"s": scope, "id": recording_id, "old": UUID(program["legacy_id"])},
        )
        changed.add(recording_id)
        program_results.append(
            {
                "recording_id": str(recording_id),
                "confirmed_works": len(work_ids),
                "new_works": created,
                "source": program["source"],
                "track_data": "Existing release has no tracks; "
                "individual track data remains pending",
            }
        )
    for identifier in sorted(changed, key=str):
        _snapshot(
            c, actor, scope, identifier, "catalog.assigned_and_references_migrated", p, privacy
        )
    return {
        "changed": True,
        "retired": results,
        "changed_objects": len(changed),
        "images_detached": len(plan.get("images", [])),
        "pending": pending,
        "recording_programs": program_results,
    }
