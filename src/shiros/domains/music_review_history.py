"""Parse historical reviews; original dates never replace server recording times."""

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from shiros.core.schemas import utc_now

LOCAL_ZONE = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class ReviewRow:
    rating: int | None
    rating_scale: int
    comment: str
    original_at: datetime | None
    import_key: str


def original_date(value: str) -> datetime | None:
    if not value:
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            stamp = datetime.combine(
                datetime.strptime(value, "%Y-%m-%d").date(), time(), LOCAL_ZONE
            )
        else:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=LOCAL_ZONE)
    except (ValueError, OverflowError):
        raise ValueError("music.invalid_review_date") from None
    if stamp.astimezone(LOCAL_ZONE).date() > utc_now().astimezone(LOCAL_ZONE).date():
        raise ValueError("music.review_future_date")
    return stamp


def parse_reviews(content: str, format: str) -> list[ReviewRow]:
    rows: Any
    try:
        if format == "csv":
            rows = list(csv.DictReader(io.StringIO(content.lstrip("\ufeff"))))
        else:
            data = json.loads(content.lstrip("\ufeff"))
            rows = data.get("reviews") if isinstance(data, dict) else data
        if not isinstance(rows, list) or not 1 <= len(rows) <= 1000:
            raise ValueError()
        normalized = []
        for row in rows:
            if not isinstance(row, dict) or set(row) - {
                "date",
                "rating",
                "rating_scale",
                "comment",
            }:
                raise ValueError()
            raw_scale = row.get("rating_scale")
            if raw_scale in (None, ""):
                raw_scale = 7
            raw_rating = row.get("rating")
            if isinstance(raw_scale, bool) or isinstance(raw_rating, bool):
                raise ValueError()
            scale = int(str(raw_scale))
            if str(raw_scale) not in ("5", "7"):
                raise ValueError()
            rating = None if raw_rating in (None, "", 0, "0") else int(str(raw_rating))
            if rating is not None and (str(raw_rating) != str(rating) or not 1 <= rating <= scale):
                raise ValueError()
            comment = row.get("comment")
            when = row.get("date")
            if comment is None:
                comment = ""
            if when is None:
                when = ""
            if not isinstance(comment, str) or len(comment) > 20000 or not isinstance(when, str):
                raise ValueError()
            stamp = original_date(when.strip())
            normalized.append((rating, scale, comment, stamp.isoformat() if stamp else None))
    except (ValueError, TypeError, OverflowError, csv.Error) as error:
        if str(error).startswith("music."):
            raise
        raise ValueError("music.invalid_review_import") from None
    digest = hashlib.sha256(json.dumps(normalized, ensure_ascii=False).encode()).hexdigest()
    return [
        ReviewRow(
            rating,
            scale,
            comment,
            datetime.fromisoformat(stamp) if stamp else None,
            hashlib.sha256(f"{digest}:{index}".encode()).hexdigest(),
        )
        for index, (rating, scale, comment, stamp) in enumerate(normalized)
    ]
