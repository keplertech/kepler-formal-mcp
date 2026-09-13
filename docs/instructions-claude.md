# Configure An MCP Client

Install the Python dependencies and packaged Kepler binary as described in the
[README](../README.md). Configure your client's stdio MCP connection using
absolute paths. This example uses the conventional `mcpServers` configuration:

```json
{
  "mcpServers": {
    "kepler-formal": {
      "command": "/absolute/path/to/kepler-formal-mcp/.venv/bin/python",
      "args": ["/absolute/path/to/kepler-formal-mcp/server.py"],
      "env": {
        "KEPLER_FORMAL_BIN": "/absolute/path/to/nix-profile/bin/kepler-formal",
        "KEPLER_FORMAL_WORKSPACE": "/absolute/path/to/design-workspace",
        "KEPLER_FORMAL_AI_OUTPUT_DIR": "/absolute/path/to/design-workspace/runs"
      }
    }
  }
}
```

The configuration-file location depends on the MCP client and platform. Do not
point `PYTHONPATH` at a Kepler source tree. The wrapper invokes the packaged CLI;
it does not import a Naja build from that tree.

After restarting/reconnecting the client, discover the three typed tools:
`verify_sec`, `run_kepler_formal_yaml`, `create_yaml_and_run_kepler_formal`.
Use `verify_sec` for new integrations. Both design paths and all `.lib` paths must
be inside the configured workspace. Tools may narrow the writable output root,
but cannot expand it; each call creates its own `sec-*` directory there.

For a missing binary, inspect `KEPLER_FORMAL_BIN` and executable permissions.
For a missing input, check the workspace and path: relative tool paths resolve
against the workspace, while relative paths inside YAML resolve against that
YAML's directory. For a partial proof, inspect coverage and skipped reports;
do not present a non-blocking warning as full equivalence.
