"""Audit a test snapshot without printing any catalogue text or credentials."""

import gzip
import json
from pathlib import Path

from shiros.core.privacy import SECRET
from shiros.music_seed import COLUMNS, LOCAL_PATH


def main() -> None:
    path = Path("build/desktop-test/music-seed.json.gz")
    with gzip.open(path, "rt", encoding="utf-8") as f:
        payload = json.load(f)
    assert set(payload["tables"]) == set(COLUMNS)
    findings = []
    nested_keys = set()

    def scan(value: object, location: str) -> None:
        if isinstance(value, dict):
            for key, part in value.items():
                if key != "content":
                    scan(part, location + "." + key)
        elif isinstance(value, list):
            for index, part in enumerate(value):
                scan(part, location + "." + str(index))
        elif isinstance(value, str):
            if SECRET.search(value) or LOCAL_PATH.search(value):
                findings.append(location)

    scan(payload["tables"], "tables")
    for row in payload["tables"]["music_profiles"]:
        nested_keys.update(row["data"])
    assert not findings, "Sensitive field locations: " + ",".join(findings)
    result = {
        "private_tables": 0,
        "credential_patterns": 0,
        "local_paths": 0,
        "counts": payload["counts"],
        "profile_keys": sorted(nested_keys),
    }
    Path("build/desktop-test/seed-audit.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
