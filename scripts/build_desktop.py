"""Build from an explicit program/runtime allowlist; never copy a data directory."""

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--postgres", type=Path, required=True, help="Runtime Library directory")
    parser.add_argument("--iscc", type=Path, required=True)
    parser.add_argument("--skip-freeze", action="store_true")
    args = parser.parse_args()
    output = ROOT / "dist" / "desktop"
    work = ROOT / "build" / "desktop"
    output.mkdir(parents=True, exist_ok=True)
    if not args.skip_freeze:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "PyInstaller",
                "--noconfirm",
                "--clean",
                "--onedir",
                "--windowed",
                "--name",
                "ShirOS",
                "--distpath",
                str(output),
                "--workpath",
                str(work),
                "--specpath",
                str(work),
                "--collect-all",
                "shiros",
                "--collect-all",
                "webview",
                "--collect-all",
                "psycopg",
                "--collect-all",
                "psycopg_binary",
                "--collect-submodules",
                "uvicorn",
                "--collect-submodules",
                "alembic",
                "--hidden-import",
                "httpx",
                "--hidden-import",
                "fastapi.testclient",
                "--add-data",
                str(ROOT / "migrations") + ";migrations",
                str(ROOT / "desktop" / "entry.py"),
            ],
            check=True,
            cwd=ROOT,
        )
    base = output / "ShirOS"
    runtime = base / "_internal" / "postgres"
    # Only migration sources, never interpreter caches.
    migration_target = base / "_internal" / "migrations"
    if migration_target.exists():
        migration_target.resolve().relative_to(base.resolve())
        shutil.rmtree(migration_target)
    shutil.copytree(
        ROOT / "migrations", migration_target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
    )
    for folder in ("bin", "lib", "share"):
        shutil.copytree(
            args.postgres / folder,
            runtime / folder,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("*.pdb", "*.lib", "*.a", "*.pyc", "__pycache__"),
        )
    # Conda's VC runtime DLLs live alongside Library, not inside it.
    for dll in args.postgres.parent.glob("*.dll"):
        shutil.copy2(dll, runtime / "bin" / dll.name)
    license_root = base / "ThirdPartyLicenses"
    cache = ROOT / ".local" / "mamba" / "pkgs" / "https" / "conda.anaconda.org" / "conda-forge"
    for channel in ("win-64", "noarch"):
        if (cache / channel).exists():
            for package in (cache / channel).iterdir():
                licenses = package / "info" / "licenses"
                if licenses.is_dir():
                    shutil.copytree(licenses, license_root / package.name, dirs_exist_ok=True)
    for edition in ("full", "core"):
        destination = output / ("ShirOS-" + edition)
        if destination.exists():
            destination.resolve().relative_to(output.resolve())
            shutil.rmtree(destination)
        shutil.copytree(base, destination, dirs_exist_ok=True)
        (destination / "edition.json").write_text(
            json.dumps({"edition": edition}), encoding="utf-8"
        )
        shutil.copytree(ROOT / "desktop" / "gpt-sync", destination / "GPT-Sync", dirs_exist_ok=True)
        shutil.copy2(ROOT / "docs" / "user-manual.md", destination / "使用说明书.md")
        shutil.copy2(ROOT / "prompt.txt", destination / "prompt.txt")
        shutil.copy2(ROOT / "docs" / "desktop-third-party.md", destination / "第三方说明.md")
        if edition == "core":
            web = destination / "_internal" / "shiros" / "apps" / "web"
            (web / "music.js").unlink(missing_ok=True)
        forbidden = {
            ".env",
            "pgdata",
            "database.json",
            "session.json",
            "browser-session",
            "backups",
            "memory-clients",
            "inbox",
            ".git",
            "__pycache__",
        }
        for path in destination.rglob("*"):
            if path.name in forbidden or path.suffix.lower() in {".dump", ".log"}:
                raise RuntimeError("Data in bundle: " + str(path.relative_to(destination)))
        entries = [
            {
                "path": p.relative_to(destination).as_posix(),
                "size": p.stat().st_size,
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            }
            for p in sorted(destination.rglob("*"))
            if p.is_file()
        ]
        manifest = output / ("ShirOS-" + edition + "-manifest.json")
        manifest.write_text(
            json.dumps(
                {
                    "version": "0.5.0",
                    "edition": edition,
                    "contains_user_data": False,
                    "files": entries,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        subprocess.run(
            [
                str(args.iscc),
                "/DEdition=" + edition,
                "/DPayload=" + str(destination),
                "/DOutput=" + str(output),
                str(ROOT / "desktop" / "installer.iss"),
            ],
            check=True,
        )
    checksums = []
    for path in sorted(output.glob("*.exe")):
        checksums.append(hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.name)
    (output / "SHA256SUMS.txt").write_text("\n".join(checksums) + "\n", encoding="ascii")


if __name__ == "__main__":
    main()
