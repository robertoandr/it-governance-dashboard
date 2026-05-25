import json
import logging
from datetime import UTC, datetime

import psycopg
import requests

from config import settings

log = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"


def collect_github_pats():
    log.info("Coletando inventário de PATs do GitHub...")

    headers = {
        "Authorization": f"Bearer {settings.GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    url = f"{GITHUB_API}/orgs/{settings.GITHUB_ORG}/personal-access-tokens"
    pats = []

    try:
        while url:
            r = requests.get(url, headers=headers, timeout=30)
            if r.status_code == 404:
                log.warning("Org sem fine-grained PAT policy. Pulando.")
                return
            r.raise_for_status()
            pats.extend(r.json())
            url = r.links.get("next", {}).get("url")
    except requests.RequestException as e:
        log.exception("Erro na API GitHub: %s", e)
        return

    log.info("%d PATs encontrados", len(pats))

    now = datetime.now(UTC)
    org_tag = json.dumps({"org": settings.GITHUB_ORG})

    with psycopg.connect(settings.POSTGRES_DSN) as conn:
        with conn.cursor() as cur:
            for pat in pats:
                cur.execute(
                    """
                    INSERT INTO governance.pats (
                        platform, token_id, token_hint, token_type,
                        owner_login, name, scopes, created_at, expires_at,
                        last_used_at, status
                    ) VALUES (
                        'github', %s, %s, 'fine_grained',
                        %s, %s, %s, %s, %s, %s, 'active'
                    )
                    ON CONFLICT (platform, token_id) DO UPDATE SET
                        expires_at = EXCLUDED.expires_at,
                        last_used_at = EXCLUDED.last_used_at,
                        status = EXCLUDED.status,
                        collected_at = NOW()
                """,
                    (
                        str(pat["id"]),
                        pat.get("token_last_eight"),
                        pat["owner"]["login"],
                        pat.get("name"),
                        json.dumps(pat.get("permissions", {})),
                        pat.get("created_at"),
                        pat.get("expires_at"),
                        pat.get("last_used_at"),
                    ),
                )

            metrics = [
                (now, "github_pats_total", "github", org_tag, len(pats)),
                (
                    now,
                    "github_pats_with_expiration",
                    "github",
                    org_tag,
                    sum(1 for p in pats if p.get("expires_at")),
                ),
                (
                    now,
                    "github_pats_expiring_7d",
                    "github",
                    org_tag,
                    sum(1 for p in pats if _expiring_within(p.get("expires_at"), 7)),
                ),
                (
                    now,
                    "github_pats_expiring_30d",
                    "github",
                    org_tag,
                    sum(1 for p in pats if _expiring_within(p.get("expires_at"), 30)),
                ),
            ]
            cur.executemany(
                """
                INSERT INTO governance.metrics
                    (time, metric_name, source, tags, value_num)
                VALUES (%s, %s, %s, %s, %s)
            """,
                metrics,
            )

        conn.commit()

    log.info("Inventário concluído (%d métricas gravadas)", len(metrics))


def _expiring_within(expires_at_str, days: int) -> bool:
    if not expires_at_str:
        return False
    try:
        exp = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
        delta = exp - datetime.now(UTC)
        return 0 <= delta.days <= days
    except (ValueError, TypeError):
        return False
