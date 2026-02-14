"""HumioMCP - FastMCP server exposing Humio/LogScale tools."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession

from .config import AppConfig, load_config
from .humio_client import HumioClient


# ---------------------------------------------------------------------------
# Lifespan: load config once at startup
# ---------------------------------------------------------------------------


@dataclass
class AppContext:
    """Shared application context available to all tools."""

    config: AppConfig


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:  # noqa: ARG001
    """Load configuration on startup."""
    config = load_config()
    yield AppContext(config=config)


# ---------------------------------------------------------------------------
# FastMCP server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "HumioMCP",
    instructions=(
        "MCP server for querying Humio/LogScale dashboards and executing searches. "
        "Use list_dashboards to discover dashboards, get_dashboard_queries to inspect "
        "their queries, and execute_search to run log queries."
    ),
    lifespan=app_lifespan,
)


def _get_client(ctx: Context[ServerSession, AppContext]) -> tuple[AppConfig, None]:
    """Extract config from context."""
    return ctx.request_context.lifespan_context.config  # type: ignore[return-value]


def _make_client(config: AppConfig, cluster: str | None) -> HumioClient:
    """Create a HumioClient for the given (or default) cluster."""
    cluster_cfg = config.get_cluster(cluster)
    return HumioClient(cluster_cfg)


# ---------------------------------------------------------------------------
# Tool: list_dashboards
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_dashboards(
    repo: str,
    ctx: Context[ServerSession, AppContext],
    cluster: str = "",
    search_filter: str = "",
) -> str:
    """List all dashboards in a Humio repository/view.

    Args:
        repo: The repository or view name in Humio.
        cluster: (Optional) Cluster name from config. Uses default if empty.
        search_filter: (Optional) Filter dashboards by name substring.

    Returns:
        JSON with dashboard id, name, description for each dashboard.
    """
    config: AppConfig = ctx.request_context.lifespan_context.config
    client = _make_client(config, cluster or None)
    result = await client.list_dashboards(repo, search_filter or None)
    return result.model_dump_json(indent=2)


# ---------------------------------------------------------------------------
# Tool: get_dashboard_queries
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_dashboard_queries(
    repo: str,
    dashboard_name: str,
    ctx: Context[ServerSession, AppContext],
    cluster: str = "",
) -> str:
    """Get all search queries from a specific Humio dashboard.

    Returns the query string, time range, and widget info for each widget
    in the dashboard.

    Args:
        repo: The repository or view name in Humio.
        dashboard_name: The exact name of the dashboard.
        cluster: (Optional) Cluster name from config. Uses default if empty.

    Returns:
        JSON containing each widget's query string, time range, title, and ID.
    """
    config: AppConfig = ctx.request_context.lifespan_context.config
    client = _make_client(config, cluster or None)
    result = await client.get_dashboard_queries(repo, dashboard_name)
    return result.model_dump_json(indent=2)


# ---------------------------------------------------------------------------
# Tool: execute_search
# ---------------------------------------------------------------------------


@mcp.tool()
async def execute_search(
    repo: str,
    query_string: str,
    ctx: Context[ServerSession, AppContext],
    start: str = "24h",
    end: str = "now",
    cluster: str = "",
    max_results: int = 200,
) -> str:
    """Execute a search query on Humio/LogScale and return results as JSON.

    Supports both relative time ('24h', '7d', '30m') and
    ISO 8601 ('2024-01-01T00:00:00Z') for start/end.

    Args:
        repo: The repository or view name to search.
        query_string: The Humio search query string (e.g. 'error | count()').
        start: Start time - relative ('24h', '7d') or ISO 8601. Default '24h'.
        end: End time - relative or ISO 8601. Use 'now' for current time.
        cluster: (Optional) Cluster name from config. Uses default if empty.
        max_results: Maximum events to return (default 200).

    Returns:
        JSON with query results including events array and metadata.
    """
    config: AppConfig = ctx.request_context.lifespan_context.config
    client = _make_client(config, cluster or None)
    result = await client.execute_search(
        repo=repo,
        query_string=query_string,
        start=start,
        end=end,
        max_results=max_results,
    )
    return result.model_dump_json(indent=2)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main():
    """Run the HumioMCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
