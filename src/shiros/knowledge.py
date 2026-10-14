"""Canonical L2 references and reviewed, dependency-bound L1/L0 maintenance.

No summarizer is silently substituted for a model. Clients compose coherent drafts
from the evidence packet; only an explicit trusted review publishes a revision.
"""

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection, Engine, text

from shiros.adapters.database.records import lock_writes
from shiros.core.privacy import PrivacyInput, RuleBasedPrivacyPolicy
from shiros.identity import DatabasePermissions
from shiros.visibility import visible_memory


class KnowledgeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    id: UUID | None = None
    domain_id: UUID | None = None
    layer: Literal["L0", "L1", "L2"] = "L1"
    query: str = Field(default="", max_length=1000)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)
    request_id: UUID | None = None
    expected_revision: int = Field(default=0, ge=0)
    reason: Literal["manual", "daily", "weekly", "milestone", "rebuild"] = "manual"
    music: bool = False
    confirm: bool = False
    body: dict[str, str] = Field(default_factory=dict, max_length=8)
    progress: dict[str, str] = Field(default_factory=dict, max_length=8)


READ_ACTIONS = {"domains", "search", "object", "layers", "jobs", "evidence", "context"}
MCP_WRITE_ACTIONS = {"enqueue", "claim", "draft", "checkpoint", "cancel", "schedule"}
SECTIONS = {"long_term", "current", "changes", "exceptions", "unfinished"}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _safe(value: str) -> None:
    result = RuleBasedPrivacyPolicy().evaluate(PrivacyInput(text=value, reviewed=True))
    if len(value) > 20000 or not result.persistence_allowed or result.safe_text != value:
        raise PermissionError("knowledge.unsafe_content")


