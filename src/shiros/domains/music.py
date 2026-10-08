"""Music MVP: explicit relational records and append-only user history.

Clients are bound to an actor/scope by the adapters. No music text is promoted
to Shared Memory. Title similarity is never an identity or deduplication key.
"""

import csv
import hashlib
import io
import json
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import Connection, Engine, text

from shiros.adapters.database.records import insert_record, lock_writes, next_sequence
from shiros.core.events import Event
from shiros.core.privacy import SECRET, PrivacyInput, RuleBasedPrivacyPolicy, Sensitivity
from shiros.core.records import EntityRecord, PersistenceMetadata, Source
from shiros.core.schemas import EvidenceLevel, Provenance, UtcTimestamp, utc_now
from shiros.domains import music_taxonomy
from shiros.domains.music_images import normalize
from shiros.identity import DatabasePermissions, audit

Kind = Literal["release", "work", "recording", "person", "group", "organization", "track"]
Role = Literal[
    "composer",
    "conductor",
    "performer",
    "singer",
    "ensemble",
    "label",
    "work",
    "recording",
    "reissue_of",
    "parent_work",
]
Status = Literal["wishlist", "listening", "listened"]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class RelationInput(Input):
    object_id: UUID
    role: Role


class TrackInput(Input):
    title: str = Field(min_length=1, max_length=500)
    disc: int = Field(default=1, ge=1, le=100)
    position: int = Field(ge=1, le=1000)
    duration_seconds: int | None = Field(default=None, gt=0)
    relations: list[RelationInput] = Field(default_factory=list, max_length=50)


class ObjectInput(Input):
    kind: Kind = "release"
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=20000)
    release_year: int | None = Field(default=None, ge=1, le=9999)
    catalogue: str | None = Field(default=None, max_length=100)
    barcode: str | None = Field(default=None, pattern=r"^[0-9]{8,14}$")
    status: Status | None = None
    relations: list[RelationInput] = Field(default_factory=list, max_length=100)
    tag_ids: list[UUID] = Field(default_factory=list, max_length=50)
    genre_ids: list[UUID] = Field(default_factory=list, max_length=50)
    tracks: list[TrackInput] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def valid(self) -> "ObjectInput":
        if not self.title.strip() or self.kind == "track":
            raise ValueError("music.invalid_object")
        if self.kind != "release" and (
            self.tracks or self.status or self.release_year or self.barcode or self.description
        ):
            raise ValueError("music.release_fields")
        if self.catalogue and self.kind not in ("release", "work"):
            raise ValueError("music.catalogue_fields")
        positions = [(t.disc, t.position) for t in self.tracks]
        if len(set(positions)) != len(positions):
            raise ValueError("music.duplicate_track_position")
        return self


class MusicRequest(Input):
    id: UUID | None = None
    revision: int | None = Field(default=None, ge=1)
    object: ObjectInput | None = None
    query: str = Field(default="", max_length=500)
    view: Literal["all", "five-star", "featured", "frequent"] = "all"
    kind: Kind = "release"
    status: Status | None = None
    tag_id: UUID | None = None
    genre_id: UUID | None = None
    untagged: bool = False
    namespace: Literal["tag", "genre"] = "tag"
    name: str = Field(default="", max_length=64)
    parent_id: UUID | None = None
    image_data: str = Field(default="", max_length=11184812)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=100)
    rating: int | None = Field(default=None, ge=1, le=5)
    comment: str = Field(default="", max_length=20000)
    library: Literal["featured", "frequent"] = "featured"
    active: bool = True
    reason: str = Field(default="", max_length=2000)
    position: int = Field(default=0, ge=0)
    started_at: UtcTimestamp | None = None
    timezone: str = Field(default="Asia/Shanghai", max_length=100)
    actual_seconds: int | None = Field(default=None, gt=0)
    completed: bool = False
    platform: str = Field(default="manual", min_length=1, max_length=100)
    source_event_id: str | None = Field(default=None, min_length=1, max_length=250)
    url: str = Field(default="", max_length=2000)
    content: str = Field(default="", max_length=300000)
    format: Literal["csv", "json"] = "json"
    filename: str = Field(default="douban.json", max_length=250)


