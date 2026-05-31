"""
Coletor InfluxDB - usa HTTP direto pra contornar bug da lib influxdb-client
que não distingue entre org name (org=) e org ID (orgID=) na query.

.. deprecated::
    Este módulo será migrado para ``app.services.influx_service`` no Sprint 10F.
    Não adicione novas funcionalidades aqui — use o novo serviço.
    Ver: docs/MIGRATION.md
"""

import csv
import io
import logging
import warnings

import requests

import config

# Deprecation: migrar para app.services.influx_service no Sprint 10F. Ver docs/MIGRATION.md.
warnings.warn(
    "collectors.influx is deprecated. Use app.services.influx_service (Sprint 10F). See docs/MIGRATION.md.",
    DeprecationWarning,
    stacklevel=2,
)

log = logging.getLogger(__name__)


class InfluxCollector:
    def __init__(self):
        self._url = config.INFLUX_URL.rstrip("/")
        self._headers = {
            "Authorization": f"Token {config.INFLUX_TOKEN}",
            "Accept": "application/csv",
            "Content-Type": "application/vnd.flux",
        }
        org = config.INFLUX_ORG
        if len(org) == 16 and all(c in "0123456789abcdef" for c in org.lower()):
            self._org_param = f"orgID={org}"
        else:
            self._org_param = f"org={org}"
        self._query_url = f"{self._url}/api/v2/query?{self._org_param}"

    def _run_flux(self, flux: str) -> list[dict]:
        try:
            r = requests.post(self._query_url, headers=self._headers, data=flux, timeout=15)
            log.debug("flux url=%s len_resp=%d sample=%s", self._query_url, len(r.text), r.text[:300])
            if r.status_code != 200:
                log.warning("Influx HTTP %s: %s", r.status_code, r.text[:200])
                return []
            records = []
            reader = csv.DictReader(io.StringIO(r.text))
            for row in reader:
                if row.get("_value") in (None, "") and row.get("_field") in (None, ""):
                    continue
                records.append(row)
            return records
        except Exception as e:
            log.warning("Influx query exception: %s", e)
            return []

    def _last_values(self, measurement, window="-6h"):
        flux = f'from(bucket: "{config.INFLUX_BUCKET}") |> range(start: {window}) |> filter(fn: (r) => r._measurement == "{measurement}") |> last()'
        out = {}
        for rec in self._run_flux(flux):
            field = rec.get("_field")
            val = rec.get("_value")
            if not field or val in (None, ""):
                continue
            try:
                out[field] = float(val) if "." in str(val) else int(val)
            except (ValueError, TypeError):
                out[field] = val
        return out

    def _last_values_by_tag(self, measurement, tag, window="-6h"):
        flux = f'from(bucket: "{config.INFLUX_BUCKET}") |> range(start: {window}) |> filter(fn: (r) => r._measurement == "{measurement}") |> last()'
        items = {}
        for rec in self._run_flux(flux):
            tag_value = rec.get(tag, "?")
            field = rec.get("_field")
            val = rec.get("_value")
            if not field or val in (None, ""):
                continue
            if tag_value not in items:
                items[tag_value] = {tag: tag_value}
            try:
                items[tag_value][field] = float(val) if "." in str(val) else int(val)
            except (ValueError, TypeError):
                items[tag_value][field] = val
        return list(items.values())

    def get_mfa(self):
        v = self._last_values("m365_mfa")
        hab = float(v.get("habilitados") or 0)
        enf = float(v.get("enforced") or 0)
        des = float(v.get("desabilitados") or 0)
        total = hab + enf + des
        pct = round(((hab + enf) / total) * 100, 1) if total else 0
        return {
            "habilitados": int(hab),
            "enforced": int(enf),
            "desabilitados": int(des),
            "total": int(total),
            "pct": pct,
        }

    def get_service_health(self):
        rows = self._last_values_by_tag("m365_service_status", "service")
        return sorted(rows, key=lambda r: -int(r.get("status_code") or 0))

    def get_incidents(self):
        v = self._last_values("m365_incidents")
        return {"total_ativos": int(v.get("total_ativos") or 0)}

    def get_users(self):
        v = self._last_values("m365_users")
        ativos = int(v.get("ativos") or 0)
        inativos = int(v.get("inativos") or 0)
        total = int(v.get("total") or (ativos + inativos))
        pct_ativos = round((ativos / total) * 100, 1) if total else 0
        return {"ativos": ativos, "inativos": inativos, "total": total, "pct_ativos": pct_ativos}

    def close(self):
        pass
