"""Thread de polling para o módulo de alertas CFTV.

Executa em background, consultando o Zabbix a cada POLL_INTERVAL segundos
e acionando o AlertEngine para processar o ciclo de alertas.

Uso típico (em app.py, após criar a instância Flask):

    poller = AlertPoller.from_config()
    poller.start()
    # Para encerrar graciosamente (ex: SIGTERM):
    poller.stop()
"""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager

import structlog

import config
from itgov.clients.zabbix_asset_client import ZabbixAssetClient
from itgov.db.session import get_session
from itgov.services.alert_engine import AlertEngine
from itgov.services.teams_notifier import TeamsNotifier
from itgov.services.zabbix_hierarchy import HierarchyCache, ZabbixHierarchyClient

log = structlog.get_logger(__name__)


def _session_factory():
    """Context manager que fornece uma Session SQLAlchemy e faz rollback em erro."""

    @contextmanager
    def _inner():
        session = get_session()
        try:
            yield session
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return _inner()


class AlertPoller:
    """Thread de fundo que executa o ciclo de alertas periodicamente.

    Args:
        engine: Instância do AlertEngine.
        icmp_client: ZabbixAssetClient para buscar status icmpping.
        interval: Intervalo entre polls em segundos (padrão: 60).
    """

    def __init__(
        self,
        engine: AlertEngine,
        icmp_client: ZabbixAssetClient,
        interval: int = 60,
    ) -> None:
        self._engine = engine
        self._icmp_client = icmp_client
        self._interval = interval
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._loop,
            name="alert-poller",
            daemon=True,
        )

    @classmethod
    def from_config(cls) -> AlertPoller:
        """Cria AlertPoller a partir das variáveis de ambiente / config.

        Vars obrigatórias: ZABBIX_URL, ZABBIX_USER ou ZABBIX_TOKEN
        Vars opcionais: WEBHOOK_URL, POLL_INTERVAL, FLAP_THRESHOLD
        """
        zabbix_url: str = config.ZABBIX_URL
        token: str | None = getattr(config, "ZABBIX_TOKEN", None) or os.environ.get("ZABBIX_TOKEN") or None
        user: str = getattr(config, "ZABBIX_USER", "")
        password: str = getattr(config, "ZABBIX_PASSWORD", "")
        webhook_url: str = getattr(config, "WEBHOOK_URL", "") or os.environ.get("WEBHOOK_URL", "")
        interval: int = getattr(config, "POLL_INTERVAL", 60)
        flap_threshold: int = getattr(config, "FLAP_THRESHOLD", 2)

        hierarchy_client = ZabbixHierarchyClient(
            url=zabbix_url,
            token=token,
            user=user,
            password=password,
        )
        icmp_client = ZabbixAssetClient(
            url=zabbix_url,
            token=token,
            user=user,
            password=password,
        )
        cache = HierarchyCache(hierarchy_client)
        notifier = TeamsNotifier(webhook_url) if webhook_url else None
        if not webhook_url:
            log.warning("teams_webhook_nao_configurado", var="WEBHOOK_URL")

        engine = AlertEngine(
            hierarchy_cache=cache,
            session_factory=_session_factory,
            notifier=notifier,
            flap_threshold=flap_threshold,
        )
        return cls(engine=engine, icmp_client=icmp_client, interval=interval)

    def start(self) -> None:
        """Inicia a thread de polling em background."""
        log.info("alert_poller_iniciando", interval=self._interval)
        self._thread.start()

    def stop(self) -> None:
        """Sinaliza parada e aguarda a thread encerrar (timeout 5s)."""
        log.info("alert_poller_parando")
        self._stop_event.set()
        self._thread.join(timeout=5)

    def _loop(self) -> None:
        log.info("alert_poller_loop_iniciado")
        while not self._stop_event.is_set():
            start = time.monotonic()
            try:
                self._ciclo()
            except Exception as exc:
                log.error("alert_poller_ciclo_falhou", error=str(exc))
            elapsed = time.monotonic() - start
            sleep_time = max(0.0, self._interval - elapsed)
            self._stop_event.wait(timeout=sleep_time)
        log.info("alert_poller_loop_encerrado")

    def _ciclo(self) -> None:
        tree = self._engine._cache.get()
        hostids = list(tree.all_hosts.keys())
        if not hostids:
            log.debug("alert_poller_sem_hosts")
            return

        icmp_status = self._icmp_client.get_icmp_status(hostids)
        log.debug("alert_poller_ciclo", hosts=len(hostids), com_icmp=len(icmp_status))

        self._engine.run_cycle(icmp_status)