def _safe(value: str) -> str:
    decision = RuleBasedPrivacyPolicy().evaluate(PrivacyInput(text=value, reviewed=True))
    if not decision.persistence_allowed or decision.safe_text != value:
        raise PermissionError("privacy.unsafe_music")
    return value


def _rows(c: Connection, sql: str, **params: Any) -> list[dict[str, Any]]:
    return [dict(r) for r in c.execute(text(sql), params).mappings()]


def _object(c: Connection, scope: UUID, identifier: UUID) -> dict[str, Any]:
    rows = _rows(
        c, "SELECT * FROM music_objects WHERE id=:id AND scope_id=:s", id=identifier, s=scope
    )
    if not rows:
        raise ValueError("music.not_found")
    return rows[0]


def _provenance(
    c: Connection, actor: UUID, scope: UUID, title: str, body: str
) -> tuple[Provenance, PersistenceMetadata]:
    body, title = _safe(body), _safe(title)
    p = Provenance(
        source_id=uuid4(),
        observed_at=utc_now(),
        created_by=str(actor),
        level=EvidenceLevel.EXPLICIT_STATEMENT,
        confidence=1,
    )
    privacy = PersistenceMetadata(sensitivity=Sensitivity.PERSONAL, reviewed_by=actor)
    insert_record(
        c,
        "sources",
        Source(
            id=p.source_id,
            scope_id=scope,
            provenance=p,
            privacy=privacy,
            kind="note",
            title=title,
            text=body,
        ),
    )
    return p, privacy


def _new(
    c: Connection, scope: UUID, kind: str, title: str, p: Provenance, privacy: PersistenceMetadata
) -> UUID:
    identifier = uuid4()
    insert_record(
        c,
        "entities",
        EntityRecord(
            id=identifier,
            scope_id=scope,
            kind="music." + kind,
            title=_safe(title.strip()),
            provenance=p,
            privacy=privacy,
        ),
    )
    c.execute(
        text("INSERT INTO music_objects(id,scope_id,kind,title) VALUES (:id,:s,:k,:t)"),
        {"id": identifier, "s": scope, "k": kind, "t": title.strip()},
    )
    return identifier


def _event(
    c: Connection,
    actor: UUID,
    scope: UUID,
    identifier: UUID,
    p: Provenance,
    privacy: PersistenceMetadata,
) -> int:
    sequence = next_sequence(c)
    insert_record(
        c,
        "events",
        Event(
            scope_id=scope,
            provenance=p,
            privacy=privacy,
            entity_id=identifier,
            record_id=identifier,
            event_type="music.changed",
            sequence=sequence,
        ),
    )
    audit(c, actor, scope, identifier, "edit")
    return sequence


RELATION_KINDS = {
    "composer": {"person"},
    "conductor": {"person"},
    "performer": {"person", "group"},
    "singer": {"person"},
    "ensemble": {"group"},
    "label": {"organization"},
    "work": {"work"},
    "recording": {"recording"},
    "reissue_of": {"release"},
    "parent_work": {"work"},
}


def _relations(
    c: Connection, scope: UUID, identifier: UUID, relations: list[RelationInput]
) -> None:
    subject = _object(c, scope, identifier)
    for relation in relations:
        target = _object(c, scope, relation.object_id)
        if target["kind"] not in RELATION_KINDS[relation.role] or identifier == relation.object_id:
            raise ValueError("music.invalid_relation")
        if relation.role == "parent_work" and subject["kind"] != "work":
            raise ValueError("music.invalid_relation")
        if relation.role == "parent_work" and c.scalar(
            text(
                "WITH RECURSIVE parents(id) AS (SELECT CAST(:target AS uuid) UNION "
                "SELECT r.object_id FROM music_relations r JOIN parents p ON r.subject_id=p.id "
                "WHERE r.role='parent_work' AND r.scope_id=:s) SELECT 1 FROM parents WHERE id=:id"
            ),
            {"target": relation.object_id, "s": scope, "id": identifier},
        ):
            raise ValueError("music.work_cycle")
        if relation.role == "reissue_of" and subject["kind"] != "release":
            raise ValueError("music.invalid_relation")
        c.execute(
            text("INSERT INTO music_relations VALUES (:s,:id,:o,:r) ON CONFLICT DO NOTHING"),
            {"s": scope, "id": identifier, "o": relation.object_id, "r": relation.role},
        )


