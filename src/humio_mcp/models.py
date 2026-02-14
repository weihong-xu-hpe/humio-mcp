"""Pydantic models for HumioMCP."""

from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, Field


class DashboardInfo(BaseModel):
    """Summary information about a dashboard."""

    id: str = Field(description="Dashboard ID")
    name: str = Field(description="Dashboard display name")
    description: Optional[str] = Field(default=None, description="Dashboard description")


class DashboardListResult(BaseModel):
    """Result of listing dashboards."""

    cluster: str
    repo: str
    dashboards: List[DashboardInfo]
    total: int


class WidgetQuery(BaseModel):
    """A single query/widget from a dashboard."""

    widget_id: str = Field(description="Widget ID")
    widget_title: str = Field(default="", description="Widget title/name")
    query_string: str = Field(description="The search query string")
    start: str = Field(default="", description="Query start time")
    end: str = Field(default="", description="Query end time")


class DashboardQueriesResult(BaseModel):
    """Result of getting dashboard queries."""

    cluster: str
    repo: str
    dashboard_name: str
    dashboard_id: str
    queries: List[WidgetQuery]
    total: int


class SearchResult(BaseModel):
    """Result of executing a search query."""

    cluster: str
    repo: str
    query_string: str
    start: str
    end: str
    events: List[dict[str, Any]] = Field(
        default_factory=list, description="Search result events/rows"
    )
    total_events: int = Field(description="Number of events returned")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Additional metadata from the query"
    )
