from __future__ import annotations

from datetime import UTC, datetime

from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

import config

BUCKET = "m365_security"

# Retention: 90 days (set via UI or terraform — InfluxDB client v2 does not
# expose shard-duration in the Python API; configure bucket on first deploy).
RETENTION_SECONDS = 90 * 24 * 3600


def get_client() -> InfluxDBClient:
    return InfluxDBClient(url=config.INFLUX_URL, token=config.INFLUX_TOKEN, org=config.INFLUX_ORG)


def write_sp_risk(sp: dict, write_api=None) -> None:
    """Write a single SP risk record to `service_principal_risk`."""
    client = None
    if write_api is None:
        client = get_client()
        write_api = client.write_api(write_options=SYNCHRONOUS)

    point = (
        Point("service_principal_risk")
        .tag("risk_level", sp["risk_level"])
        .tag("sp_type", sp.get("sp_type", "Application"))
        .tag("tenant_id", sp["tenant_id"])
        .field("risk_score", int(sp["risk_score"]))
        .field("days_since_sign_in", int(sp["days_since_sign_in"]) if sp.get("days_since_sign_in") is not None else -1)
        .field("permission_count", int(sp.get("permission_count", 0)))
        .field("dangerous_perm_count", int(sp.get("dangerous_perm_count", 0)))
        .field("has_expired_creds", bool(sp.get("has_expired_creds", False)))
        .field("sp_object_id", str(sp["sp_object_id"]))
        .time(datetime.now(UTC), WritePrecision.SECONDS)
    )
    write_api.write(bucket=BUCKET, record=point)

    if client:
        client.close()


def write_summary_snapshot(tenant_id: str, stats: dict, write_api=None) -> None:
    """Write hourly aggregation snapshot to `sp_summary_snapshot`."""
    client = None
    if write_api is None:
        client = get_client()
        write_api = client.write_api(write_options=SYNCHRONOUS)

    point = (
        Point("sp_summary_snapshot")
        .tag("tenant_id", tenant_id)
        .field("total_sps", int(stats["total_sps"]))
        .field("critical_count", int(stats.get("critical_count", 0)))
        .field("high_count", int(stats.get("high_count", 0)))
        .field("medium_count", int(stats.get("medium_count", 0)))
        .field("ok_count", int(stats.get("ok_count", 0)))
        .field("avg_risk_score", float(stats.get("avg_risk_score", 0.0)))
        .time(datetime.now(UTC), WritePrecision.SECONDS)
    )
    write_api.write(bucket=BUCKET, record=point)

    if client:
        client.close()


# ---------------------------------------------------------------------------
# Flux queries
# ---------------------------------------------------------------------------

QUERY_TOP20_CRITICAL = f"""
from(bucket: "{BUCKET}")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "service_principal_risk")
  |> filter(fn: (r) => r.risk_level == "CRITICAL")
  |> filter(fn: (r) => r._field == "risk_score")
  |> top(n: 20, columns: ["_value"])
"""

QUERY_AVG_RISK_7D = f"""
from(bucket: "{BUCKET}")
  |> range(start: -7d)
  |> filter(fn: (r) => r._measurement == "service_principal_risk")
  |> filter(fn: (r) => r._field == "risk_score")
  |> aggregateWindow(every: 1d, fn: mean, createEmpty: false)
  |> yield(name: "avg_risk_7d")
"""

QUERY_RISK_DISTRIBUTION = f"""
from(bucket: "{BUCKET}")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "service_principal_risk")
  |> filter(fn: (r) => r._field == "risk_score")
  |> group(columns: ["risk_level"])
  |> count()
  |> yield(name: "risk_distribution")
"""


def run_query(flux: str):
    with get_client() as client:
        return client.query_api().query(flux)
