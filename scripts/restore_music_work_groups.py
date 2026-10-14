"""Apply the user's corrected cover-card hierarchy, retaining migration snapshots."""

import argparse
import hashlib
import json
from pathlib import Path
from uuid import UUID

from sqlalchemy import text

from shiros.adapters.database.connection import make_engine
from shiros.adapters.database.records import lock_writes
from shiros.config import Settings
from shiros.domains.music import ObjectInput, RelationInput, _provenance, _save, _snapshot
from shiros.identity import DatabasePermissions, LocalIdentity


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--isolated-result", type=Path)
    args = parser.parse_args()
    path = Path("docs/music-work-groups-v0717.json")
    plan = json.loads(path.read_text(encoding="utf-8"))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if not args.database.endswith("_test"):
        assert args.isolated_result
        proof = json.loads(args.isolated_result.read_text(encoding="utf-8"))
        assert proof["verified"] and proof["plan_sha256"] == digest
    engine = make_engine(Settings().database_url().set(database=args.database))
    with engine.begin() as c:
        actor = LocalIdentity(engine).current(c)
        scope = c.scalar(
            text("SELECT scope_id FROM local_grants WHERE actor_id=:id AND role='owner'"),
            {"id": actor},
        )
        assert scope
        DatabasePermissions(c).require(actor, scope, "music_write")
        lock_writes(c)
        current = (
            c.execute(
                text(
                    "SELECT id,title,image_id,revision,archived FROM music_object_records "
                    "WHERE scope_id=:s AND id=ANY(:ids)"
                ),
                {"s": scope, "ids": [UUID(row["id"]) for row in plan["restore"]]},
            )
            .mappings()
            .all()
        )
        if not any(row["archived"] for row in current):
            assert all(
                c.scalar(
                    text("SELECT 1 FROM music_objects WHERE id=:id AND scope_id=:s"),
                    {"id": UUID(g["id"]), "s": scope},
                )
                for g in plan["groups"]
            )
            result = {"verified": True, "plan_sha256": digest, "changed": False}
        else:
            assert len(current) == len(plan["restore"])
            expected = {row["id"]: row["revision"] for row in plan["restore"]}
            assert all(
                row["archived"] and row["revision"] == expected[str(row["id"])] for row in current
            )
            images_before: dict[UUID, UUID | None] = dict(
                c.execute(
                    text("SELECT id,image_id FROM music_object_records WHERE scope_id=:s"),
                    {"s": scope},
                ).all()
            )
            p, privacy = _provenance(
                c, actor, scope, "User-corrected work browsing", plan["source"]
            )
            changed = set()
            for entry in plan["restore"]:
                identifier = UUID(entry["id"])
                snapshot = c.scalar(
                    text(
                        "SELECT snapshot FROM music_catalog_redirects "
                        "WHERE work_id=:id AND scope_id=:s"
                    ),
                    {"id": identifier, "s": scope},
                )
                assert snapshot
                c.execute(
                    text(
                        "UPDATE music_object_records SET archived=false,title=:title "
                        "WHERE id=:id AND scope_id=:s"
                    ),
                    {"id": identifier, "s": scope, "title": entry["title"]},
                )
                c.execute(
                    text(
                        "UPDATE music_profiles SET data=jsonb_set(data,'{work_form}',"
                        "'\"folder\"'::jsonb) WHERE id=:id AND scope_id=:s"
                    ),
                    {"id": identifier, "s": scope},
                )
                for relation in snapshot["incoming"]:
                    if relation["role"] != "parent_work":
                        continue
                    values = {
                        "s": scope,
                        "child": UUID(relation["subject_id"]),
                        "id": identifier,
                        "start": relation["started_on"],
                        "end": relation["ended_on"],
                        "source": relation["source_text"],
                        "part": relation["performance_role"],
                    }
                    c.execute(
                        text(
                            "INSERT INTO music_relations VALUES "
                            "(:s,:child,:id,'parent_work') ON CONFLICT DO NOTHING"
                        ),
                        values,
                    )
                    c.execute(
                        text(
                            "INSERT INTO music_relation_details "
                            "(scope_id,subject_id,object_id,role,started_on,ended_on,"
                            "source_text,performance_role) VALUES "
                            "(:s,:child,:id,'parent_work',:start,:end,:source,:part) "
                            "ON CONFLICT(subject_id,object_id,role) DO NOTHING"
                        ),
                        values,
                    )
                    changed.add(values["child"])
                changed.add(identifier)
            for order, group in enumerate(plan["groups"], 1):
                identifier = _save(
                    c,
                    actor,
                    scope,
                    ObjectInput(
                        kind="work",
                        title=group["title"],
                        work_form="folder",
                        music_classes=["classical"],
                        source_notes=plan["source"] + "\n" + group["source"],
                        description="个人作品浏览分组；成员沿用原有作品档案。",
                        relations=[
                            RelationInput(object_id=UUID(group["composer_id"]), role="composer"),
                            RelationInput(object_id=UUID(group["parent"]), role="parent_work"),
                        ],
                    ),
                    p,
                    privacy,
                    new_identifier=UUID(group["id"]),
                )
                group["applied_id"] = str(identifier)
                c.execute(
                    text(
                        "UPDATE music_profiles SET data=jsonb_set(data,'{browse_order}',"
                        "CAST(:position AS jsonb)) WHERE id=:id AND scope_id=:s"
                    ),
                    {"position": str(order), "id": identifier, "s": scope},
                )
                for member in group["members"]:
                    c.execute(
                        text(
                            "DELETE FROM music_relations WHERE subject_id=:child "
                            "AND object_id=:parent AND scope_id=:s AND role='parent_work'"
                        ),
                        {"child": UUID(member), "parent": UUID(group["parent"]), "s": scope},
                    )
                    c.execute(
                        text("INSERT INTO music_relations VALUES (:s,:child,:id,'parent_work')"),
                        {"s": scope, "child": UUID(member), "id": identifier},
                    )
                    c.execute(
                        text(
                            "INSERT INTO music_relation_details "
                            "(scope_id,subject_id,object_id,role,source_text) VALUES "
                            "(:s,:child,:id,'parent_work',:source)"
                        ),
                        {
                            "s": scope,
                            "child": UUID(member),
                            "id": identifier,
                            "source": group["source"],
                        },
                    )
                    changed.add(UUID(member))
            for identifier in changed:
                _snapshot(c, actor, scope, identifier, "music.browsing_groups_restored", p, privacy)
            assert all(
                image
                == c.scalar(
                    text("SELECT image_id FROM music_object_records WHERE id=:id AND scope_id=:s"),
                    {"id": identifier, "s": scope},
                )
                for identifier, image in images_before.items()
            )
            result = {
                "verified": True,
                "plan_sha256": digest,
                "changed": True,
                "restored_groups": len(current),
                "groups": plan["groups"],
                "original_images_preserved": True,
            }
        # Also repairs the order for a group created by an interrupted earlier rollout.
        for order, group in enumerate(plan["groups"], 1):
            identifier = UUID(group["id"])
            existing = c.scalar(
                text(
                    "SELECT data->>'browse_order' FROM music_profiles WHERE id=:id AND scope_id=:s"
                ),
                {"id": identifier, "s": scope},
            )
            if existing != str(order):
                evidence, privacy = _provenance(
                    c, actor, scope, "Work browsing order", plan["source"]
                )
                c.execute(
                    text(
                        "UPDATE music_profiles SET data=jsonb_set(data,'{browse_order}',"
                        "CAST(:position AS jsonb)) WHERE id=:id AND scope_id=:s"
                    ),
                    {"position": str(order), "id": identifier, "s": scope},
                )
                _snapshot(c, actor, scope, identifier, "music.browsing_order", evidence, privacy)
                result["order_repaired"] = True
            actual: set[UUID] = set(
                c.execute(
                    text(
                        "SELECT subject_id FROM music_relations WHERE "
                        "object_id=:id AND scope_id=:s AND role='parent_work'"
                    ),
                    {"id": identifier, "s": scope},
                ).scalars()
            )
            assert actual == {UUID(member) for member in group["members"]}
        for correction in plan.get("cover_corrections", []):
            identifier = UUID(correction["id"])
            row = (
                c.execute(
                    text(
                        "SELECT image_id,revision FROM music_objects WHERE id=:id AND scope_id=:s"
                    ),
                    {"id": identifier, "s": scope},
                )
                .mappings()
                .one()
            )
            if row["image_id"] is None:
                continue
            assert (
                row["image_id"] == UUID(correction["image_id"])
                and row["revision"] == correction["revision"]
            )
            evidence, privacy = _provenance(
                c,
                actor,
                scope,
                "Group cover correction",
                "User rejected the Mozart Symphony No. 31 score as the generic symphonies cover. "
                "Original image and attribution retained; "
                "this group can receive the user's design.",
            )
            c.execute(
                text("UPDATE music_objects SET image_id=NULL WHERE id=:id AND scope_id=:s"),
                {"id": identifier, "s": scope},
            )
            _snapshot(c, actor, scope, identifier, "music.group_cover_corrected", evidence, privacy)
            result["cover_corrected"] = True
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
