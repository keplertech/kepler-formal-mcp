# Configuring Kepler Formal MCP in Claude Desktop

First install the project in a virtual environment using the [README](../README.md). The environment includes the published Kepler Formal and NajaEDA packages.

Add this entry to your Claude Desktop MCP configuration, replacing both paths with absolute paths on your machine:

```json
{
  "mcpServers": {
    "kepler-formal": {
      "command": "/absolute/path/to/kepler-formal-mcp/.venv/bin/kepler-formal-mcp",
      "env": {
        "KEPLER_FORMAL_AI_OUTPUT_DIR": "/absolute/path/to/verification-output"
      }
    }
  }
}
```

On Windows, use the virtual environment's executable and JSON-escaped backslashes:

```json
{
  "mcpServers": {
    "kepler-formal": {
      "command": "C:\\absolute\\path\\kepler-formal-mcp\\.venv\\Scripts\\kepler-formal-mcp.exe",
      "env": {
        "KEPLER_FORMAL_AI_OUTPUT_DIR": "C:\\absolute\\path\\verification-output"
      }
    }
  }
}
```

No `PYTHONPATH` or Kepler Formal source directory is required. The executable uses its virtual environment even when Claude starts from another directory. Save the configuration and restart Claude Desktop.

Use absolute design and library paths in tool calls because desktop clients may launch servers from an unexpected directory. The output environment variable controls where generated YAML and logs can be written; each call can override it with `allowed_output_dir`.

For example:

> Use `create_yaml_and_run_kepler_formal` to compare `/my/designs/reference.v` and `/my/designs/candidate.v`, with no Liberty libraries. Report the verification verdict.

For an existing YAML file, paths inside it are relative to its directory. Ask Claude to use `run_kepler_formal_yaml` with the YAML file's absolute path. The MCP reads the designs directly; a filesystem MCP is only needed if you also want Claude to create or edit the design files.

If connection fails, check that the configured executable exists and that the environment was installed successfully with `python -m pip check`. Run the executable in a terminal to inspect errors on stderr. It normally waits silently for MCP messages on stdin.
