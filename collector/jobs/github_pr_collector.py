"""GitHub PR collector — writes gov_github_pr to InfluxDB governance_raw bucket.

Schema (Grafana-compatible):
  measurement: gov_github_pr
  tags:        repo=<owner>/<repo>, state=merged|open|closed,
               author_team=<team>|unknown
  fields:      count=1,
               time_to_merge_seconds=<N> (merged, backward-compat),
               cycle_time_seconds=<N> (merged, DORA alias),
               review_comments=<N>,
               additions=<N> (merged, from individual PR endpoint),
               deletions=<N> (merged, from individual PR endpoint),
               changed_files=<N> (merged, from individual PR endpoint)
  timestamp:   merged_at (merged) | updated_at (open/closed)

Note: additions/deletions/changed_files require one extra API call per
merged PR (individual endpoint). open/closed PRs skip this enrichment.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS
from pydantic import BaseModel, Field
from tenacity import retry, stop_after_attempt, wait_exponential
from utils.team_mapper import get_team

from config import settings

log = structlog.get_logger(__name__)

_GITHUB_API = "https://api.github.com"
_BACKFILL_DAYS = 90
_PAGE_SIZE = 100

# ETag cache: repo → {state: etag}
_etag_cache: dict[str, dict[str, str]] = {}


class GitHubPR(BaseModel):
    number: int
    state: str
    created_at: datetime
    updated_at: datetime
    merged_at: datetime | None = None
    closed_at: datetime | None = None
    title: str = ""
    additions: int = 0
    deletions: int = 0
    changed_files: int = 0
    review_comments: int = Field(default=0, alias="review_comments")
    author_login: str = ""

    model_config = {"populate_by_name": True}

    @classmethod
    def from_api_item(cls, item: dict) -> GitHubPR:
        """Construct from a GitHub API PR list item, extracting author login."""
        author_login = (item.get("user") or {}).get("login", "")
        return cls(**{**item, "author_login": author_login})

    @property
    def resolved_state(self) -> str:
        if self.merged_at:
            return "merged"
        if self.state == "closed":
            return "closed"
        return "open"

    @property
    def reference_timestamp(self) -> datetime:
        if self.merged_at:
            return self.merged_at
        return self.updated_at

    @property
    def time_to_merge_seconds(self) -> float | None:
        if not self.merged_at:
            return None
        return (self.merged_at - self.created_at).total_seconds()


def pr_to_point(repo: str, pr: GitHubPR, author_login: str = "") -> Point:
    """Convert a GitHubPR to an InfluxDB Point with the Grafana-compatible schema."""
    author_team = get_team(author_login) if author_login else "unknown"
    p = (
        Point("gov_github_pr")
        .tag("repo", repo)
        .tag("state", pr.resolved_state)
        .tag("author_team", author_team)
        .field("count", 1)
        .field("review_comments", pr.review_comments)
        .time(pr.reference_timestamp, WritePrecision.S)
    )
    if pr.time_to_merge_seconds is not None:
        p = (
            p.field("time_to_merge_seconds", pr.time_to_merge_seconds)
            .field("cycle_time_seconds", pr.time_to_merge_seconds)
            .field("additions", pr.additions)
            .field("deletions", pr.deletions)
            .field("changed_files", pr.changed_files)
        )
    return p


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    reraise=True,
)
async def _fetch_page(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    params: dict[str, Any],
) -> httpx.Response:
    resp = await client.get(url, headers=headers, params=params, timeout=30)
    if resp.status_code == 304:
        return resp
    resp.raise_for_status()
    return resp


async def _fetch_pr_detail(
    client: httpx.AsyncClient,
    repo: str,
    pr_number: int,
    headers: dict[str, str],
) -> dict[str, int]:
    """Fetch additions/deletions/changed_files from the individual PR endpoint.

    Returns a dict with those three keys, or empty dict on error.
    """
    url = f"{_GITHUB_API}/repos/{repo}/pulls/{pr_number}"
    try:
        resp = await _fetch_page(client, url, headers, {})
        if resp.status_code == 304:
            return {}
        data = resp.json()
        return {
            "additions": data.get("additions", 0),
            "deletions": data.get("deletions", 0),
            "changed_files": data.get("changed_files", 0),
        }
    except Exception as exc:
        log.warning("pr_detail_fetch_failed", repo=repo, pr=pr_number, error=str(exc))
        return {}


async def fetch_prs(repo: str, state: str, since: datetime) -> list[GitHubPR]:
    """Fetch PRs for a repo/state combination with ETag caching and pagination."""
    owner_repo = repo  # already "owner/repo"
    url = f"{_GITHUB_API}/repos/{owner_repo}/pulls"
    headers = {
        "Authorization": f"Bearer {settings.GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    etag = _etag_cache.get(repo, {}).get(state)
    if etag:
        headers["If-None-Match"] = etag

    params: dict[str, Any] = {
        "state": state,
        "sort": "updated",
        "direction": "desc",
        "per_page": _PAGE_SIZE,
    }

    prs: list[GitHubPR] = []
    log_ctx = log.bind(repo=repo, state=state)

    async with httpx.AsyncClient() as client:
        page = 1
        while True:
            params["page"] = page
            try:
                resp = await _fetch_page(client, url, headers, params)
            except httpx.HTTPStatusError as exc:
                log_ctx.warning("github_api_error", status=exc.response.status_code)
                break

            if resp.status_code == 304:
                log_ctx.debug("github_etag_hit", page=page)
                break

            new_etag = resp.headers.get("ETag")
            if new_etag and page == 1:
                _etag_cache.setdefault(repo, {})[state] = new_etag

            items = resp.json()
            if not items:
                break

            # Stop paginating when PRs are older than backfill window
            stop = False
            for item in items:
                updated = datetime.fromisoformat(item["updated_at"].replace("Z", "+00:00"))
                if updated < since:
                    stop = True
                    break
                prs.append(GitHubPR.from_api_item(item))

            log_ctx.debug("github_page_fetched", page=page, count=len(items))

            if stop or "next" not in resp.links:
                break
            page += 1

    return prs


def _write_points(points: list[Point]) -> None:
    client = InfluxDBClient(
        url=settings.INFLUX_URL,
        token=settings.INFLUX_TOKEN,
        org=settings.INFLUX_ORG,
    )
    try:
        write_api = client.write_api(write_options=SYNCHRONOUS)
        write_api.write(
            bucket=settings.INFLUX_BUCKET_RAW,
            org=settings.INFLUX_ORG,
            record=points,
        )
    finally:
        client.close()


async def _enrich_merged_pr(
    client: httpx.AsyncClient,
    repo: str,
    pr: GitHubPR,
    headers: dict[str, str],
) -> GitHubPR:
    """Fetch additions/deletions/changed_files for a merged PR and return enriched copy."""
    detail = await _fetch_pr_detail(client, repo, pr.number, headers)
    if not detail:
        return pr
    return pr.model_copy(update=detail)


async def _collect_repo(repo: str, since: datetime) -> int:
    """Collect all states for one repo and write to InfluxDB. Returns point count."""
    all_points: list[Point] = []
    headers = {
        "Authorization": f"Bearer {settings.GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    async with httpx.AsyncClient() as client:
        for state in ("open", "closed"):  # closed includes merged
            try:
                prs = await fetch_prs(repo, state, since)
            except Exception as exc:
                log.warning("fetch_failed", repo=repo, state=state, error=str(exc))
                continue

            enriched: list[GitHubPR] = []
            for pr in prs:
                if pr.resolved_state == "merged":
                    pr = await _enrich_merged_pr(client, repo, pr, headers)
                enriched.append(pr)

            for pr in enriched:
                all_points.append(pr_to_point(repo, pr, author_login=pr.author_login))

            log.info("prs_fetched", repo=repo, state=state, count=len(prs))

    if all_points:
        _write_points(all_points)
        log.info("points_written", repo=repo, count=len(all_points))

    return len(all_points)


async def _run_async() -> None:
    since = datetime.now(UTC) - timedelta(days=_BACKFILL_DAYS)

    repos: list[str] = []
    if settings.GITHUB_REPOS:
        try:
            repos = json.loads(settings.GITHUB_REPOS)
        except json.JSONDecodeError:
            repos = [r.strip() for r in settings.GITHUB_REPOS.split(",") if r.strip()]
    if not repos and settings.GITHUB_ORG:
        repos = [f"{settings.GITHUB_ORG}/it-governance-dashboard"]

    log.info("github_pr_collector_start", repos=repos, since=since.isoformat())
    total = 0
    for repo in repos:
        total += await _collect_repo(repo, since)
    log.info("github_pr_collector_done", total_points=total)


def run() -> None:
    """Synchronous entry point for APScheduler."""
    asyncio.run(_run_async())
