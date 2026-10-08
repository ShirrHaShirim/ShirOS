"""Explicit, bounded local file preview. No scans, models, or persistence here."""

import csv
import hashlib
import io
import json
import os
import unicodedata
from pathlib import Path
from typing import Literal
from uuid import UUID

from shiros.core.privacy import PrivacyDecision, PrivacyInput, RuleBasedPrivacyPolicy
from shiros.core.schemas import EvidenceLevel, Record, UtcTimestamp, utc_now

MAX_BYTES = 65536
KINDS = {".txt": "text", ".md": "markdown", ".json": "json", ".csv": "csv"}


class SourcePreview(Record):
    kind: Literal["text", "markdown", "json", "csv"]
    origin: Literal["manual-local-file", "manual-browser-input"] = "manual-local-file"
    title: str
    original_text: str
    normalized_text: str
    observed_at: UtcTimestamp
    privacy: PrivacyDecision


class MemoryCandidate(Record):
    scope_id: UUID
    source_id: UUID
    original_text: str
    text: str
    suggested_fact_level: EvidenceLevel = EvidenceLevel.EXPLICIT_STATEMENT
    suggested_entity_links: tuple[UUID, ...] = ()
    confidence: float
    status: Literal["pending", "edited", "approved", "rejected", "invalidated"]
    revision: int
    reviewer_id: UUID
    created_by: UUID
    observed_at: UtcTimestamp
    privacy_state: Literal["staging_allowed"] = "staging_allowed"
    sensitivity: Literal["ordinary", "personal"]
    memory_revision_id: UUID | None = None
    review_reason: str = "manual_stage"
    target_memory_id: UUID | None = None
    expected_memory_revision: int | None = None
    proposed_title: str | None = None
    proposed_sources: list[dict[str, object]] | None = None


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def preview_file(root: Path, filename: str) -> SourcePreview:
    if (
        not filename
        or Path(filename).name != filename
        or "/" in filename
        or "\\" in filename
        or ":" in filename
    ):
        raise ValueError("intake.invalid_path")
    base = root.resolve(strict=True)
    chosen = base / filename
    if chosen.is_symlink() or chosen.is_junction() or not chosen.is_file():
        raise ValueError("intake.invalid_path")
    resolved = chosen.resolve(strict=True)
    if resolved.parent != base or chosen.suffix.lower() not in KINDS:
        raise ValueError("intake.invalid_type")
    if chosen.stat().st_nlink != 1:
        raise ValueError("intake.invalid_path")
    if chosen.stat().st_size > MAX_BYTES:
        raise ValueError("intake.too_large")
    # Recheck the resolved target and size around the single bounded read.
    with resolved.open("rb") as stream:
        if os.fstat(stream.fileno()).st_nlink != 1:
            raise ValueError("intake.invalid_path")
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES or chosen.resolve(strict=True) != resolved:
        raise ValueError("intake.changed_file")
    try:
        original = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("intake.invalid_content") from None
    return preview_content(filename, original, origin="manual-local-file")


def preview_content(
    filename: str,
    original: str,
    *,
    origin: Literal["manual-local-file", "manual-browser-input"] = "manual-browser-input",
) -> SourcePreview:
    """Normalize explicitly supplied browser text without creating an inbox file."""
    suffix = Path(filename).suffix.lower()
    if suffix not in KINDS or not filename or len(filename) > 250:
        raise ValueError("intake.invalid_type")
    if Path(filename).name != filename or any(c in filename for c in ("/", "\\", ":")):
        raise ValueError("intake.invalid_path")
    if len(original.encode("utf-8")) > MAX_BYTES:
        raise ValueError("intake.too_large")
    try:
        normalized = unicodedata.normalize("NFKC", original).replace("\r\n", "\n").strip()
        if suffix == ".json":
            normalized = json.dumps(json.loads(normalized), ensure_ascii=False, indent=2)
        elif suffix == ".csv":
            rows = list(csv.reader(io.StringIO(normalized), strict=True))
            output = io.StringIO()
            csv.writer(output, lineterminator="\n").writerows(rows)
            normalized = output.getvalue().strip()
    except (ValueError, csv.Error, RecursionError):
        raise ValueError("intake.invalid_content") from None
    if not normalized:
        raise ValueError("intake.empty")
    policy = RuleBasedPrivacyPolicy()
    decisions = [
        policy.evaluate(PrivacyInput(text=value, reviewed=True))
        for value in (
            original,
            normalized,
            filename,
        )
    ]
    decision = decisions[1]
    if any(not item.persistence_allowed for item in decisions):
        decision = decision.model_copy(update={"persistence_allowed": False, "safe_text": None})
    return SourcePreview(
        origin=origin,
        kind=KINDS[suffix],
        title=filename,
        original_text=original,
        normalized_text=normalized,
        observed_at=utc_now(),
        privacy=decision,
    )
