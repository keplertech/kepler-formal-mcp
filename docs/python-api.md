# Python API coverage

The MCP uses published Kepler Formal `0.5.0`. Its verification API is
`verify_designs(reference, candidate, options=VerificationOptions(...))`.
NajaEDA loads both designs inside the worker, and Kepler borrows their existing
native handles for the check.

All nine `VerificationOptions` fields are available through
`create_yaml_and_run_kepler_formal` and existing YAML configurations:

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

The MCP creates a log path when one is omitted. Its log level defaults to
`info`; an explicit null delegates that setting to the library. Unspecified
SEC settings use the library defaults: PDR, dual-rail steady encoding, and
`max_k=32`. Non-null SEC options cannot be used with LEC.

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
`inconclusive`, and other outcomes. A completed verification is not necessarily
an equivalence proof, and the native exit code is not a substitute for the
verdict.

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

## Version and capability discovery

Call `get_kepler_formal_info` without arguments for the installed Kepler version
and git revision, NajaEDA version, supported enums/statuses, Python option
defaults, and result field names. This exposes `version()` and `git_hash()`
through MCP and reads the API metadata from the installed package.

`NativeDesign` and `from_najaeda()` operate on live Python/C++ objects within
one process. The worker uses this interface internally; raw pointers and handles
from another process cannot be sent through MCP JSON. The `najaeda` export is a
compatibility alias for the separate NajaEDA package, not a remote netlist API.

The tests compare MCP option names, enum choices, and returned fields against
the installed Python API, so missing coverage is detected when the dependency
is upgraded. CLI-only features absent from the Python API, including CNF and
BTOR2 export, remain unsupported and are explicitly rejected.
