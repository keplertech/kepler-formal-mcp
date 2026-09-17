# Persistent verification sessions

Sessions retain loaded designs between MCP calls. Use a managed session to load
files once, or attach to a Python process that already owns NajaEDA designs.
The existing YAML tools still run each comparison in an independent worker.

## Managed sessions

Call these MCP tools in order:

1. `open_session(allowed_output_dir="/absolute/path/results")` creates and selects
   a session. Keep its returned `session_id`.
2. `load_designs(input_paths=["/absolute/reference.v", "/absolute/candidate.v"],
   liberty_files=[])` loads the pair as `reference` and `candidate`.
3. `verify_session()` checks those loaded designs. Repeated calls reuse the same
   Python process and native netlists; input files are not reread.
4. `close_session()` releases the session and its process.

`verify_session` accepts the same verification settings as the file tools:
`verification`, `solver`, `max_k`, `sec_engine`, `sec_encoding`,
`allow_boundary_mismatch`, `report_skipped_outputs`, `log_level`, and
`log_file_name`. For example, use `verification="sec", max_k=10,
sec_engine="pdr", sec_encoding="binary"` for a bounded SEC run.

Open more sessions for independent designs. `list_sessions()` shows them;
`set_session(session_id=...)` changes the active one. Pass `session_id` directly
to loading, verification, or closing to target a session without switching.
Custom `names=["golden", "revised"]` in `load_designs` can be selected with
`verify_session(design1="golden", design2="revised")`.

## Attach to existing NajaEDA designs

Run the bridge in the Python process that owns your designs, using the same
installed Kepler Formal and NajaEDA packages as the MCP server:

```python
from pathlib import Path
from kepler_formal_mcp.session_bridge import SessionBridge

# reference and candidate are your already loaded NajaEDA design objects.
# Raw SNLDesigns, NajaEDA Instances, and KF NativeDesign handles are accepted.
with SessionBridge(output_dir=Path("results").resolve()) as bridge:
    bridge.register_design("reference", reference)
    bridge.register_design("candidate", candidate)
    print("Attach MCP using:", bridge.connection_file)
    input("Keep this process running; press Enter when finished. ")
```

Then call `attach_session(connection_file="/path/printed/by/the/bridge")` from
MCP, followed by `verify_session()`. Registering captures the selected design:
subsequent NajaEDA top-design changes do not retarget the registered handle.
Verification runs inside the owning Python process on its existing pointers.
Only authenticated local commands and results cross the connection; netlists
are not serialized or copied between processes.

The connection file contains a credential. Keep it private and attach only to
a bridge you intend to control. The bridge offers registered-design operations,
not arbitrary Python execution.

Coordinate edits in the owning Python process with the bridge's lock:

```python
with bridge.lock:
    # Edit reference or candidate with NajaEDA here.
    ...
```

The next verification sees those edits. Do not destroy registered designs or
their universe while the bridge uses them. `close_session()` detaches MCP from
an attached session; it leaves the bridge and caller's netlists alive.
`bridge.close()` stops the bridge and releases its borrowed references without
destroying the caller's universe.

## Timeouts and outputs

Both session types restrict logs to their selected output directory. Every
completed verification saves the full Python result as `verification-result.json`
inside a fresh `reports-<id>` directory and returns its contents in `reports`,
with `report_format="structured-v1"`, `report_id` and `report_paths`.
This includes checked/proved counts, unproven outputs, skipped observed outputs,
the semantic outcome and reason. Missing data is never replaced with empty lists.

`get_session_reports(session_id=..., report_id=...)` retrieves the latest result
without re-running the solver. The optional ID rejects stale requests. Previous
reports remain on disk; closing a session does not delete them. A report refers
to the designs at verification time, not to later edits by the caller.

Managed sessions can additionally produce the native category-specific text
reports in their private working directory. Attached sessions now accept
`report_skipped_outputs=True`, but report those details through the structured
Python result rather than native text files. They neither change the caller's
working directory nor write category reports there. Consumers must check the
reported format; do not manufacture empty native text files as proof of no skips.

A managed-session timeout terminates its process and invalidates that session;
open and load a new one to retry. An attached-session timeout stops waiting but
does not kill the owning application. Verification may still be running there;
overlapping operations are rejected until it finishes.
