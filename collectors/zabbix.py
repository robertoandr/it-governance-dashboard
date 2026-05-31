"""
Coletor Zabbix via JSON-RPC API.
  - get_host_summary, get_problems (originais)
  - get_active_triggers, get_m365_items, acknowledge_event (Sprint 1)

.. deprecated::
    Este módulo será migrado para ``app.services.zabbix_service`` no Sprint 10F.
    Não adicione novas funcionalidades aqui — use o novo serviço.
    Ver: docs/MIGRATION.md
"""

import logging
import warnings

import requests

import config

# Deprecation: migrar para app.services.zabbix_service no Sprint 10F. Ver docs/MIGRATION.md.
warnings.warn(
    "collectors.zabbix is deprecated. Use app.services.zabbix_service (Sprint 10F). See docs/MIGRATION.md.",
    DeprecationWarning,
    stacklevel=2,
)

log = logging.getLogger(__name__)

SEVERITY_LABELS = {
    "0": "not_classified",
    "1": "information",
    "2": "warning",
    "3": "average",
    "4": "high",
    "5": "disaster",
}


class ZabbixCollector:
    def __init__(self):
        self._token = None

    def _call(self, method, params, use_auth=True):
        body = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
        if use_auth:
            body["auth"] = self._auth()
        r = requests.post(config.ZABBIX_URL, json=body, timeout=15)
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            ed = data["error"].get("data", "").lower()
            if use_auth and ("re-login" in ed or "session" in ed):
                self._token = None
                body["auth"] = self._auth()
                r = requests.post(config.ZABBIX_URL, json=body, timeout=15)
                data = r.json()
            if "error" in data:
                raise RuntimeError(f"Zabbix API error: {data['error']}")
        return data.get("result", {})

    def _auth(self):
        if self._token:
            return self._token
        r = self._call(
            "user.login",
            {"username": config.ZABBIX_USER, "password": config.ZABBIX_PASSWORD},
            use_auth=False,
        )
        self._token = r if isinstance(r, str) else (r.get("token") if isinstance(r, dict) else None)
        if not self._token:
            raise RuntimeError("Falha ao autenticar no Zabbix")
        return self._token

    def get_host_summary(self):
        hosts = self._call(
            "host.get",
            {
                "output": ["host", "status"],
                "selectInterfaces": ["available"],
                "monitored_hosts": True,
                "filter": {"status": 0},
            },
        )
        total = len(hosts)
        up = down = 0
        for h in hosts:
            ifaces = h.get("interfaces", [])
            if any(i.get("available") == "1" for i in ifaces):
                up += 1
            elif any(i.get("available") == "2" for i in ifaces):
                down += 1
        up_pct = round((up / total) * 100, 1) if total else 0
        return {"total": total, "up": up, "down": down, "up_pct": up_pct}

    def get_problems(self, limit=50):
        problems = self._call(
            "problem.get",
            {
                "output": ["eventid", "name", "severity", "clock", "objectid", "acknowledged"],
                "sortfield": "eventid",
                "sortorder": "DESC",
                "limit": limit,
                "recent": True,
            },
        )
        if not problems:
            return {"by_severity": dict.fromkeys(SEVERITY_LABELS.values(), 0), "items": []}
        trigger_ids = list({p["objectid"] for p in problems})
        triggers = self._call(
            "trigger.get",
            {
                "output": ["triggerid"],
                "selectHosts": ["host", "name"],
                "triggerids": trigger_ids,
            },
        )
        t2h = {}
        for t in triggers:
            hosts_list = t.get("hosts", [])
            if hosts_list:
                t2h[t["triggerid"]] = hosts_list[0].get("name") or hosts_list[0].get("host", "?")
        by_sev = dict.fromkeys(SEVERITY_LABELS.values(), 0)
        rows = []
        for p in problems:
            sev = SEVERITY_LABELS.get(str(p.get("severity", "0")), "not_classified")
            by_sev[sev] = by_sev.get(sev, 0) + 1
            rows.append(
                {
                    "eventid": p["eventid"],
                    "host": t2h.get(p["objectid"], "?"),
                    "name": p["name"],
                    "severity": sev,
                    "severity_num": int(p.get("severity", 0)),
                    "clock": int(p.get("clock", 0)),
                    "acknowledged": p.get("acknowledged") == "1",
                }
            )
        return {"by_severity": by_sev, "items": rows}

    def get_active_triggers(self, limit=200):
        triggers = self._call(
            "trigger.get",
            {
                "output": ["triggerid", "description", "priority", "lastchange", "value"],
                "selectHosts": ["host", "name"],
                "selectLastEvent": ["eventid", "acknowledged", "name"],
                "filter": {"value": 1, "status": 0},
                "monitored": True,
                "active": True,
                "only_true": True,
                "sortfield": ["priority", "lastchange"],
                "sortorder": "DESC",
                "limit": limit,
            },
        )
        out = []
        for t in triggers:
            host_obj = (t.get("hosts") or [{}])[0]
            le = t.get("lastEvent") or {}
            out.append(
                {
                    "triggerid": t["triggerid"],
                    "host": host_obj.get("name") or host_obj.get("host", "?"),
                    "description": t.get("description", "?"),
                    "severity": SEVERITY_LABELS.get(str(t.get("priority", "0")), "not_classified"),
                    "severity_num": int(t.get("priority", 0)),
                    "lastchange": int(t.get("lastchange", 0)),
                    "eventid": le.get("eventid"),
                    "acknowledged": le.get("acknowledged") == "1",
                }
            )
        return out

    def get_m365_items(self):
        items = self._call(
            "item.get",
            {
                "output": [
                    "itemid",
                    "key_",
                    "name",
                    "lastvalue",
                    "lastclock",
                    "value_type",
                    "units",
                ],
                "search": {"key_": "m365."},
                "searchByAny": False,
                "monitored": True,
            },
        )
        out = {}
        for it in items:
            key = it.get("key_")
            if not key or not key.startswith("m365."):
                continue
            raw = it.get("lastvalue", "")
            vt = str(it.get("value_type", "4"))
            try:
                if vt == "0":
                    val = float(raw) if raw else 0.0
                elif vt == "3":
                    val = int(raw) if raw else 0
                else:
                    val = raw
            except (ValueError, TypeError):
                val = raw
            out[key] = {
                "name": it.get("name", key),
                "value": val,
                "clock": int(it.get("lastclock", 0)),
                "units": it.get("units", ""),
            }
        return out

    def acknowledge_event(self, eventid, message="Resolvido via dashboard"):
        result = self._call(
            "event.acknowledge",
            {
                "eventids": str(eventid),
                "action": 6,
                "message": message[:2048],
            },
        )
        return {"ok": True, "result": result}

    def unacknowledge_event(self, eventid):
        """Remove ack de um evento. action=16 = unack (bitmask)."""
        result = self._call(
            "event.acknowledge",
            {
                "eventids": str(eventid),
                "action": 16,
                "message": "Reaberto via dashboard",
            },
        )
        return {"ok": True, "result": result}

    def get_cftv_summary(self):
        """Resumo de todos hosts do host group CFTV: por subcategoria + lista de down."""
        groups = self._call(
            "hostgroup.get",
            {
                "output": ["groupid", "name"],
                "search": {"name": "CFTV"},
            },
        )
        gids = [g["groupid"] for g in groups if g["name"].startswith("CFTV")]
        if not gids:
            return {"enabled": False, "total": 0, "by_subcat": {}, "down_list": []}

        hosts = self._call(
            "host.get",
            {
                "output": ["hostid", "host", "name", "maintenance_status"],
                "groupids": gids,
                "selectInterfaces": ["ip"],
                "selectItems": ["key_", "lastvalue", "lastclock"],
                "selectTags": ["tag", "value"],
            },
        )

        by_subcat = {}
        down_list = []
        maint_list = []
        maint_count = 0
        for h in hosts:
            subcat = "outros"
            andar = "?"
            for tg in h.get("tags", []):
                if tg["tag"] == "subcategory":
                    subcat = tg["value"]
                elif tg["tag"] == "andar":
                    andar = tg["value"]
            if subcat not in by_subcat:
                by_subcat[subcat] = {"total": 0, "up": 0, "down": 0, "nodata": 0, "maint": 0}
            by_subcat[subcat]["total"] += 1

            ip = (h.get("interfaces") or [{}])[0].get("ip", "?")

            in_maint = h.get("maintenance_status") == "1"
            if in_maint:
                by_subcat[subcat]["maint"] += 1
                maint_list.append(
                    {
                        "host": h.get("host"),
                        "name": h.get("name"),
                        "ip": ip,
                        "andar": andar,
                        "subcat": subcat,
                        "hostid": h.get("hostid"),
                        "in_maint": True,
                    }
                )
                maint_count += 1

            ping = next((i for i in h.get("items", []) if i["key_"] == "icmpping"), None)
            if not ping or not ping.get("lastclock") or ping["lastclock"] == "0":
                by_subcat[subcat]["nodata"] += 1
            elif ping["lastvalue"] == "1":
                by_subcat[subcat]["up"] += 1
            else:
                by_subcat[subcat]["down"] += 1
                if not in_maint:
                    ip = (h.get("interfaces") or [{}])[0].get("ip", "?")
                    down_list.append(
                        {
                            "host": h["host"],
                            "name": h.get("name", h["host"]),
                            "ip": ip,
                            "subcat": subcat,
                            "andar": andar,
                            "in_maint": in_maint,
                            "hostid": h.get("hostid"),
                        }
                    )

        total = sum(s["total"] for s in by_subcat.values())
        up = sum(s["up"] for s in by_subcat.values())
        down = sum(s["down"] for s in by_subcat.values())
        return {
            "enabled": True,
            "total": total,
            "up": up,
            "down": down,
            "nodata": sum(s["nodata"] for s in by_subcat.values()),
            "maint": maint_count,
            "up_pct": round((up / total) * 100, 1) if total else 0,
            "by_subcat": by_subcat,
            "down_list": sorted(down_list, key=lambda x: (x["subcat"], x["host"])),
            "maint_list": sorted(maint_list, key=lambda x: (x["subcat"], x["host"])),
        }
