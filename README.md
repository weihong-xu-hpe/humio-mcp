# HumioMCP

MCP server for querying Humio/LogScale dashboards and executing search queries.

Built with [FastMCP](https://github.com/modelcontextprotocol/python-sdk) (Python).

## Features

- **list_dashboards** — List all dashboards in a Humio repo/view, with optional name filtering
- **get_dashboard_queries** — Extract all search queries (with time ranges) from a dashboard's widgets
- **execute_search** — Run a search query and get results as JSON (default limit: 200 events)
- Multi-cluster support via TOML config
- Both relative time (`24h`, `7d`) and ISO 8601 (`2024-01-01T00:00:00Z`) supported

---

## Quick Start for Others

### Option A: One-click VS Code config (recommended)

No clone needed. Add this to your VS Code `settings.json` or `.vscode/mcp.json`:

```json
{
  "servers": {
    "humio-mcp": {
      "type": "stdio",
      "command": "uvx",
      "args": [
        "--from", "git+https://github.com/weihong-xu-hpe/HumioMCP.git",
        "humio-mcp"
      ],
      "env": {
        "HUMIO_MCP_CONFIG": "${userHome}/.config/humio-mcp/config.toml"
      }
    }
  }
}
```

Then create the config file at `~/.config/humio-mcp/config.toml`:

```toml
default_cluster = "us-west-2"

[clusters.us-west-2]
url = "https://your-humio-url.example.com/logs"
token = "your-api-token"
```

That's it — `uvx` handles install and updates automatically.

> **Prerequisite:** [uv](https://docs.astral.sh/uv/getting-started/installation/) must be installed (`brew install uv` or `curl -LsSf https://astral.sh/uv/install.sh | sh`)

### Option B: Clone and run locally

```bash
git clone https://github.com/weihong-xu-hpe/HumioMCP.git
cd HumioMCP
uv sync

cp config.example.toml config.toml
# Edit config.toml with your cluster URLs and API tokens
```

VS Code config for local clone:

```json
{
  "servers": {
    "humio-mcp": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "--directory", "/path/to/HumioMCP", "mcp", "run", "src/humio_mcp/server.py"]
    }
  }
}
```

### Option C: Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "humio-mcp": {
      "command": "uvx",
      "args": [
        "--from", "git+https://github.com/weihong-xu-hpe/HumioMCP.git",
        "humio-mcp"
      ],
      "env": {
        "HUMIO_MCP_CONFIG": "/Users/yourname/.config/humio-mcp/config.toml"
      }
    }
  }
}
```

---

## Configuration

Edit `config.toml` (or `~/.config/humio-mcp/config.toml`):

```toml
default_cluster = "us-west-2"

[clusters.us-west-2]
url = "https://mira-us-west-2.cloudops.ccs.arubathena.com/logs"
token = "your-api-token"

[clusters.eu-central-1]
url = "https://mira-eu-central-1.example.com/logs"
token = "another-token"
```

Config search order:
1. `HUMIO_MCP_CONFIG` environment variable
2. `./config.toml`
3. `~/.config/humio-mcp/config.toml`

## Development

```bash
# MCP Inspector (interactive debugging)
uv run mcp dev src/humio_mcp/server.py

# Stdio mode
uv run mcp run src/humio_mcp/server.py
```

## Tools

### list_dashboards
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| repo | str | Yes | Repository/view name |
| cluster | str | No | Cluster name (default from config) |
| search_filter | str | No | Filter by name substring |

### get_dashboard_queries
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| repo | str | Yes | Repository/view name |
| dashboard_name | str | Yes | Dashboard name |
| cluster | str | No | Cluster name |

### execute_search
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| repo | str | Yes | Repository/view name |
| query_string | str | Yes | Humio search query |
| start | str | No | Start time (default: `24h`) |
| end | str | No | End time (default: `now`) |
| cluster | str | No | Cluster name |
| max_results | int | No | Max events (default: 200) |