def _rating(c: Connection, scope: UUID, identifier: UUID) -> int | None:
    value = c.scalar(
        text(
            "SELECT rating FROM music_reviews WHERE scope_id=:s AND object_id=:id "
            "ORDER BY sequence DESC LIMIT 1"
        ),
        {"s": scope, "id": identifier},
    )
    return int(value) if value is not None else None


def _detail(c: Connection, scope: UUID, identifier: UUID) -> dict[str, Any]:
    item = _object(c, scope, identifier)
    for kind, table in [
        ("release", "music_releases"),
        ("work", "music_works"),
        ("recording", "music_recordings"),
        ("track", "music_tracks"),
    ]:
        if item["kind"] == kind:
            item.update(_rows(c, f"SELECT * FROM {table} WHERE id=:id", id=identifier)[0])
    item["relations"] = _rows(
        c,
        "SELECT r.*,o.title,o.kind FROM music_relations r JOIN "
        "music_objects o ON o.id=r.object_id WHERE r.subject_id=:id "
        "AND r.scope_id=:s",
        id=identifier,
        s=scope,
    )
    item["related"] = _rows(
        c,
        "SELECT r.role,o.id,o.title,o.kind FROM music_relations r JOIN "
        "music_objects o ON o.id=r.subject_id WHERE r.object_id=:id "
        "AND r.scope_id=:s",
        id=identifier,
        s=scope,
    )
    item["tracks"] = _rows(
        c,
        "SELECT t.*,o.title FROM music_tracks t JOIN music_objects o "
        "ON o.id=t.id WHERE t.release_id=:id AND t.scope_id=:s "
        "ORDER BY disc,position",
        id=identifier,
        s=scope,
    )
    item["reviews"] = _rows(
        c,
        "SELECT * FROM music_reviews WHERE object_id=:id AND scope_id=:s ORDER BY sequence DESC",
        id=identifier,
        s=scope,
    )
    item["rating"] = item["reviews"][0]["rating"] if item["reviews"] else None
    item["memberships"] = _rows(
        c,
        "SELECT DISTINCT ON (library) * FROM music_memberships "
        "WHERE object_id=:id AND scope_id=:s ORDER BY library,sequence DESC",
        id=identifier,
        s=scope,
    )
    item["listening"] = _rows(
        c,
        "SELECT * FROM music_listening_events WHERE object_id=:id "
        "AND scope_id=:s ORDER BY started_at DESC LIMIT 100",
        id=identifier,
        s=scope,
    )
    item["sources"] = _rows(
        c,
        "SELECT * FROM music_playback_sources WHERE object_id=:id "
        "AND scope_id=:s ORDER BY created_at",
        id=identifier,
        s=scope,
    )
    item["tags"] = music_taxonomy.assigned(c, scope, identifier, "tag")
    item["genres"] = music_taxonomy.assigned(c, scope, identifier, "genre")
    item["history"] = _rows(
        c,
        "SELECT h.id,h.revision,h.action,h.source_id,h.created_at "
        "FROM music_history h WHERE object_id=:id AND scope_id=:s "
        "ORDER BY revision DESC LIMIT 100",
        id=identifier,
        s=scope,
    )
    return item


def _snapshot(
    c: Connection,
    actor: UUID,
    scope: UUID,
    identifier: UUID,
    action: str,
    p: Provenance,
    privacy: PersistenceMetadata,
    *,
    advance: bool = True,
) -> None:
    if advance:
        c.execute(
            text("UPDATE music_objects SET revision=revision+1,updated_at=now() WHERE id=:id"),
            {"id": identifier},
        )
    item = _detail(c, scope, identifier)
    for key in ("history", "listening", "reviews"):
        item.pop(key, None)
    sequence = _event(c, actor, scope, identifier, p, privacy)
    c.execute(
        text(
            "INSERT INTO music_history(id,scope_id,object_id,revision,action,snapshot,"
            "source_id,created_by,sequence) VALUES (:id,:s,:o,:v,:a,CAST(:b AS jsonb),:p,:u,:q)"
        ),
        {
            "id": uuid4(),
            "s": scope,
            "o": identifier,
            "v": item["revision"],
            "a": action,
            "b": json.dumps(item, default=str, ensure_ascii=False),
            "p": p.source_id,
            "u": actor,
            "q": sequence,
        },
    )


