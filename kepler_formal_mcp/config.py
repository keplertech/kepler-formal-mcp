"""Translate file-based MCP requests into Python verification options."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, get_args

import yaml

from .options import LogLevel, Mode, SecEncoding, SecEngine, Solver


SUPPORTED_KEYS = {
    "format", "input_paths", "liberty_files", "verification", "mode", "solver",
    "max_k", "sec_engine", "sec_encoding", "allow_boundary_mismatch",
    "report_skipped_outputs", "log_file", "log_level", "cnf_export", "cnf_export_path",
    "set_as_boundary", "learn_internal_relations", "allow_x_equality_in_internal_relations",
    "binary_relation_round", "latch_abstraction", "data_latch_model",
    "sec_reset_cycles", "sec_reset_ports", "btor2_export_path", "btor2_dump_only",
}
# SEC proof-model switches. The library enables them, and only SEC may disable them.
SEC_MODEL_OPTIONS = (
    "learn_internal_relations", "allow_x_equality_in_internal_relations",
    "binary_relation_round", "latch_abstraction", "data_latch_model",
)


def resolve_path(value: str, base: Path) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Paths must be nonempty strings")
    path = Path(value).expanduser()
    return (path if path.is_absolute() else base / path).resolve()


def output_root(override: str | None = None) -> Path:
    return resolve_path(
        override or os.environ.get("KEPLER_FORMAL_AI_OUTPUT_DIR") or str(Path.cwd()),
        Path.cwd(),
    )


def allowed_path(path: Path, root: Path) -> Path:
    path = path.resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"Path not allowed outside output directory {root}: {path}")
    return path


def load_yaml(path: Path) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("YAML config must be a mapping")
    return document


def _boolean(config: dict, key: str) -> bool:
    value = config.get(key, False)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


def _pairs(config: dict, key: str, second: type, shape: str) -> list[list]:
    """Read a list of [nonempty string, ``second`` value] entries; null means none."""
    value = config.get(key)
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{key} must be a list of {shape} pairs")
    for pair in value:
        if (not isinstance(pair, (list, tuple)) or len(pair) != 2
                or not isinstance(pair[0], str) or not pair[0]
                or not isinstance(pair[1], second) or pair[1] == ""):
            raise ValueError(f"Each {key} entry must be a {shape} pair")
    return [list(pair) for pair in value]


def normalize(config: dict, yaml_path: Path, root: Path,
              log_file_name: str | None = None) -> dict[str, Any]:
    unknown = set(config) - SUPPORTED_KEYS
    if unknown:
        raise ValueError("Unsupported Python-library settings: " + ", ".join(sorted(map(str, unknown))))
    if config.get("format", "verilog") != "verilog":
        raise ValueError("The MCP Python-library loader supports format: verilog")
    if _boolean(config, "cnf_export"):
        raise ValueError("CNF export is not supported by the Kepler Python library; set cnf_export: false")

    inputs = config.get("input_paths")
    if not isinstance(inputs, list) or len(inputs) != 2:
        raise ValueError("input_paths must contain exactly two Verilog files")
    libraries = config.get("liberty_files")
    if libraries is None:
        libraries = []
    if not isinstance(libraries, list):
        raise ValueError("liberty_files must be a list of paths")
    resolved_inputs = [resolve_path(value, yaml_path.parent) for value in inputs]
    resolved_libraries = [resolve_path(value, yaml_path.parent) for value in libraries]
    for path in resolved_inputs + resolved_libraries:
        if not path.is_file():
            raise ValueError(f"Input file not found: {path}")

    mode = config.get("verification", config.get("mode", "lec"))
    if "mode" in config and config["mode"] != mode:
        raise ValueError("mode and verification must agree")
    result = {
        "input_paths": [str(path) for path in resolved_inputs],
        "liberty_files": [str(path) for path in resolved_libraries],
        "mode": mode,
        "solver": config.get("solver", "kissat"),
        "log_level": config.get("log_level", "info"),
        "allow_boundary_mismatch": _boolean(config, "allow_boundary_mismatch"),
        "report_skipped_outputs": _boolean(config, "report_skipped_outputs"),
    }
    for key, choices in (("mode", get_args(Mode)),
                         ("solver", get_args(Solver)),
                         ("log_level", (*get_args(LogLevel), None))):
        if result[key] not in choices:
            raise ValueError(f"{key} must be one of: {', '.join(map(str, choices))}")
    for key, choices in (("sec_engine", get_args(SecEngine)),
                         ("sec_encoding", get_args(SecEncoding))):
        value = config.get(key)
        if value is not None and value not in choices:
            raise ValueError(f"{key} must be one of: {', '.join(choices)}")
        result[key] = value
    max_k = config.get("max_k")
    if max_k is not None and (type(max_k) is not int or max_k < 0):
        raise ValueError("max_k must be a non-negative integer")
    result["max_k"] = max_k
    if mode == "lec" and any(result[key] is not None for key in ("max_k", "sec_engine", "sec_encoding")):
        raise ValueError("max_k, sec_engine and sec_encoding require verification: sec")
    if mode == "sec" and result["allow_boundary_mismatch"]:
        raise ValueError("allow_boundary_mismatch is only supported for LEC")

    result["set_as_boundary"] = _pairs(config, "set_as_boundary", str, "[design1 path, design2 path]")
    # Omitted switches keep the library default instead of a copy made here.
    for key in SEC_MODEL_OPTIONS:
        if key in config:
            result[key] = _boolean(config, key)
    reset_cycles = config.get("sec_reset_cycles")
    if reset_cycles is not None and (type(reset_cycles) is not int or reset_cycles <= 0):
        raise ValueError("sec_reset_cycles must be a positive integer")
    reset_ports = _pairs(config, "sec_reset_ports", bool, "[port name, active value]")
    if len({name for name, _ in reset_ports}) != len(reset_ports):
        raise ValueError("sec_reset_ports must not repeat a port name")
    if (reset_cycles is None) != (not reset_ports):
        raise ValueError("sec_reset_cycles and sec_reset_ports must be given together")
    result.update(sec_reset_cycles=reset_cycles, sec_reset_ports=reset_ports,
                  btor2_dump_only=_boolean(config, "btor2_dump_only"))
    if mode == "lec":
        sec_only = [key for key in SEC_MODEL_OPTIONS if result.get(key) is False]
        sec_only += [key for key in ("sec_reset_cycles", "sec_reset_ports",
                                     "btor2_export_path", "btor2_dump_only") if config.get(key)]
        if sec_only:
            raise ValueError(", ".join(sec_only) + " can only be changed with verification: sec")

    requested_log = log_file_name if log_file_name is not None else config.get("log_file")
    log_file = (resolve_path(requested_log, yaml_path.parent) if requested_log is not None
                else root / f"{yaml_path.stem}.log")
    log_file = allowed_path(log_file, root)
    if log_file in resolved_inputs + resolved_libraries + [yaml_path.resolve()]:
        raise ValueError("The log file must not overwrite the YAML config or an input file")
    result["log_file"] = str(log_file)

    export = config.get("btor2_export_path")
    if export is not None:
        export = allowed_path(resolve_path(export, yaml_path.parent), root)
        if export in resolved_inputs + resolved_libraries + [yaml_path.resolve(), log_file]:
            raise ValueError("The BTOR2 export must not overwrite the YAML config, the log or an input file")
    elif result["btor2_dump_only"]:
        raise ValueError("btor2_dump_only requires btor2_export_path")
    result["btor2_export_path"] = str(export) if export is not None else None
    return result
