"""Run the installed Python library with isolated native output and a hard timeout."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile


def tail(text: str | bytes | None) -> str:
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    return "\n".join((text or "").splitlines()[-120:])


def error_result(message: str, yaml_path: Path | None = None) -> dict:
    return {
        "status": "error", "exit_code": -1, "verdict": "error",
        "yaml_config": str(yaml_path) if yaml_path is not None else None,
        "stdout_tail": "", "stderr_tail": message,
    }


def run(request: dict, yaml_path: Path, timeout_seconds: int) -> dict:
    if type(timeout_seconds) is not int or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be a positive integer")
    log_path = Path(request["log_file"])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # Native code can write directly to stdout and cannot be interrupted by a
    # Python thread timeout. A worker keeps both behaviors away from MCP stdio.
    with tempfile.TemporaryDirectory(prefix="kepler-mcp-") as directory:
        work = Path(directory)
        request_path, result_path = work / "request.json", work / "result.json"
        request_path.write_text(json.dumps(request), encoding="utf-8")
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "kepler_formal_mcp.worker", str(request_path), str(result_path)],
                cwd=work, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=timeout_seconds, check=False,
            )
        except subprocess.TimeoutExpired as error:
            result = error_result(f"Kepler-Formal timed out after {timeout_seconds} seconds", yaml_path)
            result.update(stdout_tail=tail(error.stdout), generated_log_file=str(log_path))
            return result
        if not result_path.is_file():
            result = error_result("Kepler Python worker exited without a verification result", yaml_path)
            result.update(exit_code=completed.returncode, stdout_tail=tail(completed.stdout),
                          stderr_tail=tail(completed.stderr) or result["stderr_tail"],
                          generated_log_file=str(log_path))
            return result
        verification = json.loads(result_path.read_text(encoding="utf-8"))
        status = "error" if completed.returncode != 0 or verification["status"] == "error" else "success"
        return {
            "status": status,
            "exit_code": verification["exit_code"],
            "verdict": verification["status"],
            "verification_result": verification,
            "yaml_config": str(yaml_path),
            "generated_log_file": str(log_path),
            "stdout_tail": tail(completed.stdout),
            "stderr_tail": tail(completed.stderr),
            "log_tail": tail(log_path.read_text(encoding="utf-8", errors="replace")) if log_path.is_file() else "",
        }
