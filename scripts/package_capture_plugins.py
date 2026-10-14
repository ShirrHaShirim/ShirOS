"""Package only an explicit list of redistributable source files; no local state."""

import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "desktop/plugin-packages"


def package(folder: str, files: list[str], filename: str) -> None:
    source = ROOT / folder
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with ZipFile(OUTPUT / filename, "w", compression=ZIP_DEFLATED) as archive:
        for name in files:
            path = source / name
            if path.is_symlink() or path.is_junction() or not path.is_file():
                raise ValueError("Invalid packaged source file")
            archive.write(path, source.name + "/" + name)
        archive.write(ROOT / "docs/conversation-sync-v073.md", source.name + "/USAGE.md")
    with ZipFile(OUTPUT / filename) as archive:
        assert archive.testzip() is None
        assert all(
            ".local" not in name and "capture.json" not in name for name in archive.namelist()
        )


def main() -> None:
    plugin = json.loads(
        (ROOT / "plugins/conversation-sync/plugin.json").read_text(encoding="utf-8")
    )
    assert plugin["name"] == "conversation-sync" and plugin["version"] == "0.7.4"
    manifest = json.loads((ROOT / "desktop/gpt-sync/manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.7.4.1" and manifest["manifest_version"] == 3
    package(
        "plugins/conversation-sync",
        [
            "plugin.json",
            "README.md",
            "opencode.js",
            "hooks/hooks.json",
            "hooks/capture_hook.py",
            "skills/conversation-sync/SKILL.md",
        ],
        "conversation-sync-0.7.4.zip",
    )
    package(
        "desktop/gpt-sync",
        [
            "manifest.json",
            "README.md",
            "background.js",
            "content.js",
            "popup.html",
            "popup.js",
            "popup.css",
            "icon.png",
        ],
        "shiros-browser-sync-0.7.4.1.zip",
    )
    print("Two source-only plugin packages created and verified.")


if __name__ == "__main__":
    main()
