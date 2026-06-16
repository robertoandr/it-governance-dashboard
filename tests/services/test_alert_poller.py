"""Testes unitários do AlertPoller — ciclo de polling em background."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from itgov.services.alert_poller import AlertPoller, start_poller
from itgov.services.zabbix_hierarchy import HierarchyTree


def _engine_com_hosts(hostids: list[str]) -> MagicMock:
    engine = MagicMock()
    tree = HierarchyTree(dvrs={}, cameras={}, standalone={}, lojas={}, all_hosts=dict.fromkeys(hostids))
    engine._cache.get.return_value = tree
    return engine


class TestCiclo:
    def test_ciclo_sem_hosts_nao_chama_icmp_nem_run_cycle(self) -> None:
        engine = _engine_com_hosts([])
        icmp_client = MagicMock()
        poller = AlertPoller(engine=engine, icmp_client=icmp_client, interval=60)

        poller._ciclo()

        icmp_client.get_icmp_status.assert_not_called()
        engine.run_cycle.assert_not_called()

    def test_ciclo_com_hosts_consulta_icmp_e_roda_engine(self) -> None:
        engine = _engine_com_hosts(["10", "20"])
        icmp_client = MagicMock()
        icmp_client.get_icmp_status.return_value = {"10": "up", "20": "down"}
        poller = AlertPoller(engine=engine, icmp_client=icmp_client, interval=60)

        poller._ciclo()

        icmp_client.get_icmp_status.assert_called_once()
        called_hostids = set(icmp_client.get_icmp_status.call_args[0][0])
        assert called_hostids == {"10", "20"}
        engine.run_cycle.assert_called_once_with({"10": "up", "20": "down"})


class TestStartStop:
    def test_start_inicia_a_thread(self) -> None:
        engine = _engine_com_hosts([])
        poller = AlertPoller(engine=engine, icmp_client=MagicMock(), interval=60)

        poller.start()
        try:
            assert poller._thread.is_alive()
        finally:
            poller.stop()

    def test_stop_sinaliza_e_aguarda_encerramento(self) -> None:
        engine = _engine_com_hosts([])
        poller = AlertPoller(engine=engine, icmp_client=MagicMock(), interval=60)

        poller.start()
        poller.stop()

        assert poller._stop_event.is_set()
        assert not poller._thread.is_alive()

    def test_loop_roda_ao_menos_um_ciclo_antes_de_parar(self) -> None:
        engine = _engine_com_hosts([])
        poller = AlertPoller(engine=engine, icmp_client=MagicMock(), interval=0)

        with patch.object(poller, "_ciclo") as mock_ciclo:
            poller.start()
            poller._stop_event.wait(timeout=0.2)
            poller.stop()

        assert mock_ciclo.call_count >= 1

    def test_loop_continua_apos_ciclo_levantar_excecao(self) -> None:
        engine = _engine_com_hosts([])
        poller = AlertPoller(engine=engine, icmp_client=MagicMock(), interval=0)

        with patch.object(poller, "_ciclo", side_effect=RuntimeError("zabbix indisponível")):
            poller.start()
            poller._stop_event.wait(timeout=0.2)
            poller.stop()

        # não deve propagar — thread já não está mais viva por causa do stop(), não de crash
        assert not poller._thread.is_alive()


class TestFromConfig:
    def test_from_config_monta_poller_com_webhook(self) -> None:
        mock_config = MagicMock()
        mock_config.ZABBIX_URL = "https://zabbix.example.com"
        mock_config.ZABBIX_TOKEN = "tok-123"
        mock_config.ZABBIX_USER = ""
        mock_config.ZABBIX_PASSWORD = ""
        mock_config.WEBHOOK_URL = "https://teams.example.com/webhook"
        mock_config.POLL_INTERVAL = 30
        mock_config.FLAP_THRESHOLD = 3

        with (
            patch("itgov.services.alert_poller.config", mock_config),
            patch("itgov.services.alert_poller.ZabbixHierarchyClient") as mock_hclient,
            patch("itgov.services.alert_poller.ZabbixAssetClient") as mock_iclient,
            patch("itgov.services.alert_poller.TeamsNotifier") as mock_notifier,
        ):
            poller = AlertPoller.from_config()

        mock_hclient.assert_called_once_with(url="https://zabbix.example.com", token="tok-123", user="", password="")
        mock_iclient.assert_called_once_with(url="https://zabbix.example.com", token="tok-123", user="", password="")
        mock_notifier.assert_called_once_with("https://teams.example.com/webhook")
        assert poller._interval == 30

    def test_from_config_sem_webhook_nao_cria_notifier(self) -> None:
        mock_config = MagicMock()
        mock_config.ZABBIX_URL = "https://zabbix.example.com"
        mock_config.ZABBIX_TOKEN = ""
        mock_config.ZABBIX_USER = "user"
        mock_config.ZABBIX_PASSWORD = "pass"
        mock_config.WEBHOOK_URL = ""
        mock_config.POLL_INTERVAL = 60
        mock_config.FLAP_THRESHOLD = 2

        with (
            patch("itgov.services.alert_poller.config", mock_config),
            patch("itgov.services.alert_poller.ZabbixHierarchyClient"),
            patch("itgov.services.alert_poller.ZabbixAssetClient"),
            patch("itgov.services.alert_poller.TeamsNotifier") as mock_notifier,
            patch.dict("os.environ", {}, clear=False),
        ):
            AlertPoller.from_config()

        mock_notifier.assert_not_called()


class TestStartPoller:
    def test_nao_inicia_quando_lock_ocupado(self) -> None:
        with (
            patch("itgov.services.singleton.try_acquire_poller_lock", return_value=False),
            patch.object(AlertPoller, "from_config") as mock_from_config,
        ):
            start_poller()

        mock_from_config.assert_not_called()

    def test_inicia_poller_quando_lock_adquirido(self) -> None:
        mock_poller = MagicMock()
        with (
            patch("itgov.services.singleton.try_acquire_poller_lock", return_value=True),
            patch.object(AlertPoller, "from_config", return_value=mock_poller),
        ):
            start_poller()

        mock_poller.start.assert_called_once()

    def test_falha_ao_montar_poller_nao_propaga(self) -> None:
        with (
            patch("itgov.services.singleton.try_acquire_poller_lock", return_value=True),
            patch.object(AlertPoller, "from_config", side_effect=RuntimeError("config inválida")),
        ):
            start_poller()  # não deve levantar
