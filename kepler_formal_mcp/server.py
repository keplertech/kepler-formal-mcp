"""MCP tools backed by the published Kepler Formal Python library."""

from __future__ import annotations

import json
import logging
from pathlib import Path
import sys

from mcp.server.fastmcp import FastMCP
import yaml

from . import config, runner
from .options import LogLevel, Mode, SecEncoding, SecEngine, Solver


app = FastMCP("kepler-formal")


@app.tool()
def get_kepler_formal_info() -> str:
    """Report the installed versions, build revision, and supported Python API.

    Lists verification modes, solvers, SEC engines/encodings, option defaults,
    result fields and statuses from the installed Kepler library. Native design
    handles are process-local Python objects and cannot be passed through MCP.
    """
    try:
        result = runner.get_info()
    except (OSError, ValueError) as error:
        result = runner.error_result(str(error))
    return json.dumps(result, indent=2)


@app.tool()
def run_kepler_formal_yaml(
    yaml_file: str,
    timeout_seconds: int = 600,
    log_file_name: str | None = None,
    allowed_output_dir: str | None = None,
) -> str:
    """Compare two Verilog designs using an existing YAML config and the Python API.

    Relative input/library/log paths are relative to the YAML file. The config
    is never rewritten. Logs must be under allowed_output_dir (or the
    KEPLER_FORMAL_AI_OUTPUT_DIR environment setting; default: launch directory).
    `verdict` reports equivalence independently of operation `status`/exit_code.
    Unsupported CLI-only YAML options are rejected, including CNF export.
    """
    yaml_path = None
    try:
        yaml_path = config.resolve_path(yaml_file, Path.cwd())
        root = config.output_root(allowed_output_dir)
        request = config.normalize(config.load_yaml(yaml_path), yaml_path, root, log_file_name)
        result = runner.run(request, yaml_path, timeout_seconds)
    except (OSError, ValueError, yaml.YAMLError) as error:
        result = runner.error_result(str(error), yaml_path)
    return json.dumps(result, indent=2)


@app.tool()
def create_yaml_and_run_kepler_formal(
    input_paths: list[str],
    liberty_files: list[str],
    yaml_output_path: str = "test_config_verilog.yaml",
    log_level: LogLevel | None = "info",
    solver: Solver = "kissat",
    cnf_export: bool = False,
    cnf_export_path: str = "./sat.cnf",
    log_file_name: str | None = None,
    allowed_output_dir: str | None = None,
    timeout_seconds: int = 600,
    verification: Mode = "lec",
    max_k: int | None = None,
    sec_engine: SecEngine | None = None,
    sec_encoding: SecEncoding | None = None,
    allow_boundary_mismatch: bool = False,
    report_skipped_outputs: bool = False,
) -> str:
    """Load exactly two Verilog designs in NajaEDA and check them with Kepler Python.

    Input/library paths are relative to the launch directory. YAML output is
    relative to allowed_output_dir (or KEPLER_FORMAL_AI_OUTPUT_DIR; default:
    launch directory). log_file_name is relative to the generated YAML file.
    Use verification='lec' or 'sec'; max_k/SEC engine/encoding apply only to SEC.
    CNF export is unavailable in the Python API and cnf_export must stay false.
    Read `verdict` for equivalent/different/inconclusive, not the native exit code.
    """
    yaml_path = None
    try:
        root = config.output_root(allowed_output_dir)
        yaml_path = config.allowed_path(config.resolve_path(yaml_output_path, root), root)
        document = {
            "format": "verilog",
            "input_paths": [str(config.resolve_path(path, Path.cwd())) for path in input_paths],
            "liberty_files": [str(config.resolve_path(path, Path.cwd())) for path in liberty_files],
            "log_level": log_level, "solver": solver, "cnf_export": cnf_export,
            "verification": verification, "max_k": max_k, "sec_engine": sec_engine,
            "sec_encoding": sec_encoding, "allow_boundary_mismatch": allow_boundary_mismatch,
            "report_skipped_outputs": report_skipped_outputs,
        }
        request = config.normalize(document, yaml_path, root, log_file_name)
        if str(yaml_path) in request["input_paths"] + request["liberty_files"]:
            raise ValueError("The YAML output must not overwrite an input file")
        if type(timeout_seconds) is not int or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be a positive integer")
        document["log_file"] = request["log_file"]
        document = {key: value for key, value in document.items()
                    if value is not None or key == "log_level"}
        text = yaml.safe_dump(document, sort_keys=False)
        yaml_path.parent.mkdir(parents=True, exist_ok=True)
        yaml_path.write_text(text, encoding="utf-8")
        result = runner.run(request, yaml_path, timeout_seconds)
        result.update(generated_yaml=str(yaml_path), generated_yaml_preview=text)
    except (OSError, ValueError, yaml.YAMLError) as error:
        result = runner.error_result(str(error), yaml_path)
    return json.dumps(result, indent=2)


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="[kepler-mcp] [%(levelname)s] %(message)s")
    app.run()
