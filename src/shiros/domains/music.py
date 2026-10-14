"""Music MVP: explicit relational records and append-only user history.

Clients are bound to an actor/scope by the adapters. No music text is promoted
to Shared Memory. Title similarity is never an identity or deduplication key.
"""

import csv
import hashlib
import io
import json
import re
from datetime import date, datetime
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
from shiros.domains import music_catalog, music_classes, music_profiles, music_taxonomy
from shiros.domains.music_browse import WorkCoverage
from shiros.domains.music_classes import MusicClass
from shiros.domains.music_identity_catalogue import PART_ROLES, ui_catalogue
from shiros.domains.music_images import normalize
from shiros.domains.music_review_history import original_date, parse_reviews
from shiros.identity import DatabasePermissions, audit

Kind = Literal["release", "work", "recording", "person", "group", "organization", "track"]
Role = Literal[
    "composer",
    "arranger",
    "conductor",
    "performer",
    "singer",
    "ensemble",
    "label",
    "work",
    "recording",
    "reissue_of",
    "parent_work",
    "parent_recording",
    "publisher",
    "member",
    "former_member",
    "chief_conductor",
    "suborganization",
    "collaborator",
]
Status = Literal["wishlist", "listening", "listened"]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class RelationInput(Input):
    object_id: UUID
    role: Role
    started_on: date | None = None
    ended_on: date | None = None
    source_text: str = Field(default="", max_length=5000)
    performance_role: str = Field(default="", max_length=100)

    @model_validator(mode="after")
    def dates_valid(self) -> "RelationInput":
        if self.performance_role and self.performance_role not in PART_ROLES:
            raise ValueError("music.invalid_performance_role")
        if self.started_on and self.ended_on and self.ended_on < self.started_on:
            raise ValueError("music.invalid_dates")
        return self


class TrackInput(Input):
    id: UUID | None = None
    title: str = Field(min_length=1, max_length=500)
    disc: int = Field(default=1, ge=1, le=100)
    position: int = Field(ge=1, le=1000)
    duration_seconds: int | None = Field(default=None, gt=0)
    relations: list[RelationInput] = Field(default_factory=list, max_length=50)
    description: str = Field(default="", max_length=20000)
    personal_notes: str = Field(default="", max_length=20000)
    source_notes: str = Field(default="", max_length=10000)
    lyrics: str = Field(default="", max_length=20000)
    critical_reception: str = Field(default="", max_length=20000)
    tag_ids: list[UUID] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def title_valid(self) -> "TrackInput":
        if not self.title.strip():
            raise ValueError("music.invalid_track")
        return self


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
    catalog_primary: str | None = Field(default=None, max_length=100)
    catalog_auxiliary: list[str] | None = Field(default=None, max_length=10)
    catalog_source: str = Field(default="", max_length=5000)
    identity_ids: list[UUID] = Field(default_factory=list, max_length=50)
    tracks: list[TrackInput] = Field(default_factory=list, max_length=1000)
    aliases: list[str] = Field(default_factory=list, max_length=50)
    roles: list[str] = Field(default_factory=list, max_length=30)
    recorded_on: date | None = None
    location: str = Field(default="", max_length=500)
    work_form: Literal["", "album", "collection", "folder"] = ""
    composed_on: str = Field(default="", max_length=100)
    released_on: date | None = None
    medium: str = Field(default="", max_length=100)
    edition: str = Field(default="", max_length=500)
    personal_notes: str = Field(default="", max_length=20000)
    source_notes: str = Field(default="", max_length=10000)
    lyrics: str = Field(default="", max_length=20000)
    critical_reception: str = Field(default="", max_length=20000)
    recording_history: str = Field(default="", max_length=20000)
    music_class: MusicClass = ""
    music_classes: list[MusicClass] = Field(default_factory=list, max_length=8)
    country: str = Field(default="", max_length=200)
    born_on: date | None = None
    died_on: date | None = None
    founded_on: str = Field(default="", max_length=100)
    dissolved_on: str = Field(default="", max_length=100)
    official_url: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def valid(self) -> "ObjectInput":
        if self.catalog_primary is not None or self.catalog_auxiliary is not None:
            if self.kind != "work":
                raise ValueError("music.catalog_work_only")
            if self.catalog_auxiliary is not None and self.catalog_primary is None:
                raise ValueError("music.catalog_primary_required")
        if not self.title.strip() or self.kind == "track":
            raise ValueError("music.invalid_object")
        if self.lyrics.strip() and (
            self.kind != "work" or self.work_form in ("album", "collection", "folder")
        ):
            raise ValueError("music.lyrics_work_only")
        if self.recording_history.strip() and self.kind != "recording":
            raise ValueError("music.recording_fields")
        if (self.country or self.official_url) and self.kind not in (
            "person",
            "group",
            "organization",
        ):
            raise ValueError("music.profile_fields")
        if (self.born_on or self.died_on) and self.kind != "person":
            raise ValueError("music.person_fields")
        if (self.founded_on or self.dissolved_on) and self.kind not in ("group", "organization"):
            raise ValueError("music.organization_fields")
        if self.born_on and self.died_on and self.died_on < self.born_on:
            raise ValueError("music.invalid_dates")
        if self.official_url:
            _public_url(self.official_url)
        if self.kind != "release" and (
            self.tracks or self.status or self.release_year or self.barcode
        ):
            raise ValueError("music.release_fields")
        if self.catalogue and self.kind not in ("release", "work"):
            raise ValueError("music.catalogue_fields")
        if self.genre_ids and self.kind != "work":
            raise ValueError("music.genre_work_only")
        if (self.music_class or self.music_classes) and self.kind != "work":
            raise ValueError("music.work_fields")
        if self.identity_ids and self.kind not in ("person", "group", "organization"):
            raise ValueError("music.identity_people_only")
        if self.roles and self.kind not in ("person", "group", "organization"):
            raise ValueError("music.roles_people_only")
        if any(not x.strip() or len(x) > 500 for x in self.aliases + self.roles):
            raise ValueError("music.invalid_profile")
        if (self.recorded_on or self.location) and self.kind != "recording":
            raise ValueError("music.recording_fields")
        if (self.composed_on or self.work_form) and self.kind != "work":
            raise ValueError("music.work_fields")
        if (self.released_on or self.medium or self.edition) and self.kind != "release":
            raise ValueError("music.release_fields")
        positions = [(t.disc, t.position) for t in self.tracks]
        if len(set(positions)) != len(positions):
            raise ValueError("music.duplicate_track_position")
        return self


class ImageFrameInput(Input):
    x: float = Field(default=50, ge=0, le=100)
    y: float = Field(default=50, ge=0, le=100)
    zoom: float = Field(default=1, ge=1, le=4)


class MusicRequest(Input):
    id: UUID | None = None
    revision: int | None = Field(default=None, ge=1)
    object: ObjectInput | None = None
    query: str = Field(default="", max_length=500)
    view: Literal["all", "five-star", "featured", "frequent"] = "all"
    kind: Kind = "release"
    kinds: list[Kind] = Field(default_factory=list, max_length=7)
    cross_library: bool = False
    person_id: UUID | None = None
    profile_id: UUID | None = None
    profile_section: Literal["work", "recording", "release", "members"] = "work"
    work_id: UUID | None = None
    parent_work_id: UUID | None = None
    root_works: bool = False
    catalog_category: str | None = Field(default=None, max_length=100)
    composer_id: UUID | None = None
    catalog_unknown_composer: bool = False
    catalog_genre_ids: list[UUID] = Field(default_factory=list, max_length=50)
    root_recordings: bool = False
    parent_recording_id: UUID | None = None
    include_child_works: bool = False
    source_revisions: dict[UUID, int] = Field(default_factory=dict, max_length=100)
    recording_id: UUID | None = None
    recording_works_id: UUID | None = None
    scope_recording_id: UUID | None = None
    sort: Literal[
        "newest",
        "updated",
        "title_asc",
        "title_desc",
        "date_desc",
        "date_asc",
        "rating_desc",
        "work_order",
        "recording_order",
        "catalog_order",
    ] = "newest"
    date_from: date | None = None
    date_to: date | None = None
    status: Status | None = None
    tag_id: UUID | None = None
    genre_id: UUID | None = None
    identity_id: UUID | None = None
    untagged: bool = False
    namespace: Literal["tag", "genre", "identity"] = "tag"
    music_class: MusicClass = ""
    relations: list[RelationInput] = Field(default_factory=list, max_length=100)
    relation_role: Role | None = None
    name: str = Field(default="", max_length=64)
    parent_id: UUID | None = None
    image_data: str = Field(default="", max_length=11184812)
    image_frame: ImageFrameInput | None = None
    image_source_url: str = Field(default="", max_length=2000)
    image_credit: str = Field(default="", max_length=1000)
    image_license: str = Field(default="", max_length=500)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=100)
    rating: int | None = Field(default=None, ge=0, le=7)
    rating_scale: Literal[5, 7] = 7
    rating_min: int | None = Field(default=None, ge=0, le=7)
    rating_max: int | None = Field(default=None, ge=0, le=7)
    unrated: bool = False
    track: TrackInput | None = None
    comment: str = Field(default="", max_length=20000)
    review_id: UUID | None = None
    historical_date: date | None = None
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
    event_kind: Literal["manual", "playback", "external"] = "manual"
    url: str = Field(default="", max_length=2000)
    content: str = Field(default="", max_length=300000)
    format: Literal["csv", "json"] = "json"
    filename: str = Field(default="douban.json", max_length=250)
    sync_status: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    object_ids: list[UUID] = Field(default_factory=list, max_length=100)


def _safe(value: str) -> str:
    # Canonical catalogue UUIDs can contain numeric groups that resemble phones.
    # Protect only this exact identifier syntax; surrounding prose is still checked.
    inspected = re.sub(
        r"(?<![\w-])[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}(?![\w-])",
        "[CATALOGUE_UUID]",
        value,
    )
    decision = RuleBasedPrivacyPolicy().evaluate(PrivacyInput(text=inspected, reviewed=True))
    if not decision.persistence_allowed or decision.safe_text != inspected:
        raise PermissionError("privacy.unsafe_music")
    return value


def _public_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("music.invalid_url")
    if SECRET.search(value):
        raise PermissionError("privacy.unsafe_music")
    return value


def _safe_profile(profile: dict[str, Any]) -> None:
    """Validate typed catalogue URLs like playback sources; check all other prose."""
    inspected = dict(profile)
    if inspected.get("official_url"):
        _public_url(inspected.pop("official_url"))
    credits = {}
    for identifier, attribution in inspected.pop("image_attributions", {}).items():
        credit = dict(attribution)
        if credit.get("source_url"):
            _public_url(credit.pop("source_url"))
        credits[identifier] = credit
    inspected["image_attributions"] = credits
    _safe(json.dumps(inspected, ensure_ascii=False))


def _rows(c: Connection, sql: str, **params: Any) -> list[dict[str, Any]]:
    return [dict(r) for r in c.execute(text(sql), params).mappings()]


def _search_pattern(term: str) -> str:
    """User-entered percent and underscore characters are literal, not SQL wildcards."""
    return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _search_condition(alias: str, parameter: str) -> str:
    """Search one scoped entity, without changing the library's object kind."""
    return f"{alias}.id IN (SELECT id FROM search_matches_{parameter})"


