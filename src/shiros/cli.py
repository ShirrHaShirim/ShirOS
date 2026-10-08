"""Unified developer commands. Run from the repository root."""

import argparse
import asyncio
import io
import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid4

import uvicorn
from alembic import command
from alembic.config import Config

from shiros.adapters.database.connection import check_database, make_engine
from shiros.backup import BackupManager
from shiros.config import Settings
from shiros.core.context import ContextBundle
from shiros.core.conversations import SendMessageRequest
from shiros.core.jobs import RunRequest
from shiros.i18n import Locale, default_catalog
from shiros.identity import LocalIdentity
from shiros.memory_demo import run_memory_demo
from shiros.review_console import run_review_console
from shiros.review_workflow import ReviewWorkflow
from shiros.services import build_services


async def demo() -> None:
    services = build_services()
    conversation = await services.conversations.create_conversation("mock-echo-v1")
    response = await services.conversations.send_message(
        SendMessageRequest(
            conversation_id=conversation.id,
            request_id=uuid4(),
            content="Hello ShirOS",
        )
    )
    run = await services.worker.run(
        RunRequest(
            job_id=uuid4(),
            task="Synthetic smoke test",
            context=ContextBundle(token_count=0),
        )
    )
    print(response.content)
    print(run.output)


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="shiros")
    parser.add_argument(
        "command",
        choices=[
            "dev",
            "test",
            "integration",
            "lint",
            "typecheck",
            "migrate",
            "db-health",
            "demo",
            "memory-demo",
            "review-init",
            "review",
            "web",
            "open",
        ],
    )
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--locale", choices=[locale.value for locale in Locale], default="zh-CN")
    parser.add_argument("--scope")
    args = parser.parse_args()
    checks = {
        "test": ["pytest", "-m", "not integration"],
        "integration": ["pytest", "-m", "integration"],
        "lint": ["ruff", "check", "."],
        "typecheck": ["mypy"],
    }
    if args.command in checks:
        raise SystemExit(subprocess.call([sys.executable, "-m", *checks[args.command]]))
    if args.command == "dev":
        uvicorn.run("shiros.apps.api:app", host="127.0.0.1", port=args.port, access_log=False)
    elif args.command == "open":
        from shiros.browser_session import open_session

        try:
            open_session()
        except Exception:
            print(
                default_catalog().translate("core", "error.operation_failed", Locale(args.locale))
            )
            raise SystemExit(1) from None
    elif args.command == "web":
        from shiros.apps.workbench import create_workbench
        from shiros.browser_session import start_session

        engine = make_engine(Settings().database_url())
        try:
            identity = LocalIdentity(engine)
            scope = UUID(args.scope) if args.scope else identity.bootstrap()
            if not 1024 <= args.port <= 65535:
                raise ValueError("port.invalid")
            token = start_session(args.port)
            app = create_workbench(engine, identity, scope, args.port, token)
            uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)
        finally:
            engine.dispose()
    elif args.command == "demo":
        asyncio.run(demo())
    elif args.command == "memory-demo":
        engine = None
        locale = Locale(args.locale)
        try:
            engine = make_engine(Settings().database_url())
            asyncio.run(run_memory_demo(engine, locale))
        except Exception:
            print(
                default_catalog().translate("core", "error.operation_failed", locale),
                file=sys.stderr,
            )
            raise SystemExit(1) from None
        finally:
            if engine is not None:
                engine.dispose()
    elif args.command in {"review-init", "review"}:
        engine = None
        locale = Locale(args.locale)
        catalog = default_catalog()
        project_root = Path(__file__).resolve().parents[2]
        local_root = project_root / ".local"
        inbox = local_root / "inbox"
        try:
            inbox.mkdir(parents=True, exist_ok=True)
            settings = Settings()
            engine = make_engine(settings.database_url())
            identity = LocalIdentity(engine)
            if args.command == "review-init":
                scope = identity.bootstrap()
                print(catalog.translate("review", "review.initialized", locale).format(scope=scope))
            else:
                if args.scope is None:
                    print(
                        catalog.translate("review", "review.scope_required", locale),
                        file=sys.stderr,
                    )
                    raise SystemExit(2)
                scope = UUID(args.scope)
                workflow = ReviewWorkflow(engine, identity, inbox)
                backups = BackupManager(settings, local_root / "backups")
                run_review_console(workflow, identity, scope, inbox, backups, locale)
        except Exception:
            print(catalog.translate("review", "review.error_generic", locale), file=sys.stderr)
            raise SystemExit(1) from None
        finally:
            if engine is not None:
                engine.dispose()
    elif args.command == "migrate":
        try:
            command.upgrade(Config("alembic.ini"), "head")
        except Exception:
            print(
                "Migration failed. Check database configuration and availability.", file=sys.stderr
            )
            raise SystemExit(1) from None
        print("Migration complete")
    elif args.command == "db-health":
        engine = None
        try:
            engine = make_engine(Settings().database_url())
            health = check_database(engine)
            print(health.model_dump_json())
            raise SystemExit(0 if health.ready else 1)
        except Exception:
            print("Database unavailable or not migrated.", file=sys.stderr)
            raise SystemExit(1) from None
        finally:
            if engine is not None:
                engine.dispose()


if __name__ == "__main__":
    main()