def _review(
    c: Connection,
    actor: UUID,
    scope: UUID,
    identifier: UUID,
    rating: int | None,
    comment: str,
    origin: str,
    p: Provenance,
    privacy: PersistenceMetadata,
) -> None:
    seq = _event(c, actor, scope, identifier, p, privacy)
    c.execute(
        text(
            "INSERT INTO music_reviews(id,scope_id,object_id,rating,comment,origin,"
            "source_id,created_by,sequence) VALUES (:id,:s,:o,:r,:c,:origin,:p,:a,:q)"
        ),
        {
            "id": uuid4(),
            "s": scope,
            "o": identifier,
            "r": rating,
            "c": _safe(comment),
            "origin": origin,
            "p": p.source_id,
            "a": actor,
            "q": seq,
        },
    )


def _save(
    c: Connection,
    actor: UUID,
    scope: UUID,
    value: ObjectInput,
    p: Provenance,
    privacy: PersistenceMetadata,
    identifier: UUID | None = None,
) -> UUID:
    _safe(value.catalogue or "")
    creating = identifier is None
    identifier = identifier or _new(c, scope, value.kind, value.title, p, privacy)
    if _object(c, scope, identifier)["kind"] != value.kind:
        raise ValueError("music.kind_immutable")
    c.execute(
        text("UPDATE music_objects SET title=:t WHERE id=:id"),
        {"id": identifier, "t": _safe(value.title.strip())},
    )
    if value.kind == "release":
        c.execute(
            text(
                "INSERT INTO music_releases(id,scope_id,description,release_year,catalogue,barcode,"
                "status) VALUES (:id,:s,:d,:y,:c,:b,:st) ON CONFLICT(id) DO UPDATE SET "
                "description=:d,release_year=:y,catalogue=:c,barcode=:b,status=:st"
            ),
            {
                "id": identifier,
                "s": scope,
                "d": _safe(value.description),
                "y": value.release_year,
                "c": value.catalogue,
                "b": value.barcode,
                "st": value.status,
            },
        )
    elif value.kind == "work":
        c.execute(
            text(
                "INSERT INTO music_works(id,catalogue) VALUES (:id,:c) "
                "ON CONFLICT(id) DO UPDATE SET catalogue=:c"
            ),
            {"id": identifier, "c": value.catalogue},
        )
    elif value.kind == "recording":
        c.execute(
            text("INSERT INTO music_recordings(id) VALUES (:id) ON CONFLICT DO NOTHING"),
            {"id": identifier},
        )
    c.execute(text("DELETE FROM music_relations WHERE subject_id=:id"), {"id": identifier})
    _relations(c, scope, identifier, value.relations)
    music_taxonomy.assign(c, scope, identifier, "tag", value.tag_ids)
    music_taxonomy.assign(c, scope, identifier, "genre", value.genre_ids)
    # Tracks are created once; stable track IDs and their histories are never replaced.
    if not creating and value.tracks:
        raise ValueError("music.tracks_create_only")
    for track in value.tracks:
        track_id = _new(c, scope, "track", track.title, p, privacy)
        c.execute(
            text("INSERT INTO music_tracks VALUES (:id,:r,:s,:d,:n,:seconds)"),
            {
                "id": track_id,
                "r": identifier,
                "s": scope,
                "d": track.disc,
                "n": track.position,
                "seconds": track.duration_seconds,
            },
        )
        _relations(c, scope, track_id, track.relations)
        _snapshot(c, actor, scope, track_id, "created", p, privacy, advance=False)
    _snapshot(
        c,
        actor,
        scope,
        identifier,
        "created" if creating else "updated",
        p,
        privacy,
        advance=not creating,
    )
    return identifier


