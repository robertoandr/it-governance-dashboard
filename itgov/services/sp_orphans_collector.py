from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog
from opentelemetry.trace import StatusCode

from itgov.observability import get_meter, get_tracer
from itgov.services.delta_token_store import DeltaTokenStore
from itgov.services.graph_client import GraphClient
from itgov.services.influx_schema import write_sp_risk, write_summary_snapshot
from itgov.utils.risk_scoring import calculate_risk_score

log = structlog.get_logger(__name__)

_RESOURCE = "servicePrincipals"
_METER_NAME = "itgov.m365"


def _collections_counter():
    return get_meter(_METER_NAME).create_counter(
        "m365_collections_total",
        unit="1",
        description="Total SP collection cycles by tenant and status.",
    )


def _collection_duration_histogram():
    return get_meter(_METER_NAME).create_histogram(
        "m365_collection_duration_seconds",
        unit="s",
        description="SP collection cycle duration by tenant.",
    )


def _risk_count_gauge():
    return get_meter(_METER_NAME).create_gauge(
        "m365_sps_risk_count",
        unit="{SP}",  # avoid Prometheus _ratio suffix (unit "1" triggers it)
        description="Current SP count by tenant and risk_level.",
    )


def _orphans_actionable_gauge():
    return get_meter(_METER_NAME).create_gauge(
        "m365_sps_orphans_actionable",
        unit="{SP}",  # avoid Prometheus _ratio suffix
        description="Current count of critical+high risk SPs (orphans requiring action) by tenant.",
    )


DANGEROUS_PERMISSIONS = {
    "Application.ReadWrite.All",
    "Directory.ReadWrite.All",
    "RoleManagement.ReadWrite.Directory",
    "AppRoleAssignment.ReadWrite.All",
    "Group.ReadWrite.All",
    "User.ReadWrite.All",
    "Mail.ReadWrite",
    "Files.ReadWrite.All",
    "Sites.FullControl.All",
    "Exchange.ManageAsApp",
}


@dataclass
class CollectionResult:
    mode: str  # "full" or "delta"
    count: int
    tenant_id: str
    critical: int = 0
    high: int = 0
    medium: int = 0
    ok: int = 0
    avg_risk_score: float = 0.0


def _days_since(dt_str: str | None) -> int | None:
    if not dt_str:
        return None
    try:
        dt = datetime.fromisoformat(dt_str.rstrip("Z")).replace(tzinfo=UTC)
        return (datetime.now(UTC) - dt).days
    except ValueError:
        return None


def _has_expired_creds(sp: dict) -> bool:
    now = datetime.now(UTC)
    for cred in sp.get("passwordCredentials", []) + sp.get("keyCredentials", []):
        end_str = cred.get("endDateTime")
        if not end_str:
            continue
        try:
            end = datetime.fromisoformat(end_str.rstrip("Z")).replace(tzinfo=UTC)
            if end < now:
                return True
        except ValueError:
            continue
    return False


def _extract_permissions(sp: dict) -> list[str]:
    return [role.get("value", "") for role in sp.get("appRoles", []) if role.get("value")]


def _build_sp_record(sp: dict, tenant_id: str) -> dict:
    permissions = _extract_permissions(sp)
    dangerous_count = sum(1 for p in permissions if p in DANGEROUS_PERMISSIONS)
    sp_age_days = _days_since(sp.get("createdDateTime")) or 0
    days_since_sign_in = _days_since((sp.get("signInActivity") or {}).get("lastSignInDateTime"))
    has_expired = _has_expired_creds(sp)

    risk_score, risk_level = calculate_risk_score(
        {
            "sp_age_days": sp_age_days,
            "days_since_sign_in": days_since_sign_in,
            "permissions": permissions,
            "permission_count": len(permissions),
            "has_expired_creds": has_expired,
        }
    )

    return {
        "sp_object_id": sp["id"],
        "tenant_id": tenant_id,
        "sp_type": sp.get("servicePrincipalType", "Application"),
        "risk_score": risk_score,
        "risk_level": risk_level,
        "days_since_sign_in": days_since_sign_in if days_since_sign_in is not None else -1,
        "permission_count": len(permissions),
        "dangerous_perm_count": dangerous_count,
        "has_expired_creds": has_expired,
    }