def _search_matches(parameter: str) -> str:
    """A keyword's matching associations are reused by Work hierarchy expansion."""
    return (
        f"search_matches_{parameter} AS MATERIALIZED "
        f"(SELECT DISTINCT link.origin_id AS id FROM search_links link JOIN search_names target "
        f"ON target.id=link.target_id WHERE "
        f"(target.title ILIKE :{parameter} OR target.aliases ILIKE :{parameter} "
        f"OR target.catalogue ILIKE :{parameter} OR target.barcode ILIKE :{parameter} "
        f"OR target.genres ILIKE :{parameter} OR target.tags ILIKE :{parameter}))"
    )


# Build scoped associations once. Outward traversal through a shared composition
# never transfers another recording's performers onto a release or recording.
SEARCH_CTES = """
search_paths AS MATERIALIZED (SELECT * FROM music_work_paths WHERE scope_id=:s),
search_relations AS MATERIALIZED (
 SELECT r.* FROM music_relations r JOIN music_objects a ON a.id=r.subject_id
 JOIN music_objects b ON b.id=r.object_id WHERE r.scope_id=:s
 AND a.scope_id=:s AND b.scope_id=:s
), search_tracks AS MATERIALIZED (
 SELECT t.id,t.release_id FROM music_tracks t JOIN music_objects o ON o.id=t.id
 WHERE t.scope_id=:s AND o.scope_id=:s
), search_genres AS MATERIALIZED (
 SELECT path.entity_id,string_agg(n.name,' ') AS names FROM search_paths path
 JOIN music_taxonomy_links tag ON tag.object_id=path.work_id
 AND tag.scope_id=:s AND tag.namespace='genre'
 JOIN music_taxonomy n ON n.id=tag.node_id AND n.scope_id=:s AND NOT n.deleted
 GROUP BY path.entity_id
), search_tags AS MATERIALIZED (
 SELECT tag.object_id,string_agg(n.name,' ') AS names FROM music_taxonomy_links tag
 JOIN music_taxonomy n ON n.id=tag.node_id AND n.scope_id=:s AND NOT n.deleted
 WHERE tag.scope_id=:s AND tag.namespace='tag' GROUP BY tag.object_id
), search_names AS MATERIALIZED (
 SELECT o.id,o.kind,o.title,p.data->>'aliases' AS aliases,
 COALESCE(w.catalogue,release.catalogue) AS catalogue,release.barcode,
 genres.names AS genres,tags.names AS tags
 FROM music_objects o LEFT JOIN music_profiles p ON p.id=o.id AND p.scope_id=:s
 LEFT JOIN music_works w ON w.id=o.id LEFT JOIN music_releases release ON release.id=o.id
 LEFT JOIN search_genres genres ON genres.entity_id=o.id
 LEFT JOIN search_tags tags ON tags.object_id=o.id
 WHERE o.scope_id=:s
), search_contents AS MATERIALIZED (
 SELECT subject_id AS origin_id,object_id AS target_id FROM search_relations
 WHERE role<>'parent_recording'
 UNION SELECT t.release_id,r.object_id FROM search_tracks t JOIN search_relations r
 ON r.subject_id=t.id
), search_links AS MATERIALIZED (
 SELECT id AS origin_id,id AS target_id FROM search_names
 UNION SELECT origin_id,target_id FROM search_contents
 UNION SELECT a.origin_id,b.object_id FROM search_contents a JOIN search_names target
 ON target.id=a.target_id AND target.kind IN ('work','recording')
 JOIN search_relations b ON b.subject_id=a.target_id JOIN search_names person
 ON person.id=b.object_id AND person.kind IN ('person','group','organization')
 UNION SELECT entity_id,work_id FROM search_paths
 UNION SELECT p.entity_id,r.object_id FROM search_paths p JOIN search_relations r
 ON r.subject_id=p.work_id AND r.role IN ('composer','arranger','publisher')
 UNION SELECT object_id,subject_id FROM search_relations
 UNION SELECT parent.object_id,credit.object_id FROM search_relations parent
 JOIN search_relations credit ON credit.subject_id=parent.subject_id
 WHERE parent.role='parent_recording' AND credit.role<>'parent_recording'
 UNION SELECT r.object_id,parent.subject_id FROM search_relations r
 JOIN search_relations parent ON parent.object_id=r.subject_id
 AND parent.role IN ('work','recording','parent_work')
 UNION SELECT r.object_id,p.entity_id FROM search_relations r JOIN search_paths p
 ON p.work_id=r.subject_id
 UNION SELECT r.object_id,t.release_id FROM search_relations r JOIN search_tracks t
 ON t.id=r.subject_id
 UNION SELECT work_id,entity_id FROM search_paths
 UNION SELECT p.work_id,r.object_id FROM search_paths p JOIN search_relations r
 ON r.subject_id=p.entity_id JOIN search_names person ON person.id=r.object_id
 AND person.kind IN ('person','group','organization')
 UNION SELECT r.object_id,p.work_id FROM search_relations r JOIN search_paths p
 ON p.entity_id=r.subject_id
 JOIN search_names credited ON credited.id=r.object_id
 AND credited.kind IN ('person','group','organization')
) """


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
    c: Connection,
    scope: UUID,
    kind: str,
    title: str,
    p: Provenance,
    privacy: PersistenceMetadata,
    *,
    identifier: UUID | None = None,
) -> UUID:
    identifier = identifier or uuid4()
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
    "composer": {"person", "group", "organization"},
    "arranger": {"person"},
    "conductor": {"person"},
    "performer": {"person", "group", "organization"},
    "singer": {"person"},
    "ensemble": {"group", "organization"},
    "label": {"organization"},
    "work": {"work"},
    "recording": {"recording"},
    "reissue_of": {"release"},
    "parent_work": {"work"},
    "parent_recording": {"recording"},
    "publisher": {"organization", "group"},
    "member": {"person"},
    "former_member": {"person"},
    "chief_conductor": {"person"},
    "suborganization": {"group", "organization"},
    "collaborator": {"person", "group", "organization"},
}

# These are directional credits. A person's memberships are recorded on the group,
# and a work's performers belong to a recording, rather than the composition.
SUBJECT_ROLES = {
    "work": {"composer", "arranger", "publisher", "parent_work"},
    "recording": {
        "work",
        "composer",
        "arranger",
        "conductor",
        "performer",
        "singer",
        "ensemble",
        "parent_recording",
    },
    "release": {
        "work",
        "recording",
        "composer",
        "arranger",
        "conductor",
        "performer",
        "singer",
        "ensemble",
        "label",
        "publisher",
        "reissue_of",
    },
    "track": {
        "work",
        "recording",
        "composer",
        "arranger",
        "conductor",
        "performer",
        "singer",
        "ensemble",
    },
    "person": {"collaborator"},
    "group": {"member", "former_member", "chief_conductor", "suborganization", "collaborator"},
    "organization": {"member", "former_member", "suborganization", "collaborator"},
}


def _relation_keys(c: Connection, scope: UUID, identifier: UUID) -> set[tuple[UUID, str]]:
    return {
        (row["object_id"], row["role"])
        for row in _rows(
            c,
            "SELECT object_id,role FROM music_relations WHERE subject_id=:id AND scope_id=:s",
            id=identifier,
            s=scope,
        )
    }


def _relations(
    c: Connection,
    scope: UUID,
    identifier: UUID,
    relations: list[RelationInput],
    existing: set[tuple[UUID, str]] | None = None,
) -> None:
    subject = _object(c, scope, identifier)
    classes = music_classes.effective(
        c, scope, [identifier] + [r.object_id for r in relations if r.role in ("work", "recording")]
    )
    for relation in relations:
        target = _object(c, scope, relation.object_id)
        if relation.performance_role and (
            relation.role != PART_ROLES[relation.performance_role]
            or subject["kind"] not in ("recording", "track")
        ):
            raise ValueError("music.invalid_relation")
        unchanged = (relation.object_id, relation.role) in (existing or set())
        old_part = c.scalar(
            text(
                "SELECT performance_role FROM music_relation_details "
                "WHERE subject_id=:id AND object_id=:o AND role=:r"
            ),
            {"id": identifier, "o": relation.object_id, "r": relation.role},
        )
        if target["kind"] not in RELATION_KINDS[relation.role] or identifier == relation.object_id:
            raise ValueError("music.invalid_relation")
        if relation.role not in SUBJECT_ROLES[subject["kind"]] and (
            relation.object_id,
            relation.role,
        ) not in (existing or set()):
            raise ValueError("music.invalid_relation")
        if relation.role == "recording" and subject["kind"] not in ("release", "track"):
            raise ValueError("music.invalid_relation")
        if relation.role == "work" and subject["kind"] not in ("release", "recording", "track"):
            raise ValueError("music.invalid_relation")
        if relation.role == "parent_recording":
            if subject["kind"] != "recording":
                raise ValueError("music.invalid_relation")
            # One explicit level, reusable leaves, never title/release-based guessing.
            if c.scalar(
                text(
                    "SELECT 1 FROM music_relations r JOIN music_objects a ON a.id=r.subject_id "
                    "JOIN music_objects b ON b.id=r.object_id WHERE r.scope_id=:s "
                    "AND r.role='parent_recording' AND (r.object_id=:id OR r.subject_id=:target)"
                ),
                {"s": scope, "id": identifier, "target": relation.object_id},
            ):
                raise ValueError("music.recording_nesting")
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
        if not unchanged or classes or (relation.performance_role or "") != (old_part or ""):
            if relation.role == "conductor" and "classical" not in classes:
                raise ValueError("music.class_relation_mismatch")
            if relation.performance_role and not classes.intersection(
                music_classes.PART_CLASSES[relation.performance_role]
            ):
                raise ValueError("music.class_relation_mismatch")
        c.execute(
            text("INSERT INTO music_relations VALUES (:s,:id,:o,:r) ON CONFLICT DO NOTHING"),
            {"s": scope, "id": identifier, "o": relation.object_id, "r": relation.role},
        )
        c.execute(
            text(
                "INSERT INTO music_relation_details(scope_id,subject_id,object_id,role,"
                "started_on,ended_on,source_text,performance_role) "
                "VALUES (:s,:id,:o,:r,:start,:end,:source,:part) "
                "ON CONFLICT(subject_id,object_id,role) DO UPDATE SET "
                "started_on=:start,ended_on=:end,source_text=:source,performance_role=:part"
            ),
            {
                "s": scope,
                "id": identifier,
                "o": relation.object_id,
                "r": relation.role,
                "start": relation.started_on,
                "end": relation.ended_on,
                "source": _safe(relation.source_text),
                "part": relation.performance_role,
            },
        )


def _rating(c: Connection, scope: UUID, identifier: UUID) -> int | None:
    value = c.scalar(
        text(
            "SELECT rating FROM music_reviews WHERE scope_id=:s AND object_id=:id "
            "AND NOT EXISTS(SELECT 1 FROM music_review_deletions d "
            "WHERE d.review_id=music_reviews.id) "
            "ORDER BY coalesce(original_at,created_at) DESC,sequence DESC LIMIT 1"
        ),
        {"s": scope, "id": identifier},
    )
    return int(value) if value is not None else None


