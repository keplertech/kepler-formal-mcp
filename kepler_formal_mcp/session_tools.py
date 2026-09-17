"""MCP session tools; the native runtime stays in a worker or caller interpreter."""

from __future__ import annotations

import atexit
from functools import wraps
import json
from pathlib import Path

from . import config
from .options import LogLevel, Mode, SecEncoding, SecEngine, Solver
from .runner import error_result
from .session_manager import SessionManager
from .tool_dispatch import threaded_tool


manager = SessionManager()
atexit.register(manager.close_all)


def _json_result(function):
    @wraps(function)
    def invoke(*args, **kwargs):
        try:
            result = function(*args, **kwargs)
        except (OSError, ValueError, RuntimeError) as error:
            result = error_result(str(error))
        return json.dumps(result, indent=2)
    return invoke


@_json_result
def open_session(allowed_output_dir: str | None = None) -> str:
    """Start and select a persistent Python worker with its own NajaEDA universe."""
    return manager.open(config.output_root(allowed_output_dir))


@_json_result
def attach_session(connection_file: str) -> str:
    """Bind to SessionBridge running in an existing Python interpreter on this machine.

    The caller must register its designs in that bridge. Closing this MCP
    session only detaches; it never deletes the caller's designs or interpreter.
    """
    return manager.attach(config.resolve_path(connection_file, Path.cwd()))


@_json_result
def set_session(session_id: str) -> str:
    """Select the default session used by subsequent load_designs/verify_session calls."""
    return manager.select(session_id)


@_json_result
def list_sessions() -> str:
    """List this MCP server's sessions and the currently selected session ID."""
    return manager.list()


@_json_result
def load_designs(input_paths: list[str], liberty_files: list[str] | None = None,
                 names: list[str] | None = None, session_id: str | None = None,
                 timeout_seconds: int = 600) -> str:
    """Load two Verilog designs once into a managed session for repeated verification.

    Names default to reference/candidate and must not already exist. Inputs
    are relative to the MCP launch directory. Attached interpreters load and
    register their own designs through SessionBridge.register_design instead.
    """
    request = {"operation": "load",
               "input_paths": [str(config.resolve_path(path, Path.cwd())) for path in input_paths],
               "liberty_files": [str(config.resolve_path(path, Path.cwd())) for path in (liberty_files or [])]}
    if names is not None:
        request["names"] = names
    return manager.call(request, session_id, timeout_seconds)


@_json_result
def verify_session(design1: str = "reference", design2: str = "candidate",
                   session_id: str | None = None, verification: Mode = "lec",
                   solver: Solver = "kissat", max_k: int | None = None,
                   sec_engine: SecEngine | None = None, sec_encoding: SecEncoding | None = None,
                   allow_boundary_mismatch: bool = False, report_skipped_outputs: bool = False,
                   log_file_name: str | None = None, log_level: LogLevel | None = "info",
                   timeout_seconds: int = 600) -> str:
    """Verify registered designs in place without reopening Python or rereading files.

    Log paths are relative to the session's fixed output directory. Managed
    timeouts terminate/invalidate that session. Attached timeouts leave the
    caller alive and verification may still be running; retries can report busy.
    Attached sessions retain skipped-output details in verification-result.json
    from the Python result, never writing native reports into the caller's cwd.
    """
    return manager.call({"operation": "verify", "design1": design1, "design2": design2,
                         "options": {"mode": verification, "solver": solver, "max_k": max_k,
                                     "sec_engine": sec_engine, "sec_encoding": sec_encoding,
                                     "allow_boundary_mismatch": allow_boundary_mismatch,
                                     "report_skipped_outputs": report_skipped_outputs,
                                     "log_file": log_file_name, "log_level": log_level}},
                         session_id, timeout_seconds)


@_json_result
def get_session_reports(session_id: str | None = None, report_id: str | None = None) -> str:
    """Retrieve the latest completed proof, coverage and skipped/unproven outputs.

    Works for managed and attached sessions without reloading or verifying.
    An optional report_id rejects a stale request after another verification.
    Results include the exact native result, JSON report contents and saved path.
    An old report is historical evidence, not proof of subsequent caller edits.
    """
    return manager.call({"operation": "reports", "report_id": report_id}, session_id, 30)


@_json_result
def close_session(session_id: str | None = None) -> str:
    """Close a managed worker, or detach from a caller-owned Python interpreter."""
    return manager.close(session_id)


def register(app):
    for function in (open_session, attach_session, set_session, list_sessions,
                     load_designs, verify_session, get_session_reports, close_session):
        threaded_tool(app)(function)
