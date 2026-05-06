# Configuring Kepler Formal MCP in Claude Desktop

This guide explains how to add the Kepler Formal MCP server to Claude Desktop.

**Note:** This guide assumes you have already installed Kepler Formal MCP. See the main README and build script for installation instructions.

## Configuration

### Step 1: Locate Your Configuration File

Find your Claude Desktop configuration file:
- **Linux/Mac**: `~/.config/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

### Step 2: Add Kepler Formal to Your MCP Servers

Edit your configuration file and add the Kepler Formal server. Replace the placeholders with your actual paths:
- `<mcp-server-name>`: A name for this server (e.g., `kepler`, `kepler-formal`, `formal-verification`)
- `<path-to-kepler-formal-mcp>`: Full path to your Kepler Formal MCP repository
- `<path-to-kepler-formal-src>`: Path to the Kepler Formal source code (usually `<path-to-kepler-formal-mcp>/thirdparty/kepler-formal/src`)

```json
{
  "mcpServers": {
    "<mcp-server-name>": {
      "command": "python3",
      "args": [
        "<path-to-kepler-formal-mcp>/server.py"
      ],
      "env": {
        "PYTHONPATH": "<path-to-kepler-formal-src>"
      }
    }
  }
}
```

### Step 3: Verify

1. Save the configuration file
2. Restart Claude Desktop
3. Check that your MCP server appears as "Connected" in Claude's MCP server list

## Strongly Recommended: Add a Shared Folder

Adding a shared folder is strongly recommended. Without it, you'll need to copy-paste potentially large files directly into Claude's prompt, which is inefficient and can hit token limits.

With the filesystem MCP server, Claude can access your files directly:

```json
"filesystem": {
  "command": "npx",
  "args": [
    "-y",
    "@modelcontextprotocol/server-filesystem",
    "<path-to-shared-folder>"
  ]
}
```

Replace `<path-to-shared-folder>` with an absolute path to a folder where you want to store files accessible to Claude. This allows Claude to read large design files, test vectors, and documentation without copy-pasting.

## Troubleshooting

**Server won't connect:**
- Verify paths are absolute (not relative)
- Restart Claude Desktop after saving the config
- Ensure the config file is valid JSON
- Check that `<path-to-kepler-formal-mcp>/server.py` exists

**"Command not found" for python3:**
- Use the full path to Python: `/usr/bin/python3` instead of `python3`

**Invalid JSON errors:**
- Use a JSON validator to check your config file
- Ensure all commas and quotes are correct
