# Persistent verification sessions

Sessions retain loaded designs between MCP calls. Use a managed session to load
files once, or attach to a Python process that already owns NajaEDA designs.
The existing YAML tools still run each comparison in an independent worker.

## Managed sessions

Call these MCP tools in order:

1. `open_session(allowed_output_dir="/absolute/path/results")` creates and selects
   a session. Keep its returned `session_id`.
2. `load_designs(input_paths=["/absolute/reference.v", "/absolute/candidate.v"],
   liberty_files=[])` returns two native references in `loaded`, in input order.
3. `verify_session(design1=loaded[0], design2=loaded[1], session_id=...)` checks
   those explicit designs. Repeated calls reuse the same
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
Each design reference contains `session_id`, `db_id`, `library_id`, and
`design_id`. All three native IDs are required: two databases can contain the
same module name and identical library/design IDs. The session ID prevents
using those coordinates in a different interpreter or bridge.

The server resolves `(db_id, library_id, design_id)` directly through
`NLUniverse.getSNLDesign` on every request. It does not retain a design-name
dictionary or infer a design from the current top. `set_session` refreshes
the available design list from the native universe; names are display metadata.

## Attach to existing NajaEDA designs

Run the bridge in the Python process that owns your designs, using the same
installed Kepler Formal and NajaEDA packages as the MCP server:

```python
from pathlib import Path
from kepler_formal_mcp.session_bridge import SessionBridge

# reference and candidate are your already loaded NajaEDA design objects.
# Raw SNLDesigns, NajaEDA Instances, and KF NativeDesign handles are accepted.
with SessionBridge(output_dir=Path("results").resolve()) as bridge:
    first_id = bridge.design_reference(reference)
    second_id = bridge.design_reference(candidate)
    print("Native design references:", first_id, second_id)
    print("Attach MCP using:", bridge.connection_file)
    input("Keep this process running; press Enter when finished. ")
```

Then call `attach_session(connection_file="/path/printed/by/the/bridge")` from
MCP, followed by `verify_session(design1=first_id, design2=second_id,
session_id=first_id["session_id"])`. `design_reference` only reads native IDs;
it does not register, copy or retain the design. You can also construct the
reference from `design.getNLID()` and the bridge's `session_id`, or take the
references returned by attachment. Subsequent NajaEDA top-design changes do
not retarget those IDs.
Verification runs inside the owning Python process on its existing pointers.
Only authenticated local commands and results cross the connection; netlists
are not serialized or copied between processes.

The connection file contains a credential. Keep it private and attach only to
a bridge you intend to control. A trusted attached client may verify designs
throughout that universe by native ID; registration is no longer an access
allowlist. The bridge offers verification operations, not arbitrary Python
execution or design editing.

Coordinate edits in the owning Python process with the bridge's lock:

```python
with bridge.lock:
    # Edit reference or candidate with NajaEDA here.
    ...
```

The next verification sees those edits. Do not destroy designs or
their universe while the bridge uses them. `close_session()` detaches MCP from
an attached session; it leaves the bridge and caller's netlists alive.
`bridge.close()` stops the bridge and releases its borrowed references without
destroying the caller's universe.

Native IDs are live addresses, not persistent UUIDs or generation counters.
Missing IDs and a replaced universe are rejected. To avoid native ID reuse
after deletion/reload, close the bridge **before** deleting/reloading designs
or databases, then create a new bridge and obtain new session-scoped references.
In-place connectivity edits do not require new references. Never persist an
ID for use in another kernel or after restarting the session.

## Migrating from named designs

This is the v2 session protocol. Upgrade the MCP server and owning-process
wrapper together; old connection files are rejected. Replace
`register_design(name, design)` with `design_reference(design)`, and pass the
returned objects explicitly as `design1` and `design2`. `load_designs` no longer
accepts `names`, and `verify_session` has no default design pair. File-based
verification tools are unchanged.

## Timeouts and outputs

Both session types restrict logs to their selected output directory. Every
completed verification saves the full Python result as `verification-result.json`
inside a fresh `reports-<id>` directory and returns its contents in `reports`,
with `report_format="structured-v1"`, `report_id` and `report_paths`.
Responses and retained reports include the exact `design1` and `design2`
references checked, so consumers can reject evidence for the wrong pair.
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
