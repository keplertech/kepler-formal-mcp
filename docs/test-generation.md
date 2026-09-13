# Compare Design Variants

Put the original mapped Verilog, a separate edited candidate and their Liberty
libraries under the host-configured `KEPLER_FORMAL_WORKSPACE`.

Ask the agent to call `verify_sec` with those paths. For example:

```json
{
  "golden": "example/original.v",
  "revised": "example/candidate.v",
  "liberty_files": ["example/cells.lib"],
  "sec_engine": "pdr",
  "sec_encoding": "dual_rail_steady",
  "max_k": 32
}
```

The server snapshots the inputs, constructs a mandatory SEC request, runs the
packaged backend, and returns structured evidence. No verification mode or
arbitrary shell command comes from the agent. The original design and YAML, if
used, remain untouched. Read full logs through the returned paths when needed;
the response includes only bounded stdout/stderr tails.

Require `status == "proved"`, complete coverage and the expected output count
when validating a known reference regression. `partial` and `inconclusive` are
non-blocking warnings, but do not establish full equivalence. `counterexample`
and `error` reject the candidate. Interpret proof claims within the returned SEC
encoding and bound, as described in the [proof contract](../README.md#proof-contract).

The native integration tests cover equivalent and inequivalent small designs,
an actual MCP call, and a parser/tool failure. Run them with the configured
packaged binary before relying on a new package revision.
