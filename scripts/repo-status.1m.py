#!/usr/bin/env python3
"""SwiftBar/xbar plugin for local Hermes repo status."""

import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = ROOT / "venv" / "bin" / "python"
if sys.version_info < (3, 10) and VENV_PYTHON.exists():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), *sys.argv])

sys.path.insert(0, str(ROOT))

from hermes_cli.repo_status import format_swiftbar_menu  # noqa: E402


if __name__ == "__main__":
    print(
        format_swiftbar_menu(
            ["--fetch-stale", "--fetch-interval", "300", *sys.argv[1:]]
        )
    )
