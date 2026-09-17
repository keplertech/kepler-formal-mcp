"""Run one verification in a separate process using the installed Python API.

Native parsers and solvers may write directly to stdout. The MCP server captures
this process's output and reads the structured verdict from a separate JSON file.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import traceback
from typing import Any


def verify(request: dict[str, Any]) -> dict[str, Any]:
    """Load the requested designs in NajaEDA and borrow them for verification."""
    # Import NajaEDA first: it arranges the shared native runtime used by KF.
    from najaeda import naja
    from .verification import OPTION_NAMES, load_designs, verify_loaded

    if naja.NLUniverse.get() is not None:
        raise RuntimeError("The verification worker requires a fresh Naja universe")

    universe = naja.NLUniverse.create()
    try:
        _databases, designs = load_designs(universe, request["input_paths"], request["liberty_files"])
        return verify_loaded(
            designs[0], designs[1],
            {name: request[name] for name in OPTION_NAMES if name in request},
        )
    finally:
        # This universe belongs solely to this short-lived worker. The MCP
        # server and any callers' in-memory netlists are never reset.
        universe.destroy()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", type=Path)
    parser.add_argument("result", type=Path)
    arguments = parser.parse_args(argv)
    try:
        request = json.loads(arguments.request.read_text(encoding="utf-8"))
        operation = request.get("operation", "verify")
        if operation == "verify":
            result = verify(request)
        elif operation == "info":
            from .capabilities import get_capabilities

            result = get_capabilities()
        else:
            raise ValueError(f"Unknown worker operation: {operation!r}")
    except Exception as error:
        traceback.print_exc(file=sys.stderr)
        result = {
            "status": "error",
            "exit_code": -1,
            "reason": f"{type(error).__name__}: {error}",
            "equivalent": False,
            "conclusive": False,
            "coverage_percent": None,
        }
    arguments.result.write_text(json.dumps(result) + "\n", encoding="utf-8")
    return 1 if result["status"] == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
