# kepler-formal-mcp

MCP tools for checking whether two Verilog designs are equivalent. NajaEDA loads the designs, then the published **Kepler Formal Python library** verifies them using the same native Naja runtime.

## Install

Use CPython 3.10–3.14 on Linux x86_64/aarch64, macOS ARM64, or Windows AMD64, where the pinned dependencies provide wheels.

```bash
git clone https://github.com/keplertech/kepler-formal-mcp.git
cd kepler-formal-mcp
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --only-binary=kepler-formal,najaeda .
```

On Windows, create and activate the environment with PowerShell:

```powershell
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --only-binary=kepler-formal,najaeda .
```

Installing this project installs `kepler-formal==0.5.0`, which requires `najaeda==0.7.24`, plus the MCP SDK and PyYAML. No Nix, submodule checkout, C++ build, or system compiler setup is needed.

Start the stdio MCP server with:

```bash
kepler-formal-mcp
```

The existing `python server.py` launcher also works using the installed environment. For desktop clients, use the absolute path to `.venv/bin/kepler-formal-mcp` (Windows: `.venv\Scripts\kepler-formal-mcp.exe`). See [Claude Desktop configuration](docs/instructions-claude.md).

## Tools

- `run_kepler_formal_yaml`: verify the pair specified by an existing YAML file.
- `create_yaml_and_run_kepler_formal`: create a YAML file from two Verilog paths and optional common Liberty libraries, then verify the pair.

Both tools use the Python library in a separate Python worker for each call. This keeps native solver output out of the MCP stdio channel and allows `timeout_seconds` to stop a verification. The worker loads both designs with NajaEDA and passes their live design handles to `kepler_formal.verify_designs`.

A minimal YAML file is:

```yaml
format: verilog
input_paths:
  - reference.v
  - candidate.v
liberty_files: []
verification: lec
solver: kissat
cnf_export: false
```

Exactly two structural Verilog netlist files are required. Synthesize behavioral RTL before loading it with NajaEDA. Paths inside an existing YAML file are resolved relative to that file; paths passed to the create tool are resolved relative to the server's launch directory. The create tool writes absolute design/library paths into its generated YAML.

Set `KEPLER_FORMAL_AI_OUTPUT_DIR` to a writable output directory, or pass `allowed_output_dir` to a tool. The default is the server's launch directory. Generated YAML and logs must stay inside the selected output directory. An existing YAML file is read without rewriting it.

Supported verification settings are `verification` (`lec` or `sec`; `mode` is an alias), `solver`, `max_k`, `sec_engine`, `sec_encoding`, `allow_boundary_mismatch`, `report_skipped_outputs`, `log_file`, and `log_level`. CNF export is unavailable through this library API: `cnf_export` defaults to `false`, and `true` returns a clear error. Unsupported CLI-only YAML options also return an error.

## Results

The returned JSON separates tool execution from the verification verdict:

- `status`: `success` when verification ran, or `error` for invalid input, timeout, or a worker failure.
- `verdict`: the equivalence outcome, such as `equivalent`, `different`, or `inconclusive`.
- `verification_result`: the structured result returned by Kepler Formal.
- `stdout_tail` / `stderr_tail`: captured worker output for diagnosis.

Always inspect `verdict`: a successful execution can report different designs. A bounded SEC check can be inconclusive; it is not an equivalence proof.

See [the small design example](docs/test-generation.md) for equivalent and different inputs.

## Test

```bash
python -m unittest discover -s tests -v
```

CI runs the tests against the published dependencies on Linux x86_64, macOS ARM64, and Windows AMD64 with Python 3.10 and 3.14. Tests cover the real Python verifier, configuration validation, worker isolation, and MCP stdio calls.
