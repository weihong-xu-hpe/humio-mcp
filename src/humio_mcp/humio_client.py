"""Humio/LogScale API client using GraphQL and REST."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from .config import ClusterConfig
from .models import (
    DashboardInfo,
    DashboardListResult,
    DashboardQueriesResult,
    SearchResult,
    WidgetQuery,
)

# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

_RELATIVE_SUFFIXES = {
    "s": "seconds",
    "m": "minutes",
    "h": "hours",
    "d": "days",
    "w": "weeks",
}


def _is_relative_time(t: str) -> bool:
    """Check if a time string is relative (e.g. '24h', '7d', '30m')."""
    if not t:
        return False
    t = t.strip()
    return len(t) >= 2 and t[-1] in _RELATIVE_SUFFIXES and t[:-1].isdigit()


def _to_epoch_ms(t: str) -> int:
    """Convert a time string to epoch milliseconds.

    Supports:
      - Relative: '24h', '7d', '30m', '60s', '2w'
      - ISO 8601: '2024-01-01T00:00:00Z'
      - Epoch ms as bare integer string
    """
    import datetime

    t = t.strip()

    # Relative time
    if _is_relative_time(t):
        amount = int(t[:-1])
        unit = t[-1]
        delta_kwargs = {_RELATIVE_SUFFIXES[unit]: amount}
        dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            **delta_kwargs
        )
        return int(dt.timestamp() * 1000)

    # Bare epoch ms
    if t.isdigit():
        return int(t)

    # ISO 8601
    # Handle 'Z' suffix for Python < 3.11 compatibility
    iso = t.replace("Z", "+00:00")
    dt = datetime.datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return int(dt.timestamp() * 1000)


# ---------------------------------------------------------------------------
# Humio Client
# ---------------------------------------------------------------------------


class HumioClient:
    """Async client for Humio/LogScale APIs."""

    def __init__(self, cluster: ClusterConfig, timeout: float = 30.0):
        self.cluster = cluster
        self.base_url = cluster.url
        self.headers = {
            "Authorization": f"Bearer {cluster.token}",
            "Content-Type": "application/json",
        }
        self.timeout = timeout

    def _http_client(self, timeout: float | httpx.Timeout | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            headers=self.headers,
            timeout=timeout or self.timeout,
            verify=False,  # Many internal deployments use self-signed certs
        )

    # ------------------------------------------------------------------
    # GraphQL helper
    # ------------------------------------------------------------------

    async def _graphql(self, query: str, variables: dict | None = None) -> dict:
        """Execute a GraphQL query against the Humio API."""
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables

        async with self._http_client() as client:
            resp = await client.post("/graphql", json=payload)
            resp.raise_for_status()
            data = resp.json()

        if "errors" in data and data["errors"]:
            error_msgs = "; ".join(e.get("message", str(e)) for e in data["errors"])
            raise RuntimeError(f"Humio GraphQL error: {error_msgs}")

        return data.get("data", {})

    # ------------------------------------------------------------------
    # Dashboards
    # ------------------------------------------------------------------

    async def list_dashboards(
        self, repo: str, search_filter: str | None = None
    ) -> DashboardListResult:
        """List dashboards in a repository/view."""
        query = """
        query ListDashboards($repo: String!) {
          searchDomain(name: $repo) {
            dashboards {
              id
              name
              description
            }
          }
        }
        """
        data = await self._graphql(query, {"repo": repo})

        dashboards_raw = (
            data.get("searchDomain", {}).get("dashboards") or []
        )

        dashboards = [
            DashboardInfo(
                id=d["id"],
                name=d.get("name", ""),
                description=d.get("description", ""),
            )
            for d in dashboards_raw
        ]

        # Optional name filter
        if search_filter:
            lf = search_filter.lower()
            dashboards = [d for d in dashboards if lf in d.name.lower()]

        return DashboardListResult(
            cluster=self.cluster.name,
            repo=repo,
            dashboards=dashboards,
            total=len(dashboards),
        )

    async def get_dashboard_queries(
        self, repo: str, dashboard_name: str
    ) -> DashboardQueriesResult:
        """Get all widget queries from a dashboard by name.

        Uses inline fragments on ``QueryBasedWidget`` which exposes
        ``queryString``, ``start`` and ``end`` directly as scalar fields.
        """
        query = """
        query GetDashboard($repo: String!) {
          searchDomain(name: $repo) {
            dashboards {
              id
              name
              description
              widgets {
                ... on QueryBasedWidget {
                  id
                  title
                  queryString
                  start
                  end
                  isLive
                  widgetType
                }
              }
              sections {
                id
                title
                widgetIds
              }
            }
          }
        }
        """
        data = await self._graphql(query, {"repo": repo})

        dashboards_raw = (
            data.get("searchDomain", {}).get("dashboards") or []
        )

        # Find matching dashboard (case-insensitive)
        target = None
        name_lower = dashboard_name.lower()
        for d in dashboards_raw:
            if d.get("name", "").lower() == name_lower:
                target = d
                break

        if target is None:
            available = [d.get("name", "") for d in dashboards_raw]
            raise ValueError(
                f"Dashboard '{dashboard_name}' not found in repo '{repo}'. "
                f"Available dashboards: {available}"
            )

        # Extract queries from QueryBasedWidget instances
        queries: list[WidgetQuery] = []

        for w in target.get("widgets") or []:
            # Non-QueryBasedWidget entries appear as empty dicts
            # because only the inline fragment fields are requested.
            query_string = w.get("queryString")
            if not query_string:
                continue

            queries.append(
                WidgetQuery(
                    widget_id=w.get("id", ""),
                    widget_title=w.get("title", ""),
                    query_string=query_string,
                    start=w.get("start", ""),
                    end=w.get("end", ""),
                )
            )

        return DashboardQueriesResult(
            cluster=self.cluster.name,
            repo=repo,
            dashboard_name=target.get("name", dashboard_name),
            dashboard_id=target.get("id", ""),
            queries=queries,
            total=len(queries),
        )

    # ------------------------------------------------------------------
    # Search / Query execution
    # ------------------------------------------------------------------

    async def execute_search(
        self,
        repo: str,
        query_string: str,
        start: str = "24h",
        end: str = "now",
        max_results: int = 200,
    ) -> SearchResult:
        """Execute a search query using the REST API.

        The REST query API is simpler and more reliable for one-shot queries
        than the GraphQL createQueryJob flow.

        Args:
            repo: Repository/view name.
            query_string: The Humio query string.
            start: Start time - relative ('24h','7d') or ISO 8601.
            end: End time - relative or ISO 8601. Use 'now' for current time.
            max_results: Maximum number of events to return (default 200).
        """
        # Build time range
        if end.strip().lower() == "now":
            end_ms = int(time.time() * 1000)
        else:
            end_ms = _to_epoch_ms(end)

        start_ms = _to_epoch_ms(start)

        payload: dict[str, Any] = {
            "queryString": query_string,
            "start": start_ms,
            "end": end_ms,
            "isLive": False,
        }

        # Search queries can take a long time depending on data volume.
        # Use streaming to handle chunked transfer-encoding properly,
        # and a generous read timeout (5 min) for large scans.
        search_timeout = httpx.Timeout(
            connect=10.0, read=300.0, write=10.0, pool=10.0
        )
        async with self._http_client(timeout=search_timeout) as client:
            lines: list[str] = []
            async with client.stream(
                "POST",
                f"/api/v1/repositories/{repo}/query",
                json=payload,
                headers={"Accept": "application/x-ndjson"},
            ) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_text():
                    lines.append(chunk)

        # Parse NDJSON (newline-delimited JSON) from streamed chunks
        text = "".join(lines).strip()
        events: list[dict[str, Any]] = []
        if text:
            for line in text.split("\n"):
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    # Some lines might be metadata; skip
                    continue

        # Limit results
        truncated = events[:max_results]

        return SearchResult(
            cluster=self.cluster.name,
            repo=repo,
            query_string=query_string,
            start=start,
            end=end,
            events=truncated,
            total_events=len(truncated),
            metadata={
                "total_before_limit": len(events),
                "max_results": max_results,
                "truncated": len(events) > max_results,
            },
        )
