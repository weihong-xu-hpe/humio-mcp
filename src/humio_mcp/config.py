"""Configuration loading for HumioMCP."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib  # type: ignore[import]
    except ImportError:
        import tomli as tomllib  # type: ignore[import,no-redef]


@dataclass
class ClusterConfig:
    """Configuration for a single Humio cluster."""

    name: str
    url: str
    token: str
    skip_ssl_verify: bool = False


@dataclass
class AppConfig:
    """Application-level configuration."""

    default_cluster: str
    clusters: dict[str, ClusterConfig] = field(default_factory=dict)

    def get_cluster(self, name: str | None = None) -> ClusterConfig:
        """Get cluster config by name, or return the default."""
        cluster_name = name or self.default_cluster
        if cluster_name not in self.clusters:
            available = ", ".join(self.clusters.keys()) or "(none)"
            raise ValueError(
                f"Unknown cluster '{cluster_name}'. Available: {available}"
            )
        return self.clusters[cluster_name]


def load_config(config_path: str | Path | None = None) -> AppConfig:
    """Load configuration from a TOML file.

    Search order:
      1. Explicit path
      2. HUMIO_MCP_CONFIG env var
      3. ./config.toml
      4. ~/.config/humio-mcp/config.toml
    """
    import os

    if config_path is None:
        config_path = os.environ.get("HUMIO_MCP_CONFIG")

    candidates: list[Path] = []
    if config_path is not None:
        candidates.append(Path(config_path))
    else:
        candidates.append(Path("config.toml"))
        candidates.append(Path.home() / ".config" / "humio-mcp" / "config.toml")

    resolved: Path | None = None
    for p in candidates:
        if p.is_file():
            resolved = p
            break

    if resolved is None:
        searched = ", ".join(str(p) for p in candidates)
        raise FileNotFoundError(
            f"No config.toml found. Searched: {searched}. "
            "Copy config.example.toml to config.toml and fill in your credentials."
        )

    with open(resolved, "rb") as f:
        raw = tomllib.load(f)

    default_cluster = raw.get("default_cluster", "")
    clusters_raw: dict = raw.get("clusters", {})

    clusters: dict[str, ClusterConfig] = {}
    for name, info in clusters_raw.items():
        clusters[name] = ClusterConfig(
            name=name,
            url=info["url"].rstrip("/"),
            token=info["token"],
            skip_ssl_verify=info.get("skip_ssl_verify", False),
        )

    if not clusters:
        raise ValueError("No clusters defined in config.toml")

    if default_cluster and default_cluster not in clusters:
        raise ValueError(
            f"default_cluster '{default_cluster}' not found in [clusters]"
        )

    # If no default specified, use the first cluster
    if not default_cluster:
        default_cluster = next(iter(clusters))

    return AppConfig(default_cluster=default_cluster, clusters=clusters)
