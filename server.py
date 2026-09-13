#!/usr/bin/env python3
"""SEC-only MCP adapter for an explicitly installed Kepler Formal executable."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
from typing import Literal

from mcp.server.fastmcp import FastMCP
import yaml

from formal_results import VerificationResult, parse_sec_result


app = FastMCP("kepler-formal")
MAX_YAML_BYTES = 1024 * 1024
MAX_TIMEOUT_SECONDS = 3600
SKIPPED_REPORTS = {"skipped_multi_driver_pos.txt", "skipped_logical_loop_pos.txt", "skipped_no_driver_pos.txt"}
CONFIG_KEYS = {
    "format", "input_paths", "liberty_files", "verification", "report_skipped_pos",
    "max_k", "sec_engine", "sec_encoding", "log_level", "solver", "compact_mode",
    "cnf_export", "cnf_export_path", "log_file", "verilog_design1_top", "verilog_design2_top",
}


class UniqueKeyLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in result:
            raise ValueError("YAML mapping keys must be unique strings")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def _workspace_root() -> Path:
    return Path(os.environ.get("KEPLER_FORMAL_WORKSPACE", Path.cwd())).expanduser().resolve()


def _within(path: Path, root: Path) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"Path is outside the configured workspace/output root: {resolved}")
    return resolved


def _input_file(value: str, base: Path) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("Input paths must be nonempty strings")
    path = Path(value).expanduser()
    path = _within(path if path.is_absolute() else base / path, _workspace_root())
    if not path.is_file():
        raise ValueError(f"Input file does not exist: {path}")
    return path


def _output_root(requested: str | None) -> Path:
    configured = Path(os.environ.get("KEPLER_FORMAL_AI_OUTPUT_DIR", _workspace_root() / "runs")).expanduser().resolve()
    if requested:
        path = Path(requested).expanduser()
        configured = _within(path if path.is_absolute() else configured / path, configured)
    configured.mkdir(parents=True, exist_ok=True)
    return configured


def _binary_path() -> Path:
    configured = os.environ.get("KEPLER_FORMAL_BIN")
    if configured:
        candidate = Path(configured).expanduser()
        if not candidate.is_absolute():
            raise ValueError("KEPLER_FORMAL_BIN must be an absolute executable path")
    else:
        found = shutil.which("kepler-formal")
        if not found:
            raise ValueError("Install packaged Kepler Formal, then set KEPLER_FORMAL_BIN or PATH. No source-build fallback is used.")
        candidate = Path(found)
    candidate = candidate.resolve()
    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        raise ValueError(f"Kepler Formal is not executable: {candidate}")
    return candidate


def _relative_name(value: str, label: str) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError(f"Invalid {label}")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or path == Path("."):
        raise ValueError(f"{label} must be a relative path inside the fresh run directory")
    return path


def _normalize_config(config: dict, base: Path) -> dict:
    if not isinstance(config, dict):
        raise ValueError("Configuration must be a YAML mapping")
    unknown = set(config) - CONFIG_KEYS
    if unknown:
        raise ValueError(f"Unsupported configuration fields: {sorted(unknown)}")
    if config.get("format", "verilog") != "verilog":
        raise ValueError("This adapter currently accepts mapped Verilog configurations, not RTL/flist formats")
    if config.get("verification", "sec") != "sec":
        raise ValueError("This MCP server requires SEC; LEC cannot be selected")
    if config.get("report_skipped_pos", True) is not True:
        raise ValueError("Skipped-output reporting cannot be disabled")
    if config.get("cnf_export", False) is not False:
        raise ValueError("CNF export is incompatible with SEC; use cnf_export: false")
    data = dict(config, format="verilog", verification="sec", report_skipped_pos=True, cnf_export=False)
    for key, options, default in (
        ("sec_engine", {"pdr", "imc", "k_induction"}, "pdr"),
        ("sec_encoding", {"binary", "dual_rail_steady"}, "dual_rail_steady"),
        ("solver", {"kissat", "cadical", "glucose"}, "kissat"),
        ("log_level", {"info", "debug", "trace"}, "info"),
    ):
        value = data.setdefault(key, default)
        if not isinstance(value, str) or value not in options:
            raise ValueError(f"Invalid {key}: {value!r}")
    bound = data.setdefault("max_k", 32)
    if type(bound) is not int or bound < 0:
        raise ValueError("max_k must be a nonnegative integer")
    if type(data.setdefault("compact_mode", False)) is not bool:
        raise ValueError("compact_mode must be boolean")
    for key in ("verilog_design1_top", "verilog_design2_top"):
        if key in data and (not isinstance(data[key], str) or not data[key].strip()):
            raise ValueError(f"{key} must be a nonempty string")
    for key in ("input_paths", "liberty_files"):
        paths = data.get(key, [])
        if not isinstance(paths, list) or (key == "input_paths" and len(paths) != 2):
            raise ValueError("input_paths must contain exactly two files; liberty_files must be a list")
        resolved = [_input_file(value, base) for value in paths]
        if key == "liberty_files" and any(path.suffix.lower() != ".lib" for path in resolved):
            raise ValueError("liberty_files accepts only Liberty .lib files, not executable primitive libraries")
        data[key] = [str(path) for path in resolved]
    return data


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _save(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _tail(path: Path) -> str:
    if not path.is_file():
        return ""
    with path.open("rb") as stream:
        stream.seek(0, 2)
        stream.seek(max(0, stream.tell() - 16384))
        return stream.read().decode("utf-8", errors="replace")


def _execute(command: list[str], run: Path, timeout: int) -> tuple[int, bool]:
    with (run / "stdout.log").open("w") as stdout, (run / "stderr.log").open("w") as stderr:
        process = subprocess.Popen(command, cwd=run, stdin=subprocess.DEVNULL,
                                   stdout=stdout, stderr=stderr, start_new_session=os.name == "posix")
        try:
            return process.wait(timeout=timeout), False
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            process.wait()
            return process.returncode, True


def _run(config: dict, *, base: Path, timeout_seconds: int = 600,
         allowed_output_dir: str | None = None, yaml_output_path: str = "config.yaml",
         log_file_name: str | None = None, source_yaml: str | None = None) -> VerificationResult:
    result = VerificationResult()
    run = None
    started = time.monotonic()
    try:
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= MAX_TIMEOUT_SECONDS:
            raise ValueError(f"timeout_seconds must be between 1 and {MAX_TIMEOUT_SECONDS}")
        binary = _binary_path()
        data = _normalize_config(config, base)
        yaml_name = _relative_name(yaml_output_path, "yaml_output_path")
        log_name = _relative_name(log_file_name or data.get("log_file", "kepler.log"), "log_file")
        cnf_name = _relative_name(data.get("cnf_export_path", "sat.cnf"), "cnf_export_path")
        reserved = {"inputs", "stdout.log", "stderr.log", "result.json", "command.json",
                    "input-manifest.json", "request.yaml", "boundary_terms.txt"}
        names = (yaml_name, log_name, cnf_name)
        if len(set(names)) != 3 or any(p.parts[0] in reserved or p.parts[0].startswith(("skipped", "miter_log_")) for p in names):
            raise ValueError("Output names must be distinct and cannot overwrite evidence or inputs")
        if any(a in b.parents for a in names for b in names if a != b):
            raise ValueError("Conflicting output paths")
        run = Path(tempfile.mkdtemp(prefix="sec-", dir=_output_root(allowed_output_dir)))
        result.run_directory = str(run)
        inputs = run / "inputs"
        inputs.mkdir()
        manifest = []
        for key in ("input_paths", "liberty_files"):
            copies = []
            for index, value in enumerate(data[key]):
                source = Path(value)
                target = inputs / f"{key}-{index}{source.suffix}"
                before = _digest(source)
                shutil.copyfile(source, target)
                if _digest(target) != before or _digest(source) != before:
                    raise ValueError("Input changed while being snapshotted")
                target.chmod(0o444)
                manifest.append({"source": value, "snapshot": str(target), "sha256": before})
                result.input_hashes[value] = before
                copies.append(str(target))
            data[key] = copies
        if source_yaml is not None:
            (run / "request.yaml").write_text(source_yaml, encoding="utf-8")
        _save(run / "input-manifest.json", manifest)
        yaml_path, log_path = run / yaml_name, run / log_name
        for path in (yaml_path, log_path):
            path.parent.mkdir(parents=True, exist_ok=True)
        data.update(log_file=str(log_path), cnf_export_path=str(run / cnf_name))
        yaml_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        result.yaml_config = str(yaml_path)
        result.generated_log_file = str(log_path)
        result.command = [str(binary), "--config", str(yaml_path)]
        _save(run / "command.json", {"argv": result.command, "cwd": str(run), "timeout_seconds": timeout_seconds})
        result.exit_code, result.timed_out = _execute(result.command, run, timeout_seconds)
        if result.timed_out:
            result.message = "Kepler Formal timed out; no equivalence claim is made."
        elif not log_path.is_file() or not log_path.stat().st_size:
            result.message = "Kepler did not produce the required fresh proof log."
        else:
            verdict = parse_sec_result(log_path.read_text(encoding="utf-8", errors="replace"), result.exit_code)
            for field in ("status", "equivalent", "blocking", "coverage", "engine", "encoding", "bound", "message", "warnings"):
                setattr(result, field, getattr(verdict, field))
            if result.engine != data["sec_engine"] or result.encoding != data["sec_encoding"]:
                raise ValueError("Kepler did not confirm the requested SEC engine and encoding")
        for item in manifest:
            if _digest(Path(item["source"])) != item["sha256"] or _digest(Path(item["snapshot"])) != item["sha256"]:
                raise ValueError("An original input or read-only snapshot changed during verification")
        result.skipped_reports = {path.name: str(path) for path in sorted(run.glob("skipped*_pos.txt")) if path.is_file()}
        if result.status != "error" and not SKIPPED_REPORTS.issubset(result.skipped_reports):
            raise ValueError("Kepler did not produce all required skipped-output reports")
        if result.status == "proved" and any(Path(path).read_text().strip() for path in result.skipped_reports.values()):
            raise ValueError("Full coverage contradicts nonempty skipped-output reports")
    except (OSError, ValueError, yaml.YAMLError) as exc:
        result.status, result.equivalent, result.blocking = "error", None, True
        result.message = str(exc)
    finally:
        result.seconds = time.monotonic() - started
        if run is not None:
            result.stdout_log, result.stderr_log = str(run / "stdout.log"), str(run / "stderr.log")
            result.stdout_tail, result.stderr_tail = _tail(run / "stdout.log"), _tail(run / "stderr.log")
            result.result_file = str(run / "result.json")
            _save(Path(result.result_file), result.model_dump())
    return result


@app.tool()
def verify_sec(golden: str, revised: str, liberty_files: list[str],
               timeout_seconds: int = 600, max_k: int = 32,
               sec_engine: Literal["pdr", "imc", "k_induction"] = "pdr",
               sec_encoding: Literal["dual_rail_steady", "binary"] = "dual_rail_steady",
               allowed_output_dir: str | None = None) -> VerificationResult:
    """Prove two mapped Verilog files with mandatory SEC and coverage reporting.

    Partial/inconclusive is a non-blocking warning, never full equivalence.
    All inputs must be under the host-configured KEPLER_FORMAL_WORKSPACE.
    allowed_output_dir can select only a child of the configured output root.
    """
    return _run({"input_paths": [golden, revised], "liberty_files": liberty_files,
                 "max_k": max_k, "sec_engine": sec_engine, "sec_encoding": sec_encoding},
                base=_workspace_root(), timeout_seconds=timeout_seconds, allowed_output_dir=allowed_output_dir)


@app.tool()
def run_kepler_formal_yaml(yaml_file: str, timeout_seconds: int = 600,
                           log_file_name: str | None = None,
                           allowed_output_dir: str | None = None) -> VerificationResult:
    """Validate and copy a mapped-Verilog YAML request; never modify the original.

    SEC and skipped-output reporting are mandatory. Unknown fields, LEC,
    CNF export and output paths outside the fresh run are rejected.
    Relative input paths resolve against the original YAML's directory.
    """
    try:
        path = _input_file(yaml_file, _workspace_root())
        with path.open("rb") as stream:
            raw = stream.read(MAX_YAML_BYTES + 1)
        if len(raw) > MAX_YAML_BYTES:
            raise ValueError("YAML request exceeds the 1 MiB limit")
        text = raw.decode("utf-8")
        config = yaml.load(text, Loader=UniqueKeyLoader)
        return _run(config, base=path.parent, timeout_seconds=timeout_seconds,
                    allowed_output_dir=allowed_output_dir, log_file_name=log_file_name, source_yaml=text)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        return VerificationResult(message=str(exc))


@app.tool()
def create_yaml_and_run_kepler_formal(input_paths: list[str], liberty_files: list[str],
                                     yaml_output_path: str = "config.yaml", log_level: str = "info",
                                     solver: str = "kissat", cnf_export: bool = False,
                                     cnf_export_path: str = "sat.cnf", log_file_name: str | None = None,
                                     allowed_output_dir: str | None = None,
                                     timeout_seconds: int = 600) -> VerificationResult:
    """Compatibility tool: generate a fresh SEC config and return a typed verdict.

    Output filenames are relative to a unique run directory. CNF export is
    disabled because Kepler does not support it for SEC. No source builds occur.
    """
    return _run({"input_paths": input_paths, "liberty_files": liberty_files,
                 "log_level": log_level, "solver": solver, "cnf_export": cnf_export,
                 "cnf_export_path": cnf_export_path}, base=_workspace_root(),
                timeout_seconds=timeout_seconds, allowed_output_dir=allowed_output_dir,
                yaml_output_path=yaml_output_path, log_file_name=log_file_name)


def main():
    logging.basicConfig(level=logging.INFO, format="[kepler-mcp] %(levelname)s %(message)s")
    app.run()


if __name__ == "__main__":
    main()