class KnowledgeService:
    def __init__(self, engine: Engine, actor: UUID, scope: UUID) -> None:
        self.engine, self.actor, self.scope = engine, actor, scope

    def _audit(self, c: Connection, subject: UUID, action: str, detail: Any = None) -> None:
        c.execute(
            text(
                "INSERT INTO knowledge_audit(id,scope_id,actor_id,subject_id,action,detail) "
                "VALUES (:id,:s,:a,:o,:action,CAST(:detail AS jsonb))"
            ),
            {
                "id": uuid4(),
                "s": self.scope,
                "a": self.actor,
                "o": subject,
                "action": action,
                "detail": _json(detail or {}),
            },
        )

    def _domain(self, c: Connection, identifier: UUID) -> dict[str, Any]:
        row = (
            c.execute(
                text(
                    "SELECT d.*,t.name FROM knowledge_domains d JOIN memory_tags t "
                    "ON t.id=d.id AND t.scope_id=d.scope_id WHERE d.id=:id AND d.scope_id=:s "
                    "AND NOT t.deleted AND t.parent_id IS NULL"
                ),
                {"id": identifier, "s": self.scope},
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ValueError("knowledge.domain_not_enabled")
        return dict(row)

    def _objects(self, c: Connection, domain: UUID | None = None) -> list[dict[str, Any]]:
        include_music = True
        clause = ""
        if domain is not None:
            definition = self._domain(c, domain)
            include_music = definition["music"]
            clause = (
                " AND EXISTS (WITH RECURSIVE tree AS (SELECT id FROM memory_tags WHERE id=:d "
                "AND scope_id=:s AND NOT deleted UNION ALL SELECT t.id FROM memory_tags t "
                "JOIN tree ON t.parent_id=tree.id WHERE t.scope_id=:s AND NOT t.deleted) "
                "SELECT 1 FROM memory_tag_links l JOIN tree ON tree.id=l.tag_id "
                "WHERE l.scope_id=:s AND l.memory_id=m.memory_id)"
            )
        params = {"s": self.scope, "d": domain}
        rows = (
            c.execute(
                text(
                    "SELECT m.memory_id AS id,m.revision,m.source_id,m.text,"
                    "coalesce(md.title,s.title,'') AS title,m.domain,"
                    "m.fact_level AS evidence_level, "
                    "m.observed_at,m.confidence,m.verified FROM memories m "
                    "JOIN sources s ON s.id=m.source_id AND s.scope_id=m.scope_id "
                    "LEFT JOIN memory_revision_metadata md ON md.revision_id=m.id "
                    "WHERE m.scope_id=:s AND m.persistence_allowed AND "
                    "m.confidentiality='standard' "
                    "AND s.persistence_allowed AND s.confidentiality='standard' "
                    "AND NOT EXISTS(SELECT 1 FROM memories newer WHERE newer.supersedes_id=m.id) "
                    "AND " + visible_memory() + clause + " ORDER BY m.memory_id LIMIT 2001"
                ),
                params,
            )
            .mappings()
            .all()
        )
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                _safe(row["title"])
                _safe(row["text"])
            except PermissionError:
                continue
            result.append(
                {
                    **dict(row),
                    "owner": "memory",
                    "layer": "L2",
                    "locator": f"shiros://memory/{row['id']}",
                    "evidence_level": row["evidence_level"],
                }
            )
        if include_music:
            music = (
                c.execute(
                    text(
                        "SELECT o.id,o.revision,o.title,o.kind,h.source_id,"
                        "s.fact_level AS evidence_level,s.observed_at,s.confidence,s.verified "
                        "FROM music_objects o "
                        "JOIN music_history h ON h.object_id=o.id AND h.scope_id=o.scope_id "
                        "AND h.revision=o.revision "
                        "JOIN sources s ON s.id=h.source_id AND s.scope_id=h.scope_id "
                        "WHERE o.scope_id=:s AND s.persistence_allowed AND "
                        "s.confidentiality='standard' "
                        "AND NOT EXISTS(SELECT 1 FROM revocations rv WHERE rv.scope_id=:s AND "
                        "rv.kind='source' "
                        "AND rv.target_id=h.source_id) ORDER BY o.id LIMIT 2001"
                    ),
                    params,
                )
                .mappings()
                .all()
            )
            for row in music:
                try:
                    _safe(row["title"])
                except PermissionError:
                    continue
                evidence: Sequence[UUID] = (
                    c.execute(
                        text(
                            "SELECT s.id FROM sources s WHERE s.scope_id=:s AND "
                            "s.persistence_allowed "
                            "AND s.confidentiality='standard' AND s.id IN ("
                            "SELECT source_id FROM music_history WHERE object_id=:id AND "
                            "scope_id=:s UNION "
                            "SELECT source_id FROM music_reviews WHERE object_id=:id AND "
                            "scope_id=:s UNION "
                            "SELECT source_id FROM music_listening_events WHERE object_id=:id "
                            "AND scope_id=:s) "
                            "AND NOT EXISTS(SELECT 1 FROM revocations rv WHERE rv.scope_id=:s "
                            "AND rv.kind='source' AND rv.target_id=s.id) ORDER BY s.id"
                        ),
                        {"s": self.scope, "id": row["id"]},
                    )
                    .scalars()
                    .all()
                )
                result.append(
                    {
                        **dict(row),
                        "owner": "music",
                        "layer": "L2",
                        "locator": f"shiros://music/{row['id']}",
                        "evidence_level": row["evidence_level"],
                        "evidence_signature": _digest(evidence),
                    }
                )
        if len(result) > 2000:
            raise ValueError("knowledge.domain_too_large")
        return result

    def _packet(self, c: Connection, head: Any) -> tuple[list[dict[str, Any]], str]:
        # L0 records references to domain syntheses, never the old Snapshot table.
        if head["layer"] == "L0":
            rows = (
                c.execute(
                    text(
                        "SELECT h.id,h.revision,h.domain_key,h.layer,v.fingerprint FROM "
                        "knowledge_heads h "
                        "JOIN knowledge_versions v ON v.head_id=h.id AND v.revision=h.revision "
                        "WHERE h.scope_id=:s AND h.layer='L1' ORDER BY h.id"
                    ),
                    {"s": self.scope},
                )
                .mappings()
                .all()
            )
            refs = [
                {
                    "owner": "synthesis",
                    "id": str(r["id"]),
                    "revision": r["revision"],
                    "fingerprint": r["fingerprint"],
                    "current_fingerprint": self._packet(c, r)[1],
                }
                for r in rows
            ]
        else:
            objects = self._objects(c, UUID(head["domain_key"]))
            refs = [
                {
                    "owner": r["owner"],
                    "id": str(r["id"]),
                    "revision": r["revision"],
                    "source_id": str(r["source_id"]),
                    "evidence_signature": r.get("evidence_signature"),
                }
                for r in objects
            ]
            definition = self._domain(c, UUID(head["domain_key"]))
            # Domain rename / canonical binding changes are also dependencies.
            return refs, _digest([definition["name"], definition["music"], refs])
        return refs, _digest(refs)

    def _head(self, c: Connection, layer: str, domain: UUID | None) -> Any:
        if layer == "L2" or (layer == "L1" and domain is None):
            raise ValueError("knowledge.invalid_layer")
        key = str(domain) if layer == "L1" else "global"
        if layer == "L1":
            self._domain(c, UUID(key))
        c.execute(
            text(
                "INSERT INTO knowledge_heads(id,scope_id,layer,domain_key) VALUES (:id,:s,:l,:k) "
                "ON CONFLICT(scope_id,layer,domain_key) DO NOTHING"
            ),
            {"id": uuid4(), "s": self.scope, "l": layer, "k": key},
        )
        return (
            c.execute(
                text(
                    "SELECT * FROM knowledge_heads WHERE scope_id=:s AND layer=:l AND domain_key=:k"
                ),
                {"s": self.scope, "l": layer, "k": key},
            )
            .mappings()
            .one()
        )

    def _job(self, c: Connection, identifier: UUID | None) -> Any:
        row = (
            c.execute(
                text(
                    "SELECT j.*,h.layer,h.domain_key FROM knowledge_jobs j JOIN knowledge_heads h "
                    "ON h.id=j.head_id WHERE j.scope_id=:s AND j.id=:id FOR UPDATE OF j"
                ),
                {"s": self.scope, "id": identifier},
            )
            .mappings()
            .first()
        )
        if row is None:
            raise ValueError("knowledge.job_not_found")
        return row

    def _enqueue(self, c: Connection, r: KnowledgeRequest) -> dict[str, Any]:
        if r.request_id is None:
            raise ValueError("knowledge.request_id_required")
        digest = _digest([r.layer, r.domain_id, r.reason])
        previous = (
            c.execute(
                text(
                    "SELECT * FROM knowledge_jobs WHERE scope_id=:s AND created_by=:a AND "
                    "request_id=:r"
                ),
                {"s": self.scope, "a": self.actor, "r": r.request_id},
            )
            .mappings()
            .first()
        )
        if previous is not None:
            if previous["request_hash"] != digest:
                raise ValueError("knowledge.request_conflict")
            return {"id": str(previous["id"]), "state": previous["state"], "reused": True}
        head = self._head(c, r.layer, r.domain_id)
        refs, fingerprint = self._packet(c, head)
        if head["checkpoint"] == fingerprint and r.reason not in {"rebuild", "weekly"}:
            return {"state": "unchanged", "revision": head["revision"]}
        active = (
            c.execute(
                text(
                    "SELECT id,state,fingerprint FROM knowledge_jobs WHERE head_id=:h "
                    "AND state IN ('queued','claimed','awaiting_review')"
                ),
                {"h": head["id"]},
            )
            .mappings()
            .first()
        )
        if active is not None and active["fingerprint"] != fingerprint:
            c.execute(
                text("UPDATE knowledge_jobs SET state='cancelled',updated_at=now() WHERE id=:id"),
                {"id": active["id"]},
            )
            self._audit(c, active["id"], "superseded-dependencies")
            active = None
        if active is not None:
            return {"id": str(active["id"]), "state": active["state"], "reused": True}
        identifier = uuid4()
        c.execute(
            text(
                "INSERT INTO knowledge_jobs(id,scope_id,head_id,created_by,request_id,request_hash,"
                "reason,state,base_revision,fingerprint,dependencies) VALUES "
                "(:id,:s,:h,:a,:r,:hash,:reason,'queued',:v,:f,CAST(:deps AS jsonb))"
            ),
            {
                "id": identifier,
                "s": self.scope,
                "h": head["id"],
                "a": self.actor,
                "r": r.request_id,
                "hash": digest,
                "reason": r.reason,
                "v": head["revision"],
                "f": fingerprint,
                "deps": _json(refs),
            },
        )
        self._audit(c, identifier, "enqueue", {"reason": r.reason})
        return {"id": str(identifier), "state": "queued", "dependencies": len(refs)}

    def _layers(self, c: Connection, domain: UUID | None) -> list[dict[str, Any]]:
        rows = (
            c.execute(
                text(
                    "SELECT * FROM knowledge_heads WHERE scope_id=:s "
                    "AND (CAST(:d AS text) IS NULL OR domain_key=:d OR layer='L0') ORDER BY "
                    "layer,domain_key"
                ),
                {"s": self.scope, "d": str(domain) if domain else None},
            )
            .mappings()
            .all()
        )
        result: list[dict[str, Any]] = []
        for head in rows:
            try:
                _, fingerprint = self._packet(c, head)
                stale = fingerprint != head["checkpoint"]
                # L0 depends on L1 validity as well as its revision IDs.
                if head["layer"] == "L0":
                    stale = stale or any(x["stale"] for x in result if x["layer"] == "L1")
            except ValueError:
                stale = True
            versions = (
                c.execute(
                    text(
                        "SELECT revision,created_at,body,dependencies,evidence_level,fingerprint "
                        "FROM knowledge_versions WHERE head_id=:h ORDER BY revision DESC LIMIT 20"
                    ),
                    {"h": head["id"]},
                )
                .mappings()
                .all()
            )
            result.append(
                {
                    "id": str(head["id"]),
                    "layer": head["layer"],
                    "domain_key": head["domain_key"],
                    "revision": head["revision"],
                    "stale": stale,
                    "versions": [
                        {
                            **dict(v),
                            "body": None if stale or v["fingerprint"] != fingerprint else v["body"],
                        }
                        for v in versions
                    ],
                }
            )
        # Do not expose a global synthesis relying on any stale domain synthesis.
        if any(x["stale"] for x in result if x["layer"] == "L1"):
            for item in result:
                if item["layer"] == "L0":
                    item["stale"] = True
                    for version in item["versions"]:
                        version["body"] = None
        return result

    def run(
        self, action: str, r: KnowledgeRequest, *, trusted_review: bool = False
    ) -> dict[str, Any]:
        with self.engine.begin() as c:
            p = DatabasePermissions(c)
            p.require(self.actor, self.scope, "read")
            if action not in READ_ACTIONS:
                lock_writes(c)
                if action in {"domain-enable", "publish"}:
                    if not trusted_review:
                        raise PermissionError("knowledge.human_review_required")
                    p.require(
                        self.actor,
                        self.scope,
                        "admin" if action == "domain-enable" or r.layer == "L0" else "review",
                    )
                elif not (
                    p.has(self.actor, self.scope, "propose")
                    or p.has(self.actor, self.scope, "review")
                ):
                    raise PermissionError("permission.denied")
            if action == "domain-enable":
                root = c.execute(
                    text(
                        "SELECT id FROM memory_tags WHERE id=:d AND scope_id=:s "
                        "AND parent_id IS NULL AND NOT deleted"
                    ),
                    {"d": r.domain_id, "s": self.scope},
                ).first()
                if root is None:
                    raise ValueError("knowledge.top_level_domain_required")
                c.execute(
                    text(
                        "INSERT INTO knowledge_domains(id,scope_id,music) VALUES (:d,:s,:m) "
                        "ON CONFLICT(id,scope_id) DO UPDATE SET music=:m"
                    ),
                    {"d": r.domain_id, "s": self.scope, "m": r.music},
                )
                assert r.domain_id is not None
                self._audit(c, r.domain_id, "domain-enable", {"music": r.music})
                return {"enabled": True}
            if action == "domains":
                rows = (
                    c.execute(
                        text(
                            "SELECT d.id,t.name,d.music FROM knowledge_domains d JOIN "
                            "memory_tags t "
                            "ON t.id=d.id AND t.scope_id=d.scope_id WHERE d.scope_id=:s AND "
                            "NOT t.deleted "
                            "AND t.parent_id IS NULL ORDER BY t.name"
                        ),
                        {"s": self.scope},
                    )
                    .mappings()
                    .all()
                )
                return {"items": [dict(x) for x in rows], "layers": self._layers(c, r.domain_id)}
            if action in {"search", "object"}:
                items = self._objects(c, r.domain_id)
                if action == "object":
                    obj = next((x for x in items if x["id"] == r.id), None)
                    if obj is None:
                        raise ValueError("knowledge.object_not_found")
                    if obj["owner"] == "music":
                        from shiros.domains.music import _detail

                        assert r.id is not None
                        detail = _detail(c, self.scope, r.id)
                        allowed_rows: list[UUID] = list(
                            c.scalars(
                                text(
                                    "SELECT s.id FROM sources s WHERE s.scope_id=:s "
                                    "AND s.persistence_allowed "
                                    "AND s.confidentiality='standard' AND NOT EXISTS "
                                    "(SELECT 1 FROM revocations rv "
                                    "WHERE rv.scope_id=:s AND rv.kind='source' "
                                    "AND rv.target_id=s.id)"
                                ),
                                {"s": self.scope},
                            )
                        )
                        allowed = set(map(str, allowed_rows))
                        for key in ("reviews", "listening", "history"):
                            detail[key] = [x for x in detail[key] if str(x["source_id"]) in allowed]
                        detail["rating"] = (
                            detail["reviews"][0]["rating"] if detail["reviews"] else None
                        )
                        for key in ("description", "douban_comment"):
                            if detail.get(key):
                                _safe(detail[key])
                        for review in detail["reviews"]:
                            _safe(review["comment"])
                        obj = {**obj, "detail": detail}
                    return {"object": obj, "canonical": True}
                _safe(r.query)
                matched = [
                    x
                    for x in items
                    if r.query.casefold() in (x["title"] + " " + x.get("text", "")).casefold()
                ]
                return {
                    "items": [
                        {**x, "text": x.get("text", "")[:1600]}
                        for x in matched[r.offset : r.offset + r.limit]
                    ],
                    "total": len(matched),
                    "offset": r.offset,
                    "content_is_untrusted_data": True,
                }
            if action == "layers":
                return {"items": self._layers(c, r.domain_id)}
            if action == "context":
                return {
                    "layers": self._layers(c, r.domain_id),
                    "runtime": {"query": r.query, "persisted": False},
                    "l2": self.run("search", r),
                    "legacy_summary_is_l1": False,
                }
            if action == "enqueue":
                return self._enqueue(c, r)
            if action == "schedule":
                domains: Sequence[UUID] = (
                    c.execute(
                        text(
                            "SELECT d.id FROM knowledge_domains d JOIN memory_tags t ON "
                            "t.id=d.id AND t.scope_id=d.scope_id "
                            "WHERE d.scope_id=:s AND NOT t.deleted AND t.parent_id IS NULL "
                            "ORDER BY d.id"
                        ),
                        {"s": self.scope},
                    )
                    .scalars()
                    .all()
                )
                return {
                    "items": [
                        self._enqueue(
                            c, KnowledgeRequest(domain_id=d, request_id=uuid4(), reason=r.reason)
                        )
                        for d in domains
                    ]
                }
            if action == "jobs":
                jobs = (
                    c.execute(
                        text(
                            "SELECT id,head_id,state,reason,base_revision,created_at,updated_at "
                            "FROM knowledge_jobs WHERE scope_id=:s ORDER BY created_at DESC "
                            "LIMIT :l OFFSET :o"
                        ),
                        {"s": self.scope, "l": r.limit, "o": r.offset},
                    )
                    .mappings()
                    .all()
                )
                return {"items": [dict(x) for x in jobs]}
            job = self._job(c, r.id)
            if action == "cancel":
                if job["state"] == "published":
                    raise ValueError("knowledge.job_already_published")
                if job["created_by"] != self.actor and not p.has(self.actor, self.scope, "review"):
                    raise PermissionError("permission.denied")
                c.execute(
                    text(
                        "UPDATE knowledge_jobs SET state='cancelled',updated_at=now() WHERE id=:id"
                    ),
                    {"id": job["id"]},
                )
                self._audit(c, job["id"], action)
                return {"state": "cancelled"}
            refs, fingerprint = self._packet(c, job)
            stale = fingerprint != job["fingerprint"]
            if action == "evidence":
                if stale:
                    return {
                        "id": str(job["id"]),
                        "state": job["state"],
                        "stale": True,
                        "draft": None,
                    }
                objects = (
                    self._objects(c, UUID(job["domain_key"]))
                    if job["layer"] == "L1"
                    else self._layers(c, None)
                )
                return {
                    "id": str(job["id"]),
                    "layer": job["layer"],
                    "base_revision": job["base_revision"],
                    "fingerprint": fingerprint,
                    "dependencies": refs,
                    "draft": job["draft"],
                    "progress": job["progress"],
                    "items": objects[r.offset : r.offset + r.limit],
                    "total": len(objects),
                    "stale": False,
                }
            if stale:
                raise ValueError("knowledge.dependencies_changed")
            if action == "claim":
                if job["state"] not in {"queued", "claimed"}:
                    raise ValueError("knowledge.job_not_claimable")
                now = datetime.now(UTC)
                if job["claim_actor"] not in {None, self.actor} and job["lease_until"] > now:
                    raise ValueError("knowledge.job_busy")
                c.execute(
                    text(
                        "UPDATE knowledge_jobs SET state='claimed',claim_actor=:a,"
                        "lease_until=:until,updated_at=now() WHERE id=:id"
                    ),
                    {"id": job["id"], "a": self.actor, "until": now + timedelta(minutes=15)},
                )
                self._audit(c, job["id"], action)
                return {"state": "claimed", "lease_seconds": 900, "progress": job["progress"]}
            if action in {"draft", "checkpoint"}:
                if (
                    job["state"] != "claimed"
                    or job["claim_actor"] != self.actor
                    or job["lease_until"] <= datetime.now(UTC)
                ):
                    raise ValueError("knowledge.claim_required")
                content = r.body if action == "draft" else r.progress
                if action == "draft" and (
                    set(content) != SECTIONS or not all(x.strip() for x in content.values())
                ):
                    raise ValueError("knowledge.synthesis_sections_required")
                _safe(_json(content))
                field = "draft" if action == "draft" else "progress"
                c.execute(
                    text(
                        f"UPDATE knowledge_jobs SET {field}=CAST(:b AS jsonb),state=:state,"
                        "updated_at=now() WHERE id=:id"
                    ),
                    {
                        "id": job["id"],
                        "b": _json(content),
                        "state": "awaiting_review" if action == "draft" else "claimed",
                    },
                )
                self._audit(c, job["id"], action, content)
                return {
                    "state": "awaiting_review" if action == "draft" else "claimed",
                    "published": False,
                }
            if action == "publish":
                if job["layer"] == "L0":
                    p.require(self.actor, self.scope, "admin")
                    if not r.confirm:
                        raise PermissionError("knowledge.l0_confirmation_required")
                if job["state"] == "published":
                    return {"state": "published", "reused": True}
                if job["state"] != "awaiting_review":
                    raise ValueError("knowledge.draft_required")
                head = (
                    c.execute(
                        text(
                            "SELECT * FROM knowledge_heads WHERE id=:h AND scope_id=:s FOR UPDATE"
                        ),
                        {"h": job["head_id"], "s": self.scope},
                    )
                    .mappings()
                    .one()
                )
                if (
                    r.expected_revision != head["revision"]
                    or head["revision"] != job["base_revision"]
                ):
                    raise ValueError("knowledge.revision_conflict")
                # No-change weekly reviews do not fabricate another revision.
                previous = c.execute(
                    text("SELECT body FROM knowledge_versions WHERE head_id=:h AND revision=:v"),
                    {"h": head["id"], "v": head["revision"]},
                ).scalar()
                revision = head["revision"]
                if previous != job["draft"] or head["checkpoint"] != fingerprint:
                    revision += 1
                    c.execute(
                        text(
                            "INSERT INTO knowledge_versions(id,scope_id,head_id,revision,"
                            "job_id,body,dependencies,"
                            "fingerprint,created_by,approved_by) VALUES (:id,:s,:h,:v,:j,"
                            "CAST(:b AS jsonb),"
                            "CAST(:deps AS jsonb),:f,:a,:reviewer)"
                        ),
                        {
                            "id": uuid4(),
                            "s": self.scope,
                            "h": head["id"],
                            "v": revision,
                            "j": job["id"],
                            "b": _json(job["draft"]),
                            "deps": _json(refs),
                            "f": fingerprint,
                            "a": job["claim_actor"],
                            "reviewer": self.actor,
                        },
                    )
                c.execute(
                    text("UPDATE knowledge_heads SET revision=:v,checkpoint=:f WHERE id=:h"),
                    {"h": head["id"], "v": revision, "f": fingerprint},
                )
                c.execute(
                    text(
                        "UPDATE knowledge_jobs SET state='published',updated_at=now() WHERE id=:id"
                    ),
                    {"id": job["id"]},
                )
                self._audit(c, job["id"], action, {"revision": revision})
                return {
                    "state": "published",
                    "revision": revision,
                    "checkpoint_advanced": revision != head["revision"]
                    or head["checkpoint"] != fingerprint,
                }
            raise ValueError("knowledge.invalid_action")
