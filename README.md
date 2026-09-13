# kepler-formal-mcp

An MCP interface to a **packaged Kepler Formal executable**, with mandatory
Sequential Equivalence Checking (SEC), explicit proof coverage and retained
per-run evidence. It does not build or install Kepler when a tool is called.

## Install

Use Python 3.11+ and an installed Kepler release supporting SEC YAML configuration.
The mapped-Verilog integration is tested against the same pinned package as 22b:

```sh
# With Nix and nix-command/flakes enabled, configure the public binary cache.
nix run --max-jobs 0 --builders '' \
  github:NixOS/nixpkgs/8ce4ef6cb6f871616146b9fe26d2a5ae594e94fe#cachix \
  -- use keplertech

nix profile add --max-jobs 0 --builders '' \
  'git+https://github.com/keplertech/kepler-formal?rev=0e0abf2aa6979e337aead8996983952dc1742530&submodules=1#kepler-formal'
kepler-formal --help

# In this MCP repository; no Kepler submodule checkout is needed.
python3 -m venv .venv
.venv/bin/python -m pip install --only-binary=:all: -r requirements.txt
```

The cache is public; downloads need no token. Configuring cache trust may require
administrator permission on multi-user Nix. The no-build flags fail on a cache
miss rather than compiling. The existing submodule/build script is retained for
manual developer use only; neither is selected automatically by this server.

## Configure The MCP Process

Set these environment variables **on the host**, not through a model tool call:

| Variable | Purpose |
| --- | --- |
| `KEPLER_FORMAL_BIN` | Optional absolute executable path, including a Nix profile/store path. Otherwise search `PATH`. An invalid explicit path fails without fallback. |
| `KEPLER_FORMAL_WORKSPACE` | Root containing permitted design, Liberty and request files. Defaults to the server's startup working directory, not its installation directory. |
| `KEPLER_FORMAL_AI_OUTPUT_DIR` | Writable artifact root. Defaults to `<workspace>/runs`. The model cannot broaden this root. |

Run `.venv/bin/python /absolute/path/to/kepler-formal-mcp/server.py` over stdio.
No `PYTHONPATH` injection from a Kepler or Naja source tree is required.
See [client configuration](docs/instructions-claude.md).

## Tools

- `verify_sec(golden, revised, liberty_files, ...)`: preferred typed interface.
  Supports `max_k`, `sec_engine`, `sec_encoding` and a bounded timeout.
- `run_kepler_formal_yaml(yaml_file, ...)`: validate a YAML request and write a
  fresh normalized copy. Relative input paths resolve against the original
  YAML's directory; the original YAML is never modified.
- `create_yaml_and_run_kepler_formal(input_paths, liberty_files, ...)`: preserves
  the existing convenience tool name, now using the same mandatory SEC path.

All tools return an MCP structured result, rather than a JSON string containing
only a process-success flag. The text representation is still available through
MCP. Clients should inspect `status`, `equivalent`, `blocking` and `coverage`.

### Scope And Compatibility

This adapter currently validates **two mapped Verilog files plus Liberty files**.
It does not silently forward arbitrary backend options: unknown YAML fields,
RTL/flist formats, executable Python primitive libraries, LEC, disabled skipped
reporting, and export-only configurations fail explicitly. Add schema and
integration tests before extending the supported formats.

Supported YAML keys are `format`, `input_paths`, `liberty_files`, `verification`,
`report_skipped_pos`, `max_k`, `sec_engine`, `sec_encoding`, `log_level`, `solver`,
`compact_mode`, `verilog_design1_top`, `verilog_design2_top`, `log_file`,
`cnf_export` and `cnf_export_path`. SEC and skipped reporting default on and cannot
be disabled. CNF export defaults **off** and cannot be enabled: Kepler does not
support CNF export in SEC mode. Export-only is not a proof.

`allowed_output_dir` now chooses only a subdirectory of the host-configured root.
Output filenames (`yaml_output_path`, `log_file_name`, and YAML output paths) must
be relative, non-conflicting names inside the unique run directory. Absolute
paths, traversal, symlink escapes and evidence/input overwrites are rejected.
These constraints intentionally replace the earlier caller-controlled write root.

## Proof Contract

| `status` | `equivalent` | `blocking` | Meaning |
| --- | --- | --- | --- |
| `proved` | `true` | `false` | All existing observed outputs proved under the reported SEC assumptions. |
| `partial` | `null` | `false` | Some outputs are proved; others are unproved/skipped. Warning, not full equivalence. |
| `inconclusive` | `null` | `false` | No definitive result. Warning, not equivalence or a mismatch. |
| `counterexample` | `false` | `true` | Definitive behavioral difference; reject candidate. |
| `error` | `null` | `true` | Invalid request, missing tool, timeout, extraction failure, inconsistent/missing evidence or execution error. |

Coverage distinguishes **checked**, **proved**, **existing**, **skipped**, and
**unproved** outputs. A missing proved count stays unknown, not zero or 100%.
Exit status alone never proves equivalence. A proof for a checked subset becomes
`partial`, not `proved`; zero checkable outputs is an error.
The requested skipped-output reports must exist; missing reports do not mean
zero skipped outputs.

Results include engine, encoding, proof bound, exit code, timing, input hashes,
full log paths, bounded log tails, skipped-report paths and the fresh run directory.
`dual_rail_steady` compares binary-defined outputs under the steady-state
abstraction; it does not prove that outputs become defined.

Each run retains `result.json`, `command.json`, a normalized YAML config, an input
manifest, read-only input snapshots, stdout/stderr and the Kepler proof/skip logs.
Input hashes are checked before and after execution. Runs never overwrite one
another, including concurrent calls. Timeouts kill the backend process group on
POSIX and retain diagnostics.

The server constrains its writes but is **not an OS sandbox**. Run it with only
the necessary filesystem access and a trusted packaged backend. Verilog includes
and external file dependencies are not bundled automatically. An orchestration
layer must still require verification before accepting an edit; MCP alone cannot
prevent a model from skipping the verification tool. A strict regression such as
GCD should additionally require `status == "proved"` and its expected output count.

## Tests

```sh
.venv/bin/python -m unittest discover -s tests -v

# Also exercise a real packaged backend, including an MCP round trip:
KEPLER_FORMAL_INTEGRATION_BIN="$(command -v kepler-formal)" \
  .venv/bin/python -m unittest discover -s tests -v
```

The default tests use fake backend processes to test transport, timeouts, policy,
concurrency and verdict parsing. Native tests are visibly skipped unless the
integration binary is configured; offline checks are not formal evidence.
