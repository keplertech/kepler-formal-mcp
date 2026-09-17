"""Shared NajaEDA loading and native verification for isolated or live sessions."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any


OPTION_NAMES = (
    "mode", "solver", "max_k", "sec_engine", "sec_encoding",
    "allow_boundary_mismatch", "report_skipped_outputs", "log_file", "log_level",
)


def load_designs(universe: Any, input_paths: list[str], liberty_files: list[str]):
    """Load two independent databases, rolling back only these databases on error."""
    from najaeda import naja

    if not isinstance(input_paths, list) or len(input_paths) != 2:
        raise ValueError("input_paths must contain exactly two Verilog files")
    if not isinstance(liberty_files, list):
        raise ValueError("liberty_files must be a list of paths")
    for path in input_paths + liberty_files:
        if not isinstance(path, str) or not Path(path).is_absolute() or not Path(path).is_file():
            raise ValueError(f"Input must be an existing absolute file path: {path!r}")

    databases, designs = [], []
    try:
        for path in input_paths:
            database = naja.NLDB.create(universe)
            databases.append(database)
            if liberty_files:
                database.loadLibertyPrimitives(liberty_files)
            database.loadVerilog([path])
            design = database.getTopDesign()
            if design is None:
                raise RuntimeError(f"NajaEDA did not find a top design in {path}")
            designs.append(design)
        return databases, designs
    except Exception:
        for database in reversed(databases):
            database.destroy()
        raise


def verify_loaded(design1: Any, design2: Any, options: dict[str, Any]) -> dict[str, Any]:
    """Borrow existing designs and return only owning, JSON-compatible result data."""
    from kepler_formal import VerificationOptions, verify_designs

    if not isinstance(options, dict):
        raise ValueError("options must be a mapping")
    unknown = set(options) - set(OPTION_NAMES)
    if unknown:
        raise ValueError("Unsupported verification options: " + ", ".join(sorted(map(str, unknown))))
    result = verify_designs(design1, design2, options=VerificationOptions(**options))
    serialized = asdict(result)
    serialized.update(
        status=result.status.value,
        equivalent=result.equivalent,
        conclusive=result.conclusive,
        coverage_percent=result.coverage_percent,
    )
    return serialized
