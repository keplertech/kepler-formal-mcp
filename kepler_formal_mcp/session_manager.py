"""Manage persistent workers and authenticated connections to caller interpreters."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from threading import Lock, RLock
import time
from typing import Any
from uuid import UUID

from .runner import error_result, tail


PROTOCOL = "kepler-formal-mcp-session-v2"


def _descriptor(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("protocol") != PROTOCOL:
        raise ValueError("Not a Kepler session connection file")
    if value.get("host") != "127.0.0.1":
        raise ValueError("Session bridges must use the IPv4 loopback address")
    if type(value.get("port")) is not int or not 0 < value["port"] < 65536:
        raise ValueError("Invalid session bridge port")
    token = value.get("token")
    if not isinstance(token, str) or len(token) < 32:
        raise ValueError("Invalid session bridge token")
    if not isinstance(value.get("session_id"), str):
        raise ValueError("Invalid session ID")
    UUID(value["session_id"])
    return value


def _request(descriptor: dict, request: dict, timeout_seconds: int) -> dict:
    """Send only JSON to a local, authenticated bridge; never deserialize Python objects."""
    if type(timeout_seconds) is not int or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be a positive integer")
    deadline = time.monotonic() + timeout_seconds
    with socket.create_connection((descriptor["host"], descriptor["port"]), timeout_seconds) as connection:
        connection.sendall(json.dumps({"token": descriptor["token"], "request": request}).encode() + b"\n")
        chunks = bytearray()
        while b"\n" not in chunks:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Session request timed out")
            connection.settimeout(remaining)
            chunk = connection.recv(65536)
            if not chunk:
                raise ConnectionError("Session bridge closed before returning a result")
            chunks.extend(chunk)
            if len(chunks) > 64 * 1024 * 1024:
                raise ValueError("Session response exceeds 64 MiB")
        result = json.loads(chunks.split(b"\n", 1)[0])
        if not isinstance(result, dict):
            raise ValueError("Invalid session response")
        return result


@dataclass
class _Session:
    descriptor: dict = field(repr=False)
    metadata: dict
    process: subprocess.Popen | None = None
    workspace: Any = None
    streams: list = field(default_factory=list, repr=False)
    call_lock: Any = field(default_factory=Lock, repr=False)


class SessionManager:
    def __init__(self):
        self._sessions: dict[str, _Session] = {}
        self._active: str | None = None
        self._lock = RLock()

    @property
    def active_session_id(self) -> str | None:
        with self._lock:
            return self._active

    def _remember(self, entry: _Session) -> dict:
        session_id = entry.metadata["session_id"]
        with self._lock:
            # Re-attaching the same bridge selects the existing connection.
            if session_id not in self._sessions:
                self._sessions[session_id] = entry
            self._active = session_id
        return {**entry.metadata, "active": True}

    def open(self, output_dir: Path) -> dict:
        output_dir = output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        workspace = tempfile.TemporaryDirectory(prefix="kepler-session-")
        root = Path(workspace.name)
        streams = [(root / name).open("wb") for name in ("stdout.txt", "stderr.txt")]
        entry = _Session({}, {}, workspace=workspace, streams=streams)
        try:
            connection_file = root / "connection.json"
            entry.process = subprocess.Popen(
                [sys.executable, "-m", "kepler_formal_mcp.session_worker",
                 "--connection-file", str(connection_file), "--output-dir", str(output_dir)],
                cwd=root, stdin=subprocess.PIPE, stdout=streams[0], stderr=streams[1],
            )
            deadline = time.monotonic() + 30
            while not connection_file.is_file():
                if entry.process.poll() is not None:
                    details = (root / "stderr.txt").read_text(encoding="utf-8", errors="replace")
                    raise RuntimeError("Session worker failed to start: " + tail(details))
                if time.monotonic() >= deadline:
                    raise TimeoutError("Session worker startup timed out after 30 seconds")
                time.sleep(0.02)
            entry.descriptor = _descriptor(connection_file)
            entry.metadata = self._inspect(entry.descriptor)
            if entry.metadata["kind"] != "managed":
                raise ValueError("Worker did not create a managed session")
            return self._remember(entry)
        except Exception:
            self._dispose(entry)
            raise

    def _inspect(self, descriptor: dict) -> dict:
        metadata = _request(descriptor, {"operation": "inspect"}, 30)
        if metadata.get("status") != "success":
            raise ValueError(metadata.get("reason", metadata.get("stderr_tail", "Session bridge rejected attachment")))
        if (metadata.get("session_id") != descriptor["session_id"]
                or metadata.get("kind") not in {"managed", "attached"}
                or not isinstance(metadata.get("output_dir"), str)):
            raise ValueError("Bridge identity does not match its connection file")
        return metadata

    def attach(self, connection_file: Path) -> dict:
        descriptor = _descriptor(connection_file)
        metadata = self._inspect(descriptor)
        # An attached connection never acquires process/universe ownership,
        # even if the external bridge happens to manage its own universe.
        metadata["kind"] = "attached"
        return self._remember(_Session(descriptor, metadata))

    def _get(self, session_id: str | None) -> tuple[str, _Session]:
        with self._lock:
            selected = session_id if session_id is not None else self._active
            if selected not in self._sessions:
                raise ValueError(f"Unknown or closed session: {selected!r}")
            return selected, self._sessions[selected]

    def select(self, session_id: str) -> dict:
        selected, entry = self._get(session_id)
        response = self.call({"operation": "inspect"}, selected, 30)
        if response["status"] == "success":
            with self._lock:
                if self._sessions.get(selected) is not entry:
                    raise ValueError(f"Session closed while being selected: {selected!r}")
                self._active = selected
            response["active"] = True
        return response

    def list(self) -> dict:
        with self._lock:
            return {"status": "success", "active_session_id": self._active,
                    "sessions": [dict(entry.metadata) for entry in self._sessions.values()]}

    def call(self, request: dict, session_id: str | None = None, timeout_seconds: int = 600) -> dict:
        if type(timeout_seconds) is not int or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be a positive integer")
        selected, entry = self._get(session_id)
        if not entry.call_lock.acquire(blocking=False):
            return {**error_result("Session is busy"), "session_id": selected, "busy": True}
        try:
            # A close can win the race between lookup and acquiring this lock.
            self._get(selected)
            offsets = []
            if entry.workspace is not None:
                for name in ("stdout.txt", "stderr.txt"):
                    offsets.append((Path(entry.workspace.name) / name).stat().st_size)
            try:
                response = _request(entry.descriptor, request, timeout_seconds)
            except TimeoutError:
                managed = entry.process is not None
                response = error_result(f"Session request timed out after {timeout_seconds} seconds")
                response.update(session_id=selected, session_invalidated=managed,
                                may_still_be_running=not managed)
                if managed:
                    self._forget(selected)
                    self._dispose(entry, force=True)
                return response
            except (OSError, ValueError) as error:
                response = error_result(f"Session connection failed: {error}")
                response.update(session_id=selected, session_invalidated=True)
                self._forget(selected)
                self._dispose(entry, force=True)
                return response
            if response.get("status") == "success" and "designs" in response:
                entry.metadata.update(response)
                entry.metadata["kind"] = "managed" if entry.process is not None else "attached"
            response.update(session_id=selected, pid=entry.metadata["pid"])
            if entry.workspace is not None:
                for name, key, offset in zip(("stdout.txt", "stderr.txt"), ("stdout_tail", "stderr_tail"), offsets):
                    with (Path(entry.workspace.name) / name).open("rb") as stream:
                        stream.seek(offset)
                        captured = tail(stream.read())
                    if captured:
                        response[key] = captured
            return response
        finally:
            entry.call_lock.release()

    def _forget(self, selected: str):
        with self._lock:
            self._sessions.pop(selected, None)
            if self._active == selected:
                self._active = None

    def _dispose(self, entry: _Session, force: bool = False):
        process = entry.process
        if process is not None:
            if process.stdin is not None:
                process.stdin.close()
            if force and process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        for stream in entry.streams:
            stream.close()
        if entry.workspace is not None:
            entry.workspace.cleanup()

    def close(self, session_id: str | None = None) -> dict:
        selected, entry = self._get(session_id)
        if not entry.call_lock.acquire(blocking=False):
            return {**error_result("Session is busy; wait for its request to finish"),
                    "session_id": selected, "busy": True}
        try:
            self._forget(selected)
            self._dispose(entry)
            return {"status": "success", "session_id": selected,
                    "state": "closed" if entry.process is not None else "detached"}
        finally:
            entry.call_lock.release()

    def close_all(self):
        with self._lock:
            entries = list(self._sessions.items())
            self._sessions.clear()
            self._active = None
        for _, entry in entries:
            # Called on server shutdown: terminate owned workers, never callers.
            self._dispose(entry, force=True)
