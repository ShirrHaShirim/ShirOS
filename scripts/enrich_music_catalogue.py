"""Apply an explicitly curated public metadata plan through the local Music API.

No name-based identity merging, database writes, ratings, memberships or listening.
Work hierarchy never implies child Recordings. One complete Recording can link
multiple Works; ordered Release tracks retain each Work and its precise credits.
Preview is the default. Use --apply after reviewing the JSON plan.
"""

import argparse
import base64
import json
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from shiros.domains.music import ObjectInput


def editable(item: dict[str, Any]) -> dict[str, Any]:
    """Preserve editable facts, precise credits and classifications; never echo tracks."""
    result = {key: item[key] for key in ObjectInput.model_fields if key in item}
    result["tracks"] = []
    if item["kind"] != "work":
        # Detail responses expose the linked Works' effective classifications.
        # They are read-only on recordings, releases and profiles.
        result["music_class"] = ""
        result["music_classes"] = []
        result["composed_on"] = ""
    result["relations"] = [
        {
            key: row[key]
            for key in (
                "object_id",
                "role",
                "started_on",
                "ended_on",
                "source_text",
                "performance_role",
            )
            if key in row
        }
        for row in item.get("relations", [])
    ]
    result["tag_ids"] = [row["id"] for row in item.get("tags", [])]
    result["genre_ids"] = (
        [row["id"] for row in item.get("genres", [])] if item["kind"] == "work" else []
    )
    result["identity_ids"] = [
        row["id"] for row in item.get("identities", []) if not row.get("derived")
    ]
    return result


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--session", type=Path, default=Path(".local/browser-session/session.json"))
    parser.add_argument("--output", type=Path, default=Path(".local/music-enrichment-result.json"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8"))

    def api(action: str, payload: dict[str, Any]) -> dict[str, Any]:
        session = json.loads(args.session.read_text(encoding="utf-8"))
        request = Request(
            f"http://127.0.0.1:{int(session['port'])}/ui-api/music/{action}",
            data=json.dumps(payload, ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json", "X-Shiros-Token": session["token"]},
        )
        try:
            with urlopen(request, timeout=90) as response:
                return dict(json.load(response))
        except HTTPError as exc:
            raise RuntimeError(json.load(exc).get("error", "request_failed")) from None

    originals: list[dict[str, Any]] = []
    result = []
    backup = args.output.with_suffix(".before.json")
    for entry in plan["items"]:
        record: dict[str, Any] = {"id": entry["id"], "changes": []}
        try:
            item = api("detail", {"id": entry["id"]})["item"]
            record["title"] = item["title"]
            if item["kind"] != entry["kind"] or item["title"] != entry["title"]:
                raise ValueError("Plan identity does not match the current object")
            originals.append(item)
            if args.apply:
                # Write a recovery record before touching each object.
                backup.parent.mkdir(parents=True, exist_ok=True)
                backup.write_text(json.dumps(originals, ensure_ascii=False), encoding="utf-8")
            obj = editable(item)
            fields = entry.get("fields", {})
            forbidden = set(fields) - set(ObjectInput.model_fields)
            if forbidden or {"kind", "title", "tracks", "status", "personal_notes"} & set(fields):
                raise ValueError("Plan contains unsupported catalogue fields")
            for key, value in fields.items():
                if key == "relations":
                    existing = {(r["object_id"], r["role"]) for r in obj["relations"]}
                    obj["relations"] += [
                        r for r in value if (r["object_id"], r["role"]) not in existing
                    ]
                elif key in ("genre_ids", "aliases", "music_classes"):
                    obj[key] = list(dict.fromkeys([*obj.get(key, []), *value]))
                elif key == "source_notes":
                    if value and value not in obj.get(key, ""):
                        obj[key] = (obj.get(key, "") + "\n" + value).strip()
                elif not obj.get(key):
                    obj[key] = value
            validated = ObjectInput.model_validate(obj).model_dump(mode="json")
            if obj != editable(item):
                record["changes"].append("facts / relations / genres")
                if args.apply:
                    item = api(
                        "save",
                        {"id": item["id"], "revision": item["revision"], "object": validated},
                    )["item"]
            for source in entry.get("sources", []):
                if any(s["url"] == source["url"] for s in item.get("sources", [])):
                    continue
                record["changes"].append("source")
                if args.apply:
                    item = api(
                        "source", {"id": item["id"], "revision": item["revision"], **source}
                    )["item"]
            image = entry.get("image")
            if image and not item.get("image_id"):
                record["changes"].append("image")
                if args.apply:
                    # Only local, inspected assets. The server never fetches remote URLs.
                    content = Path(image["path"]).read_bytes()
                    if len(content) > 8 * 1024 * 1024:
                        raise ValueError("Image exceeds 8 MiB")
                    item = api(
                        "image-upload",
                        {
                            "id": item["id"],
                            "revision": item["revision"],
                            "filename": Path(image["path"]).name,
                            "image_data": base64.b64encode(content).decode(),
                            "image_source_url": image.get("source_url", ""),
                            "image_credit": image.get("credit", ""),
                            "image_license": image.get("license", ""),
                        },
                    )["item"]
            record["status"] = "applied" if args.apply else "preview"
        except (ValueError, RuntimeError, OSError) as exc:
            record["status"] = "failed"
            record["error"] = str(exc)
        result.append(record)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(record["title"] if "title" in record else record["id"], record["status"], flush=True)


if __name__ == "__main__":
    main()
