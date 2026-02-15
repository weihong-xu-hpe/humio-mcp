"""Humio/LogScale API client using GraphQL and REST."""

from __future__ import annotations

import asyncio
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

    # Retryable transport errors: connection drops, incomplete reads,
    # and remote protocol violations (e.g. "incomplete chunked read").
    _RETRYABLE_ERRORS = (
        httpx.ReadError,
        httpx.ConnectError,
        httpx.RemoteProtocolError,
        httpx.CloseError,
    )

    # ------------------------------------------------------------------
    # Job-based query (create job → poll → fetch results)
    # Avoids long-lived streaming connections that get killed by
    # load balancers / reverse proxies with idle timeouts.
    # ------------------------------------------------------------------

    async def _create_query_job(
        self, repo: str, payload: dict[str, Any]
    ) -> str:
        """Create a query job and return its ID."""
        async with self._http_client() as client:
            resp = await client.post(
                f"/api/v1/repositories/{repo}/queryjobs",
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        return data["id"]

    async def _poll_query_job(
        self, repo: str, job_id: str, poll_interval: float = 2.0
    ) -> list[dict[str, Any]]:
        """Poll a query job until it completes, then return events.

        Each poll is a short-lived HTTP request, so idle timeouts
        on load balancers / proxies are not a problem.
        """
        poll_timeout = httpx.Timeout(connect=15.0, read=30.0, write=10.0, pool=10.0)

        async with self._http_client(timeout=poll_timeout) as client:
            while True:
                resp = await client.get(
                    f"/api/v1/repositories/{repo}/queryjobs/{job_id}",
                )
                resp.raise_for_status()
                data = resp.json()

                done = data.get("done", False)
                events = data.get("events", [])

                if done:
                    return events

                await asyncio.sleep(poll_interval)

    async def _delete_query_job(self, repo: str, job_id: str) -> None:
        """Best-effort cleanup of a finished query job."""
        try:
            async with self._http_client() as client:
                await client.delete(
                    f"/api/v1/repositories/{repo}/queryjobs/{job_id}",
                )
        except Exception:  # noqa: BLE001
            pass  # Cleanup is best-effort

    # ------------------------------------------------------------------
    # Streaming fallback (kept for reference / simple queries)
    # ------------------------------------------------------------------

    async def _stream_search_response(
        self, repo: str, payload: dict[str, Any]
    ) -> list[str]:
        """Stream search response with automatic retries on connection errors."""
        max_retries = 3
        search_timeout = httpx.Timeout(
            connect=15.0, read=600.0, write=15.0, pool=30.0
        )
        
        last_error: Exception | None = None
        for attempt in range(max_retries):
            try:
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
                return lines
            except self._RETRYABLE_ERRORS as e:
                last_error = e
                wait = 2.0 * (attempt + 1)  # 2s, 4s, 6s backoff
                if attempt < max_retries - 1:
                    await asyncio.sleep(wait)
        
        raise last_error or RuntimeError("Search request failed")

    @staticmethod
    def _parse_ndjson(lines: list[str]) -> list[dict[str, Any]]:
        """Parse NDJSON (newline-delimited JSON) from streamed chunks."""
        text = "".join(lines).strip()
        events: list[dict[str, Any]] = []
        if not text:
            return events
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events

    async def execute_search(
        self,
        repo: str,
        query_string: str,
        start: str = "24h",
        end: str = "now",
        max_results: int = 200,
    ) -> SearchResult:
        """Execute a search query using the Job API (create → poll → fetch).

        Uses short-lived HTTP requests to avoid idle-timeout disconnections
        from load balancers and reverse proxies.

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

        # Use Job API: short-lived requests that survive idle timeouts.
        # Falls back to streaming if job API fails (e.g. older Humio versions).
        job_id: str | None = None
        try:
            job_id = await self._create_query_job(repo, payload)
            events = await self._poll_query_job(repo, job_id)
        except (httpx.HTTPStatusError, KeyError):
            # Job API not available or returned unexpected format;
            # fall back to streaming with retries.
            job_id = None
            lines = await self._stream_search_response(repo, payload)
            events = self._parse_ndjson(lines)
        finally:
            if job_id:
                await self._delete_query_job(repo, job_id)

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
