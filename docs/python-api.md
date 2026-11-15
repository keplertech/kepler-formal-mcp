# Python API coverage

The MCP uses published Kepler Formal `1.0.0`. Its verification API is
`verify_designs(reference, candidate, options=VerificationOptions(...))`.
NajaEDA loads both designs inside the worker, and Kepler borrows their existing
native handles for the check.

All nineteen `VerificationOptions` fields are available through
`create_yaml_and_run_kepler_formal`, `verify_session`, and existing YAML configurations
(attached sessions cannot request process-relative skipped-output report files):

| Python option | MCP argument | YAML key | Accepted values |
| --- | --- | --- | --- |
| `mode` | `verification` | `verification` or `mode` | `lec`, `sec` |
| `solver` | `solver` | `solver` | `kissat`, `cadical`, `glucose` |
| `max_k` | `max_k` | `max_k` | Nonnegative integer or null; SEC only |
| `sec_engine` | `sec_engine` | `sec_engine` | `pdr`, `k_induction`, `imc`, or null |
| `sec_encoding` | `sec_encoding` | `sec_encoding` | `dual_rail_steady`, `binary`, or null |
| `allow_boundary_mismatch` | Same name | Same name | Boolean; LEC only |
| `report_skipped_outputs` | Same name | Same name | Boolean |
| `log_file` | `log_file_name` | `log_file` | Path within the allowed output directory |
| `log_level` | `log_level` | `log_level` | `info`, `debug`, or null |
| `set_as_boundary` | Same name | Same name | List of `[design1 path, design2 path]` leaf-instance pairs |
| `learn_internal_relations` | Same name | Same name | Boolean, default true; false is SEC only |
| `allow_x_equality_in_internal_relations` | Same name | Same name | Boolean, default true; false is SEC only |
| `binary_relation_round` | Same name | Same name | Boolean, default true; false is SEC only |
| `latch_abstraction` | Same name | Same name | Boolean, default true; false is SEC only |
| `data_latch_model` | Same name | Same name | Boolean, default true; false is SEC only |
| `sec_reset_cycles` | Same name | Same name | Positive integer or null; SEC only, with `sec_reset_ports` |
| `sec_reset_ports` | Same name | Same name | List of `[port name, active value]` pairs; SEC only |
| `btor2_export_path` | Same name | Same name | Path within the allowed output directory, or null; SEC only |
| `btor2_dump_only` | Same name | Same name | Boolean; SEC only, requires `btor2_export_path` |

The MCP creates a log path when one is omitted. Its log level defaults to
`info`; an explicit null delegates that setting to the library. Unspecified
SEC settings use the library defaults: PDR, dual-rail steady encoding, and
`max_k=32`. Non-null SEC options cannot be used with LEC.

YAML keys are these Python option names. The CLI's `sec_reset` map and its
`btor2_export` and `dump_only` keys are rejected like other CLI-only options.
A switch omitted from an existing YAML file keeps the library default.

## Boundaries, reset bootstrap and BTOR2 export

`set_as_boundary` pairs a leaf instance of the first design with its
counterpart in the second, by slash-separated paths relative to each top. The
proof then compares what drives those instances and treats their outputs as
shared inputs, in LEC or SEC. Both instances must expose the same pins.

`sec_reset_cycles` and `sec_reset_ports` are given together. Each listed
top-level input is held at its active value (`true` for 1) for that many
cycles, then at the opposite value while SEC checks the designs.

`btor2_export_path` writes the prepared SEC obligation before solving. A
relative path is resolved like a log path: from the YAML file's directory in
the file tools, and from the output directory in a session. With
`btor2_dump_only` no proof runs and the verdict is `exported`. For example:

```json
{
  "input_paths": ["/designs/reference.v", "/designs/candidate.v"],
  "liberty_files": ["/designs/cells.lib"],
  "verification": "sec",
  "set_as_boundary": [["u_core/u_mem", "u_core/u_mem_impl"]],
  "sec_reset_cycles": 4,
  "sec_reset_ports": [["rst_n", false]],
  "btor2_export_path": "obligation.btor2",
  "allowed_output_dir": "/verification-output"
}
```

## SEC verification

For example, call `create_yaml_and_run_kepler_formal` with:

```json
{
  "input_paths": ["/designs/reference.v", "/designs/candidate.v"],
  "liberty_files": ["/designs/cells.lib"],
  "verification": "sec",
  "sec_engine": "pdr",
  "sec_encoding": "dual_rail_steady",
  "max_k": 32,
  "report_skipped_outputs": true,
  "allowed_output_dir": "/verification-output"
}
```

Use the returned `verdict` to distinguish `equivalent`, `different`,
`inconclusive`, `exported`, and other outcomes. A completed verification is not
necessarily an equivalence proof, and the native exit code is not a substitute
for the verdict.

## Results and diagnostics

`verification_result` contains every field from `VerificationResult`, including
the bound, reason, coverage counts, unproven outputs, and skipped observed
outputs. It also includes the computed `equivalent`, `conclusive`, and
`coverage_percent` properties.

With `report_skipped_outputs=true`, `reports` returns the contents of any native
`skipped_multi_driver_pos.txt`, `skipped_no_driver_pos.txt`, and
`skipped_logical_loop_pos.txt` files before the temporary worker directory is
deleted. Reports can also accompany a failed or timed-out verification. Empty
reports are preserved as empty strings; these files are not copied elsewhere.

When `btor2_export_path` is set, `generated_btor2_file` names the file this run
wrote. It is null when the run failed before exporting, even if an older file
exists at that path.

## Version and capability discovery

Call `get_kepler_formal_info` without arguments for the installed Kepler version
and git revision, NajaEDA version, supported enums/statuses, Python option
defaults, and result field names. This exposes `version()` and `git_hash()`
through MCP and reads the API metadata from the installed package.
With an active session it queries that interpreter; otherwise it uses a temporary worker.

`NativeDesign` and `from_najaeda()` operate on live Python/C++ objects within
one process. The worker uses this interface internally; raw pointers and handles
from another process cannot be sent through MCP JSON. The `najaeda` export is a
compatibility alias for the separate NajaEDA package, not a remote netlist API.
To reuse loaded designs or bind to an existing interpreter, use the
[session tools and Python bridge](sessions.md). Verification then runs in the
process that owns those designs.

The tests compare MCP option names, enum choices, and returned fields against
the installed Python API, so missing coverage is detected when the dependency
is upgraded. CLI-only features absent from the Python API, including CNF
export, remain unsupported and are explicitly rejected.
