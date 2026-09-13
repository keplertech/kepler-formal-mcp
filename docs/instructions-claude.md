# Configure a Desktop MCP Client

Complete the [Nix and Python installation](../README.md#installation) first.
The server uses stdio and can be launched by a desktop client that supports
local MCP servers, including Claude Desktop.

Open the client's MCP configuration and add this server entry, replacing
all placeholder paths with absolute paths:

```json
{
  "mcpServers": {
    "kepler-formal": {
      "command": "/absolute/path/to/kepler-formal-mcp/.venv/bin/python",
      "args": ["/absolute/path/to/kepler-formal-mcp/server.py"],
      "env": {
        "KEPLER_FORMAL_BINARY": "/absolute/path/to/nix-profile/bin/kepler-formal",
        "KEPLER_FORMAL_AI_OUTPUT_DIR": "/absolute/path/to/design-project/runs/mcp"
      }
    }
  }
}
```

Find the checker path with `command -v kepler-formal` after installation. For
`install_kepler_formal.sh --profile /absolute/path/to/profile`, use
`/absolute/path/to/profile/bin/kepler-formal`. You can omit
`KEPLER_FORMAL_BINARY` when `kepler-formal` is already on the client's `PATH`.
Use the installed Nix wrapper, which supplies the checker's runtime; do not
add a checker source directory to `PYTHONPATH`.

Save the configuration and restart the client. The two available tools are
`run_kepler_formal_yaml` and `create_yaml_and_run_kepler_formal`. Design files
are read directly from the local filesystem. A separate filesystem MCP
server is optional if the client also needs to inspect or edit those files.

If the server does not connect, run the same absolute Python command from a
terminal, check that its MCP dependencies are installed, and validate the
client configuration JSON. If a tool reports a missing checker, test the exact
`KEPLER_FORMAL_BINARY` path with `--help`. Relative paths and shell shorthand
such as `~` in client command fields may not be expanded by the client.
