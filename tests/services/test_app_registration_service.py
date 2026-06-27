"""Testes para itgov/services/app_registration_service.py — pilar Aplicativos."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from itgov.services.app_registration_service import calcular_resumo_apps


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _app(
    nome: str = "app-teste",
    app_id: str = "id-1",
    password_end_in_days: int | None = None,
    cert_end_in_days: int | None = None,
) -> dict:
    app: dict = {"displayName": nome, "appId": app_id, "passwordCredentials": [], "keyCredentials": []}
    if password_end_in_days is not None:
        app["passwordCredentials"] = [{"endDateTime": _iso(datetime.now(UTC) + timedelta(days=password_end_in_days))}]
    if cert_end_in_days is not None:
        app["keyCredentials"] = [{"endDateTime": _iso(datetime.now(UTC) + timedelta(days=cert_end_in_days))}]
    return app


class TestCalcularResumoApps:
    def test_lista_vazia_retorna_zeros(self) -> None:
        resumo = calcular_resumo_apps([])

        assert resumo.total_apps == 0
        assert resumo.secrets_expirando_30d == 0
        assert resumo.secrets_expirados == 0
        assert resumo.expirando == []

    def test_conta_total_de_apps(self) -> None:
        apps = [_app(app_id="1"), _app(app_id="2"), _app(app_id="3")]

        resumo = calcular_resumo_apps(apps)

        assert resumo.total_apps == 3

    def test_secret_expirando_em_15_dias_conta_como_expirando_30d(self) -> None:
        apps = [_app(password_end_in_days=15)]

        resumo = calcular_resumo_apps(apps)

        assert resumo.secrets_expirando_30d == 1
        assert resumo.secrets_expirados == 0
        assert len(resumo.expirando) == 1
        assert resumo.expirando[0].tipo == "password"

    def test_secret_expirado_conta_como_expirado_nao_como_expirando(self) -> None:
        apps = [_app(password_end_in_days=-5)]

        resumo = calcular_resumo_apps(apps)

        assert resumo.secrets_expirados == 1
        assert resumo.secrets_expirando_30d == 0
        # -5 ou -6 dependendo do timing exato entre a fixture e o cálculo
        assert resumo.expirando[0].dias_restantes in (-5, -6)

    def test_secret_distante_no_futuro_nao_conta(self) -> None:
        apps = [_app(password_end_in_days=180)]

        resumo = calcular_resumo_apps(apps)

        assert resumo.secrets_expirando_30d == 0
        assert resumo.secrets_expirados == 0
        assert resumo.expirando == []

    def test_certificado_expirando_e_classificado_como_certificate(self) -> None:
        apps = [_app(cert_end_in_days=10)]

        resumo = calcular_resumo_apps(apps)

        assert resumo.expirando[0].tipo == "certificate"

    def test_app_com_password_e_certificado_expirando_conta_os_dois(self) -> None:
        apps = [_app(password_end_in_days=5, cert_end_in_days=10)]

        resumo = calcular_resumo_apps(apps)

        assert resumo.secrets_expirando_30d == 2

    def test_resultado_ordenado_pelo_mais_urgente_primeiro(self) -> None:
        apps = [
            _app(app_id="longe", password_end_in_days=25),
            _app(app_id="expirado", password_end_in_days=-10),
            _app(app_id="perto", password_end_in_days=5),
        ]

        resumo = calcular_resumo_apps(apps)

        ids_ordenados = [c.app_id for c in resumo.expirando]
        assert ids_ordenados == ["expirado", "perto", "longe"]

    def test_app_sem_credenciais_nao_aparece_em_expirando(self) -> None:
        apps = [_app()]

        resumo = calcular_resumo_apps(apps)

        assert resumo.expirando == []
