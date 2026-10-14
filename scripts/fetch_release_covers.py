"""Fetch missing artwork from each Release's original, stable Douban entry.

This prepares local assets only. Existing covers and formal music data are untouched.
Requests are paced, honor rate limits, and never attempt to bypass blocked pages.
"""

import argparse
import hashlib
import io
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from PIL import Image, UnidentifiedImageError


class Metadata(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.values = {}

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "meta" and values.get("property", "").startswith("og:"):
            self.values[values["property"]] = values.get("content", "")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--interval", type=float, default=3.2)
    p.add_argument("--limit", type=int)
    p.add_argument("--retry-failed", action="store_true")
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    assets = args.output / "assets"
    assets.mkdir(exist_ok=True)
    log = args.output / "downloads.jsonl"
    previous = {}
    if log.exists():
        previous = {
            r["id"]: r
            for line in log.read_text(encoding="utf-8").splitlines()
            if (r := json.loads(line))
        }
    rows = json.loads(args.input.read_text(encoding="utf-8"))
    rows = [
        r
        for r in rows
        if r["id"] not in previous
        or args.retry_failed
        and previous[r["id"]]["status"] != "downloaded"
    ]
    if args.limit:
        rows = rows[: args.limit]
    mutex = threading.Lock()
    next_page = 0.0
    blocked = threading.Event()

    def paced():
        nonlocal next_page
        with mutex:
            delay = max(0, next_page - time.monotonic())
            next_page = time.monotonic() + delay + args.interval
        if delay:
            time.sleep(delay)

    def get(url, *, page=False):
        if page:
            paced()
        request = Request(
            url,
            headers={
                "User-Agent": "ShirOS/0.7.18 personal catalogue artwork lookup",
                "Referer": "https://music.douban.com/",
            },
        )
        with urlopen(request, timeout=20) as response:
            data = response.read(8 * 1024 * 1024 + 1)
            if len(data) > 8 * 1024 * 1024:
                raise ValueError("asset_too_large")
            return data, response.url

    def fetch(row):
        result = {k: row[k] for k in ("id", "title", "revision", "douban_id")}
        if not row["douban_id"]:
            return {**result, "status": "unavailable", "reason": "no_source_id"}
        if blocked.is_set():
            return {**result, "status": "unavailable", "reason": "source_access_blocked"}
        source = f"https://music.douban.com/subject/{row['douban_id']}/"
        result["source_url"] = source
        try:
            html, page_url = get(source, page=True)
            if urlsplit(page_url).path.rstrip("/") != f"/subject/{row['douban_id']}":
                raise ValueError("source_redirected")
            parser = Metadata()
            parser.feed(html.decode("utf-8"))
            meta = parser.values
            image = meta.get("og:image", "")
            if not meta.get("og:title") or "/view/subject/" not in image:
                raise ValueError("no_cover_metadata")
            parts = urlsplit(image)
            if not (parts.hostname or "").endswith(".doubanio.com"):
                raise ValueError("unexpected_image_host")
            result.update(image_url=image, page_title=meta["og:title"])
            data, actual_url = get(image)
            with Image.open(io.BytesIO(data)) as bitmap:
                bitmap.verify()
            with Image.open(io.BytesIO(data)) as bitmap:
                width, height = bitmap.size
                if bitmap.format not in ("JPEG", "PNG", "WEBP"):
                    raise ValueError("unsupported_image")
                if min(width, height) < 128 or not 0.5 <= width / height <= 2:
                    raise ValueError("unsuitable_dimensions")
            path = assets / (row["id"] + ".img")
            path.write_bytes(data)
            (assets / (row["id"] + ".html")).write_bytes(html)
            result.update(
                status="downloaded",
                path=str(path.resolve()),
                image_url=actual_url,
                page_title=meta["og:title"],
                width=width,
                height=height,
                sha256=hashlib.sha256(data).hexdigest(),
                match="original_douban_subject_id",
            )
        except HTTPError as exc:
            # Respect denial rather than swapping identities or evading the service.
            if exc.code in (403, 418, 429):
                blocked.set()
            result.update(status="unavailable", reason=f"http_{exc.code}")
        except UnidentifiedImageError:
            result.update(status="unavailable", reason="image_not_accessible_or_invalid")
        except Exception as exc:
            result.update(status="unavailable", reason=str(exc)[:150])
        return result

    total, ready = 0, 0
    with (
        ThreadPoolExecutor(max_workers=args.workers) as pool,
        log.open("a", encoding="utf-8") as out,
    ):
        for future in as_completed([pool.submit(fetch, row) for row in rows]):
            result = future.result()
            out.write(json.dumps(result, ensure_ascii=False) + "\n")
            out.flush()
            previous[result["id"]] = result
            total += 1
            ready += result["status"] == "downloaded"
            if total % 25 == 0 or total <= 5 and result["status"] != "downloaded":
                print(
                    json.dumps(
                        {
                            "checked": total,
                            "ready": ready,
                            "last_status": result["status"],
                            "reason": result.get("reason"),
                        }
                    ),
                    flush=True,
                )
    (args.output / "downloads.json").write_text(
        json.dumps(list(previous.values()), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "checked_this_run": total,
                "ready_this_run": ready,
                "total_ready": sum(x["status"] == "downloaded" for x in previous.values()),
            }
        )
    )


if __name__ == "__main__":
    main()