def _detail(c: Connection, scope: UUID, identifier: UUID) -> dict[str, Any]:
    compatibility = music_catalog.redirect(c, scope, identifier)
    if compatibility:
        item = _rows(
            c,
            "SELECT * FROM music_object_records WHERE id=:id AND scope_id=:s",
            id=identifier,
            s=scope,
        )[0]
        item["catalog_redirect"] = {
            key: value for key, value in compatibility.items() if key != "snapshot"
        }
    else:
        item = _object(c, scope, identifier)
    profile = c.scalar(
        text("SELECT data FROM music_profiles WHERE id=:id AND scope_id=:s"),
        {"id": identifier, "s": scope},
    )
    item.update(profile or {})
    if item["kind"] == "work":
        item["catalog_assignments"] = music_catalog.assignment(c, scope, identifier)
        item["catalog_candidates"] = music_catalog.candidates(c, scope, identifier)
    item.pop("is_album_recording", None)  # Legacy inferred grouping is still retired.
    item["image_info"] = (
        (profile or {}).get("image_attributions", {}).get(str(item["image_id"]), {})
    )
    item["image_frame"] = (profile or {}).get("image_frames", {}).get(str(item["image_id"]))
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
        "SELECT r.*,o.title,o.kind,d.started_on,d.ended_on,d.source_text,d.performance_role "
        "FROM music_relations r "
        "LEFT JOIN music_relation_details d USING(subject_id,object_id,role) JOIN "
        "music_objects o ON o.id=r.object_id WHERE r.subject_id=:id "
        "AND r.scope_id=:s",
        id=identifier,
        s=scope,
    )
    item["related"] = _rows(
        c,
        "SELECT r.role,o.id,o.title,o.kind FROM music_relations r JOIN "
        "music_objects o ON o.id=r.subject_id WHERE r.object_id=:id "
        "AND r.scope_id=:s AND r.role<>'parent_recording'",
        id=identifier,
        s=scope,
    )
    item["legacy_catalog_relations"] = _rows(
        c,
        "SELECT r.object_id,o.title,redirect.category_code,redirect.composer_id "
        "FROM music_relations r JOIN music_catalog_redirects redirect ON "
        "redirect.work_id=r.object_id "
        "AND redirect.scope_id=r.scope_id JOIN music_object_records o ON o.id=r.object_id "
        "WHERE r.subject_id=:id AND r.scope_id=:s AND r.role='work' AND o.archived",
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
        "SELECT * FROM music_reviews WHERE object_id=:id AND scope_id=:s "
        "AND NOT EXISTS(SELECT 1 FROM music_review_deletions d WHERE d.review_id=music_reviews.id) "
        "ORDER BY coalesce(original_at,created_at) DESC,sequence DESC",
        id=identifier,
        s=scope,
    )
    item["rating"] = item["reviews"][0]["rating"] if item["reviews"] else None
    item["rating_scale"] = item["reviews"][0]["rating_max"] if item["reviews"] else 7
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
    item["identities"] = music_profiles.identities(c, scope, item)
    item["music_classes"] = sorted(music_classes.effective(c, scope, [identifier]))
    item["recording_ids"] = (
        [
            row["object_id"]
            for row in _rows(
                c,
                "SELECT DISTINCT r.object_id FROM music_relations r WHERE r.scope_id=:s "
                "AND r.role='recording' AND (r.subject_id=:id OR r.subject_id IN "
                "(SELECT id FROM music_tracks WHERE release_id=:id AND scope_id=:s))",
                id=identifier,
                s=scope,
            )
        ]
        if item["kind"] == "release"
        else []
    )
    item["genres"] = _rows(
        c,
        "SELECT n.* FROM music_taxonomy n JOIN music_effective_genres g ON g.node_id=n.id "
        "WHERE g.object_id=:id AND g.scope_id=:s AND NOT n.deleted ORDER BY n.name",
        id=identifier,
        s=scope,
    )
    item["legacy_genres"] = (
        music_taxonomy.assigned(c, scope, identifier, "genre") if item["kind"] != "work" else []
    )
    item["works"] = _rows(
        c,
        "SELECT o.* FROM music_work_paths p JOIN music_objects o ON o.id=p.work_id "
        "WHERE p.entity_id=:id AND p.scope_id=:s AND o.id<>:id ORDER BY o.title",
        id=identifier,
        s=scope,
    )
    item["child_works"] = (
        _rows(
            c,
            "SELECT o.id,o.kind,o.title,o.image_id FROM music_relations r JOIN music_objects o "
            "ON o.id=r.subject_id AND o.scope_id=r.scope_id WHERE r.scope_id=:s "
            "AND r.object_id=:id AND r.role='parent_work' ORDER BY o.title,o.id",
            s=scope,
            id=identifier,
        )
        if item["kind"] == "work"
        else []
    )
    item["parent_recording_count"] = sum(
        1 for row in item["relations"] if row["role"] == "parent_recording"
    )
    if item["parent_recording_count"]:
        item["rating"] = None  # Historical reviews survive; children are not review targets.
    item["child_recording_count"] = int(
        c.scalar(
            text(
                "SELECT count(*) FROM music_relations r JOIN music_objects child "
                "ON child.id=r.subject_id "
                "WHERE r.scope_id=:s AND r.object_id=:id AND r.role='parent_recording'"
            ),
            {"s": scope, "id": identifier},
        )
        or 0
    )
    item["releases"] = _rows(
        c,
        "SELECT DISTINCT o.* FROM music_objects o WHERE o.scope_id=:s AND o.kind='release' "
        "AND (EXISTS(SELECT 1 FROM music_relations r WHERE r.subject_id=o.id "
        "AND r.object_id=:id AND r.role='recording') OR EXISTS(SELECT 1 FROM music_tracks t "
        "JOIN music_relations r ON r.subject_id=t.id WHERE t.release_id=o.id "
        "AND r.object_id=:id AND r.role='recording')) ORDER BY o.title",
        id=identifier,
        s=scope,
    )
    item["contributors"] = _rows(
        c,
        "SELECT DISTINCT o.id,o.title,o.kind,r.role,COALESCE(d.performance_role,'') "
        "AS performance_role,origin.id AS origin_id,origin.kind AS origin_kind,"
        "origin.title AS origin_title FROM music_objects o "
        "JOIN music_relations r ON r.object_id=o.id AND r.scope_id=o.scope_id "
        "JOIN music_objects origin ON origin.id=r.subject_id AND origin.scope_id=r.scope_id "
        "LEFT JOIN music_relation_details d ON d.subject_id=r.subject_id "
        "AND d.object_id=r.object_id AND d.role=r.role WHERE r.scope_id=:s "
        "AND r.role IN ('composer','arranger','conductor','performer','singer','ensemble',"
        "'label','publisher') "
        "AND (r.subject_id=:id OR (r.role IN ('composer','arranger') AND r.subject_id IN "
        "(SELECT work_id FROM music_work_paths WHERE entity_id=:id AND scope_id=:s)) "
        "OR r.subject_id IN (SELECT id FROM music_tracks WHERE release_id=:id AND scope_id=:s) "
        "OR r.subject_id IN (SELECT object_id FROM music_relations "
        "WHERE scope_id=:s AND role='recording' AND (subject_id=:id OR subject_id IN "
        "(SELECT id FROM music_tracks WHERE release_id=:id AND scope_id=:s)))) "
        "ORDER BY o.title,r.role,origin.title",
        id=identifier,
        s=scope,
    )
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
    rating_scale: int = 5,
    original_at: datetime | None = None,
    import_key: str | None = None,
) -> None:
    seq = _event(c, actor, scope, identifier, p, privacy)
    c.execute(
        text(
            "INSERT INTO music_reviews(id,scope_id,object_id,rating,comment,origin,"
            "source_id,created_by,sequence,rating_max,original_at,import_key) "
            "VALUES (:id,:s,:o,:r,:c,:origin,:p,:a,:q,:scale,:date,:key)"
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
            "scale": rating_scale,
            "date": original_at,
            "key": import_key,
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
    *,
    new_identifier: UUID | None = None,
) -> UUID:
    _safe(value.catalogue or "")
    creating = identifier is None
    identifier = identifier or _new(
        c, scope, value.kind, value.title, p, privacy, identifier=new_identifier
    )
    if _object(c, scope, identifier)["kind"] != value.kind:
        raise ValueError("music.kind_immutable")
    previous_profile = (
        c.scalar(
            text("SELECT data FROM music_profiles WHERE id=:id AND scope_id=:s"),
            {"id": identifier, "s": scope},
        )
        or {}
    )
    previous_roles = previous_profile.get("roles", [])
    if value.roles and value.roles != previous_roles:
        raise ValueError("music.identity_from_relations")
    c.execute(
        text("UPDATE music_objects SET title=:t WHERE id=:id"),
        {"id": identifier, "t": _safe(value.title.strip())},
    )
    profile = value.model_dump(
        mode="json",
        include={
            "description",
            "work_form",
            "aliases",
            "roles",
            "composed_on",
            "released_on",
            "medium",
            "edition",
            "personal_notes",
            "source_notes",
            "lyrics",
            "critical_reception",
            "recording_history",
            "music_class",
            "music_classes",
            "country",
            "born_on",
            "died_on",
            "founded_on",
            "dissolved_on",
            "official_url",
        },
    )
    # Picture credits belong to immutable image identities, not editable form data.
    for field in (
        "lyrics",
        "critical_reception",
        "recording_history",
        "music_class",
        "music_classes",
    ):
        if field not in value.model_fields_set and field in previous_profile:
            profile[field] = previous_profile[field]
    if previous_profile.get("image_attributions"):
        profile["image_attributions"] = previous_profile["image_attributions"]
    if previous_profile.get("image_frames"):
        profile["image_frames"] = previous_profile["image_frames"]
    _safe_profile(profile)
    c.execute(
        text(
            "INSERT INTO music_profiles VALUES (:id,:s,CAST(:data AS jsonb)) "
            "ON CONFLICT(id) DO UPDATE SET data=EXCLUDED.data"
        ),
        {"id": identifier, "s": scope, "data": json.dumps(profile)},
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
            text(
                "INSERT INTO music_recordings(id,recorded_on,location) VALUES (:id,:d,:l) "
                "ON CONFLICT(id) DO UPDATE SET recorded_on=:d,location=:l"
            ),
            {"id": identifier, "d": value.recorded_on, "l": _safe(value.location)},
        )
    existing_relations = _relation_keys(c, scope, identifier)
    c.execute(text("DELETE FROM music_relations WHERE subject_id=:id"), {"id": identifier})
    _relations(c, scope, identifier, value.relations, existing_relations)
    music_taxonomy.assign(c, scope, identifier, "tag", value.tag_ids)
    music_taxonomy.assign(c, scope, identifier, "identity", value.identity_ids)
    if value.kind == "work":
        classes: set[str] = {str(value) for value in [value.music_class, *value.music_classes]} - {
            ""
        }
        classes.update(
            music_classes.effective(
                c, scope, [r.object_id for r in value.relations if r.role == "parent_work"]
            )
        )
        for genre in value.genre_ids:
            required = music_classes.genre_class(c, scope, genre)
            if required and required not in classes:
                # Preserve old unclassified tags until the work is explicitly classified.
                previous = music_taxonomy.assigned(c, scope, identifier, "genre")
                if (
                    creating
                    or value.music_class
                    or value.music_classes
                    or previous_profile.get("music_classes")
                    or previous_profile.get("music_class")
                    or genre not in {node["id"] for node in previous}
                ):
                    raise ValueError("music.class_genre_mismatch")
        music_taxonomy.assign(c, scope, identifier, "genre", value.genre_ids)
        for dependent in _rows(
            c,
            "SELECT DISTINCT credit.subject_id,credit.role,d.performance_role "
            "FROM music_work_paths path JOIN music_relations credit "
            "ON credit.subject_id=path.entity_id AND credit.scope_id=path.scope_id "
            "LEFT JOIN music_relation_details d USING(subject_id,object_id,role) "
            "WHERE path.scope_id=:s AND path.work_id=:id "
            "AND (credit.role='conductor' OR COALESCE(d.performance_role,'')<>'')",
            s=scope,
            id=identifier,
        ):
            actual = music_classes.effective(c, scope, [dependent["subject_id"]])
            # Old unclassified records remain readable; an explicit class change cannot
            # leave incompatible performance credits behind.
            if (
                value.music_classes
                or value.music_class
                or previous_profile.get("music_classes")
                or previous_profile.get("music_class")
            ):
                part = dependent["performance_role"] or ""
                if (dependent["role"] == "conductor" and "classical" not in actual) or (
                    part and not actual.intersection(music_classes.PART_CLASSES[part])
                ):
                    raise ValueError("music.class_relation_mismatch")
    # Tracks are created once; stable track IDs and their histories are never replaced.
    for track in value.tracks:
        if track.id:
            raise ValueError("music.use_track_save")
        if c.scalar(
            text(
                "SELECT 1 FROM music_tracks WHERE release_id=:id AND disc=:disc AND position=:pos"
            ),
            {"id": identifier, "disc": track.disc, "pos": track.position},
        ):
            raise ValueError("music.duplicate_track_position")
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
        music_taxonomy.assign(c, scope, track_id, "tag", track.tag_ids)
        track_profile = track.model_dump(
            include={
                "description",
                "source_notes",
                "personal_notes",
                "lyrics",
                "critical_reception",
            }
        )
        _safe(json.dumps(track_profile, ensure_ascii=False))
        c.execute(
            text("INSERT INTO music_profiles VALUES (:id,:s,CAST(:data AS jsonb))"),
            {"id": track_id, "s": scope, "data": json.dumps(track_profile)},
        )
        _snapshot(c, actor, scope, track_id, "created", p, privacy, advance=False)
    if value.kind == "work" and "classical" in [
        profile.get("music_class"),
        *profile.get("music_classes", []),
    ]:
        _safe(value.catalog_source)
        music_catalog.seed_genre_map(c, scope)
        music_catalog.maintain(
            c,
            actor,
            scope,
            identifier,
            value.catalog_primary,
            value.catalog_auxiliary,
            value.catalog_source,
            p.source_id,
        )
    elif value.catalog_primary is not None:
        raise ValueError("music.catalog_classical_only")
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


READ_ACTIONS = {
    "browse-context",
    "catalog-candidates",
    "catalog-tree",
    "catalog-categories",
    "list",
    "relation-options",
    "profile-sections",
    "relation-context",
    "detail",
    "export",
    "stats",
    "listening",
    "taxonomy",
    "image-read",
    "review-export",
    "review-import-preview",
}


def _editable_relations(item: dict[str, Any]) -> list[RelationInput]:
    return [
        RelationInput.model_validate({k: row[k] for k in RelationInput.model_fields if k in row})
        for row in item["relations"]
    ]


def _consolidate_recordings(
    c: Connection, actor: UUID, scope: UUID, r: MusicRequest
) -> dict[str, Any]:
    """Explicit, transactional repair of synthetic track-level recording records.

    No release/title-based guessing. Existing personal history blocks automatic
    consolidation; source records and archival snapshots remain recoverable.
    """
    if not r.id or not r.object_ids or not r.reason.strip():
        raise ValueError("music.consolidation_required")
    identifiers = set(r.object_ids)
    if r.id in identifiers or set(r.source_revisions) != identifiers:
        raise ValueError("music.consolidation_required")
    target = _detail(c, scope, r.id)
    parts = [_detail(c, scope, identifier) for identifier in sorted(identifiers)]
    if c.scalar(
        text(
            "SELECT 1 FROM music_relations WHERE scope_id=:s AND role='parent_recording' "
            "AND (subject_id=ANY(:ids) OR object_id=ANY(:ids)) LIMIT 1"
        ),
        {"s": scope, "ids": [r.id, *identifiers]},
    ):
        raise ValueError("music.recording_group_in_use")
    if target["kind"] != "recording" or any(part["kind"] != "recording" for part in parts):
        raise ValueError("music.invalid_relation")
    if target["revision"] != r.revision or any(
        part["revision"] != r.source_revisions[part["id"]] for part in parts
    ):
        raise ValueError("music.revision_conflict")
    if any(
        part.get(key)
        for part in parts
        for key in ("reviews", "memberships", "listening", "personal_notes", "tags")
    ):
        raise ValueError("music.consolidation_personal_history")
    # Check all incoming references before changing anything, including references
    # that cannot be represented as release containment or track credits.
    incoming = {
        row["id"]: _detail(c, scope, row["id"]) for part in parts for row in part["related"]
    }
    if any(item["kind"] not in ("release", "track") for item in incoming.values()):
        raise ValueError("music.invalid_relation")
    p, privacy = _provenance(c, actor, scope, "Music recording consolidation", r.reason)

    def unique(relations: list[RelationInput]) -> list[RelationInput]:
        result: dict[tuple[UUID, str], RelationInput] = {}
        for relation in relations:
            result.setdefault((relation.object_id, relation.role), relation)
        return list(result.values())

    values = {key: target[key] for key in ObjectInput.model_fields if key in target}
    values.update(tracks=[], music_class="", music_classes=[], composed_on="")
    relations = _editable_relations(target)
    for part in parts:
        relations += _editable_relations(part)
    for relation in relations:
        relation.source_text = relation.source_text.replace("相应子录音", "发行曲目")
    values["relations"] = unique(relations)
    values["tag_ids"] = [node["id"] for node in target["tags"]]
    values["aliases"] = list(
        dict.fromkeys(
            [
                *target.get("aliases", []),
                *(part["title"] for part in parts),
                *(alias for part in parts for alias in part.get("aliases", [])),
            ]
        )
    )
    correction = "录音按完整收录范围建档；逐曲作品和演出分工保存在发行曲目中，不再产生子录音。"
    values["source_notes"] = (target.get("source_notes", "") + "\n" + correction).strip()
    values["description"] = target.get("description", "").replace(
        "逐曲人员保留在子录音中", "逐曲人员保留在发行曲目中"
    )
    _save(c, actor, scope, ObjectInput.model_validate(values), p, privacy, r.id)
    for part in parts:
        for source in part["sources"]:
            c.execute(
                text(
                    "INSERT INTO music_playback_sources(id,scope_id,object_id,url,platform) "
                    "VALUES (:id,:s,:o,:url,:platform) ON CONFLICT(object_id,url) DO NOTHING"
                ),
                dict(id=uuid4(), s=scope, o=r.id, url=source["url"], platform=source["platform"]),
            )
    # Preserve exact track attribution before replacing a recording reference.
    part_lookup = {part["id"]: part for part in parts}
    for item in incoming.values():
        previous = _relation_keys(c, scope, item["id"])
        relations = _editable_relations(item)
        extras = []
        for relation in relations:
            if relation.object_id not in identifiers:
                continue
            if relation.role != "recording":
                raise ValueError("music.invalid_relation")
            if item["kind"] == "track":
                part = part_lookup[relation.object_id]
                extras += _editable_relations(part)
                for source in part["sources"]:
                    c.execute(
                        text(
                            "INSERT INTO music_playback_sources"
                            "(id,scope_id,object_id,url,platform) "
                            "VALUES (:id,:s,:o,:url,:platform) "
                            "ON CONFLICT(object_id,url) DO NOTHING"
                        ),
                        dict(
                            id=uuid4(),
                            s=scope,
                            o=item["id"],
                            url=source["url"],
                            platform=source["platform"],
                        ),
                    )
            relation.object_id = r.id
        c.execute(
            text("DELETE FROM music_relations WHERE subject_id=:id AND scope_id=:s"),
            dict(id=item["id"], s=scope),
        )
        _relations(c, scope, item["id"], unique(relations + extras), previous)
        _snapshot(c, actor, scope, item["id"], "music.recording_consolidated", p, privacy)
    _snapshot(c, actor, scope, r.id, "music.recording_consolidated", p, privacy)
    for identifier in identifiers:
        _snapshot(c, actor, scope, identifier, "music.archived_consolidated", p, privacy)
    c.execute(
        text("UPDATE music_object_records SET archived=true WHERE scope_id=:s AND id=ANY(:ids)"),
        dict(s=scope, ids=list(identifiers)),
    )
    return {
        "item": _detail(c, scope, r.id),
        "archived": len(identifiers),
        "references_updated": len(incoming),
    }


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
        if action == "catalog-candidates":
            return {"codes": music_catalog.proposed(c, scope, r.catalog_genre_ids)}
        if action == "catalog-tree":
            if r.composer_id and _object(c, scope, r.composer_id)["kind"] != "person":
                raise ValueError("music.catalog_composer_only")
            return music_catalog.tree(c, scope, r.composer_id)
        if action == "catalog-categories":
            return {"items": music_catalog.categories(c)}
        if (
            action
            in {
                "review",
                "review-history-add",
                "review-import",
                "review-import-preview",
                "review-delete",
            }
            and r.id
        ):
            if _object(c, scope, r.id)["kind"] == "recording" and c.scalar(
                text(
                    "SELECT 1 FROM music_relations parent JOIN music_objects active "
                    "ON active.id=parent.object_id WHERE parent.scope_id=:s "
                    "AND parent.subject_id=:id AND parent.role='parent_recording' LIMIT 1"
                ),
                {"s": scope, "id": r.id},
            ):
                raise ValueError("music.child_recording_review_disabled")
        if action == "recording-consolidate":
            return _consolidate_recordings(c, actor, scope, r)
        if action == "relation-context":
            ids = [
                relation.object_id
                for relation in r.relations
                if relation.role in ("work", "recording", "parent_work")
            ]
            for identifier in ids:
                _object(c, scope, identifier)
            return {
                "classes": sorted(music_classes.effective(c, scope, ids)),
                "identity_catalogue": ui_catalogue(),
            }
        if action == "profile-sections":
            if r.id is None:
                raise ValueError("music.id_required")
            if _object(c, scope, r.id)["kind"] not in ("person", "group", "organization"):
                raise ValueError("music.identity_people_only")
            return {"items": music_profiles.sections(c, scope, r.id, r.root_works)}
        if action == "relation-options":
            # The editor needs names, not each candidate's complete history and tracks.
            where = ["o.scope_id=:s", "o.kind=ANY(:kinds)"]
            candidate_params: dict[str, Any] = {
                "s": scope,
                "kinds": r.kinds or [r.kind],
                "offset": r.offset,
                "limit": r.limit,
            }
            if r.relation_role:
                candidate_params["kinds"] = sorted(
                    set(candidate_params["kinds"]) & RELATION_KINDS[r.relation_role]
                )
            if r.relation_role == "parent_recording":
                where.append(
                    "o.kind='recording' AND NOT EXISTS(SELECT 1 FROM music_relations r "
                    "JOIN music_objects a ON a.id=r.object_id "
                    "WHERE r.subject_id=o.id AND r.scope_id=:s "
                    "AND r.role='parent_recording')"
                )
            if r.id:
                where.append("o.id<>:id")
                candidate_params["id"] = r.id
            if r.query.strip():
                for index, term in enumerate(r.query.split()):
                    parameter = f"search_{index}"
                    where.append(
                        f"(o.title ILIKE :{parameter} OR p.data->>'aliases' ILIKE :{parameter})"
                    )
                    candidate_params[parameter] = _search_pattern(term)
            candidate_tables = (
                "music_objects o LEFT JOIN music_profiles p "
                "ON p.id=o.id AND p.scope_id=o.scope_id WHERE " + " AND ".join(where)
            )
            candidate_total = int(
                c.execute(
                    text("SELECT count(*) FROM " + candidate_tables), candidate_params
                ).scalar_one()
            )
            rows = _rows(
                c,
                "SELECT o.id,o.kind,o.title,COALESCE(p.data->'aliases','[]'::jsonb) "
                "AS aliases FROM "
                + candidate_tables
                + " ORDER BY o.title,o.id LIMIT :limit OFFSET :offset",
                **candidate_params,
            )
            return {
                "items": [{**row, "id": str(row["id"])} for row in rows],
                "total": candidate_total,
                "offset": r.offset,
                "limit": r.limit,
            }
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
        if action == "taxonomy-delete":
            if r.id is None:
                raise ValueError("music.id_required")
            return music_taxonomy.delete_node(c, actor, scope, r.id, r.revision)
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
            if r.sort == "catalog_order" and (r.kind != "work" or r.cross_library):
                raise ValueError("music.invalid_filter")
            if r.catalog_category or r.composer_id or r.catalog_unknown_composer:
                if r.kind != "work" or r.cross_library or r.parent_work_id:
                    raise ValueError("music.invalid_filter")
                if r.composer_id:
                    if _object(c, scope, r.composer_id)["kind"] != "person":
                        raise ValueError("music.catalog_composer_only")
                if r.catalog_category not in {
                    None,
                    *[n["code"] for n in music_catalog.categories(c)],
                }:
                    raise ValueError("music.catalog_invalid_category")
            if r.sort == "work_order" and not r.parent_work_id:
                raise ValueError("music.invalid_filter")
            if r.sort == "recording_order" and not r.parent_recording_id:
                raise ValueError("music.invalid_filter")
            where = ["o.scope_id=:s"]
            if r.root_recordings:
                if r.parent_recording_id or r.cross_library or r.root_works:
                    raise ValueError("music.invalid_filter")
                where.append(
                    "o.kind='recording' AND NOT EXISTS(SELECT 1 FROM music_relations parent "
                    "JOIN music_objects active ON active.id=parent.object_id "
                    "WHERE parent.subject_id=o.id AND parent.scope_id=:s "
                    "AND parent.role='parent_recording')"
                )
            prefix = ""
            if not r.cross_library and not r.profile_id:
                where.append("o.kind=ANY(:kinds)")
            params: dict[str, Any] = {
                "s": scope,
                "k": r.kind,
                "kinds": r.kinds or [r.kind],
                "st": r.status,
                "tag": r.tag_id,
                "genre": r.genre_id,
                "offset": r.offset,
                "limit": r.limit,
                "lib": r.view,
            }
            if r.sort == "recording_order":
                params["recording_number_pattern"] = "(?i)(?:No[.]?|Nr[.]?|第)[[:space:]]*([0-9]+)"
            if r.catalog_category or r.composer_id or r.catalog_unknown_composer:
                params.update(
                    catalog=r.catalog_category,
                    composer=r.composer_id,
                    catalog_prefix=(r.catalog_category or "") + ".%",
                )
                constraints = [
                    "w.kind='work'",
                    "(wp.data->>'music_class'='classical' OR wp.data->'music_classes' ? "
                    "'classical')",
                ]
                if r.catalog_category:
                    constraints.append(
                        "(coalesce(ca.category_code,'unclassified')=:catalog OR "
                        "ca.category_code LIKE :catalog_prefix)"
                    )
                if r.composer_id:
                    constraints.append(
                        "EXISTS(SELECT 1 FROM music_relations cr WHERE cr.scope_id=:s "
                        "AND cr.subject_id=w.id AND cr.object_id=:composer AND cr.role='composer')"
                    )
                if r.catalog_unknown_composer:
                    constraints.append(
                        "NOT EXISTS(SELECT 1 FROM music_relations cr WHERE cr.scope_id=:s "
                        "AND cr.subject_id=w.id AND cr.role='composer')"
                    )
                where.append(
                    "o.id IN (SELECT "
                    + ("path.work_id" if r.root_works else "w.id")
                    + " FROM music_objects w JOIN music_profiles wp ON wp.id=w.id "
                    "LEFT JOIN music_catalog_assignments ca ON ca.work_id=w.id AND ca.is_primary "
                    "AND ca.scope_id=w.scope_id "
                    + (
                        "JOIN music_work_paths path ON path.entity_id=w.id AND "
                        "path.scope_id=w.scope_id "
                        if r.root_works
                        else ""
                    )
                    + "WHERE w.scope_id=:s AND "
                    + " AND ".join(constraints)
                    + ")"
                )
            if r.profile_id:
                if _object(c, scope, r.profile_id)["kind"] not in (
                    "person",
                    "group",
                    "organization",
                ):
                    raise ValueError("music.identity_people_only")
                prefix = music_profiles.PROFILE_CTE
                params.update(profile=r.profile_id, section=r.profile_section)
                if r.root_works and r.profile_section == "work":
                    where.append(
                        "o.id IN (SELECT p.work_id FROM music_work_paths p JOIN profile_items i "
                        "ON p.entity_id=i.id WHERE p.scope_id=:s AND i.section='work')"
                    )
                else:
                    where.append("o.id IN (SELECT id FROM profile_items WHERE section=:section)")
            if r.root_works:
                if r.parent_work_id or r.cross_library:
                    raise ValueError("music.invalid_filter")
                where.append(
                    "o.kind='work' AND NOT EXISTS(SELECT 1 FROM music_relations parent "
                    "WHERE parent.subject_id=o.id AND parent.scope_id=:s "
                    "AND parent.role='parent_work')"
                )
            if r.parent_work_id:
                if _object(c, scope, r.parent_work_id)["kind"] != "work":
                    raise ValueError("music.invalid_relation")
                params["parent_work"] = r.parent_work_id
                where.append(
                    "o.kind='work' AND EXISTS(SELECT 1 FROM music_relations parent "
                    "WHERE parent.subject_id=o.id AND parent.scope_id=:s "
                    "AND parent.object_id=:parent_work AND parent.role='parent_work')"
                )
            if r.parent_recording_id:
                if _object(c, scope, r.parent_recording_id)["kind"] != "recording":
                    raise ValueError("music.invalid_relation")
                params["parent_recording"] = r.parent_recording_id
                where.append(
                    "o.kind='recording' AND EXISTS(SELECT 1 FROM music_relations parent "
                    "WHERE parent.subject_id=o.id AND parent.scope_id=:s "
                    "AND parent.object_id=:parent_recording AND parent.role='parent_recording')"
                )
            if r.work_id:
                if _object(c, scope, r.work_id)["kind"] != "work":
                    raise ValueError("music.invalid_relation")
                params["work"] = r.work_id
                if not r.include_child_works and (r.kinds or [r.kind]) == ["recording"]:
                    params["complete_recordings"] = WorkCoverage(c, scope).complete_recordings(
                        r.work_id
                    )
                    where.append(
                        "o.id=ANY(:complete_recordings)"
                    )
                else:
                    where.append(
                        "EXISTS(SELECT 1 FROM music_work_paths p WHERE p.entity_id=o.id "
                        "AND p.scope_id=:s AND p.work_id=:work)"
                    )
            if r.recording_works_id:
                if r.kind != "work" or r.cross_library:
                    raise ValueError("music.invalid_filter")
                coverage = WorkCoverage(c, scope)
                params["recording_works"] = coverage.display_works(r.recording_works_id)
                where.append("o.id=ANY(:recording_works)")
            if r.scope_recording_id:
                if r.kind != "work" or not r.parent_work_id or r.cross_library:
                    raise ValueError("music.invalid_filter")
                params["scoped_children"] = WorkCoverage(c, scope).child_range(
                    r.parent_work_id, r.scope_recording_id
                )
                where.append("o.id=ANY(:scoped_children)")
            if r.recording_id:
                if _object(c, scope, r.recording_id)["kind"] != "recording":
                    raise ValueError("music.invalid_relation")
                params["recording"] = r.recording_id
                where.append(
                    "o.id IN (SELECT linked.subject_id FROM music_relations linked "
                    "WHERE linked.scope_id=:s AND linked.object_id=:recording "
                    "AND linked.role='recording' UNION "
                    "SELECT track.release_id FROM music_relations linked "
                    "JOIN music_tracks track ON track.id=linked.subject_id "
                    "AND track.scope_id=linked.scope_id WHERE linked.scope_id=:s "
                    "AND linked.object_id=:recording AND linked.role='recording')"
                )
            if r.identity_id:
                node = music_taxonomy.get_node(c, scope, r.identity_id)
                if node["namespace"] != "identity":
                    raise ValueError("music.taxonomy_namespace")
                ids = [
                    row["id"]
                    for row in _rows(
                        c,
                        "WITH RECURSIVE tree AS (SELECT id FROM music_taxonomy "
                        "WHERE id=:id AND scope_id=:s AND NOT deleted UNION SELECT n.id "
                        "FROM music_taxonomy n JOIN tree t ON n.parent_id=t.id "
                        "WHERE n.scope_id=:s AND NOT n.deleted) SELECT id FROM tree",
                        id=r.identity_id,
                        s=scope,
                    )
                ]
                params["identities"] = ids
                where.append(
                    "o.kind IN ('person','group','organization') AND ("
                    "EXISTS(SELECT 1 FROM music_taxonomy_links l WHERE l.object_id=o.id "
                    "AND l.scope_id=:s AND l.namespace='identity' "
                    "AND l.node_id=ANY(:identities)) OR "
                    + music_profiles.identity_condition(scope, ids)
                    + ")"
                )
            search_conditions, search_matches = [], []
            for index, term in enumerate(r.query.split()):
                parameter = f"search_{index}"
                params[parameter] = _search_pattern(term)
                search_matches.append(_search_matches(parameter))
                condition = _search_condition("o", parameter)
                if r.root_works:
                    condition = (
                        "(" + condition + " OR o.id IN (SELECT path.work_id FROM search_paths path "
                        "JOIN music_objects child ON child.id=path.entity_id "
                        "AND child.scope_id=path.scope_id WHERE path.scope_id=:s "
                        "AND child.kind='work' AND child.id<>path.work_id AND "
                        + _search_condition("child", parameter)
                        + "))"
                    )
                search_conditions.append(condition)
            if search_conditions:
                c.execute(text("SET LOCAL jit = off"))
                # Match the scoped association graph once, then reuse IDs for
                # exact totals and the filtered page within this transaction.
                params["search_ids"] = list(
                    c.execute(
                        text(
                            "WITH "
                            + SEARCH_CTES.rstrip()
                            + ", "
                            + ", ".join(search_matches)
                            + " SELECT o.id FROM search_names o WHERE "
                            + " AND ".join(search_conditions)
                        ),
                        params,
                    ).scalars()
                )
                where.append("o.id=ANY(:search_ids)")
            if r.status:
                where.append("release.status=:st")
            if r.person_id:
                if _object(c, scope, r.person_id)["kind"] not in (
                    "person",
                    "group",
                    "organization",
                ):
                    raise ValueError("music.identity_people_only")
                selection = "SELECT id FROM profile_items"
                if r.root_works:
                    selection = (
                        "SELECT path.work_id FROM music_work_paths path JOIN profile_items i "
                        "ON i.id=path.entity_id WHERE path.scope_id=:s AND i.section='work'"
                    )
                where.append(
                    "o.id IN ("
                    + music_profiles.PROFILE_CTE.replace(":profile", ":filter_person")
                    + selection
                    + ")"
                )
                params["filter_person"] = r.person_id
            if r.date_from or r.date_to:
                if r.date_from and r.date_to and r.date_from > r.date_to:
                    raise ValueError("music.invalid_filter")
                where.append(
                    "COALESCE(rec.recorded_on,CAST(profile.data->>'released_on' AS date)) "
                    "BETWEEN :date_from AND :date_to"
                )
                params.update(date_from=r.date_from or date.min, date_to=r.date_to or date.max)
            if r.untagged:
                if r.tag_id:
                    raise ValueError("music.invalid_filter")
                where.append(
                    "NOT EXISTS(SELECT 1 FROM music_taxonomy_links l JOIN music_taxonomy n "
                    "ON n.id=l.node_id WHERE l.object_id=o.id AND l.namespace='tag' "
                    "AND NOT n.deleted)"
                )
            for node_id, namespace, parameter in [
                (r.tag_id, "tag", "tag"),
                (r.genre_id, "genre", "genre"),
            ]:
                if node_id:
                    if music_taxonomy.get_node(c, scope, node_id)["namespace"] != namespace:
                        raise ValueError("music.taxonomy_namespace")
                    links = (
                        "music_effective_genres" if namespace == "genre" else "music_taxonomy_links"
                    )
                    target = "l.object_id=o.id"
                    if r.root_works:
                        target = (
                            "(l.object_id=o.id OR l.object_id IN (SELECT path.entity_id "
                            "FROM music_work_paths path JOIN music_objects child "
                            "ON child.id=path.entity_id AND child.scope_id=path.scope_id "
                            "WHERE path.scope_id=:s AND path.work_id=o.id AND child.kind='work'))"
                        )
                    where.append(
                        f"EXISTS(SELECT 1 FROM {links} l WHERE "
                        + target
                        + " AND l.scope_id=:s AND l.node_id IN (WITH RECURSIVE tree AS ("
                        f"SELECT id FROM music_taxonomy WHERE id=:{parameter} "
                        "AND scope_id=:s AND NOT deleted "
                        "UNION SELECT n.id FROM music_taxonomy n JOIN tree t ON n.parent_id=t.id "
                        "WHERE n.scope_id=:s AND NOT n.deleted) SELECT id FROM tree))"
                    )
            if (
                r.view == "five-star"
                or r.unrated
                or r.rating_min is not None
                or r.rating_max is not None
            ):
                where.append(
                    "NOT EXISTS(SELECT 1 FROM music_relations parent JOIN music_objects active "
                    "ON active.id=parent.object_id WHERE parent.subject_id=o.id "
                    "AND parent.scope_id=:s AND parent.role='parent_recording')"
                )
            if r.view == "five-star":
                where.append("review.rating=5 AND review.rating_max=5")
            if r.unrated:
                where.append(
                    "o.kind IN ('recording','release') AND "
                    "(review.rating IS NULL OR review.rating_max<>7)"
                )
            if r.rating_min is not None or r.rating_max is not None:
                if r.kind not in ("recording", "release") and not r.cross_library:
                    raise ValueError("music.rating_recording_release_only")
                if (
                    r.rating_min is not None
                    and r.rating_max is not None
                    and r.rating_min > r.rating_max
                ):
                    raise ValueError("music.invalid_filter")
                where.append(
                    "o.kind IN ('recording','release') AND ((review.rating_max=7 "
                    "AND review.rating BETWEEN :rmin AND :rmax) OR (:rmin=0 AND "
                    "(review.rating IS NULL OR review.rating_max<>7)))"
                )
                params.update(
                    rmin=r.rating_min if r.rating_min is not None else 0,
                    rmax=r.rating_max if r.rating_max is not None else 7,
                )
            if r.view in ("featured", "frequent"):
                where.append(
                    "EXISTS(SELECT 1 FROM music_memberships m WHERE m.object_id=o.id "
                    "AND m.library=:lib AND m.active AND m.sequence=(SELECT max(sequence) "
                    "FROM music_memberships newer WHERE newer.object_id=o.id "
                    "AND newer.library=m.library))"
                )
            table_joins = (
                " FROM music_objects o LEFT JOIN music_releases release ON release.id=o.id "
                "LEFT JOIN music_recordings rec ON rec.id=o.id "
                "LEFT JOIN music_profiles profile ON profile.id=o.id "
                "LEFT JOIN LATERAL (SELECT rating,rating_max FROM music_reviews "
                "WHERE object_id=o.id "
                "AND NOT EXISTS(SELECT 1 FROM music_review_deletions d "
                "WHERE d.review_id=music_reviews.id) "
                "ORDER BY coalesce(original_at,created_at) DESC,sequence DESC LIMIT 1) "
                "review ON true "
            )
            joins = table_joins + "WHERE " + " AND ".join(where)
            ordering = {
                "catalog_order": "music_catalog_sort_key(o.title) ASC",
                "newest": "o.created_at DESC",
                "updated": "o.updated_at DESC",
                "title_asc": "o.title ASC",
                "title_desc": "o.title DESC",
                "date_desc": "COALESCE(rec.recorded_on,CAST(profile.data->>'released_on' AS date),"
                "make_date(release.release_year,1,1)) DESC NULLS LAST",
                "date_asc": "COALESCE(rec.recorded_on,CAST(profile.data->>'released_on' AS date),"
                "make_date(release.release_year,1,1)) ASC NULLS LAST",
                "rating_desc": "(review.rating::numeric/review.rating_max) DESC NULLS LAST",
                "recording_order": (
                    "(SELECT min(t.disc*1001+t.position) FROM music_tracks t "
                    "JOIN music_relations tr ON tr.subject_id=t.id AND tr.scope_id=t.scope_id "
                    "AND tr.role='recording' AND tr.object_id=o.id WHERE t.scope_id=:s "
                    "AND EXISTS(SELECT 1 FROM music_relations parent WHERE parent.scope_id=:s "
                    "AND parent.subject_id=t.release_id AND parent.role='recording' "
                    "AND parent.object_id=:parent_recording)) ASC NULLS LAST,"
                    "COALESCE(substring(o.title FROM :recording_number_pattern)::numeric,"
                    "(SELECT min(substring(w.catalogue FROM '[0-9]+')::numeric) "
                    "FROM music_relations wr JOIN music_works w ON w.id=wr.object_id "
                    "JOIN music_objects wo ON wo.id=w.id AND wo.scope_id=wr.scope_id "
                    "WHERE wr.scope_id=:s "
                    "AND wr.subject_id=o.id AND wr.role='work'),"
                    "substring(o.title FROM '[0-9]+')::numeric) ASC NULLS LAST,o.title ASC"
                ),
                "work_order": (
                    "(SELECT (data->>'browse_order')::integer FROM music_profiles "
                    "WHERE id=o.id AND scope_id=:s) ASC NULLS LAST,"
                    "(CASE WHEN (SELECT data->>'work_form' FROM music_profiles "
                    "WHERE id=:parent_work AND scope_id=:s) IN ('collection','folder') "
                    "THEN NULL ELSE "
                    "(SELECT min(t.disc*1001+t.position) FROM music_tracks t "
                    "JOIN music_relations tw ON tw.subject_id=t.id AND tw.scope_id=t.scope_id "
                    "AND tw.role='work' AND tw.object_id=o.id WHERE t.scope_id=:s AND EXISTS("
                    "SELECT 1 FROM music_work_paths p WHERE p.entity_id=t.release_id "
                    "AND p.scope_id=:s AND p.work_id=:parent_work)) END) ASC NULLS LAST,"
                    "(SELECT substring(w.catalogue FROM '[0-9]+')::numeric FROM music_works w "
                    "WHERE w.id=o.id) ASC NULLS LAST,"
                    "substring(o.title FROM '[0-9]+')::numeric ASC NULLS LAST,o.title ASC"
                ),
            }[r.sort]
            if r.root_works and r.sort in {"newest", "updated", "title_asc"}:
                ordering = (
                    "CASE WHEN profile.data->>'work_form' IN ('folder','collection','album') "
                    "THEN 0 ELSE 1 END ASC," + ordering
                )
            if search_conditions:
                params["rank_exact"] = " ".join(r.query.split())
                params["rank_prefix"] = _search_pattern(params["rank_exact"])[1:]
                title_match = " AND ".join(
                    f"o.title ILIKE :search_{index}" for index, _ in enumerate(r.query.split())
                )
                alias_match = " AND ".join(
                    f"profile.data->>'aliases' ILIKE :search_{index}"
                    for index, _ in enumerate(r.query.split())
                )
                ordering = (
                    "CASE WHEN lower(trim(o.title))=lower(:rank_exact) OR EXISTS("
                    "SELECT 1 FROM jsonb_array_elements_text("
                    "COALESCE(profile.data->'aliases','[]'::jsonb)) alias(name) "
                    "WHERE lower(trim(alias.name))=lower(:rank_exact)) THEN 0 "
                    "WHEN o.title ILIKE :rank_prefix THEN 1 "
                    f"WHEN ({title_match}) THEN 2 WHEN ({alias_match}) THEN 3 ELSE 4 END ASC,"
                    + ordering
                )
            total = c.scalar(text(prefix + "SELECT count(*)" + joins), params)
            items = _rows(
                c,
                prefix + "SELECT o.*,release.status,release.release_year,CASE WHEN EXISTS("
                "SELECT 1 FROM music_relations parent JOIN music_objects active "
                "ON active.id=parent.object_id WHERE parent.subject_id=o.id AND parent.scope_id=:s "
                "AND parent.role='parent_recording') THEN NULL ELSE review.rating END AS rating,"
                "review.rating_max AS rating_scale,profile.data->'image_frames'->"
                "CAST(o.image_id AS text) AS image_frame,"
                "(SELECT count(*) FROM music_relations child "
                "WHERE child.object_id=o.id AND child.scope_id=:s "
                "AND child.role='parent_work') AS child_work_count,"
                "(SELECT count(*) FROM music_relations child JOIN music_objects active "
                "ON active.id=child.subject_id WHERE child.object_id=o.id AND child.scope_id=:s "
                "AND child.role='parent_recording') AS child_recording_count,"
                "(SELECT count(*) FROM music_relations parent JOIN music_objects active "
                "ON active.id=parent.object_id WHERE parent.subject_id=o.id AND parent.scope_id=:s "
                "AND parent.role='parent_recording') AS parent_recording_count,"
                "profile.data->>'work_form' AS work_form,COALESCE(profile.data->'music_classes',"
                "jsonb_build_array(profile.data->>'music_class')) AS "
                "music_classes,"
                "(SELECT jsonb_agg(jsonb_build_object('id',person.id,'title',person.title)) "
                "FROM music_relations composer JOIN music_objects person "
                "ON person.id=composer.object_id AND person.scope_id=composer.scope_id "
                "WHERE composer.subject_id=o.id AND composer.scope_id=o.scope_id "
                "AND composer.role='composer') AS catalog_composers,"
                "(SELECT jsonb_agg(jsonb_build_object('category_code',a.category_code,"
                "'name_zh',n.name_zh,'name_en',n.name_en,'parent_code',n.parent_code,"
                "'is_primary',a.is_primary)) "
                "FROM music_catalog_assignments a JOIN music_catalog_categories n ON "
                "n.code=a.category_code "
                "WHERE a.work_id=o.id AND a.scope_id=o.scope_id) AS catalog_assignments"
                + joins
                + " ORDER BY "
                + ordering
                + ",o.id LIMIT :limit OFFSET :offset",
                **params,
            )
            return {"items": items, "total": total, "offset": r.offset}
        if action == "browse-context":
            if r.id is None:
                raise ValueError("music.id_required")
            return WorkCoverage(c, scope).release_range(r.id, r.work_id, r.recording_id)
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
        if action == "listening":
            try:
                ZoneInfo(r.timezone)
            except (KeyError, ValueError):
                raise ValueError("music.invalid_timezone") from None
            return {
                "items": _rows(
                    c,
                    "SELECT e.*,o.title,o.kind "
                    "FROM music_listening_events e JOIN music_objects o ON o.id=e.object_id "
                    "WHERE e.scope_id=:s ORDER BY e.started_at DESC LIMIT :limit OFFSET :offset",
                    s=scope,
                    limit=r.limit,
                    offset=r.offset,
                ),
                "daily": _rows(
                    c,
                    "SELECT (started_at AT TIME ZONE :zone)::date AS day,count(*) AS events,"
                    "sum(actual_seconds) AS actual_seconds FROM music_listening_events "
                    "WHERE scope_id=:s GROUP BY day ORDER BY day DESC LIMIT 90",
                    s=scope,
                    zone=r.timezone,
                ),
                "weekly": _rows(
                    c,
                    "SELECT date_trunc('week',started_at AT TIME ZONE :zone)::date AS day,"
                    "count(*) AS events,sum(actual_seconds) AS actual_seconds "
                    "FROM music_listening_events WHERE scope_id=:s GROUP BY day "
                    "ORDER BY day DESC LIMIT 52",
                    s=scope,
                    zone=r.timezone,
                ),
                "monthly": _rows(
                    c,
                    "SELECT date_trunc('month',started_at AT TIME ZONE :zone)::date AS day,"
                    "count(*) AS events,sum(actual_seconds) AS actual_seconds "
                    "FROM music_listening_events WHERE scope_id=:s GROUP BY day "
                    "ORDER BY day DESC LIMIT 24",
                    s=scope,
                    zone=r.timezone,
                ),
                "entities": _rows(
                    c,
                    "WITH owners AS (SELECT id AS event_id,object_id AS owner_id "
                    "FROM music_listening_events WHERE scope_id=:s UNION "
                    "SELECT e.id,p.work_id FROM music_listening_events e JOIN music_work_paths p "
                    "ON p.entity_id=e.object_id AND p.scope_id=e.scope_id WHERE e.scope_id=:s "
                    "UNION SELECT e.id,r.object_id FROM music_listening_events e "
                    "JOIN music_relations r ON r.subject_id=e.object_id AND r.scope_id=e.scope_id "
                    "WHERE e.scope_id=:s AND r.role IN "
                    "('performer','singer','conductor','ensemble')) "
                    "SELECT o.id,o.title,o.kind,count(*) AS events FROM owners a "
                    "JOIN music_objects o ON o.id=a.owner_id GROUP BY o.id,o.title,o.kind "
                    "ORDER BY events DESC,o.title LIMIT 100",
                    s=scope,
                ),
            }
        if action == "archive":
            if not r.object_ids or not r.reason.strip():
                raise ValueError("music.archive_reason_required")
            identifiers = set(r.object_ids)
            for identifier in list(identifiers):
                _object(c, scope, identifier)
                identifiers.update(
                    row["id"]
                    for row in _rows(
                        c,
                        "SELECT t.id FROM music_tracks t JOIN music_objects o ON o.id=t.id "
                        "WHERE t.release_id=:id AND o.scope_id=:s",
                        id=identifier,
                        s=scope,
                    )
                )
            if c.scalar(
                text(
                    "SELECT 1 FROM music_relations r JOIN music_objects o ON o.id=r.subject_id "
                    "WHERE r.scope_id=:s AND r.object_id=ANY(:ids) "
                    "AND NOT r.subject_id=ANY(:ids) LIMIT 1"
                ),
                {"s": scope, "ids": list(identifiers)},
            ):
                raise ValueError("music.archive_in_use")
            p, privacy = _provenance(c, actor, scope, "Music archive", r.reason)
            for identifier in identifiers:
                _snapshot(c, actor, scope, identifier, "music.archived", p, privacy)
            c.execute(
                text(
                    "UPDATE music_object_records SET archived=true "
                    "WHERE scope_id=:s AND id=ANY(:ids)"
                ),
                {"s": scope, "ids": list(identifiers)},
            )
            return {"archived": len(identifiers)}
        if action == "export":
            # Complete relational export includes history and unknown fields, not a lossy CSV.
            tables = [
                "music_objects",
                "music_object_records",
                "music_relations",
                "music_tag_links",
                "music_reviews",
                "music_review_deletions",
                "music_memberships",
                "music_listening_events",
                "music_playback_sources",
                "music_history",
                "music_taxonomy",
                "music_taxonomy_links",
                "music_taxonomy_history",
                "music_profiles",
                "music_relation_details",
                "music_catalog_assignments",
                "music_catalog_history",
                "music_catalog_genre_map",
                "music_catalog_redirects",
            ]
            data = {t: _rows(c, f"SELECT * FROM {t} WHERE scope_id=:s", s=scope) for t in tables}
            data["music_catalog_categories"] = music_catalog.categories(c)
            for t in ["music_releases", "music_works", "music_recordings", "music_tracks"]:
                data[t] = _rows(
                    c,
                    f"SELECT t.* FROM {t} t JOIN music_object_records o ON o.id=t.id "
                    "WHERE o.scope_id=:s",
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
        if action == "review-export":
            if r.id is None:
                raise ValueError("music.id_required")
            detail = _detail(c, scope, r.id)
            return {
                "format": "shiros-review-history-1",
                "exported_at": utc_now(),
                "reviews": [
                    {
                        "date": review["original_at"] or review["created_at"],
                        "rating": review["rating"],
                        "rating_scale": review["rating_max"],
                        "comment": review["comment"],
                    }
                    for review in reversed(detail["reviews"])
                ],
            }
        if action in ("review-import-preview", "review-import"):
            if r.id is None:
                raise ValueError("music.id_required")
            item = _object(c, scope, r.id)
            if item["kind"] not in ("recording", "release"):
                raise ValueError("music.rating_recording_release_only")
            review_rows = parse_reviews(r.content, r.format)
            _safe(r.content)
            review_keys = {
                row["import_key"]
                for row in _rows(
                    c,
                    "SELECT import_key FROM music_reviews WHERE scope_id=:s AND object_id=:id "
                    "AND import_key=ANY(:keys)",
                    s=scope,
                    id=r.id,
                    keys=[review.import_key for review in review_rows],
                )
            }
            new_rows = [review for review in review_rows if review.import_key not in review_keys]
            if action == "review-import-preview":
                return {
                    "count": len(review_rows),
                    "new": len(new_rows),
                    "skipped": len(review_keys),
                    "items": [
                        {
                            "rating": review.rating,
                            "rating_scale": review.rating_scale,
                            "comment": review.comment,
                            "date": review.original_at,
                        }
                        for review in review_rows[:20]
                    ],
                }
            if item["revision"] != r.revision:
                raise ValueError("music.revision_conflict")
            if new_rows:
                p, privacy = _provenance(
                    c, actor, scope, "Music review import: " + r.filename, r.content
                )
                for review in new_rows:
                    _review(
                        c,
                        actor,
                        scope,
                        r.id,
                        review.rating,
                        review.comment,
                        "import",
                        p,
                        privacy,
                        review.rating_scale,
                        review.original_at,
                        review.import_key,
                    )
                _snapshot(c, actor, scope, r.id, "review.imported", p, privacy)
            return {
                "imported": len(new_rows),
                "skipped": len(review_keys),
                "item": _detail(c, scope, r.id),
            }
        if action == "save":
            if r.object is None:
                raise ValueError("music.object_required")
            if r.id and _object(c, scope, r.id)["revision"] != r.revision:
                raise ValueError("music.revision_conflict")
            previously_complete = bool(
                r.id
                and c.scalar(
                    text(
                        "SELECT 1 FROM music_relations WHERE scope_id=:s AND role='recording' "
                        "AND (subject_id=:id OR subject_id IN "
                        "(SELECT id FROM music_tracks WHERE release_id=:id AND scope_id=:s)) "
                        "LIMIT 1"
                    ),
                    {"id": r.id, "s": scope},
                )
            )
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
            item = _detail(c, scope, identifier)
            if (
                item["kind"] == "release"
                and not item["recording_ids"]
                and (not r.id or previously_complete)
            ):
                raise ValueError("music.release_recording_required")
            return {"item": item}
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
                    ) == (value.status, row["rating"], row["comment"], row["marked_at"]) and (
                        not r.sync_status or old["status"] == value.status
                    ):
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
                if r.sync_status:
                    c.execute(
                        text("UPDATE music_releases SET status=:st WHERE id=:id"),
                        {"st": value.status, "id": identifier},
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
                        original_at=original_date(row["marked_at"].replace("/", "-"))
                        if row["marked_at"]
                        else None,
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
        if action == "track-promote":
            if item["kind"] != "track" or not r.recording_id:
                raise ValueError("music.track_required")
            parent = _object(c, scope, r.recording_id)
            if parent["kind"] != "recording":
                raise ValueError("music.invalid_relation")
            track = _detail(c, scope, r.id)
            existing = _rows(
                c,
                "SELECT DISTINCT child.subject_id AS id FROM music_relations child "
                "JOIN music_relations link ON link.object_id=child.subject_id "
                "JOIN music_objects active ON active.id=child.subject_id "
                "WHERE child.scope_id=:s AND child.role='parent_recording' "
                "AND link.subject_id=:track AND link.role='recording' LIMIT 2",
                s=scope,
                track=r.id,
            )
            if len(existing) > 1:
                raise ValueError("music.recording_ambiguous")
            if existing:
                child = existing[0]["id"]
                if (r.recording_id, "parent_recording") not in _relation_keys(c, scope, child):
                    _relations(
                        c,
                        scope,
                        child,
                        [RelationInput(object_id=r.recording_id, role="parent_recording")],
                    )
                    _snapshot(c, actor, scope, child, "recording.reused", p, privacy)
                return {"item": _detail(c, scope, child), "duplicate": True}
            relations = [
                row
                for row in _editable_relations(track)
                if row.role
                in ("work", "composer", "arranger", "conductor", "performer", "singer", "ensemble")
            ]
            relations.append(RelationInput(object_id=r.recording_id, role="parent_recording"))
            child = _save(
                c,
                actor,
                scope,
                ObjectInput(
                    kind="recording",
                    title=track["title"],
                    description=track["description"],
                    relations=relations,
                ),
                p,
                privacy,
            )
            # Share the original album asset without copying the track's personal history.
            release = _detail(c, scope, track["release_id"])
            if release["image_id"]:
                image_key = str(release["image_id"])
                inherited = {
                    key: {image_key: release[key][image_key]}
                    for key in ("image_attributions", "image_frames")
                    if image_key in release.get(key, {})
                }
                c.execute(
                    text(
                        "UPDATE music_profiles SET data=data || CAST(:extra AS jsonb) "
                        "WHERE id=:id AND scope_id=:s"
                    ),
                    {"id": child, "s": scope, "extra": json.dumps(inherited)},
                )
            c.execute(
                text("UPDATE music_objects SET image_id=:image WHERE id=:id"),
                {"id": child, "image": release["image_id"]},
            )
            _relations(c, scope, r.id, [RelationInput(object_id=child, role="recording")])
            # Keep the whole-recording reference and its playback fallback intact.
            for source in track["sources"]:
                c.execute(
                    text(
                        "INSERT INTO music_playback_sources(id,scope_id,object_id,url,platform) "
                        "VALUES (:id,:s,:o,:url,:platform) ON CONFLICT(object_id,url) DO NOTHING"
                    ),
                    {
                        "id": uuid4(),
                        "s": scope,
                        "o": child,
                        "url": source["url"],
                        "platform": source["platform"],
                    },
                )
            _snapshot(c, actor, scope, child, "track.promoted", p, privacy)
            _snapshot(c, actor, scope, r.id, "track.promoted", p, privacy)
            return {"item": _detail(c, scope, child), "duplicate": False}
        if action == "track-save":
            if item["kind"] != "track" or not r.track:
                raise ValueError("music.track_required")
            if r.track.id and r.track.id != r.id:
                raise ValueError("music.invalid_track")
            if c.scalar(
                text(
                    "SELECT 1 FROM music_tracks WHERE release_id="
                    "(SELECT release_id FROM music_tracks WHERE id=:id) "
                    "AND disc=:disc AND position=:pos AND id<>:id"
                ),
                {"id": r.id, "disc": r.track.disc, "pos": r.track.position},
            ):
                raise ValueError("music.duplicate_track_position")
            c.execute(
                text("UPDATE music_objects SET title=:title WHERE id=:id"),
                {"id": r.id, "title": _safe(r.track.title)},
            )
            c.execute(
                text(
                    "UPDATE music_tracks SET disc=:disc,position=:pos,"
                    "duration_seconds=:secs WHERE id=:id"
                ),
                {
                    "id": r.id,
                    "disc": r.track.disc,
                    "pos": r.track.position,
                    "secs": r.track.duration_seconds,
                },
            )
            existing_relations = _relation_keys(c, scope, r.id)
            c.execute(text("DELETE FROM music_relations WHERE subject_id=:id"), {"id": r.id})
            _relations(c, scope, r.id, r.track.relations, existing_relations)
            music_taxonomy.assign(c, scope, r.id, "tag", r.track.tag_ids)
            profile = r.track.model_dump(
                include={
                    "description",
                    "personal_notes",
                    "source_notes",
                    "lyrics",
                    "critical_reception",
                }
            )
            previous_profile = (
                c.scalar(
                    text("SELECT data FROM music_profiles WHERE id=:id AND scope_id=:s"),
                    {"id": r.id, "s": scope},
                )
                or {}
            )
            for key in ("lyrics", "critical_reception", "image_attributions", "image_frames"):
                if key in previous_profile and key not in r.track.model_fields_set:
                    profile[key] = previous_profile[key]
            _safe(json.dumps(profile, ensure_ascii=False))
            c.execute(
                text(
                    "INSERT INTO music_profiles VALUES (:id,:s,CAST(:data AS jsonb)) "
                    "ON CONFLICT(id) DO UPDATE SET data=EXCLUDED.data"
                ),
                {"id": r.id, "s": scope, "data": json.dumps(profile)},
            )
        elif action == "image-upload":
            if r.image_source_url:
                _public_url(r.image_source_url)
            attribution = {
                "source_url": r.image_source_url,
                "credit": r.image_credit,
                "license": r.image_license,
            }
            _safe(
                json.dumps(
                    {"credit": r.image_credit, "license": r.image_license}, ensure_ascii=False
                )
            )
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
            profile = (
                c.scalar(
                    text("SELECT data FROM music_profiles WHERE id=:id AND scope_id=:s"),
                    {"id": r.id, "s": scope},
                )
                or {}
            )
            profile.setdefault("image_attributions", {})[str(identifier)] = attribution
            c.execute(
                text(
                    "INSERT INTO music_profiles VALUES (:id,:s,CAST(:data AS jsonb)) "
                    "ON CONFLICT(id) DO UPDATE SET data=EXCLUDED.data"
                ),
                {"id": r.id, "s": scope, "data": json.dumps(profile)},
            )
        elif action == "release-metadata":
            allowed = {
                "barcode",
                "catalogue",
                "status",
                "released_on",
                "medium",
                "edition",
                "source_notes",
            }
            if item["kind"] != "release" or not r.metadata or set(r.metadata) - allowed:
                raise ValueError("music.invalid_release_metadata")
            value = ObjectInput(title=item["title"], **r.metadata)
            # Barcodes are constrained catalogue identifiers, not free-form text.
            _safe(
                json.dumps(
                    {k: v for k, v in r.metadata.items() if k != "barcode"}, ensure_ascii=False
                )
            )
            for key in ("barcode", "catalogue", "status"):
                if key in r.metadata:
                    c.execute(
                        text(f"UPDATE music_releases SET {key}=:v WHERE id=:id"),
                        {"v": getattr(value, key), "id": r.id},
                    )
            profile = {
                key: getattr(value, key)
                for key in ("released_on", "medium", "edition", "source_notes")
                if key in r.metadata
            }
            if profile:
                c.execute(
                    text(
                        "INSERT INTO music_profiles(id,scope_id,data) "
                        "VALUES (:id,:s,CAST(:d AS jsonb)) "
                        "ON CONFLICT(id) DO UPDATE SET data=music_profiles.data || EXCLUDED.data"
                    ),
                    {"id": r.id, "s": scope, "d": json.dumps(profile, default=str)},
                )
        elif action == "image-frame":
            if not item["image_id"] or r.image_frame is None:
                raise ValueError("music.image_required")
            profile = (
                c.scalar(
                    text("SELECT data FROM music_profiles WHERE id=:id AND scope_id=:s"),
                    {"id": r.id, "s": scope},
                )
                or {}
            )
            profile.setdefault("image_frames", {})[str(item["image_id"])] = (
                r.image_frame.model_dump()
            )
            c.execute(
                text("UPDATE music_profiles SET data=CAST(:data AS jsonb) WHERE id=:id"),
                {"id": r.id, "data": json.dumps(profile)},
            )
        elif action == "image-remove":
            c.execute(text("UPDATE music_objects SET image_id=NULL WHERE id=:id"), {"id": r.id})
        elif action == "review":
            if item["kind"] not in ("recording", "release"):
                raise ValueError("music.rating_recording_release_only")
            if r.rating and r.rating > r.rating_scale:
                raise ValueError("music.invalid_rating")
            if r.historical_date is not None or r.started_at is not None:
                raise ValueError("music.review_date_not_allowed")
            _review(
                c,
                actor,
                scope,
                r.id,
                r.rating or None,
                r.comment,
                "manual",
                p,
                privacy,
                r.rating_scale,
            )
        elif action == "review-history-add":
            if item["kind"] not in ("recording", "release"):
                raise ValueError("music.rating_recording_release_only")
            if r.historical_date is None:
                raise ValueError("music.invalid_review_date")
            if r.rating and r.rating > r.rating_scale:
                raise ValueError("music.invalid_rating")
            when = original_date(r.historical_date.isoformat())
            _review(
                c,
                actor,
                scope,
                r.id,
                r.rating or None,
                r.comment,
                "history_manual",
                p,
                privacy,
                r.rating_scale,
                when,
            )
        elif action == "review-delete":
            if r.review_id is None or not c.scalar(
                text(
                    "SELECT 1 FROM music_reviews WHERE id=:review AND scope_id=:s "
                    "AND object_id=:id AND NOT EXISTS(SELECT 1 FROM music_review_deletions d "
                    "WHERE d.review_id=:review)"
                ),
                {"review": r.review_id, "s": scope, "id": r.id},
            ):
                raise ValueError("music.review_not_found")
            seq = _event(c, actor, scope, r.id, p, privacy)
            c.execute(
                text(
                    "INSERT INTO music_review_deletions(id,scope_id,object_id,review_id,source_id,"
                    "created_by,sequence) VALUES (:uuid,:s,:id,:review,:source,:actor,:seq)"
                ),
                {
                    "uuid": uuid4(),
                    "s": scope,
                    "id": r.id,
                    "review": r.review_id,
                    "source": p.source_id,
                    "actor": actor,
                    "seq": seq,
                },
            )
        elif action == "membership":
            if item["kind"] != "release":
                raise ValueError("music.invalid_membership")
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
                        old["event_kind"],
                    ) != (
                        r.id,
                        r.started_at,
                        r.actual_seconds,
                        r.completed,
                        r.timezone,
                        r.event_kind,
                    ):
                        raise ValueError("music.event_identity_conflict")
                    return {"item": _detail(c, scope, r.id), "duplicate": True}
            c.execute(
                text(
                    "INSERT INTO music_listening_events(id,scope_id,object_id,started_at,"
                    "timezone,actual_seconds,platform,source_event_id,source_id,completed,"
                    "event_kind) "
                    "VALUES (:id,:s,:o,:t,:z,:secs,:p,:e,:src,:done,:kind)"
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
                    "kind": r.event_kind,
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
