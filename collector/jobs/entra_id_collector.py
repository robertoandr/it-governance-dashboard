"""Entra ID (Azure AD) collector — writes gov_entra_summary to InfluxDB.

Collects: total_users, guest_users, mfa_enabled_pct, stale_accounts_90d,
privileged_roles_count, ca_policies_count.

Token: client_credentials flow via MSAL (BaseOAuthCollector).
Schedule: every 6 hours via APScheduler.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import structlog
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

sys.path.insert(0, str(Path(__file__).parent.parent))

from base_oauth_collector import BaseOAuthCollector

from config import settings

log = structlog.get_logger("entra_id_collector")

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_SCOPES = ["https://graph.microsoft.com/.default"]

# Stale threshold: users with no sign-in for 90+ days
_STALE_DAYS = 90


class EntraIdCollector(BaseOAuthCollector):
    """Collects identity metrics from Microsoft Graph and writes to InfluxDB."""

    def __init__(self) -> None:
        if not all([settings.AZURE_TENANT_ID, settings.AZURE_CLIENT_ID, settings.AZURE_CLIENT_SECRET]):
            raise RuntimeError("AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET must be set")
        super().__init__(
            tenant_id=settings.AZURE_TENANT_ID,
            client_id=settings.AZURE_CLIENT_ID,
            client_secret=settings.AZURE_CLIENT_SECRET,
            scopes=_SCOPES,
        )

    # ── Graph API helpers ────────────────────────────────────────────────────

    def _count_users(self) -> tuple[int, int]:
        """Return (total_users, guest_users)."""
        all_users = list(self._paginate(f"{_GRAPH_BASE}/users", params={"$select": "userType", "$top": "999"}))
        total = len(all_users)
        guests = sum(1 for u in all_users if u.get("userType") == "Guest")
        return total, guests

    def _mfa_enabled_pct(self, total_users: int) -> float:
        """Return percentage of users with at least one MFA method registered."""
        if total_users == 0:
            return 0.0
        # credentialUserRegistrationDetails requires Reports.Read.All
        try:
            items = list(
                self._paginate(
                    f"{_GRAPH_BASE}/reports/credentialUserRegistrationDetails",
                    params={"$select": "isMfaRegistered", "$top": "999"},
                )
            )
            mfa_count = sum(1 for i in items if i.get("isMfaRegistered"))
            return round(mfa_count / total_users * 100, 2) if total_users else 0.0
        except Exception as exc:
            log.warning("mfa_count_failed", error=str(exc))
            return 0.0

    def _stale_accounts(self) -> int:
        """Count accounts with no interactive sign-in in the past 90 days."""
        cutoff = (datetime.now(UTC) - timedelta(days=_STALE_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            items = list(
                self._paginate(
                    f"{_GRAPH_BASE}/users",
                    params={
                        "$select": "signInActivity",
                        "$filter": f"signInActivity/lastSignInDateTime le {cutoff}",
                        "$top": "999",
                    },
                )
            )
            return len(items)
        except Exception as exc:
            log.warning("stale_accounts_failed", error=str(exc))
            return 0

    def _privileged_roles_count(self) -> int:
        """Count assignments to privileged directory roles."""
        try:
            data = self._get(f"{_GRAPH_BASE}/directoryRoles", params={"$select": "id,displayName"})
            privileged_keywords = {"Global Administrator", "Privileged Role Administrator", "Security Administrator"}
            count = 0
            for role in data.get("value", []):
                if role.get("displayName") in privileged_keywords:
                    members = list(
                        self._paginate(
                            f"{_GRAPH_BASE}/directoryRoles/{role['id']}/members",
                            params={"$select": "id"},
                        )
                    )
                    count += len(members)
            return count
        except Exception as exc:
            log.warning("privileged_roles_failed", error=str(exc))
            return 0

    def _ca_policies_count(self) -> int:
        """Count enabled Conditional Access policies."""
        try:
            data = self._get(
                f"{_GRAPH_BASE}/identity/conditionalAccess/policies",
                params={"$filter": "state eq 'enabled'", "$select": "id"},
            )
            return len(data.get("value", []))
        except Exception as exc:
            log.warning("ca_policies_failed", error=str(exc))
            return 0

    # ── Main collection cycle ────────────────────────────────────────────────

    def collect(self) -> None:
        """Collect all Entra ID metrics and write to InfluxDB."""
        log.info("entra_collection_started")
        collected_at = datetime.now(UTC)

        try:
            total_users, guest_users = self._count_users()
            mfa_pct = self._mfa_enabled_pct(total_users)
            stale = self._stale_accounts()
            priv_roles = self._privileged_roles_count()
            ca_count = self._ca_policies_count()
        except Exception as exc:
            log.error("entra_collection_failed", error=str(exc))
            raise

        log.info(
            "entra_metrics_collected",
            total_users=total_users,
            guest_users=guest_users,
            mfa_enabled_pct=mfa_pct,
            stale_accounts_90d=stale,
            privileged_roles_count=priv_roles,
            ca_policies_count=ca_count,
        )

        point = (
            Point("gov_entra_summary")
            .field("total_users", total_users)
            .field("guest_users", guest_users)
            .field("mfa_enabled_pct", mfa_pct)
            .field("stale_accounts_90d", stale)
            .field("privileged_roles_count", priv_roles)
            .field("ca_policies_count", ca_count)
            .time(collected_at, WritePrecision.SECONDS)
        )

        with InfluxDBClient(
            url=settings.INFLUX_URL,
            token=settings.INFLUX_TOKEN,
            org=settings.INFLUX_ORG,
        ) as client:
            client.write_api(write_options=SYNCHRONOUS).write(
                bucket=settings.INFLUX_BUCKET_RAW,
                record=point,
            )

        log.info("entra_metrics_written", measurement="gov_entra_summary")


def run() -> None:
    """Entry point for APScheduler."""
    try:
        EntraIdCollector().collect()
    except Exception as exc:
        log.error("entra_job_failed", error=str(exc))
