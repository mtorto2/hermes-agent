#!/usr/bin/env python3
"""SwiftBar/xbar plugin for local Hermes repo status."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hermes_cli.repo_status import format_swiftbar_menu  # noqa: E402


if __name__ == "__main__":
    print(format_swiftbar_menu(sys.argv[1:]))
