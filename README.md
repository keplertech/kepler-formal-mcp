# kepler-formal-mcp

A local stdio MCP server for [Kepler Formal](https://github.com/keplertech/kepler-formal),
providing formal equivalence checks through the packaged Nix CLI.

## Installation

Requires Python 3.10+ and [Nix 2.35+](https://nixos.org/download/).
Supported platforms: x86_64 Linux and Apple Silicon macOS.

```bash
git clone https://github.com/keplertech/kepler-formal-mcp.git
cd kepler-formal-mcp
./install_kepler_formal.sh
python3 -m venv .venv
.venv/bin/python -m pip install --only-binary=:all: -r requirements.txt
```

The installer downloads a pinned release from the public Kepler Cachix cache.
It uses your Nix profile, with source builds disabled. No checker submodule
or manual compilation is needed. See [installation details](docs/installation.md)
for the release pin, custom profiles, and cache troubleshooting.

## Usage

```bash
.venv/bin/python server.py
```

For desktop clients, follow the [client configuration guide](docs/instructions-claude.md).
Set `KEPLER_FORMAL_BINARY` to the installed executable if it is absent from
the client's `PATH`. Use absolute design paths and a fresh output directory.

- `run_kepler_formal_yaml`: run an existing YAML configuration, including SEC
  and SystemVerilog checks.
- `create_yaml_and_run_kepler_formal`: generate a Verilog LEC configuration
  from design and Liberty paths, then run it.

Results include exit codes, log paths, and output tails. Read the checker
verdict and coverage before claiming equivalence; wrapper success alone
is insufficient.

## Tests

The [Nix regression workflow](.github/workflows/nix-regression.yml) tests both
MCP tools on Linux and macOS, covering SEC proof/counterexample results,
Verilog LEC, and Python primitives. Logs are saved even on failure.
See [regression instructions](docs/regression.md) for local runs and offline tests.
