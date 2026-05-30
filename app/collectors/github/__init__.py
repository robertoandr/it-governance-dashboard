"""Coletor GitHub — PRs e workflow runs para governance_raw."""

from app.collectors.github.collector import GitHubCollector
from app.collectors.github.config import GitHubCollectorSettings

__all__ = ["GitHubCollector", "GitHubCollectorSettings"]
