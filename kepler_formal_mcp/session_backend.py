"""Persistent design handles hosted beside their native NajaEDA universe."""

from __future__ import annotations

import os
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from .runner import REPORT_FILENAMES, read_reports, tail
from .verification import OPTION_NAMES, load_designs, verify_loaded


# Naja's universe and verification caches are shared throughout an interpreter,
# including when a caller starts more than one bridge in that interpreter.
_NATIVE_LOCK = RLock()


class DesignSession:
    """Host managed netlists or borrow live caller designs without taking ownership.

    Attached callers must hold ``lock`` while changing or destroying registered
    designs, so edits cannot race a request received by the bridge.
    """

    def __init__(self, output_dir: str | Path, owned: bool = False):
        from najaeda import naja

        self.lock = _NATIVE_LOCK
        self.session_id = str(uuid4())
        self.kind = "managed" if owned else "attached"
        self.pid = os.getpid()
        self.output_dir = Path(output_dir).expanduser().resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._owned = owned
        self._closed = False
        self._designs: dict[str, Any] = {}
        self._databases: list[Any] = []
        self._input_files: set[Path] = set()
        self._workspace = Path.cwd().resolve()
        self._universe = None
        if owned:
            if naja.NLUniverse.get() is not None:
                raise RuntimeError("A managed session requires a fresh Naja universe")
            self._universe = naja.NLUniverse.create()

    def _require_open(self):
        if self._closed:
            raise RuntimeError("The design session is closed")
        if self._owned:
            from najaeda import naja

            if naja.NLUniverse.get() is not self._universe:
                raise ReferenceError("The managed session's universe is no longer active")

    def _name(self, name: Any) -> str:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Design names must be nonempty strings")
        return name

    def _metadata(self, name: str) -> dict[str, Any]:
        result = {"name": name}
        try:
            result.update(design_name=self._designs[name].najaeda_design.getName(), valid=True)
        except (RuntimeError, ReferenceError) as error:
            result.update(valid=False, reason=str(error))
        return result

    def _inspect(self) -> dict[str, Any]:
        return {
            "status": "success", "session_id": self.session_id, "kind": self.kind,
            "pid": self.pid, "output_dir": str(self.output_dir),
            "designs": [self._metadata(name) for name in self._designs],
        }

    def register_design(self, name: str, design: Any) -> dict[str, Any]:
        """Capture an existing raw design or NajaEDA Instance without copying it."""
        from kepler_formal import NativeDesign, from_najaeda

        with self.lock:
            self._require_open()
            name = self._name(name)
            if name in self._designs:
                raise ValueError(f"A design is already registered as {name!r}")
            handle = design if isinstance(design, NativeDesign) else from_najaeda(design)
            self._designs[name] = handle
            return {"status": "success", "session_id": self.session_id, **self._metadata(name)}

    def _load(self, request: dict[str, Any]) -> dict[str, Any]:
        if not self._owned:
            raise ValueError("Attached sessions borrow caller designs; load them in NajaEDA and register them")
        names = request.get("names", ["reference", "candidate"])
        if not isinstance(names, list) or len(names) != 2:
            raise ValueError("names must contain exactly two design names")
        names = [self._name(name) for name in names]
        if names[0] == names[1] or any(name in self._designs for name in names):
            raise ValueError("Design names must be distinct and not already registered")
        inputs, libraries = request.get("input_paths"), request.get("liberty_files", [])
        databases, designs = load_designs(self._universe, inputs, libraries)
        try:
            from kepler_formal import from_najaeda

            handles = [from_najaeda(design) for design in designs]
        except Exception:
            for database in reversed(databases):
                database.destroy()
            raise
        self._designs.update(zip(names, handles))
        self._databases.extend(databases)
        self._input_files.update(Path(path).resolve() for path in inputs + libraries)
        return {**self._inspect(), "loaded": names}

    def _verify(self, request: dict[str, Any]) -> dict[str, Any]:
        names = [self._name(request.get(key)) for key in ("design1", "design2")]
        for name in names:
            if name not in self._designs:
                raise ValueError(f"Unknown registered design: {name!r}")
        raw_options = request.get("options", {})
        if not isinstance(raw_options, dict):
            raise ValueError("options must be a mapping")
        unknown = set(raw_options) - set(OPTION_NAMES)
        if unknown:
            raise ValueError("Unsupported verification options: " + ", ".join(sorted(map(str, unknown))))
        options = dict(raw_options)
        reports_requested = options.get("report_skipped_outputs", False)
        if not isinstance(reports_requested, bool):
            raise ValueError("report_skipped_outputs must be a boolean")
        if reports_requested and not self._owned:
            raise ValueError("report_skipped_outputs is unavailable for attached sessions because it writes in the caller's working directory")

        log_value = options.get("log_file")
        if log_value is not None and (not isinstance(log_value, str) or not log_value.strip()):
            raise ValueError("log_file must be a nonempty path string or null")
        log_path = Path(log_value).expanduser() if log_value is not None else Path(f"verify-{uuid4().hex}.log")
        log_path = (log_path if log_path.is_absolute() else self.output_dir / log_path).resolve()
        if not log_path.is_relative_to(self.output_dir):
            raise ValueError(f"Log path is outside the session output directory: {log_path}")
        if log_path in self._input_files or log_path.is_dir():
            raise ValueError("The log file must not overwrite an input file or directory")
        options["log_file"] = str(log_path)
        if self._owned:
            if Path.cwd().resolve() != self._workspace:
                raise RuntimeError("The managed session's private working directory changed")
            for name in REPORT_FILENAMES:
                (self._workspace / name).unlink(missing_ok=True)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        result = verify_loaded(self._designs[names[0]], self._designs[names[1]], options)
        return {
            "status": "error" if result["status"] == "error" else "success",
            "session_id": self.session_id, "exit_code": result["exit_code"],
            "verdict": result["status"], "verification_result": result,
            "generated_log_file": str(log_path),
            "log_tail": tail(log_path.read_text(encoding="utf-8", errors="replace")) if log_path.is_file() else "",
            "reports": read_reports(self._workspace) if self._owned else {},
            "stdout_tail": "", "stderr_tail": "",
        }

    def dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            self._require_open()
            if not isinstance(request, dict):
                raise ValueError("Session requests must be mappings")
            operation = request.get("operation")
            if operation == "info":
                from .capabilities import get_capabilities

                return {**get_capabilities(), "session_id": self.session_id, "kind": self.kind, "pid": self.pid}
            if operation == "inspect":
                return self._inspect()
            if operation == "load":
                return self._load(request)
            if operation == "verify":
                return self._verify(request)
            raise ValueError(f"Unknown session operation: {operation!r}")

    def close(self) -> dict[str, Any]:
        with self.lock:
            if not self._closed:
                self._designs.clear()
                self._databases.clear()
                if self._owned:
                    from najaeda import naja

                    if naja.NLUniverse.get() is self._universe:
                        self._universe.destroy()
                self._closed = True
            return {"status": "success", "session_id": self.session_id, "closed": True}
