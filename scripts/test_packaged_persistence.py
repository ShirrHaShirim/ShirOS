"""Create one review only in the disposable installed tester database."""

import json
import sys
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

FIXTURE = {"rating": 6, "comment": "Packaged persistence fixture"}


def main() -> None:
    folder = Path(".local/test-package-persistence")
    session = json.loads((folder / "session.json").read_text(encoding="utf-8"))

    def api(action: str, data: dict[str, object]) -> dict[str, Any]:
        request = Request(
            f"http://127.0.0.1:{session['port']}/ui-api/music/{action}",
            data=json.dumps(data).encode(),
            headers={"Content-Type": "application/json", "X-Shiros-Token": session["token"]},
        )
        with urlopen(request, timeout=30) as response:
            return dict(json.load(response))

    if "--check" in sys.argv:
        fixture = json.loads(
            Path(".local/test-package-checks/persistence-fixture.json").read_text(encoding="utf-8")
        )
        item = api("detail", {"id": fixture["id"]})["item"]
        assert item["rating"] == FIXTURE["rating"]
        assert item["reviews"][0]["comment"] == FIXTURE["comment"]
        (folder / "quit-test").write_text("quit", encoding="ascii")
        print("Review retained after restart; requested graceful shutdown")
        return

    recording = api(
        "list", {"kind": "recording", "query": "Borodin", "root_recordings": True}
    )["items"][0]
    before = api("detail", {"id": recording["id"]})["item"]
    saved = api(
        "review",
        {
            "id": before["id"],
            "revision": before["revision"],
            "rating": FIXTURE["rating"],
            "rating_scale": 7,
            "comment": FIXTURE["comment"],
        },
    )["item"]
    assert saved["rating"] == FIXTURE["rating"]
    checks = Path(".local/test-package-checks")
    checks.mkdir(exist_ok=True)
    (checks / "persistence-fixture.json").write_text(
        json.dumps({**FIXTURE, "id": saved["id"], "revision": saved["revision"]}),
        encoding="utf-8",
    )
    (folder / "quit-test").write_text("quit", encoding="ascii")
    print("Disposable review saved; requested graceful shutdown")


if __name__ == "__main__":
    main()
