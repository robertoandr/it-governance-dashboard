"""M365 OTel smoke runner — generates a complete trace without real Graph API creds.

Patches GraphClient with a 2-page mock response, runs one full collection cycle,
and exports the trace to the local OTLP collector (localhost:4317).

Usage:
    python scripts/m365_otel_smoke.py [tenant_id] [otlp_endpoint]

Defaults:
    tenant_id     = itgov-smoke-tenant
    otlp_endpoint = localhost:4317

Trace emitted:
    m365.run_collection
      └─ m365.sp_orphans.collect
           ├─ graph.auth.token_fetch     (mocked)
           └─ graph.api.get ×2 pages    (mocked)
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

# ── Stub missing required env vars BEFORE config.py is imported ───────────────
# config.py requires these vars even when the M365 collector doesn't use them.
for _var, _val in {
    "ZABBIX_FRONT_URL": "http://localhost",
    "ZABBIX_PASSWORD": "stub-zbx-pass",
    "ZENDESK_SUBDOMAIN": "stub",
    "ZENDESK_EMAIL": "stub@stub.com",
    "ZENDESK_API_TOKEN": "stub-token",
    "AZURE_TENANT_ID": "stub-tenant",
    "AZURE_CLIENT_ID": "stub-client",
    "AZURE_CLIENT_SECRET": "stub-secret",
}.items():
    os.environ[_var] = os.environ.get(_var) or _val  # override empty strings

# ── Bootstrap OTel BEFORE any itgov import so get_tracer() is ready ──────────
_TENANT_ID = sys.argv[1] if len(sys.argv) > 1 else "itgov-smoke-tenant"
_OTLP_ENDPOINT = sys.argv[2] if len(sys.argv) > 2 else "localhost:4317"

from itgov.observability import configure_observability  # noqa: E402

configure_observability(
    service_name="itgov-m365-collector",
    service_version="smoke",
    environment="dev",
    otlp_endpoint=_OTLP_ENDPOINT,
    insecure=True,
)

from itgov.services.sp_orphans_collector import run_collection  # noqa: E402

# ── Mock data: 2 pages, mix of risk levels ────────────────────────────────────

_DELTA_LINK = f"https://graph.microsoft.com/v1.0/servicePrincipals/delta?$deltatoken=smoke-{int(time.time())}"

_PAGE1 = [
    {
        "id": f"sp-smoke-{i:03d}",
        "displayName": f"SmokeApp-{i:03d}",
        "servicePrincipalType": "Application",
        "appRoles": [],
        "passwordCredentials": [],
        "keyCredentials": [],
        "signInActivity": {"lastSignInDateTime": "2024-06-01T12:00:00Z"},
        "createdDateTime": "2023-01-15T10:00:00Z",
    }
    for i in range(20)
]

_PAGE2_RISKY = [
    {
        "id": "sp-smoke-critical-001",
        "displayName": "DangerousApp-WriteAll",
        "servicePrincipalType": "Application",
        "appRoles": [
            {"value": "Application.ReadWrite.All"},
            {"value": "Directory.ReadWrite.All"},
        ],
        "passwordCredentials": [],
        "keyCredentials": [],
        "signInActivity": None,  # never signed in → inactivity score max
        "createdDateTime": "2020-03-01T00:00:00Z",
    },
    {
        "id": "sp-smoke-high-001",
        "displayName": "HighRiskApp-GroupWrite",
        "servicePrincipalType": "Application",
        "appRoles": [{"value": "Group.ReadWrite.All"}],
        "passwordCredentials": [
            {
                "endDateTime": "2022-01-01T00:00:00Z",  # expired
                "keyId": "cred-expired-001",
            }
        ],
        "keyCredentials": [],
        "signInActivity": {"lastSignInDateTime": "2021-01-01T00:00:00Z"},
        "createdDateTime": "2019-06-01T00:00:00Z",
    },
    {
        "id": "sp-smoke-ok-001",
        "displayName": "HealthyApp-ActiveRecent",
        "servicePrincipalType": "Application",
        "appRoles": [],
        "passwordCredentials": [],
        "keyCredentials": [],
        "signInActivity": {"lastSignInDateTime": "2026-05-01T09:00:00Z"},
        "createdDateTime": "2025-01-01T00:00:00Z",
    },
]

_PAGE2 = _PAGE2_RISKY
_ALL_SPS = _PAGE1 + _PAGE2
_NEXT_LINK = "https://graph.microsoft.com/v1.0/servicePrincipals/delta?$skiptoken=smoke-page2"


async def _mock_delta_iterator(tenant_id: str, delta_link=None):
    """2-page mock: page1 (20 normal SPs) then page2 (3 risky SPs)."""
    print(f"  [mock] page 1 → {len(_PAGE1)} SPs")
    for sp in _PAGE1:
        yield sp
    print(f"  [mock] page 2 → {len(_PAGE2)} SPs (includes critical + high risk)")
    for sp in _PAGE2:
        yield sp


# ── Patch _fetch_token and _get so graph spans still fire ─────────────────────

_MOCK_TOKEN_RESP = MagicMock()
_MOCK_TOKEN_RESP.status_code = 200
_MOCK_TOKEN_RESP.json.return_value = {"access_token": "smoke-token-not-real"}


async def _mock_get_page1(client, url, token):
    return {"value": _PAGE1, "@odata.nextLink": _NEXT_LINK}


async def _mock_get_page2(client, url, token):
    return {"value": _PAGE2, "@odata.deltaLink": _DELTA_LINK}


_mock_get_calls = [_mock_get_page1, _mock_get_page2]
_call_idx = 0


async def _mock_get(client, url, token):
    global _call_idx
    fn = _mock_get_calls[min(_call_idx, len(_mock_get_calls) - 1)]
    _call_idx += 1
    return await fn(client, url, token)


async def main() -> None:
    print(f"\n{'=' * 60}")
    print("  M365 OTel Smoke Runner")
    print(f"  tenant_id  : {_TENANT_ID}")
    print(f"  endpoint   : {_OTLP_ENDPOINT}")
    print(f"  total SPs  : {len(_ALL_SPS)}  (page1={len(_PAGE1)}, page2={len(_PAGE2)})")
    print(f"{'=' * 60}\n")

    t0 = time.monotonic()

    # Mock DeltaTokenStore (no real SQLite needed)
    mock_store = AsyncMock()
    mock_store.get.return_value = None  # first run → full mode

    # Patch at the httpx layer so _fetch_token's OTel span still fires
    mock_token_resp = MagicMock()
    mock_token_resp.status_code = 200
    mock_token_resp.json.return_value = {"access_token": "smoke-token-not-real"}

    with (
        patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_token_resp)),
        patch("itgov.services.graph_client._get", new=AsyncMock(side_effect=_mock_get)),
        patch("itgov.services.sp_orphans_collector.write_sp_risk"),
        patch("itgov.services.sp_orphans_collector.write_summary_snapshot"),
        patch(
            "itgov.services.sp_orphans_collector.DeltaTokenStore",
            return_value=mock_store,
        ),
    ):
        result = await run_collection(_TENANT_ID)

    elapsed = int((time.monotonic() - t0) * 1000)

    print(f"\n{'=' * 60}")
    print("  Collection result:")
    for k, v in result.items():
        print(f"    {k:<22} {v}")
    print(f"  wall_ms  : {elapsed}")
    print(f"{'=' * 60}")

    # Give BatchSpanProcessor time to flush
    print("\n  Waiting 3s for OTLP export...")
    await asyncio.sleep(3)

    from itgov.observability import shutdown

    shutdown()
    print("\n  ✅ Trace exported → check Grafana/Tempo for 'm365.run_collection'")
    print("     Tempo search: service.name=itgov-m365-collector")
    print("     Grafana URL : http://localhost:3000/explore (datasource: Tempo)")


if __name__ == "__main__":
    asyncio.run(main())