STATUS_MAP = {
    "想听": "wishlist",
    "wish": "wishlist",
    "wishlist": "wishlist",
    "在听": "listening",
    "do": "listening",
    "listening": "listening",
    "听过": "listened",
    "collect": "listened",
    "listened": "listened",
}


def _field(row: dict[str, Any], *keys: str) -> Any:
    return next((row[k] for k in keys if k in row and row[k] not in ("", None)), None)


def parse_douban(content: str, format: str) -> list[dict[str, Any]]:
    """Validate the whole file before writes; IDs rather than titles govern updates."""
    try:
        raw = (
            json.loads(content.lstrip("\ufeff"))
            if format == "json"
            else list(csv.DictReader(io.StringIO(content.lstrip("\ufeff"))))
        )
        if isinstance(raw, dict):
            raw = raw["items"]
        if not isinstance(raw, list) or not 1 <= len(raw) <= 1000:
            raise ValueError("music.invalid_import")
        result: list[dict[str, Any]] = []
        ids: set[str] = set()
        for n, row in enumerate(raw, 1):
            if not isinstance(row, dict):
                raise ValueError(f"music.import_row_{n}")

            def field(*keys: str, data: dict[str, Any] = row) -> Any:
                return _field(data, *keys)

            identifier = str(field("douban_id", "subject_id", "id", "豆瓣ID") or "")
            title = str(field("title", "name", "标题", "名称") or "").strip()
            state = field("status", "状态")
            status = STATUS_MAP.get(str(state))
            rating = field("rating", "评分")
            if isinstance(rating, bool) or (isinstance(rating, float) and not rating.is_integer()):
                raise ValueError(f"music.import_row_{n}")
            rating = int(rating) if rating is not None else None
            if (
                not identifier.isascii()
                or not identifier.isdigit()
                or len(identifier) > 30
                or identifier in ids
                or not title
                or not status
                or (rating is not None and rating not in range(1, 6))
            ):
                raise ValueError(f"music.import_row_{n}")
            ids.add(identifier)
            year = field("release_year", "year", "年份")
            value = ObjectInput(
                title=title, release_year=int(year) if year else None, status=status
            )
            result.append(
                {
                    "douban_id": identifier,
                    "object": value,
                    "rating": rating,
                    "comment": str(field("comment", "短评") or ""),
                    "marked_at": _safe(str(field("marked_at", "标记时间", "date") or "")) or None,
                }
            )
        return result
    except (TypeError, KeyError, json.JSONDecodeError, ValueError) as exc:
        code = str(exc)
        raise ValueError(
            code if code.startswith("music.import_row_") else "music.invalid_import"
        ) from None


READ_ACTIONS = {"list", "detail", "export", "stats", "taxonomy", "image-read"}


