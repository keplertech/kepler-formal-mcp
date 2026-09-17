"""Authenticated loopback access to designs in the current Python process.

Native pointers never leave this process. Use ``bridge.lock`` while editing the
registered designs so edits and verification cannot access Naja concurrently.
Closing the bridge waits for active work and leaves caller-owned netlists alive.
"""

from __future__ import annotations

import hmac
import json
import os
from pathlib import Path
import secrets
import socketserver
import tempfile
import threading
from typing import Any


PROTOCOL = "kepler-formal-mcp-session-v1"
MAX_REQUEST_BYTES = 1024 * 1024
SOCKET_TIMEOUT_SECONDS = 10


def _error(reason: str) -> dict[str, Any]:
    return {"status": "error", "reason": reason}


class _SessionServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = False


class _SessionHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        self.connection.settimeout(SOCKET_TIMEOUT_SECONDS)
        bridge = self.server.bridge
        try:
            line = self.rfile.readline(MAX_REQUEST_BYTES + 1)
            if len(line) > MAX_REQUEST_BYTES:
                raise ValueError("Session request exceeds the size limit")
            if not line.endswith(b"\n"):
                raise ValueError("Session requests must be newline-terminated JSON")
            envelope = json.loads(line)
            if not isinstance(envelope, dict):
                raise ValueError("Session request must be a JSON object")
            token = envelope.get("token")
            if not isinstance(token, str) or not hmac.compare_digest(
                token.encode("utf-8"), bridge._token.encode("ascii")
            ):
                response = _error("Session authentication failed")
            elif self.client_address[0] != "127.0.0.1":
                response = _error("Only loopback session connections are allowed")
            else:
                response = bridge._dispatch(envelope.get("request"))
        except Exception as error:
            response = _error(f"{type(error).__name__}: {error}")
        try:
            self.wfile.write(json.dumps(response).encode("utf-8") + b"\n")
        except OSError:
            # A client can disconnect or time out without cancelling native
            # work or changing the lifetime of the caller's Python process.
            pass


class SessionBridge:
    """Expose a local design session through an authenticated TCP descriptor.

    ``owned=False`` borrows designs from the caller's Naja universe. Closing this
    bridge only releases its own references. ``owned=True`` is reserved for the
    managed worker, which creates and owns its universe.

    The connection file contains a secret token: share its path only with trusted
    local clients. A default file is created in a private temporary directory.
    """

    def __init__(self, output_dir: str | Path,
                 connection_file: str | Path | None = None, *, owned: bool = False):
        # Importing this module is safe in the MCP server; native imports happen
        # only when a bridge is instantiated in the process owning the designs.
        from .session_backend import DesignSession

        self._backend = DesignSession(output_dir, owned=owned)
        self.lock = self._backend.lock
        self._lifecycle_lock = threading.RLock()
        self._closing = threading.Event()
        self._closed = False
        self._server: _SessionServer | None = None
        self._thread: threading.Thread | None = None
        self._descriptor: dict[str, Any] | None = None
        self._descriptor_identity: tuple[int, int] | None = None
        self._token = secrets.token_hex(32)
        self._temporary: tempfile.TemporaryDirectory | None = None
        if connection_file is None:
            self._temporary = tempfile.TemporaryDirectory(prefix="kepler-session-")
            self.connection_file = Path(self._temporary.name) / "connection.json"
        else:
            requested = Path(connection_file).expanduser().absolute()
            self.connection_file = requested.parent.resolve() / requested.name

    @property
    def descriptor(self) -> dict[str, Any]:
        if self._descriptor is None or self._closed:
            raise RuntimeError("Session bridge is not running")
        return dict(self._descriptor)

    @property
    def session_id(self) -> str:
        return self._backend.session_id

    def register_design(self, name: str, design: Any) -> Any:
        with self.lock:
            if self._closing.is_set():
                raise RuntimeError("Session bridge is closed")
            return self._backend.register_design(name, design)

    def _dispatch(self, request: Any) -> dict[str, Any]:
        if not isinstance(request, dict):
            return _error("Session request payload must be a JSON object")
        if request.get("operation") not in {"inspect", "load", "verify", "info", "reports"}:
            return _error("Session operation must be inspect, load, verify, info, or reports")
        if self._closing.is_set():
            return _error("Session bridge is closing")
        if not self.lock.acquire(blocking=False):
            return _error("Session is busy with verification or caller edits; try again when idle")
        try:
            if self._closing.is_set():
                return _error("Session bridge is closing")
            return self._backend.dispatch(request)
        finally:
            self.lock.release()

    def _write_descriptor(self) -> None:
        path = self.connection_file
        path.parent.mkdir(parents=True, exist_ok=True)
        if os.path.lexists(path):
            raise FileExistsError(f"Session connection file already exists: {path}")
        fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                if hasattr(os, "fchmod"):
                    os.fchmod(stream.fileno(), 0o600)
                else:
                    os.chmod(temporary, 0o600)
                json.dump(self._descriptor, stream)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            stat = path.stat()
            self._descriptor_identity = (stat.st_dev, stat.st_ino)
        finally:
            temporary.unlink(missing_ok=True)

    def start(self) -> SessionBridge:
        with self._lifecycle_lock:
            if self._closed:
                raise RuntimeError("Session bridge is closed")
            if self._server is not None:
                return self
            server = _SessionServer(("127.0.0.1", 0), _SessionHandler)
            server.bridge = self
            self._descriptor = {
                "protocol": PROTOCOL,
                "host": "127.0.0.1",
                "port": server.server_address[1],
                "token": self._token,
                "session_id": self.session_id,
            }
            thread = threading.Thread(
                target=server.serve_forever, kwargs={"poll_interval": 0.05},
                name=f"kepler-session-{self.session_id}", daemon=True,
            )
            thread.start()
            try:
                self._write_descriptor()
            except Exception:
                server.shutdown()
                server.server_close()
                thread.join()
                self._descriptor = None
                raise
            self._server, self._thread = server, thread
            return self

    def close(self) -> None:
        """Stop serving and wait for active verification before releasing state.

        An attached bridge never terminates or resets the caller's universe. A
        native call already in progress is allowed to complete, even if its MCP
        client has disconnected or timed out.
        """
        with self._lifecycle_lock:
            if self._closed:
                return
            self._closing.set()
            if self._server is not None:
                self._server.shutdown()
                self._server.server_close()
                self._thread.join()
            try:
                with self.lock:
                    self._backend.close()
            finally:
                self._closed = True
                self._server = None
                self._thread = None
                self._descriptor = None
                if self._descriptor_identity is not None:
                    try:
                        stat = self.connection_file.lstat()
                        if (stat.st_dev, stat.st_ino) == self._descriptor_identity:
                            self.connection_file.unlink()
                    except FileNotFoundError:
                        pass
                if self._temporary is not None:
                    self._temporary.cleanup()

    def __enter__(self) -> SessionBridge:
        try:
            return self.start()
        except Exception:
            self.close()
            raise

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
