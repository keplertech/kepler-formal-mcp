# Regression checks and proof evidence

The [Nix regression workflow](../.github/workflows/nix-regression.yml) runs on
pushes, pull requests, and manual dispatch for Linux and Apple Silicon macOS.
It installs the pinned release with builds disabled and calls both tools
through MCP stdio. Failed installation cannot fall back to another binary.

Cases cover SEC equivalence and counterexample results, each with 100%
checked-output coverage (1/1), generated Verilog LEC, and the installed
Python primitive loader. Missing verdicts, incomplete coverage, wrong exit
codes, or missing logs fail the regression. Installation logs, MCP responses,
configs, input hashes, checker logs, and summaries upload even on failure.

After [installation](../README.md#installation), run from the repository:

```bash
.venv/bin/python scripts/run_nix_mcp_regression.py \
  --binary /absolute/path/to/profile/bin/kepler-formal \
  --work-dir /absolute/path/to/new-regression-run
```

The work directory must not exist. Each case stages the current single-file
server there to retain working-directory reports.

The separate [offline workflow](../.github/workflows/offline-tests.yml) runs
without Nix or the checker:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Offline checks establish wrapper behavior, not formal results.

## Interpreting design checks

For SEC/SystemVerilog, pass YAML to `run_kepler_formal_yaml`; the creation
tool defaults to Verilog LEC. Consult the pinned
[flag reference](https://github.com/keplertech/kepler-formal/blob/0e0abf2aa6979e337aead8996983952dc1742530/docs/flags-spec.md)
and [SEC reference](https://github.com/keplertech/kepler-formal/blob/0e0abf2aa6979e337aead8996983952dc1742530/docs/sec-flags-spec.md).

Use absolute inputs and fresh outputs. `allowed_output_dir` or
`KEPLER_FORMAL_AI_OUTPUT_DIR` selects YAML/log output placement; the default
is this repository. Pass a candidate YAML copy because its `log_file` may be
updated.

Wrapper `success`/`error` reflects execution. SEC codes `0/1/2/3` mean
proved/partially proved/inconclusive/counterexample, but errors can overlap
those codes and export-only mode returns zero without proof. Inspect the
actual verdict, logs, assumptions, and checked-output coverage before
claiming equivalence.
