"""Launch the ShirOS stdio MCP bridge from any working directory."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run() -> None:
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT / "src"))
    from shiros.memory_bridge import main

    main()


if __name__ == "__main__":
    run()
