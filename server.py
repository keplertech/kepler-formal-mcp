#!/usr/bin/env python3
"""MCP server exposing Kepler-Formal helpers.

Tools:
1) Run Kepler-Formal from an existing YAML config file.
2) Build a YAML config from provided design/library paths, then run tool #1.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import subprocess
import sys

from mcp.server.fastmcp import FastMCP


app = FastMCP("kepler-formal")

def _workspace_root() -> Path:
	return Path(__file__).resolve().parent


def _default_ai_output_dir() -> Path:
	"""Return the workspace-local default writable folder for AI outputs."""
	return _workspace_root()


# Writable folder for AI-generated outputs (yaml/log).
AI_OUTPUT_DIR = _default_ai_output_dir()

# Keep stdout dedicated to MCP JSON-RPC messages.
logging.basicConfig(
	level=logging.INFO,
	stream=sys.stderr,
	format="[kepler-mcp] [%(levelname)s] %(message)s",
	force=True,
)



def _binary_path() -> Path:
	# Prefer the submodule build tree under thirdparty/.
	candidates = [
		_workspace_root() / "thirdparty" / "kepler-formal" / "build" / "src" / "bin" / "kepler-formal",
		_workspace_root() / "build" / "src" / "bin" / "kepler-formal",
	]
	for candidate in candidates:
		resolved = candidate.resolve()
		if resolved.exists():
			return resolved
	return candidates[-1].resolve()


def _resolve_path(path_value: str) -> Path:
	path = Path(path_value)
	if path.is_absolute():
		return path
	return (_workspace_root() / path).resolve()


def get_allowed_dirs(allowed_output_dir: str | None = None) -> list[Path]:
	"""Return allowed writable directories for AI outputs.

	Priority:
	1) explicit tool parameter
	2) KEPLER_FORMAL_AI_OUTPUT_DIR environment variable
	3) workspace root
	"""
	if allowed_output_dir:
		return [Path(allowed_output_dir).expanduser().resolve()]

	env_value = os.environ.get("KEPLER_FORMAL_AI_OUTPUT_DIR")
	env_dir = Path(env_value) if env_value else _default_ai_output_dir()
	return [env_dir.expanduser().resolve()]


def _yaml_output_path_in_ai_dir(yaml_output_path: str, output_root: Path) -> Path:
	"""Resolve YAML output while preserving the caller-provided path."""
	candidate = Path(yaml_output_path).expanduser()
	if not candidate.name:
		candidate = Path("test_config_verilog.yaml")
	if candidate.is_absolute():
		return candidate.resolve()
	return (output_root / candidate).resolve()


def _ensure_allowed(path: Path, allowed_dirs: list[Path] | None = None) -> Path:
	resolved = path.expanduser().resolve()
	dirs = allowed_dirs if allowed_dirs is not None else get_allowed_dirs()
	for allowed_dir in dirs:
		try:
			resolved.relative_to(allowed_dir)
			return resolved
		except ValueError:
			continue
	raise ValueError(f"Path not allowed: {resolved}")


def _read_yaml_log_file(yaml_text: str, yaml_dir: Path) -> Path | None:
	for raw_line in yaml_text.splitlines():
		line = raw_line.strip()
		if not line or line.startswith("#"):
			continue
		if not line.startswith("log_file:"):
			continue

		value = line.split(":", 1)[1].strip()
		if (value.startswith('"') and value.endswith('"')) or (
			value.startswith("'") and value.endswith("'")
		):
			value = value[1:-1]
		if not value:
			return None

		path = Path(value)
		if not path.is_absolute():
			path = (yaml_dir / path).resolve()
		return path
	return None


def _normalize_log_file_path(candidate: Path, fallback_base: Path, allowed_dirs: list[Path]) -> Path:
	"""Return an allowed log file path rooted at the caller-provided base directory."""
	if candidate.is_absolute():
		allowed_candidate = candidate.resolve()
	else:
		allowed_candidate = (fallback_base / candidate).resolve()

	if allowed_candidate.name in {"", ".", ".."}:
		allowed_candidate = (fallback_base / "kepler-formal.log").resolve()
	elif allowed_candidate.suffix.lower() not in {".log", ".txt"}:
		allowed_candidate = allowed_candidate.with_suffix(".log")

	return _ensure_allowed(allowed_candidate, allowed_dirs)


def _fix_yaml_log_file(yaml_path: Path, yaml_text: str, log_file_path: Path) -> None:
	lines = yaml_text.splitlines()
	log_line = f"log_file: {json.dumps(str(log_file_path))}"
	for index, raw_line in enumerate(lines):
		if raw_line.strip().startswith("log_file:"):
			lines[index] = log_line
			yaml_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
			return

	lines.append(log_line)
	yaml_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _format_result(result: subprocess.CompletedProcess[str], yaml_path: Path) -> dict[str, object]:
	return {
		"status": "success" if result.returncode == 0 else "error",
		"exit_code": result.returncode,
		"yaml_config": str(yaml_path),
		"stdout_tail": "\n".join(result.stdout.splitlines()[-120:]),
		"stderr_tail": "\n".join(result.stderr.splitlines()[-120:]),
	}


def _run_from_yaml(
	yaml_path: Path,
	timeout_seconds: int,
	allowed_dirs: list[Path],
	log_file_name: str | None = None,
) -> dict[str, object]:
	binary = _binary_path()
	if not binary.exists():
		return {
			"status": "error",
			"exit_code": -1,
			"yaml_config": str(yaml_path),
			"stdout_tail": "",
			"stderr_tail": f"Kepler-Formal binary not found: {binary}",
		}

	if not yaml_path.exists():
		return {
			"status": "error",
			"exit_code": -1,
			"yaml_config": str(yaml_path),
			"stdout_tail": "",
			"stderr_tail": f"YAML file not found: {yaml_path}",
		}

	yaml_text = yaml_path.read_text(encoding="utf-8")
	log_file_path = _read_yaml_log_file(yaml_text, yaml_path.parent)
	if log_file_name:
		log_file_path = Path(log_file_name)
	if log_file_path is None:
		normalized_log_file_path = _normalize_log_file_path(yaml_path.with_suffix(".log"), yaml_path.parent, allowed_dirs)
		_fix_yaml_log_file(yaml_path, yaml_text, normalized_log_file_path)
		yaml_text = yaml_path.read_text(encoding="utf-8")
		log_file_path = normalized_log_file_path
	else:
		normalized_log_file_path = _normalize_log_file_path(log_file_path, yaml_path.parent, allowed_dirs)
		if normalized_log_file_path != log_file_path:
			_fix_yaml_log_file(yaml_path, yaml_text, normalized_log_file_path)
			yaml_text = yaml_path.read_text(encoding="utf-8")
		log_file_path = normalized_log_file_path

	log_file_path.parent.mkdir(parents=True, exist_ok=True)

	try:
		result = subprocess.run(
			[str(binary), "--config", str(yaml_path)],
			cwd=str(_workspace_root()),
			capture_output=True,
			text=True,
			timeout=timeout_seconds,
			check=False,
		)
	except subprocess.TimeoutExpired:
		return {
			"status": "error",
			"exit_code": -1,
			"yaml_config": str(yaml_path),
			"stdout_tail": "",
			"stderr_tail": f"Kepler-Formal timed out after {timeout_seconds} seconds",
		}

	if result.returncode == 0 and not log_file_path.exists():
		return {
			"status": "error",
			"exit_code": result.returncode,
			"yaml_config": str(yaml_path),
			"stdout_tail": "\n".join(result.stdout.splitlines()[-120:]),
			"stderr_tail": f"Expected log file was not created: {log_file_path}",
			"generated_log_file": str(log_file_path),
		}

	formatted_result = _format_result(result, yaml_path)
	formatted_result["generated_log_file"] = str(log_file_path)
	return formatted_result


@app.tool()
def run_kepler_formal_yaml(
	yaml_file: str,
	timeout_seconds: int = 600,
	log_file_name: str | None = None,
	allowed_output_dir: str | None = None,
) -> str:
	"""Run Kepler-Formal from an existing YAML file.

	Args:
		yaml_file: Path to YAML config file.
		timeout_seconds: Timeout for command execution.
		log_file_name: Optional log filename/path. Final location is forced under allowed_output_dir root.
		allowed_output_dir: Optional writable directory override for this run.
	"""
	yaml_path = _resolve_path(yaml_file)
	allowed_dirs = get_allowed_dirs(allowed_output_dir)
	for allowed_dir in allowed_dirs:
		allowed_dir.mkdir(parents=True, exist_ok=True)
	output = _run_from_yaml(
		yaml_path=yaml_path,
		timeout_seconds=timeout_seconds,
		allowed_dirs=allowed_dirs,
		log_file_name=log_file_name,
	)
	return json.dumps(output, indent=2)


@app.tool()
def create_yaml_and_run_kepler_formal(
	input_paths: list[str],
	liberty_files: list[str],
	yaml_output_path: str = "test_config_verilog.yaml",
	log_level: str = "info",
	solver: str = "kissat",
	cnf_export: bool = True,
	cnf_export_path: str = "./sat.cnf",
	log_file_name: str | None = None,
	allowed_output_dir: str | None = None,
	timeout_seconds: int = 600,
) -> str:
	"""Create a verilog YAML config file from provided data and run Kepler-Formal.

	Args:
		input_paths: Usually [golden_verilog, revised_verilog].
		liberty_files: List of .lib files.
		yaml_output_path: Where to write YAML config.
		log_level: YAML log_level value.
		solver: YAML solver value.
		cnf_export: YAML cnf_export value.
		cnf_export_path: YAML cnf_export_path value.
		log_file_name: Optional log filename/path. Final location is forced under allowed_output_dir root.
		allowed_output_dir: Optional writable directory override for this run.
		timeout_seconds: Timeout for Kepler-Formal run.
	"""
	if len(input_paths) < 2:
		return json.dumps(
			{
				"status": "error",
				"exit_code": -1,
				"stdout_tail": "",
				"stderr_tail": "input_paths must contain at least 2 files",
			},
			indent=2,
		)

	resolved_inputs = [str(_resolve_path(p)) for p in input_paths]
	resolved_libs = [str(_resolve_path(p)) for p in liberty_files]
	allowed_dirs = get_allowed_dirs(allowed_output_dir)
	for allowed_dir in allowed_dirs:
		allowed_dir.mkdir(parents=True, exist_ok=True)
	yaml_path = _ensure_allowed(_yaml_output_path_in_ai_dir(yaml_output_path, allowed_dirs[0]), allowed_dirs)
	yaml_path.parent.mkdir(parents=True, exist_ok=True)
	requested_log = Path(log_file_name) if log_file_name else yaml_path.with_suffix(".log")
	log_file_path = _normalize_log_file_path(requested_log, yaml_path.parent, allowed_dirs)

	lines: list[str] = [
		"format: verilog",
		"input_paths:",
	]
	for path in resolved_inputs:
		lines.append(f"  - {json.dumps(path)}")

	lines.append("liberty_files:")
	for path in resolved_libs:
		lines.append(f"  - {json.dumps(path)}")

	lines.extend(
		[
			f"log_level: {log_level}",
			f"solver: {solver}",
			f"cnf_export: {'true' if cnf_export else 'false'}",
			f"cnf_export_path: {cnf_export_path}",
			f"log_file: {json.dumps(str(log_file_path))}",
		]
	)

	yaml_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
	log_file_path.parent.mkdir(parents=True, exist_ok=True)

	run_result = _run_from_yaml(
		yaml_path=yaml_path,
		timeout_seconds=timeout_seconds,
		allowed_dirs=allowed_dirs,
		log_file_name=log_file_name,
	)
	run_result["generated_yaml"] = str(yaml_path)
	run_result["generated_log_file"] = str(log_file_path)
	run_result["generated_yaml_preview"] = "\n".join(lines)
	return json.dumps(run_result, indent=2)


if __name__ == "__main__":
	logging.info("Starting kepler-formal MCP server")
	app.run()