class SPOrphansCollector:
    """Collects M365 Service Principal risk data using Graph API delta queries."""

    def __init__(
        self,
        graph: GraphClient | None = None,
        store: DeltaTokenStore | None = None,
    ) -> None:
        self._graph = graph or GraphClient()
        self._store = store or DeltaTokenStore()

    async def collect(self, tenant_id: str) -> CollectionResult:
        """Run one collection cycle (full or incremental).

        Args:
            tenant_id: Microsoft tenant ID to collect for.

        Returns:
            CollectionResult with run statistics.
        """
        previous_delta = await self._store.get(_RESOURCE, tenant_id)
        is_first_run = previous_delta is None
        mode = "full" if is_first_run else "delta"

        log.info("collection.start", tenant_id=tenant_id, mode=mode)

        tracer = get_tracer("itgov.m365")
        t0 = time.monotonic()
        collection_status = "error"

        with tracer.start_as_current_span("m365.sp_orphans.collect") as span:
            span.set_attribute("m365.tenant_id", tenant_id)
            span.set_attribute("m365.delta.token_used", not is_first_run)

            counters: dict[str, int] = {
                "total": 0,
                "critical": 0,
                "high": 0,
                "medium": 0,
                "ok": 0,
            }
            score_sum = 0

            try:
                async for sp in self._graph.get_service_principals_delta(tenant_id, previous_delta):
                    record = _build_sp_record(sp, tenant_id)
                    log.debug(
                        "sp.processed",
                        sp_object_id=record["sp_object_id"],
                        risk_level=record["risk_level"],
                        risk_score=record["risk_score"],
                    )
                    write_sp_risk(record)
                    counters["total"] += 1
                    score_sum += record["risk_score"]
                    counters[record["risk_level"].lower()] += 1
                # Mark success inside try so finally sees correct status
                collection_status = "success"

            except Exception as exc:
                span.record_exception(exc)
                span.set_status(StatusCode.ERROR, str(exc))
                # Preserve previous delta on failure — next run retries from same point
                log.exception("collection.failed", tenant_id=tenant_id, mode=mode)
                raise
            finally:
                # Emit duration + status in all paths (success AND error)
                duration = time.monotonic() - t0
                _collection_duration_histogram().record(duration, {"tenant": tenant_id})
                _collections_counter().add(1, {"tenant": tenant_id, "status": collection_status})

            # Persist new delta only after successful full iteration
            if self._graph.last_delta_link:
                await self._store.set(_RESOURCE, tenant_id, self._graph.last_delta_link)

            avg_score = score_sum / counters["total"] if counters["total"] else 0.0
            write_summary_snapshot(
                tenant_id,
                {
                    "total_sps": counters["total"],
                    "critical_count": counters["critical"],
                    "high_count": counters["high"],
                    "medium_count": counters["medium"],
                    "ok_count": counters["ok"],
                    "avg_risk_score": avg_score,
                },
            )

            duration_ms = int((time.monotonic() - t0) * 1000)

            # Gauges: current risk distribution snapshot
            for level, key in [
                ("critical", "critical"),
                ("high", "high"),
                ("medium", "medium"),
                ("low", "ok"),
            ]:
                _risk_count_gauge().set(counters[key], {"tenant": tenant_id, "risk_level": level})
            _orphans_actionable_gauge().set(counters["critical"] + counters["high"], {"tenant": tenant_id})

            span.set_attribute("m365.sps.total_scanned", counters["total"])
            span.set_attribute("m365.sps.risk_critical", counters["critical"])
            span.set_attribute("m365.sps.risk_high", counters["high"])
            span.set_attribute("m365.sps.risk_medium", counters["medium"])
            span.set_attribute("m365.sps.risk_low", counters["ok"])
            span.set_attribute("m365.sps.orphans_actionable", counters["critical"] + counters["high"])
            span.set_attribute("m365.duration_ms", duration_ms)

            result = CollectionResult(
                mode=mode,
                count=counters["total"],
                tenant_id=tenant_id,
                critical=counters["critical"],
                high=counters["high"],
                medium=counters["medium"],
                ok=counters["ok"],
                avg_risk_score=avg_score,
            )
            log.info(
                "collection.done",
                tenant_id=tenant_id,
                mode=mode,
                sps_processed=result.count,
                critical=result.critical,
                high=result.high,
            )
            return result


# Module-level convenience used by legacy entrypoint
async def run_collection(tenant_id: str) -> dict:
    tracer = get_tracer("itgov.m365")
    with tracer.start_as_current_span("m365.run_collection") as span:
        span.set_attribute("m365.tenant_id", tenant_id)
        span.set_attribute("m365.collector", "sp_orphans")
        result = await SPOrphansCollector().collect(tenant_id)
        return {
            "total_sps": result.count,
            "critical_count": result.critical,
            "high_count": result.high,
            "medium_count": result.medium,
            "ok_count": result.ok,
            "avg_risk_score": result.avg_risk_score,
        }
