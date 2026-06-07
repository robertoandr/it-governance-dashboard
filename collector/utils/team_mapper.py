"""Maps GitHub author logins to team names from config/teams.yaml.

The YAML is loaded once per process and cached. Logins not found in
any team mapping fall back to "unknown".
"""

from __future__ import annotations

import functools
from pathlib import Path

import structlog
import yaml

log = structlog.get_logger(__name__)

_DEFAULT_TEAMS_PATH = Path(__file__).parent.parent / "config" / "teams.yaml"


@functools.lru_cache(maxsize=1)
def _load_mapping(teams_path: str) -> dict[str, str]:
    """Load and invert the teams.yaml into {login: team} dict. Cached per path."""
    path = Path(teams_path)
    if not path.exists():
        log.warning("teams_yaml_not_found", path=str(path))
        return {}

    try:
        data = yaml.safe_load(path.read_text())
        teams: dict[str, list[str]] = data.get("teams", {})
    except (yaml.YAMLError, AttributeError) as exc:
        log.error("teams_yaml_parse_error", error=str(exc))
        return {}

    mapping: dict[str, str] = {}
    for team_name, logins in teams.items():
        for login in logins or []:
            mapping[login] = team_name

    log.debug("teams_loaded", teams=list(teams.keys()), total_logins=len(mapping))
    return mapping


def get_team(author_login: str, teams_path: Path | None = None) -> str:
    """Return the team name for a GitHub login, or 'unknown' if not mapped.

    Args:
        author_login: GitHub username (e.g. "robertoandr").
        teams_path: Optional override for the teams YAML path (used in tests).
    """
    resolved = str(teams_path) if teams_path else str(_DEFAULT_TEAMS_PATH)
    mapping = _load_mapping(resolved)
    return mapping.get(author_login, "unknown")