class MusicService:
    def __init__(self, engine: Engine, actor: UUID, scope: UUID) -> None:
        self.engine, self.actor, self.scope = engine, actor, scope

    def run(self, action: str, request: MusicRequest) -> dict[str, Any]:
        with self.engine.begin() as c:
            permissions = DatabasePermissions(c)
            permissions.require(self.actor, self.scope, "read")
            if action not in READ_ACTIONS:
                # Explicit new domain right; memory editor permission alone is insufficient.
                permissions.require(self.actor, self.scope, "music_write")
                lock_writes(c)
            return self._run(c, action, request)

    def _run(self, c: Connection, action: str, r: MusicRequest) -> dict[str, Any]:
        actor, scope = self.actor, self.scope
        if action == "taxonomy":
            return {"items": music_taxonomy.nodes(c, scope)}
        if action == "taxonomy-seed":
            music_taxonomy.seed(c, actor, scope)
            return {"items": music_taxonomy.nodes(c, scope)}
        if action == "taxonomy-save":
            if r.id:
                music_taxonomy.get_node(c, scope, r.id)
            return {
                "node": music_taxonomy.save_node(
                    c, actor, scope, r.namespace, r.name, r.parent_id, r.id, r.revision
                )
            }
        if action == "image-read":
            rows = _rows(
                c,
                "SELECT content,media_type FROM music_images WHERE id=:id AND scope_id=:s",
                id=r.id,
                s=scope,
            )
            if not rows:
                raise ValueError("music.image_not_found")
            return {"data": bytes(rows[0]["content"]), "media_type": rows[0]["media_type"]}
        if action == "list":
            where = ["o.scope_id=:s", "o.kind=:k"]
            params: dict[str, Any] = {
                "s": scope,
                "k": r.kind,
                "q": "%" + r.query + "%",
                "st": r.status,
                "tag": r.tag_id,
                "genre": r.genre_id,
                "offset": r.offset,
                "limit": r.limit,
                "lib": r.view,
            }
            if r.query:
                where.append(
                    "(o.title ILIKE :q OR EXISTS(SELECT 1 FROM music_relations rel "
                    "JOIN music_objects t ON rel.object_id=t.id "
                    "WHERE rel.subject_id=o.id AND t.title ILIKE :q))"
                )
            if r.status:
                where.append("release.status=:st")
            if r.untagged:
                if r.tag_id:
                    raise ValueError("music.invalid_filter")
                where.append(
                    "NOT EXISTS(SELECT 1 FROM music_taxonomy_links l WHERE "
                    "l.object_id=o.id AND l.namespace='tag')"
                )
            for node_id, namespace, parameter in [
                (r.tag_id, "tag", "tag"),
                (r.genre_id, "genre", "genre"),
            ]:
                if node_id:
                    if music_taxonomy.get_node(c, scope, node_id)["namespace"] != namespace:
                        raise ValueError("music.taxonomy_namespace")
                    where.append(
                        "EXISTS(SELECT 1 FROM music_taxonomy_links l WHERE "
                        "l.object_id=o.id AND l.node_id IN (WITH RECURSIVE tree AS ("
                        f"SELECT id FROM music_taxonomy WHERE id=:{parameter} AND scope_id=:s "
                        "UNION SELECT n.id FROM music_taxonomy n JOIN tree t ON n.parent_id=t.id "
                        "WHERE n.scope_id=:s) SELECT id FROM tree))"
                    )
            if r.view in ("five-star", "featured"):
                where.append("review.rating=5")
            if r.view in ("featured", "frequent"):
                where.append(
                    "EXISTS(SELECT 1 FROM music_memberships m WHERE m.object_id=o.id "
                    "AND m.library=:lib AND m.active AND m.sequence=(SELECT max(sequence) "
                    "FROM music_memberships newer WHERE newer.object_id=o.id "
                    "AND newer.library=m.library))"
                )
            joins = (
                " FROM music_objects o LEFT JOIN music_releases release ON release.id=o.id "
                "LEFT JOIN LATERAL (SELECT rating FROM music_reviews WHERE object_id=o.id "
                "ORDER BY sequence DESC LIMIT 1) review ON true WHERE " + " AND ".join(where)
            )
            total = c.scalar(text("SELECT count(*)" + joins), params)
            items = _rows(
                c,
                "SELECT o.*,release.status,release.release_year,review.rating"
                + joins
                + " ORDER BY o.created_at DESC,o.id LIMIT :limit OFFSET :offset",
                **params,
            )
            return {"items": items, "total": total, "offset": r.offset}
        if action == "detail":
            if r.id is None:
                raise ValueError("music.id_required")
            return {"item": _detail(c, scope, r.id)}
        if action == "stats":
            return {
                "listening": _rows(
                    c,
                    "SELECT count(*) AS events,count(actual_seconds) "
                    "AS known_duration_events,sum(actual_seconds) AS actual_seconds "
                    "FROM music_listening_events WHERE scope_id=:s",
                    s=scope,
                )[0],
                "coverage": "Manually recorded events only; unknown duration is excluded.",
            }
        if action == "export":
            # Complete relational export includes history and unknown fields, not a lossy CSV.
            tables = [
                "music_objects",
                "music_relations",
                "music_tag_links",
                "music_reviews",
                "music_memberships",
                "music_listening_events",
                "music_playback_sources",
                "music_history",
                "music_taxonomy",
                "music_taxonomy_links",
                "music_taxonomy_history",
            ]
            data = {t: _rows(c, f"SELECT * FROM {t} WHERE scope_id=:s", s=scope) for t in tables}
            for t in ["music_releases", "music_works", "music_recordings", "music_tracks"]:
                data[t] = _rows(
                    c,
                    f"SELECT t.* FROM {t} t JOIN music_objects o ON o.id=t.id WHERE o.scope_id=:s",
                    s=scope,
                )
            # Preserve images in the portable export, including replaced-image history.
            import base64

            data["music_images"] = _rows(c, "SELECT * FROM music_images WHERE scope_id=:s", s=scope)
            for asset in data["music_images"]:
                asset["content"] = base64.b64encode(bytes(asset["content"])).decode("ascii")
            data["sources"] = _rows(
                c,
                "SELECT * FROM sources WHERE scope_id=:s AND id IN "
                "(SELECT source_id FROM music_history WHERE scope_id=:s "
                "UNION SELECT source_id FROM music_reviews WHERE scope_id=:s "
                "UNION SELECT source_id FROM music_listening_events WHERE scope_id=:s)",
                s=scope,
            )
            return {"format": "shiros-music-1", "exported_at": utc_now(), "tables": data}
        if action == "save":
            if r.object is None:
                raise ValueError("music.object_required")
            if r.id and _object(c, scope, r.id)["revision"] != r.revision:
                raise ValueError("music.revision_conflict")
            p, privacy = _provenance(
                c,
                actor,
                scope,
                "Music manual edit",
                "\n".join(
                    [r.object.title, r.object.description, *[t.title for t in r.object.tracks]]
                ),
            )
            identifier = _save(c, actor, scope, r.object, p, privacy, r.id)
            return {"item": _detail(c, scope, identifier)}
        if action in ("import-preview", "import"):
            rows = parse_douban(r.content, r.format)
            source_text = "\n".join(x["object"].title + "\n" + x["comment"] for x in rows)
            _safe(source_text)
            if action == "import-preview":
                return {
                    "count": len(rows),
                    "items": [dict(x, object=x["object"].model_dump()) for x in rows],
                }
            p, privacy = _provenance(c, actor, scope, "Douban import: " + r.filename, source_text)
            created = updated = unchanged = 0
            for row in rows:
                existing = _rows(
                    c,
                    "SELECT o.id,r.* FROM music_releases r JOIN music_objects o "
                    "ON o.id=r.id WHERE o.scope_id=:s AND r.douban_id=:d",
                    s=scope,
                    d=row["douban_id"],
                )
                value = row["object"]
                if existing:
                    old = existing[0]
                    if (
                        old["douban_status"],
                        old["douban_rating"],
                        old["douban_comment"],
                        old["marked_at"],
                    ) == (value.status, row["rating"], row["comment"], row["marked_at"]):
                        unchanged += 1
                        continue
                    identifier = old["id"]
                    # Preserve manual catalogue/title/status and personal rating overrides.
                    updated += 1
                else:
                    identifier = _save(c, actor, scope, value, p, privacy)
                    created += 1
                c.execute(
                    text(
                        "UPDATE music_releases SET douban_id=:d,douban_status=:st,"
                        "douban_rating=:r,douban_comment=:c,marked_at=:m WHERE id=:id"
                    ),
                    {
                        "id": identifier,
                        "d": row["douban_id"],
                        "st": value.status,
                        "r": row["rating"],
                        "c": row["comment"],
                        "m": row["marked_at"],
                    },
                )
                manual = c.scalar(
                    text(
                        "SELECT 1 FROM music_reviews WHERE object_id=:id "
                        "AND origin='manual' LIMIT 1"
                    ),
                    {"id": identifier},
                )
                if not manual:
                    _review(
                        c,
                        actor,
                        scope,
                        identifier,
                        row["rating"],
                        row["comment"],
                        "douban",
                        p,
                        privacy,
                    )
                _snapshot(c, actor, scope, identifier, "douban.imported", p, privacy)
            return {"created": created, "updated": updated, "unchanged": unchanged}
        if r.id is None:
            raise ValueError("music.id_required")
        item = _object(c, scope, r.id)
        if item["revision"] != r.revision:
            raise ValueError("music.revision_conflict")
        p, privacy = _provenance(
            c, actor, scope, "Music " + action, "\n".join([item["title"], r.comment, r.reason])
        )
        _safe(r.platform)
        if action == "image-upload":
            image_bytes, width, height = normalize(r.image_data)
            identifier = uuid4()
            c.execute(
                text(
                    "INSERT INTO music_images(id,scope_id,source_id,created_by,filename,"
                    "content,sha256,media_type,width,height) VALUES "
                    "(:id,:s,:src,:actor,:name,:data,:sha,'image/webp',:w,:h)"
                ),
                {
                    "id": identifier,
                    "s": scope,
                    "src": p.source_id,
                    "actor": actor,
                    "name": _safe(r.filename),
                    "data": image_bytes,
                    "sha": hashlib.sha256(image_bytes).hexdigest(),
                    "w": width,
                    "h": height,
                },
            )
            c.execute(
                text("UPDATE music_objects SET image_id=:image WHERE id=:id"),
                {"image": identifier, "id": r.id},
            )
        elif action == "image-remove":
            c.execute(text("UPDATE music_objects SET image_id=NULL WHERE id=:id"), {"id": r.id})
        elif action == "review":
            _review(c, actor, scope, r.id, r.rating, r.comment, "manual", p, privacy)
        elif action == "membership":
            if item["kind"] != "release" or (
                r.library == "featured" and r.active and _rating(c, scope, r.id) != 5
            ):
                raise ValueError("music.featured_requires_five_stars")
            seq = _event(c, actor, scope, r.id, p, privacy)
            c.execute(
                text("INSERT INTO music_memberships VALUES (:id,:s,:o,:l,:a,:r,:pos,:q,now())"),
                {
                    "id": uuid4(),
                    "s": scope,
                    "o": r.id,
                    "l": r.library,
                    "a": r.active,
                    "r": _safe(r.reason),
                    "pos": r.position,
                    "q": seq,
                },
            )
        elif action == "listen":
            if item["kind"] not in ("release", "recording", "track") or not r.started_at:
                raise ValueError("music.invalid_listening")
            try:
                ZoneInfo(r.timezone)
            except (KeyError, ValueError):
                raise ValueError("music.invalid_timezone") from None
            if r.source_event_id:
                existing = _rows(
                    c,
                    "SELECT * FROM music_listening_events WHERE scope_id=:s "
                    "AND platform=:p AND source_event_id=:e",
                    s=scope,
                    p=r.platform,
                    e=r.source_event_id,
                )
                if existing:
                    old = existing[0]
                    if (
                        old["object_id"],
                        old["started_at"],
                        old["actual_seconds"],
                        old["completed"],
                        old["timezone"],
                    ) != (r.id, r.started_at, r.actual_seconds, r.completed, r.timezone):
                        raise ValueError("music.event_identity_conflict")
                    return {"item": _detail(c, scope, r.id), "duplicate": True}
            c.execute(
                text(
                    "INSERT INTO music_listening_events(id,scope_id,object_id,started_at,"
                    "timezone,actual_seconds,platform,source_event_id,source_id,completed) "
                    "VALUES (:id,:s,:o,:t,:z,:secs,:p,:e,:src,:done)"
                ),
                {
                    "id": uuid4(),
                    "s": scope,
                    "o": r.id,
                    "t": r.started_at,
                    "z": r.timezone,
                    "secs": r.actual_seconds,
                    "p": r.platform,
                    "e": r.source_event_id,
                    "src": p.source_id,
                    "done": r.completed,
                },
            )
        elif action == "source":
            url = urlsplit(r.url)
            if (
                url.scheme not in ("https", "http")
                or not url.hostname
                or url.username
                or url.password
            ):
                raise ValueError("music.invalid_url")
            if SECRET.search(r.url):
                raise PermissionError("privacy.unsafe_music")
            c.execute(
                text(
                    "INSERT INTO music_playback_sources(id,scope_id,object_id,url,platform) "
                    "VALUES (:id,:s,:o,:u,:p) ON CONFLICT(object_id,url) DO NOTHING"
                ),
                {"id": uuid4(), "s": scope, "o": r.id, "u": r.url, "p": _safe(r.platform)},
            )
        else:
            raise ValueError("music.invalid_action")
        _snapshot(c, actor, scope, r.id, action, p, privacy)
        return {"item": _detail(c, scope, r.id)}
