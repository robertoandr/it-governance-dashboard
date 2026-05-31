"""
Coletor Grafana - lista dashboards disponíveis e gera URLs de embed (kiosk mode).

Pré-requisito no Grafana (uma vez):
  Em /etc/grafana/grafana.ini ajustar:
    [security]
    allow_embedding = true
    cookie_samesite = none
  Depois: sudo systemctl restart grafana-server
"""

import logging
import warnings

import requests

import config

# Deprecation: migrar para app.services.grafana_service no Sprint 10F. Ver docs/MIGRATION.md.
warnings.warn(
    "collectors.grafana is deprecated. Use app.services.grafana_service (Sprint 10F). See docs/MIGRATION.md.",
    DeprecationWarning,
    stacklevel=2,
)

log = logging.getLogger(__name__)


class GrafanaCollector:
    def __init__(self):
        if not config.GRAFANA_ENABLED:
            self._headers = None
            return
        self._headers = {"Authorization": f"Bearer {config.GRAFANA_TOKEN}"}

    def list_dashboards(self) -> list[dict]:
        """Retorna [{title, uid, url, embed_url}]."""
        if not config.GRAFANA_ENABLED:
            return []
        try:
            r = requests.get(
                f"{config.GRAFANA_URL}/api/search?type=dash-db&limit=50",
                headers=self._headers,
                timeout=10,
            )
            r.raise_for_status()
            out = []
            for d in r.json():
                uid = d.get("uid")
                if not uid:
                    continue
                out.append(
                    {
                        "title": d.get("title", uid),
                        "uid": uid,
                        "url": f"{config.GRAFANA_URL}/d/{uid}?theme=dark",
                        # kiosk=tv esconde o menu lateral e o header — bom pra iframe
                        "embed_url": f"{config.GRAFANA_URL}/d/{uid}?kiosk=tv&theme=dark&refresh=30s",
                    }
                )
            return out
        except Exception as e:
            log.warning("Falha ao listar dashboards Grafana: %s", e)
            return []

    def health(self) -> dict:
        if not config.GRAFANA_ENABLED:
            return {"ok": False, "reason": "Grafana desabilitado (token vazio)"}
        try:
            # /api/health não exige auth
            r = requests.get(f"{config.GRAFANA_URL}/api/health", timeout=5)
            r.raise_for_status()
            return {"ok": True, **r.json()}
        except Exception as e:
            return {"ok": False, "reason": str(e)}
