"""M365 OTel smoke runner — generates traces and metrics without real Graph API creds.

Patches GraphClient with a 2-page mock response, runs one full collection cycle,
and exports to the local OTLP collector (localhost:4317).

Usage:
    python scripts/m365_otel_smoke.py [tenant_id] [otlp_endpoint] [--emit-metrics]

Defaults:
    tenant_id     = itgov-smoke-tenant
    otlp_endpoint = localhost:4317

Signals emitted:
    Traces:
        m365.run_collection
          └─ m365.sp_orphans.collect
               ├─ graph.auth.token_fetch     (mocked httpx layer)
               └─ graph.api.get ×2 pages

    Metrics (with --emit-metrics):
        m365_collections_total, m365_collection_duration_seconds
        m365_graph_requests_total, m365_graph_errors_total,
        m365_graph_request_duration_seconds
        m365_sps_risk_count, m365_sps_orphans_actionable
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

# ── Parse CLI args BEFORE imports ─────────────────────────────────────────────
_args = [a for a in sys.argv[1:] if not a.startswith("--")]
_flags = {a for a in sys.argv[1:] if a.startswith("--")}

_TENANT_ID = _args[0] if len(_args) > 0 else "itgov-smoke-tenant"
_OTLP_ENDPOINT = _args[1] if len(_args) > 1 else "localhost:4317"
_EMIT_METRICS = "--emit-metrics" in _flags

# ── Stub missing required env vars BEFORE config.py is imported ───────────────
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
    os.environ[_var] = os.environ.get(_var) or _val

# ── Bootstrap OTel traces ─────────────────────────────────────────────────────
from itgov.observability import configure_observability, shutdown  # noqa: E402

configure_observability(
    service_name="itgov-m365-collector",
    service_version="smoke",
    environment="dev",
    otlp_endpoint=_OTLP_ENDPOINT,
    insecure=True,
)

# ── Bootstrap OTel metrics (optional) ────────────────────────────────────────
if _EMIT_METRICS:
    from itgov.observability import setup_metrics, shutdown_metrics

    setup_metrics(
        service_name="itgov-m365-collector",
        service_version="smoke",
        environment="dev",
        otlp_endpoint=_OTLP_ENDPOINT,
        insecure=True,
        export_interval_ms=5_000,  # flush quickly in smoke context
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

_PAGE2 = [
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
        "signInActivity": None,
        "createdDateTime": "2020-03-01T00:00:00Z",
    },
    {
        "id": "sp-smoke-high-001",
        "displayName": "HighRiskApp-GroupWrite",
        "servicePrincipalType": "Application",
        "appRoles": [{"value": "Group.ReadWrite.All"}],
        "passwordCredentials": [{"endDateTime": "2022-01-01T00:00:00Z", "keyId": "cred-expired-001"}],
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

_ALL_SPS = _PAGE1 + _PAGE2
_NEXT_LINK = "https://graph.microsoft.com/v1.0/servicePrincipals/delta?$skiptoken=smoke-page2"

_mock_get_calls = [
    lambda client, url, token: {"value": _PAGE1, "@odata.nextLink": _NEXT_LINK},
    lambda client, url, token: {"value": _PAGE2, "@odata.deltaLink": _DELTA_LINK},
]
_call_idx = 0


async def _mock_get(client, url, token):  # type: ignore[no-untyped-def]
    global _call_idx
    fn = _mock_get_calls[min(_call_idx, len(_mock_get_calls) - 1)]
    _call_idx += 1
    return fn(client, url, token)


async def main() -> None:
    print(f"\n{'=' * 60}")
    print("  M365 OTel Smoke Runner")
    print(f"  tenant_id    : {_TENANT_ID}")
    print(f"  endpoint     : {_OTLP_ENDPOINT}")
    print(f"  emit_metrics : {_EMIT_METRICS}")
    print(f"  total SPs    : {len(_ALL_SPS)}  (page1={len(_PAGE1)}, page2={len(_PAGE2)})")
    print(f"{'=' * 60}\n")

    mock_token_resp = MagicMock()
    mock_token_resp.status_code = 200
    mock_token_resp.json.return_value = {"access_token": "smoke-token-not-real"}
    mock_store = AsyncMock()
    mock_store.get.return_value = None

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
        t0 = time.monotonic()
        result = await run_collection(_TENANT_ID)
        elapsed = int((time.monotonic() - t0) * 1000)

    print(f"\n{'=' * 60}")
    print("  Collection result:")
    for k, v in result.items():
        print(f"    {k:<22} {v}")
    print(f"  wall_ms  : {elapsed}")
    print(f"{'=' * 60}")

    flush_wait = 8 if _EMIT_METRICS else 3
    print(f"\n  Waiting {flush_wait}s for OTLP export...")
    await asyncio.sleep(flush_wait)

    shutdown()
    if _EMIT_METRICS:
        shutdown_metrics()  # type: ignore[name-defined]

    print("\n  ✅ Signals exported.")
    print("     Traces  → Grafana/Tempo: service.name=itgov-m365-collector")
    if _EMIT_METRICS:
        print("     Metrics → Prometheus :9091")
        print(
            "       curl -s 'http://172.29.2.11:9091/api/v1/query?query=itgov_m365_collections_total' | python3 -m json.tool"
        )


if __name__ == "__main__":
    asyncio.run(main())
