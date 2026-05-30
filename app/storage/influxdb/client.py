"""Cliente InfluxDB para escrita de governance_metrics.

Escopo restrito: apenas governance_raw. Hourly e monthly são populados
pelas Flux tasks de downsample — o app nunca escreve neles diretamente.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import httpx

from app.storage.influxdb.guards import validate_bucket
from app.storage.influxdb.models import GovernancePoint

log = logging.getLogger(__name__)

_WRITE_BUCKET = "governance_raw"
_WRITE_URL_TMPL = "{base}/api/v2/write?org={org}&bucket={bucket}&precision=ns"


class GovernanceInfluxClient:
    """Cliente de escrita para os buckets de governança.

    Apenas escreve em governance_raw. O acesso a governance_hourly e
    governance_monthly é exclusivo das Flux tasks de downsample.

    Args:
        influx_url: URL base do InfluxDB (ex: http://localhost:8086).
        token: Token de autenticação InfluxDB v2.
        org: Nome da organização.
    """

    def __init__(self, influx_url: str, token: str, org: str) -> None:
        self._base = influx_url.rstrip("/")
        self._org = org
        self._headers = {
            "Authorization": f"Token {token}",
            "Content-Type": "text/plain; charset=utf-8",
        }
        self._write_url = _WRITE_URL_TMPL.format(base=self._base, org=org, bucket=_WRITE_BUCKET)
        validate_bucket(_WRITE_BUCKET)

    async def write_points(self, points: Sequence[GovernancePoint]) -> None:
        """Escreve um lote de pontos em governance_raw.

        Coleta todos os pontos em memória e faz um único POST (batch write).
        Cada fonte deve chamar write_points independentemente para evitar
        perda total em caso de falha parcial.

        Args:
            points: Sequência de GovernancePoint a serem escritos.

        Raises:
            httpx.HTTPStatusError: Se o InfluxDB retornar erro HTTP.
        """
        if not points:
            return

        lines = [p.to_line_protocol() for p in points]
        body = "\n".join(lines)

        log.info("influxdb.write_points bucket=%s points=%d", _WRITE_BUCKET, len(lines))

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                self._write_url,
                headers=self._headers,
                content=body.encode(),
            )

        if resp.status_code == 204:
            log.info("influxdb.write_ok points=%d", len(lines))
            return

        log.error("influxdb.write_error status=%d body=%s", resp.status_code, resp.text[:300])
        resp.raise_for_status()

    async def ping(self) -> bool:
        """Verifica conectividade com o InfluxDB.

        Returns:
            True se o InfluxDB responder com status 200/204.
        """
        async with httpx.AsyncClient(timeout=5.0) as client:
            try:
                resp = await client.get(f"{self._base}/ping")
                return resp.status_code in (200, 204)
            except httpx.HTTPError:
                return False
