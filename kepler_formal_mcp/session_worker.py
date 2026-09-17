"""Own a persistent Naja session until the parent closes the stdin pipe."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .session_bridge import SessionBridge


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connection-file", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    arguments = parser.parse_args(argv)
    with SessionBridge(arguments.output_dir, arguments.connection_file, owned=True):
        # Native stdout/stderr belong to this managed process and are captured
        # by its parent. The TCP response channel carries only JSON messages.
        while sys.stdin.buffer.read(8192):
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
