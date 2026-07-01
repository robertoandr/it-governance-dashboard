"""Testes para itgov/services/device_service.py — cálculo do pilar Dispositivos."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from itgov.services.device_service import calcular_resumo_dispositivos


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _device(
    os: str = "Windows",
    trust: str = "Workplace",
    managed: bool | None = None,
    last_signin_days_ago: int = 1,
) -> dict:
    return {
        "operatingSystem": os,
        "trustType": trust,
        "isManaged": managed,
        "approximateLastSignInDateTime": _iso(datetime.now(UTC) - timedelta(days=last_signin_days_ago)),
    }


class TestCalcularResumoDispositivos:
    def test_lista_vazia_retorna_zeros(self) -> None:
        resumo = calcular_resumo_dispositivos([])

        assert resumo.total_devices == 0
        assert resumo.stale_45d == 0
        assert resumo.managed_pct is None
        assert resumo.os_distribution == {}
        assert resumo.trust_type_distribution == {}

    def test_conta_total_e_distribuicoes(self) -> None:
        devices = [
            _device(os="Windows", trust="Workplace"),
            _device(os="Windows", trust="AzureAd"),
            _device(os="iOS", trust="Workplace"),
        ]

        resumo = calcular_resumo_dispositivos(devices)

        assert resumo.total_devices == 3
        assert resumo.os_distribution == {"Windows": 2, "iOS": 1}
        assert resumo.trust_type_distribution == {"Workplace": 2, "AzureAd": 1}

    def test_dispositivo_sem_sign_in_45_dias_conta_como_stale(self) -> None:
        devices = [
            _device(last_signin_days_ago=10),
            _device(last_signin_days_ago=200),
        ]

        resumo = calcular_resumo_dispositivos(devices)

        assert resumo.stale_45d == 1

    def test_managed_pct_none_quando_todos_isManaged_null(self) -> None:
        """isManaged sempre null (Intune não usado) -> managed_pct None, não 0%."""
        devices = [_device(managed=None), _device(managed=None)]

        resumo = calcular_resumo_dispositivos(devices)

        assert resumo.managed_pct is None

    def test_managed_pct_calculado_quando_ha_dados_reais(self) -> None:
        devices = [
            _device(managed=True),
            _device(managed=True),
            _device(managed=False),
            _device(managed=None),  # não entra no denominador
        ]

        resumo = calcular_resumo_dispositivos(devices)

        assert resumo.managed_pct == 66.7

    def test_sem_campo_sign_in_nao_conta_como_stale(self) -> None:
        device = _device()
        del device["approximateLastSignInDateTime"]

        resumo = calcular_resumo_dispositivos([device])

        assert resumo.stale_45d == 0

    def test_os_e_trust_ausentes_caem_em_desconhecido(self) -> None:
        device = {"isManaged": None}

        resumo = calcular_resumo_dispositivos([device])

        assert resumo.os_distribution == {"desconhecido": 1}
        assert resumo.trust_type_distribution == {"desconhecido": 1}
