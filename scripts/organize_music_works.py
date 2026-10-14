"""Apply the individually reviewed Work organization plan in one audited transaction."""

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


def rows(c, query):
    return [dict(r) for r in c.execute(text(query)).mappings()]


def protected(c):
    # Full fingerprints of unrelated music facts; only parent_work links may change.
    result = {}
    for table in (
        "music_reviews",
        "music_memberships",
        "music_tracks",
        "music_releases",
        "music_recordings",
        "music_works",
        "music_images",
        "music_taxonomy_links",
    ):
        result[table] = rows(c, f'SELECT * FROM "{table}" ORDER BY 1')
    result["relations"] = rows(
        c,
        "SELECT * FROM music_relations WHERE role<>'parent_work' ORDER BY  "
        "subject_id,object_id,role",
    )
    result["details"] = rows(
        c,
        "SELECT * FROM music_relation_details WHERE role<>'parent_work'  "
        "ORDER BY subject_id,object_id,role",
    )
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--isolated-result", type=Path)
    args = parser.parse_args()
    path = Path("docs/music-work-organization-v0718.json")
    plan = json.loads(path.read_text(encoding="utf-8"))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if not args.database.endswith("_test"):
        assert args.isolated_result, "An isolated verified result is required"
        proof = json.loads(args.isolated_result.read_text(encoding="utf-8"))
        assert proof["verified"] and proof["plan_sha256"] == digest
    engine = make_engine(Settings().database_url().set(database=args.database))
    with engine.begin() as c:
        actor = LocalIdentity(engine).current(c)
        scope = c.scalar(
            text("SELECT scope_id FROM local_grants WHERE actor_id=:a AND role='owner'"),
            {"a": actor},
        )
        DatabasePermissions(c).require(actor, scope, "music_write")
        lock_writes(c)
        before = protected(c)
        original = {
            str(r["id"]): r for r in rows(c, "SELECT * FROM music_object_records ORDER BY id")
        }
        profiles = {
            str(r["id"]): r["data"]
            for r in rows(c, "SELECT id,data FROM music_profiles ORDER BY id")
        }
        completed = all(
            g["id"] in original and not original[g["id"]]["archived"]
            for g in plan["folders"]
            if g["new"]
        )
        changed = set()
        if not completed:
            for identifier, expected in plan["expected"].items():
                r = original[identifier]
                assert all(
                    str(r[k]) == str(expected[k])
                    for k in ("revision", "title", "image_id", "archived")
                ), identifier
            evidence, privacy = _provenance(
                c, actor, scope, "Reviewed organization of all Works", plan["source"]
            )
            for g in plan["folders"]:
                if g["new"]:
                    _save(
                        c,
                        actor,
                        scope,
                        ObjectInput(
                            kind="work",
                            title=g["title"],
                            work_form="folder",
                            music_classes=["classical"],
                            source_notes=plan["source"],
                            relations=[
                                RelationInput(object_id=UUID(x), role="composer")
                                for x in g["composers"]
                            ],
                        ),
                        evidence,
                        privacy,
                        new_identifier=UUID(g["id"]),
                    )
                elif g["restore"]:
                    c.execute(
                        text(
                            "UPDATE music_object_records SET archived=false,title=:t WHERE  "
                            "id=:id AND scope_id=:s"
                        ),
                        {"id": UUID(g["id"]), "s": scope, "t": g["title"]},
                    )
                    c.execute(
                        text(
                            "UPDATE music_profiles SET  "
                            "data=jsonb_set(data,'{work_form}','\"folder\"'::jsonb) WHERE id=:id  "
                            "AND scope_id=:s"
                        ),
                        {"id": UUID(g["id"]), "s": scope},
                    )
                    changed.add(g["id"])
            for link in plan["unlink"]:
                values = {"s": scope, "child": UUID(link["child"]), "parent": UUID(link["parent"])}
                c.execute(
                    text(
                        "DELETE FROM music_relation_details WHERE scope_id=:s AND  "
                        "subject_id=:child AND object_id=:parent AND role='parent_work'"
                    ),
                    values,
                )
                c.execute(
                    text(
                        "DELETE FROM music_relations WHERE scope_id=:s AND  "
                        "subject_id=:child AND object_id=:parent AND role='parent_work'"
                    ),
                    values,
                )
                changed.add(link["child"])
            for link in plan["links"]:
                values = {
                    "s": scope,
                    "child": UUID(link["child"]),
                    "parent": UUID(link["parent"]),
                    "source": plan["source"],
                }
                c.execute(
                    text(
                        "INSERT INTO music_relations VALUES  "
                        "(:s,:child,:parent,'parent_work') ON CONFLICT DO NOTHING"
                    ),
                    values,
                )
                c.execute(
                    text(
                        "INSERT INTO  "
                        "music_relation_details(scope_id,subject_id,object_id,role,source_text)  "
                        "VALUES (:s,:child,:parent,'parent_work',:source) ON  "
                        "CONFLICT(subject_id,object_id,role) DO NOTHING"
                    ),
                    values,
                )
                changed.add(link["child"])
            for correction in plan["classes"]:
                c.execute(
                    text(
                        "UPDATE music_profiles SET data=data || CAST(:d AS jsonb) WHERE  "
                        "id=:id AND scope_id=:s"
                    ),
                    {
                        "id": UUID(correction["id"]),
                        "s": scope,
                        "d": json.dumps(
                            {
                                "music_classes": correction["classes"],
                                "music_class": correction["classes"][0],
                            }
                        ),
                    },
                )
                changed.add(correction["id"])
            for correction in plan["covers"]:
                c.execute(
                    text(
                        "UPDATE music_object_records SET image_id=NULL WHERE id=:id AND scope_id=:s"
                    ),
                    {"id": UUID(correction["id"]), "s": scope},
                )
                changed.add(correction["id"])
            for correction in plan["titles"]:
                c.execute(
                    text("UPDATE music_object_records SET title=:t WHERE id=:id AND scope_id=:s"),
                    {"id": UUID(correction["id"]), "s": scope, "t": correction["title"]},
                )
                changed.add(correction["id"])
            for identifier in sorted(changed):
                _snapshot(
                    c, actor, scope, UUID(identifier), "music.works_organized", evidence, privacy
                )
        # Do not surface an implementation note as the group's musical introduction.
        cleaned = 0
        for g in plan["folders"]:
            if not g["new"]:
                continue
            identifier = UUID(g["id"])
            description = c.scalar(
                text(
                    "SELECT data->>'description' FROM music_profiles WHERE id=:id AND scope_id=:s"
                ),
                {"id": identifier, "s": scope},
            )
            if description == "个人作品浏览分组；成员保留独立身份与各自封面。":
                evidence, privacy = _provenance(
                    c,
                    actor,
                    scope,
                    "Group introduction cleanup",
                    "Keep implementation explanations out of musical introductions.",
                )
                c.execute(
                    text(
                        "UPDATE music_profiles SET data=jsonb_set(data,'{description}',"
                        "'\"\"'::jsonb) WHERE id=:id AND scope_id=:s"
                    ),
                    {"id": identifier, "s": scope},
                )
                _snapshot(
                    c, actor, scope, identifier, "music.group_intro_cleaned", evidence, privacy
                )
                cleaned += 1
        after = protected(c)
        # New folder records introduce only their own Works/composer credits.
        new_ids = {UUID(g["id"]) for g in plan["folders"] if g["new"]}
        for key in after:
            if key == "music_works":
                after[key] = [r for r in after[key] if r["id"] not in new_ids]
                before[key] = [r for r in before[key] if r["id"] not in new_ids]
            elif key in ("relations", "details"):
                after[key] = [r for r in after[key] if r["subject_id"] not in new_ids]
                before[key] = [r for r in before[key] if r["subject_id"] not in new_ids]
            assert before[key] == after[key], key
        # Existing descriptions, private notes, attribution, lyrics and ordering survive.
        for r in rows(c, "SELECT id,data FROM music_profiles ORDER BY id"):
            identifier = str(r["id"])
            if identifier not in profiles:
                continue
            allowed = (
                {"work_form"}
                if identifier in {g["id"] for g in plan["folders"] if g["restore"]}
                else set()
            )
            if identifier in {x["id"] for x in plan["classes"]}:
                allowed |= {"music_classes", "music_class"}
            if UUID(identifier) in new_ids:
                allowed |= {"description"}
            assert {k: v for k, v in r["data"].items() if k not in allowed} == {
                k: v for k, v in profiles[identifier].items() if k not in allowed
            }, identifier
        edges = rows(c, "SELECT subject_id,object_id FROM music_relations WHERE role='parent_work'")
        graph = {}
        for edge in edges:
            graph.setdefault(str(edge["subject_id"]), set()).add(str(edge["object_id"]))
        active = {str(r["id"]) for r in rows(c, "SELECT id FROM music_objects WHERE kind='work'")}

        def check(identifier, path):
            assert identifier not in path, "Cycle in Work graph"
            for parent in graph.get(identifier, set()):
                assert parent in active
                check(parent, path | {identifier})

        for identifier in active:
            check(identifier, set())
        assert set(plan["reviewed_work_ids"]) <= active
        for link in plan["links"]:
            assert link["parent"] in graph.get(link["child"], set())
        for link in plan["unlink"]:
            assert link["parent"] not in graph.get(link["child"], set())
        for g in plan["folders"]:
            assert g["id"] in active
            assert any(g["id"] in parents for parents in graph.values()), "Empty folder"
        assert len(active) >= plan["baseline_count"] + 15
        if not completed:
            assert len(active) == plan["baseline_count"] + 15
        for correction in plan["titles"]:
            assert (
                c.scalar(
                    text("SELECT title FROM music_objects WHERE id=:id"),
                    {"id": UUID(correction["id"])},
                )
                == correction["title"]
            )
        for correction in plan["covers"]:
            assert (
                c.scalar(
                    text("SELECT image_id FROM music_objects WHERE id=:id"),
                    {"id": UUID(correction["id"])},
                )
                is None
            )
        for correction in plan["classes"]:
            assert (
                c.scalar(
                    text("SELECT data->'music_classes' FROM music_profiles WHERE id=:id"),
                    {"id": UUID(correction["id"])},
                )
                == correction["classes"]
            )
        result = {
            "verified": True,
            "plan_sha256": digest,
            "changed": not completed or bool(cleaned),
            "introductions_cleaned": cleaned,
            "reviewed": plan["baseline_count"],
            "active_works": len(active),
            "root_works": len(active - graph.keys()),
            "created_folders": len(new_ids),
            "restored_folders": 2,
            "protected_facts_unchanged": True,
        }
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
