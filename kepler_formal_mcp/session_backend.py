"""Persistent verification using direct native-ID lookup in the owning universe."""

from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from .runner import REPORT_FILENAMES, read_reports, tail
from .verification import OPTION_NAMES, load_designs, verify_loaded
from .design_reference import DesignReference, reference_from_design


# Naja's universe and verification caches are shared throughout an interpreter,
# including when a caller starts more than one bridge in that interpreter.
_NATIVE_LOCK = RLock()


class DesignSession:
    """Host managed netlists or borrow live caller designs without taking ownership.

    Attached callers must hold ``lock`` while changing or destroying native
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
        self._databases: list[Any] = []
        self._input_files: set[Path] = set()
        self._workspace = Path.cwd().resolve()
        self._universe = naja.NLUniverse.get()
        self._last_report = None
        if owned:
            if naja.NLUniverse.get() is not None:
                raise RuntimeError("A managed session requires a fresh Naja universe")
            self._universe = naja.NLUniverse.create()
        elif self._universe is None:
            raise RuntimeError("An attached session requires an existing Naja universe")

    def _require_open(self):
        if self._closed:
            raise RuntimeError("The design session is closed")
        from najaeda import naja

        if naja.NLUniverse.get() is not self._universe:
            raise ReferenceError("The session's Naja universe is no longer active")

    def _resolve(self, value):
        reference = DesignReference.model_validate(value)
        if reference.session_id != self.session_id:
            raise ValueError("Design reference belongs to a different session")
        design = self._universe.getSNLDesign(reference.native_key())
        if design is None:
            raise ReferenceError("Native design ID does not exist in this session")
        identity = design.getNLID()
        if (identity.getDBID(), identity.getLibraryID(), identity.getDesignID()) != reference.native_key():
            raise ReferenceError("Native lookup returned a different design identity")
        return design

    def _metadata(self, design):
        return {"design_name": design.getName(),
                "reference": reference_from_design(self.session_id, design).model_dump()}

    def _inspect(self) -> dict[str, Any]:
        return {
            "status": "success", "session_id": self.session_id, "kind": self.kind,
            "pid": self.pid, "output_dir": str(self.output_dir),
            "designs": [self._metadata(design)
                        for database in self._universe.getUserDBs()
                        for library in database.getLibraries() if not library.isPrimitives()
                        for design in library.getSNLDesigns() if not design.isPrimitive()],
            "design_addressing": "native-id-v1",
        }

    def design_reference(self, design: Any) -> dict[str, Any]:
        """Return native coordinates scoped to this session, without storing the design."""
        with self.lock:
            self._require_open()
            reference = reference_from_design(self.session_id, design)
            self._resolve(reference)
            return reference.model_dump()

    def _load(self, request: dict[str, Any]) -> dict[str, Any]:
        if not self._owned:
            raise ValueError("Attached sessions use the caller's native IDs; load designs in the owner")
        if "names" in request:
            raise ValueError("Design aliases are no longer supported; use returned native references")
        inputs, libraries = request.get("input_paths"), request.get("liberty_files", [])
        databases, designs = load_designs(self._universe, inputs, libraries)
        try:
            references = [self.design_reference(design) for design in designs]
        except Exception:
            for database in reversed(databases):
                database.destroy()
            raise
        self._databases.extend(databases)
        self._input_files.update(Path(path).resolve() for path in inputs + libraries)
        return {**self._inspect(), "loaded": references}

    def _verify(self, request: dict[str, Any]) -> dict[str, Any]:
        references = [DesignReference.model_validate(request.get(key)) for key in ("design1", "design2")]
        designs = [self._resolve(reference) for reference in references]
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
        if not self._owned:
            # Native text reports use process-global paths. Attached interpreters
            # instead persist the complete structured result without changing cwd.
            options["report_skipped_outputs"] = False

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
        self._last_report = None
        result = verify_loaded(designs[0], designs[1], options)
        report_id = uuid4().hex
        report_dir = self.output_dir / f"reports-{report_id}"
        report_dir.mkdir()
        serialized = json.dumps(result, indent=2, allow_nan=False) + "\n"
        report_path = report_dir / "verification-result.json"
        report_path.write_text(serialized, encoding="utf-8")
        reports = read_reports(self._workspace) if self._owned and reports_requested else {}
        reports["verification-result.json"] = serialized
        response = {
            "status": "error" if result["status"] == "error" else "success",
            "session_id": self.session_id, "exit_code": result["exit_code"],
            "design1": references[0].model_dump(), "design2": references[1].model_dump(),
            "verdict": result["status"], "verification_result": result,
            "generated_log_file": str(log_path),
            "log_tail": tail(log_path.read_text(encoding="utf-8", errors="replace")) if log_path.is_file() else "",
            "reports": reports,
            "report_format": "structured-v1",
            "report_id": report_id,
            "report_paths": {"verification-result.json": str(report_path)},
            "stdout_tail": "", "stderr_tail": "",
        }
        self._last_report = response
        return response

    def _reports(self, request: dict[str, Any]) -> dict[str, Any]:
        if self._last_report is None:
            raise ValueError("No completed verification report is available")
        expected = request.get("report_id")
        if expected is not None and expected != self._last_report["report_id"]:
            raise ValueError("The requested report is not the latest verification; use its saved artifact")
        # Never re-run verification or infer empty skipped lists from absent files.
        return json.loads(json.dumps(self._last_report))

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
            if operation == "reports":
                return self._reports(request)
            raise ValueError(f"Unknown session operation: {operation!r}")

    def close(self) -> dict[str, Any]:
        with self.lock:
            if not self._closed:
                self._last_report = None
                self._databases.clear()
                if self._owned:
                    from najaeda import naja

                    if naja.NLUniverse.get() is self._universe:
                        self._universe.destroy()
                self._closed = True
            return {"status": "success", "session_id": self.session_id, "closed": True}
