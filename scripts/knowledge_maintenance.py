"""External scheduler entry: queue work, never invent a synthesis or approve it."""

import argparse
import json
from uuid import UUID

from shiros.adapters.database.connection import make_engine
from shiros.config import Settings
from shiros.identity import LocalIdentity
from shiros.knowledge import KnowledgeRequest, KnowledgeService


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reason", choices=["daily", "weekly"], default="daily")
    parser.add_argument("--scope", type=UUID, required=True)
    args = parser.parse_args()
    engine = make_engine(Settings().database_url())
    try:
        with engine.connect() as c:
            actor = LocalIdentity(engine).current(c)
        result = KnowledgeService(engine, actor, args.scope).run(
            "schedule", KnowledgeRequest(reason=args.reason)
        )
        print(json.dumps(result))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
