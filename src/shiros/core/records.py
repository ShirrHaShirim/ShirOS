"""Explicit persistent records; shared metadata is not a universal content schema."""

from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from shiros.core.privacy import Sensitivity
from shiros.core.schemas import Entity, EvidenceLevel, Provenance, Record, Schema


class PersistenceMetadata(Schema):
    persistence_allowed: Literal[True] = True
    confidentiality: Literal["standard"] = "standard"
    sensitivity: Sensitivity
    reviewed_by: UUID
    policy_version: Literal["rules-v1"] = "rules-v1"


class PersistentRecord(Record):
    scope_id: UUID
    provenance: Provenance
    privacy: PersistenceMetadata


class Source(PersistentRecord):
    kind: Literal["note", "synthetic"]
    title: str
    text: str


class EntityRecord(Entity):
    scope_id: UUID
    privacy: PersistenceMetadata


class Artifact(PersistentRecord):
    media_type: Literal["text/plain"] = "text/plain"
    text: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class Relation(PersistentRecord):
    from_entity_id: UUID
    to_entity_id: UUID
    predicate: Literal["related_to", "supports"]


class Summary(PersistentRecord):
    memory_revision_id: UUID
    version: int = Field(ge=1)
    text: str
    from_sequence: int = Field(ge=1)
    through_sequence: int = Field(ge=1)
    processor_version: Literal["extractive-v1"] = "extractive-v1"


class Snapshot(Summary):
    summary_id: UUID


class Checkpoint(Record):
    scope_id: UUID
    processor: Literal["memory-layers-v1"] = "memory-layers-v1"
    through_sequence: int = Field(ge=1)


class Inference(PersistentRecord):
    text: str
    evidence_revision_ids: tuple[UUID, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def remains_inference(self) -> Self:
        if self.provenance.level != EvidenceLevel.INFERENCE or self.provenance.verified:
            raise ValueError("inference.invalid_evidence_level")
        return self
