"""Run the installed Python library with isolated native output and a hard timeout."""

from __future__ import annotations

import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile


REPORT_FILENAMES = (
    "skipped_multi_driver_pos.txt",
    "skipped_no_driver_pos.txt",
    "skipped_logical_loop_pos.txt",
)


def read_reports(directory: Path) -> dict[str, str]:
    """Collect known native diagnostics before the isolated workspace is removed."""
    reports = {}
    for name in REPORT_FILENAMES:
        report = directory / name
        try:
            mode = report.lstat().st_mode
        except FileNotFoundError:
            continue
        # Do not follow symlinks or read directories/devices created in the
        # worker directory. Report content is returned, never copied elsewhere.
        if stat.S_ISREG(mode):
            reports[name] = report.read_text(encoding="utf-8", errors="replace")
    return reports


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


def _invoke_worker(request: dict, timeout_seconds: int, yaml_path: Path | None = None) -> dict:
    if type(timeout_seconds) is not int or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be a positive integer")
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
            result.update(stdout_tail=tail(error.stdout), reports=read_reports(work))
            return result
        if not result_path.is_file():
            result = error_result("Kepler Python worker exited without a result", yaml_path)
            result.update(exit_code=completed.returncode, stdout_tail=tail(completed.stdout),
                          stderr_tail=tail(completed.stderr) or result["stderr_tail"],
                          reports=read_reports(work))
            return result
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        status = "error" if completed.returncode != 0 or payload["status"] == "error" else "success"
        return {
            "status": status,
            "exit_code": completed.returncode,
            "payload": payload,
            "stdout_tail": tail(completed.stdout),
            "stderr_tail": tail(completed.stderr),
            "reports": read_reports(work),
        }


def get_info() -> dict:
    """Query installed versions and API choices without importing native code here."""
    result = _invoke_worker({"operation": "info"}, timeout_seconds=30)
    payload = result.pop("payload", {})
    # Preserve process failures even if native code wrote a successful payload.
    status = result["status"]
    result.update(payload)
    result["status"] = status
    return result


def run(request: dict, yaml_path: Path, timeout_seconds: int) -> dict:
    if type(timeout_seconds) is not int or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be a positive integer")
    log_path = Path(request["log_file"])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    result = _invoke_worker(request, timeout_seconds, yaml_path)
    verification = result.pop("payload", None)
    result.update(yaml_config=str(yaml_path), generated_log_file=str(log_path))
    if verification is not None:
        result.update(exit_code=verification["exit_code"], verdict=verification["status"],
                      verification_result=verification)
    result["log_tail"] = (tail(log_path.read_text(encoding="utf-8", errors="replace"))
                          if log_path.is_file() else "")
    return result
