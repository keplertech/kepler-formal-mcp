# kepler-formal-mcp

A local stdio MCP server for the [Kepler Formal](https://github.com/keplertech/kepler-formal)
CLI. Install the native checker from its published Nix package, then install
the Python server dependencies. No source checkout or build of the checker
is needed.

## Installation

Prerequisites: Python 3.10+ and [Nix 2.35+](https://nixos.org/download/).
The upstream Nix package supports **x86_64 Linux** (`x86_64-linux`) and
**Apple Silicon macOS** (`aarch64-darwin`).

```bash
git clone https://github.com/keplertech/kepler-formal-mcp.git
cd kepler-formal-mcp
./install_kepler_formal.sh
kepler-formal --help

python3 -m venv .venv
.venv/bin/python -m pip install --only-binary=:all: -r requirements.txt
```

The installer uses the latest published upstream Nix package, pinned to
[`0e0abf2aa6979e337aead8996983952dc1742530`](https://github.com/keplertech/kepler-formal/tree/0e0abf2aa6979e337aead8996983952dc1742530)
(package version `1.0.0`). The
[2026-09-11 publication run](https://github.com/keplertech/kepler-formal/actions/runs/34611550143)
published and verified public-cache downloads for Linux and Apple Silicon
macOS. The installer writes to the current user's Nix profile.
Use `./install_kepler_formal.sh --profile /absolute/path/to/profile` to choose
another profile.

A public-cache query on 2026-09-13 also confirmed the pinned macOS output
`/nix/store/2gc5ms1g8mb5shrjxk2gs76i0d3zvfz6-kepler-formal-1.0.0`. Linux cache
evidence here is the upstream publication CI; a Linux installation has not
been tested locally.

The `keplertech` Cachix cache, its public signing key, and the NixOS dependency
cache are passed as command options. The script does not edit Nix configuration
or run `cachix use`. Signature checking stays enabled. Local and remote builds
are disabled with `--max-jobs 0 --builders ''`; import-from-derivation is also
disabled. An absent package or dependency fails the installation. A pinned
revision does not by itself guarantee cache availability on every platform.

As of 2026-09-13, the newer upstream `main` revision
[`571ae59d39bd200de5b22d4727d9d00930311cff`](https://github.com/keplertech/kepler-formal/tree/571ae59d39bd200de5b22d4727d9d00930311cff)
contains additional checker changes and has not had a successful Nix
publication. Its macOS output was also unavailable in a direct cache check.
The installer pins the published Nix release; it does not claim to install
that newer source revision.

If a multi-user Nix daemon rejects the cache settings, an administrator must
configure the cache trust separately using the
[upstream Nix instructions](https://github.com/keplertech/kepler-formal/blob/0e0abf2aa6979e337aead8996983952dc1742530/README.md#nix--nixos).
The install script does not make that configuration change automatically.

The `submodules=1` in the pinned Nix URL is part of upstream package source
resolution. This MCP repository does not contain a checker submodule or build
it from source. The former `build_kepler_formal.sh` and its `--install-deps`
option have been replaced by `install_kepler_formal.sh`.

## Run the server

```bash
.venv/bin/python server.py
```

The checker is resolved from `KEPLER_FORMAL_BINARY` when set, otherwise from
`kepler-formal` on `PATH`. Set an explicit executable path for desktop clients
that do not inherit your shell's Nix profile:

```bash
export KEPLER_FORMAL_BINARY="$(command -v kepler-formal)"
.venv/bin/python server.py
```

For a custom profile, use `/absolute/path/to/profile/bin/kepler-formal`.
Keep the Nix package's wrapper executable; it supplies the native runtime and
embedded Python environment. No checker source `PYTHONPATH` is needed.

See [client configuration](docs/instructions-claude.md) for a desktop setup.

## Tools

- `run_kepler_formal_yaml`: run an existing checker YAML configuration.
- `create_yaml_and_run_kepler_formal`: create a Verilog configuration from
  design and Liberty paths, then run it.

Both tools retain their existing arguments and return the checker exit code,
stdout/stderr tails, and output paths. For SEC or SystemVerilog, provide the
appropriate YAML to `run_kepler_formal_yaml`; the configuration-creation tool
uses the checker's default LEC mode. See the
[pinned checker flag reference](https://github.com/keplertech/kepler-formal/blob/0e0abf2aa6979e337aead8996983952dc1742530/docs/flags-spec.md)
and [SEC reference](https://github.com/keplertech/kepler-formal/blob/0e0abf2aa6979e337aead8996983952dc1742530/docs/sec-flags-spec.md).

Use absolute design/library paths and a fresh writable output directory.
`allowed_output_dir` or `KEPLER_FORMAL_AI_OUTPUT_DIR` selects the allowed
YAML/log output directory; the default is this repository. The existing YAML
tool may update its configuration's `log_file`, so pass a candidate copy.

The wrapper's `success`/`error` field reflects process execution and does not
replace the checker's verification summary. In SEC, exit codes `0`, `1`, `2`,
and `3` respectively mean proved, partially proved, inconclusive, and a
counterexample; execution errors can overlap those codes. Export-only mode
also returns zero without a proof. Inspect the logs and checked-output
coverage before making an equivalence claim.

## Regression checks

The [Nix MCP regression workflow](.github/workflows/nix-regression.yml) runs on
pull requests, pushes, and manual dispatch for Linux and Apple Silicon macOS.
It configures the public cache, installs the pinned release with source builds
disabled, and calls both MCP tools through the actual stdio protocol. The cases
check SEC proof and counterexample results (each with 100% coverage of one output),
generated Verilog LEC, and loading a Python-defined primitive from the installed
package. Any missing verdict, incomplete coverage, wrong exit code, or missing
log fails the regression. Installation logs, MCP responses, configs, input hashes,
checker logs, and the result summary are uploaded even on failure.

Run it locally after installing the package and Python requirements:

```bash
.venv/bin/python scripts/run_nix_mcp_regression.py \
  --binary /absolute/path/to/profile/bin/kepler-formal \
  --work-dir /absolute/path/to/new-regression-run
```

The work directory must be fresh. Each case stages a copy of the current
single-file server there so the checker's working-directory reports stay
with that case. A failed installation cannot be replaced by an older binary
in the workflow.

The separate [offline workflow](.github/workflows/offline-tests.yml) runs unit
tests without Nix or the native checker:

```bash
.venv/bin/python -m unittest discover -s tests -v
```
