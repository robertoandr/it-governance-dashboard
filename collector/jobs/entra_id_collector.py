"""Entra ID (Azure AD) collector — writes gov_entra_summary to InfluxDB.

Collects: total_users, guest_users, mfa_enabled_pct, sspr_registered_pct,
stale_accounts_90d, privileged_roles_count, ca_policies_count (+ breakdown),
admin_total, admin_sem_mfa.

Token: client_credentials flow via MSAL (BaseOAuthCollector).
Schedule: every 6 hours via APScheduler.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import requests
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

    def _mfa_enabled_pct(self, total_users: int) -> tuple[float, float]:
        """Return (mfa_pct, sspr_pct) — % users with MFA and SSPR registered."""
        if total_users == 0:
            return 0.0, 0.0
        # userRegistrationDetails requires UserAuthenticationMethod.Read.All
        try:
            items = list(
                self._paginate(
                    f"{_GRAPH_BASE}/reports/authenticationMethods/userRegistrationDetails",
                    params={"$select": "isMfaRegistered,isSsprRegistered", "$top": "999"},
                )
            )
            mfa_count = sum(1 for i in items if i.get("isMfaRegistered"))
            sspr_count = sum(1 for i in items if i.get("isSsprRegistered"))
            mfa_pct = round(mfa_count / total_users * 100, 2) if total_users else 0.0
            sspr_pct = round(sspr_count / total_users * 100, 2) if total_users else 0.0
            return mfa_pct, sspr_pct
        except Exception as exc:
            log.warning("mfa_count_failed", error=str(exc))
            return 0.0, 0.0

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

    def _ca_policies_breakdown(self) -> dict[str, Any]:
        """Return breakdown of Conditional Access policies by state.

        Returns dict with keys total/enabled/report_only/disabled (int, or
        None if the read failed) plus `error` (str | None). Failures must
        surface as None, not 0 — a fleet with 0 real CA policies and a fleet
        where the Graph call was denied are different situations, and
        collapsing both to 0 makes the dashboard silently report a fake
        "0 policies configured" KPI instead of "couldn't check".
        """
        try:
            items = list(
                self._paginate(
                    f"{_GRAPH_BASE}/identity/conditionalAccess/policies",
                    params={"$select": "id,state"},
                )
            )
            enabled = sum(1 for p in items if p.get("state") == "enabled")
            report_only = sum(1 for p in items if p.get("state") == "enabledForReportingButNotEnforced")
            disabled = sum(1 for p in items if p.get("state") == "disabled")
            return {
                "total": len(items),
                "enabled": enabled,
                "report_only": report_only,
                "disabled": disabled,
                "error": None,
            }
        except requests.exceptions.HTTPError as exc:
            http_status = exc.response.status_code if exc.response is not None else None
            error_code = None
            graph_message = None
            if exc.response is not None:
                try:
                    payload = exc.response.json().get("error", {})
                    error_code = payload.get("code")
                    graph_message = payload.get("message")
                except ValueError:
                    pass
            log.warning(
                "ca_policies_collection_failed",
                http_status=http_status,
                error_code=error_code,
                graph_message=graph_message,
            )
            error_label = f"HTTP {http_status} {error_code or ''}".strip()
            return {"total": None, "enabled": None, "report_only": None, "disabled": None, "error": error_label}
        except Exception as exc:
            log.warning("ca_policies_collection_failed", error_code="unexpected", graph_message=str(exc))
            return {
                "total": None,
                "enabled": None,
                "report_only": None,
                "disabled": None,
                "error": "unexpected_error",
            }

    def _admin_mfa_status(self) -> tuple[int, int]:
        """Return (admin_total, admin_sem_mfa) across privileged directory roles.

        Requires RoleManagement.Read.Directory and UserAuthenticationMethod.Read.All.
        Returns (0, 0) gracefully if permission is missing.
        """
        privileged_roles = {
            "Global Administrator",
            "Privileged Role Administrator",
            "Security Administrator",
            "Exchange Administrator",
            "SharePoint Administrator",
            "Compliance Administrator",
        }
        try:
            roles_data = self._get(
                f"{_GRAPH_BASE}/directoryRoles",
                params={"$select": "id,displayName"},
            )
            roles = [r for r in roles_data.get("value", []) if r.get("displayName") in privileged_roles]

            admin_ids: set[str] = set()
            for role in roles:
                members = list(
                    self._paginate(
                        f"{_GRAPH_BASE}/directoryRoles/{role['id']}/members",
                        params={"$select": "id,userPrincipalName"},
                    )
                )
                for m in members:
                    uid = m.get("id", "")
                    if uid:
                        admin_ids.add(uid)

            if not admin_ids:
                return 0, 0

            # Fetch MFA registration for all users (with pagination)
            reg_items = list(
                self._paginate(
                    f"{_GRAPH_BASE}/reports/authenticationMethods/userRegistrationDetails",
                    params={"$select": "id,userPrincipalName,isMfaRegistered", "$top": "999"},
                )
            )
            reg_by_id = {r.get("id", ""): r for r in reg_items}

            admin_total = len(admin_ids)
            admin_sem_mfa = sum(1 for uid in admin_ids if not reg_by_id.get(uid, {}).get("isMfaRegistered", False))
            return admin_total, admin_sem_mfa
        except Exception as exc:
            log.warning("admin_mfa_status_failed", error=str(exc))
            return 0, 0

    def _collect_licenses(self, write_api: Any, bucket: str, collected_at: datetime) -> None:
        """Collect M365 subscribed SKUs and write to m365_licenses measurement."""
        try:
            data = self._get(
                f"{_GRAPH_BASE}/subscribedSkus",
                params={"$select": "skuId,skuPartNumber,consumedUnits,prepaidUnits"},
            )
            skus = data.get("value", [])
            points = []
            for sku in skus:
                consumed = int(sku.get("consumedUnits", 0))
                prepaid = sku.get("prepaidUnits") or {}
                total = int(prepaid.get("enabled", 0))
                warning_units = int(prepaid.get("warning", 0))
                available = max(0, total - consumed)
                sku_id = sku.get("skuId", "")
                sku_name = sku.get("skuPartNumber", "")
                if not sku_id or total == 0:
                    continue
                points.append(
                    Point("m365_licenses")
                    .tag("sku_id", sku_id)
                    .tag("sku_name", sku_name)
                    .field("consumed", consumed)
                    .field("total", total)
                    .field("available", available)
                    .field("warning_units", warning_units)
                    .time(collected_at, WritePrecision.S)
                )
            if points:
                write_api.write(bucket=bucket, record=points)
                log.info("m365_licenses_written", count=len(points))
        except Exception as exc:
            log.warning("licenses_collect_failed", error=str(exc))

    def _collect_soft_deleted_mailboxes(self, write_api: Any, bucket: str, collected_at: datetime) -> None:
        """Query soft-deleted mailboxes via /directory/deletedItems and write to gov_exchange_mailbox.

        Alerta COBIT BAI09: caixas com deletedDateTime > 7 dias bloqueiam restore/criação
        de usuários com endereço conflitante.
        """
        alert_threshold = timedelta(days=7)
        try:
            deleted_users = list(
                self._paginate(
                    f"{_GRAPH_BASE}/directory/deletedItems/microsoft.graph.user",
                    params={
                        "$select": "id,mail,deletedDateTime,userPrincipalName",
                        "$top": "999",
                    },
                )
            )
        except Exception as exc:
            log.warning("soft_deleted_mailboxes_failed", error=str(exc))
            return

        now = datetime.now(UTC)
        # Considera apenas entradas com endereço de e-mail (= têm caixa Exchange)
        mailboxes = [u for u in deleted_users if u.get("mail")]
        total = len(mailboxes)

        pending_cleanup = 0
        oldest_days = 0
        for user in mailboxes:
            deleted_str = user.get("deletedDateTime", "")
            if not deleted_str:
                continue
            try:
                deleted_at = datetime.fromisoformat(deleted_str.replace("Z", "+00:00"))
                age_days = (now - deleted_at).days
                oldest_days = max(oldest_days, age_days)
                if age_days > alert_threshold.days:
                    pending_cleanup += 1
            except ValueError:
                pass

        point = (
            Point("gov_exchange_mailbox")
            .field("total_soft_deleted", total)
            .field("pending_cleanup", pending_cleanup)
            .field("oldest_days", oldest_days)
            .time(collected_at, WritePrecision.S)
        )
        write_api.write(bucket=bucket, record=point)
        log.info(
            "exchange_mailbox_written",
            total=total,
            pending_cleanup=pending_cleanup,
            oldest_days=oldest_days,
        )

    # ── Main collection cycle ────────────────────────────────────────────────

    def collect(self) -> None:
        """Collect all Entra ID metrics and write to InfluxDB."""
        log.info("entra_collection_started")
        collected_at = datetime.now(UTC)

        try:
            total_users, guest_users = self._count_users()
            mfa_pct, sspr_pct = self._mfa_enabled_pct(total_users)
            stale = self._stale_accounts()
            priv_roles = self._privileged_roles_count()
            ca_breakdown = self._ca_policies_breakdown()
            admin_total, admin_sem_mfa = self._admin_mfa_status()
        except Exception as exc:
            log.error("entra_collection_failed", error=str(exc))
            raise

        ca_enabled = ca_breakdown["enabled"]
        ca_ok = ca_breakdown["error"] is None

        log.info(
            "entra_metrics_collected",
            total_users=total_users,
            guest_users=guest_users,
            mfa_enabled_pct=mfa_pct,
            sspr_registered_pct=sspr_pct,
            stale_accounts_90d=stale,
            privileged_roles_count=priv_roles,
            ca_policies_count=ca_enabled,
            ca_total=ca_breakdown["total"],
            ca_report_only=ca_breakdown["report_only"],
            ca_disabled=ca_breakdown["disabled"],
            ca_collection_error=ca_breakdown["error"],
            admin_total=admin_total,
            admin_sem_mfa=admin_sem_mfa,
        )

        point = (
            Point("gov_entra_summary")
            .field("total_users", total_users)
            .field("guest_users", guest_users)
            .field("mfa_enabled_pct", mfa_pct)
            .field("sspr_registered_pct", sspr_pct)
            .field("stale_accounts_90d", stale)
            .field("privileged_roles_count", priv_roles)
            .field("admin_total", admin_total)
            .field("admin_sem_mfa", admin_sem_mfa)
            .time(collected_at, WritePrecision.S)
        )
        # CA fields are only written on success — on failure we skip them
        # entirely (no 0-writing) so the read side can tell "0 real policies"
        # apart from "collection failed" instead of silently showing zero.
        if ca_ok:
            point = (
                point.field("ca_policies_count", ca_enabled)
                .field("ca_total", ca_breakdown["total"])
                .field("ca_enabled", ca_enabled)
                .field("ca_report_only", ca_breakdown["report_only"])
                .field("ca_disabled", ca_breakdown["disabled"])
            )
        else:
            point = point.field("ca_collection_error", ca_breakdown["error"])

        with InfluxDBClient(
            url=settings.INFLUX_URL,
            token=settings.INFLUX_TOKEN,
            org=settings.INFLUX_ORG,
        ) as client:
            api = client.write_api(write_options=SYNCHRONOUS)
            api.write(bucket=settings.INFLUX_BUCKET_RAW, record=point)
            self._collect_licenses(api, settings.INFLUX_BUCKET_RAW, collected_at)
            self._collect_soft_deleted_mailboxes(api, settings.INFLUX_BUCKET_RAW, collected_at)

        log.info("entra_metrics_written", measurement="gov_entra_summary")


def run() -> None:
    """Entry point for APScheduler."""
    try:
        EntraIdCollector().collect()
    except Exception as exc:
        log.error("entra_job_failed", error=str(exc))
