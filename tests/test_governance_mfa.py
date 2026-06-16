"""Testes para o módulo de adoção de MFA — Governança de TI.

Cobertura:
- Classificação automática (sala via GUID, sala_ prefixo, não-humano, humano, externos)
- pending_review fora do denominador
- Override do banco sobrepõe a régua automática
- Cálculo de adoção exato = 87.0%
- Mock do Graph (sem rede)
- Cache TTL: Graph chamado 1x em 2 GETs consecutivos; invalidado pelo POST
- Auth: POST sem PIN → 403; POST com PIN → 200; reviewed_por gravado
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from itgov.models.db.account_classification import AccountClassificationOverride
from itgov.models.db.base import Base
from itgov.models.governance_mfa import Classificacao
from itgov.services.mfa_service import calcular_adocao_mfa, classificar_conta, gravar_override

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def db_session():
    """Sessão SQLite em memória isolada por teste."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    yield session
    session.close()
    Base.metadata.drop_all(engine)


def _usuario(
    upn: str,
    nome: str = "Usuário Teste",
    mfa: bool = True,
    ativo: bool = True,
) -> dict:
    return {
        "userPrincipalName": upn,
        "displayName": nome,
        "mfa_enabled": mfa,
        "accountEnabled": ativo,
    }


# ── Testes de classificação automática ────────────────────────────────────────


class TestClassificarConta:
    def test_sala_via_guid_no_display_name(self):
        """GUID no displayName → sala."""
        classificacao = classificar_conta(
            "reuniao01@empresa.com",
            "Sala de Reunião 1a2b3c4d-1234-5678-abcd-ef0123456789",
            habilitada=True,
        )
        assert classificacao == Classificacao.sala

    def test_sala_via_prefixo_upn(self):
        """UPN local começa com sala_ (underscore) → sala."""
        assert classificar_conta("sala_reunioes@empresa.com", "Sala Reunioes", True) == Classificacao.sala

    def test_sala_prefixo_sem_hifen_nao_eh_sala(self):
        """sala- com hífen não é capturado pela regra sala_ → cai em pending_review (disabled) ou human."""
        # conta ativa sem prefixo sala_ → human
        assert classificar_conta("sala-reunioes@empresa.com", "Sala Reunioes", True) == Classificacao.humano

    def test_nao_humano_prefixo_svc(self):
        assert classificar_conta("svc_backup@empresa.com", "Service Backup", True) == Classificacao.nao_humano

    def test_nao_humano_prefixo_bot(self):
        assert classificar_conta("bot_teams@empresa.com", "Bot Teams", True) == Classificacao.nao_humano

    def test_nao_humano_prefixo_shared(self):
        assert classificar_conta("shared_ti@empresa.com", "Caixa Compartilhada TI", True) == Classificacao.nao_humano

    def test_humano_externo_makeit(self):
        """Externos com domínio makeit → human."""
        assert classificar_conta("joao@makeit.com.br", "João Silva", True) == Classificacao.humano

    def test_humano_externo_triunfo(self):
        """Externos com domínio triunfo → human."""
        assert classificar_conta("maria@triunfo.com", "Maria Souza", True) == Classificacao.humano

    def test_pendente_revisao_conta_desabilitada(self):
        """Conta desabilitada sem classificação clara → pending_review."""
        assert (
            classificar_conta("joao.antigo@empresa.com", "João Antigo", habilitada=False)
            == Classificacao.pendente_revisao
        )

    def test_humano_conta_normal_ativa(self):
        """Conta ativa sem prefixo especial → human."""
        assert classificar_conta("ana.santos@empresa.com", "Ana Santos", True) == Classificacao.humano


# ── Testes de cálculo de adoção ───────────────────────────────────────────────


class TestCalcularAdocaoMFA:
    def test_pendente_revisao_fora_do_denominador(self, db_session):
        """Contas pending_review não entram no denominador de adoção."""
        usuarios = [
            _usuario("humano1@emp.com", mfa=True),
            _usuario("humano2@emp.com", mfa=True),
            # conta desabilitada → pending_review
            _usuario("antigo@emp.com", mfa=False, ativo=False),
        ]
        detalhe = calcular_adocao_mfa(usuarios, db_session)
        assert detalhe.denominador == 2
        assert detalhe.total_pendente_revisao == 1
        assert detalhe.adocao_pct == 100.0

    def test_sala_fora_do_denominador(self, db_session):
        """Salas não entram no denominador."""
        usuarios = [
            _usuario("humano1@emp.com", mfa=True),
            _usuario("sala_conf@emp.com", "1a2b3c4d-0000-0000-0000-ef0123456789", mfa=False),
        ]
        detalhe = calcular_adocao_mfa(usuarios, db_session)
        assert detalhe.denominador == 1
        assert detalhe.total_salas == 1
        assert detalhe.adocao_pct == 100.0

    def test_nao_humano_fora_do_denominador(self, db_session):
        """Contas não-humanas não entram no denominador."""
        usuarios = [
            _usuario("humano1@emp.com", mfa=True),
            _usuario("svc_backup@emp.com", mfa=False),
        ]
        detalhe = calcular_adocao_mfa(usuarios, db_session)
        assert detalhe.denominador == 1
        assert detalhe.total_nao_humanos == 1
        assert detalhe.adocao_pct == 100.0

    def test_adocao_exata_87_pct(self, db_session):
        """Cenário que produz exatamente 87.0% de adoção."""
        # 87 humanos com MFA + 13 humanos sem MFA = 100 humanos → 87.0%
        usuarios = (
            [_usuario(f"h{i}@emp.com", mfa=True) for i in range(87)]
            + [_usuario(f"sem{i}@emp.com", mfa=False) for i in range(13)]
            + [_usuario("svc_svc@emp.com", mfa=False)]  # non_human
            + [_usuario("sala_a@emp.com", "GUID 00000000-0000-0000-0000-000000000000", mfa=False)]  # room
            + [_usuario("antigo@emp.com", mfa=False, ativo=False)]  # pending_review
        )
        detalhe = calcular_adocao_mfa(usuarios, db_session)
        assert detalhe.denominador == 100
        assert detalhe.com_mfa == 87
        assert detalhe.sem_mfa == 13
        assert detalhe.adocao_pct == 87.0

    def test_denominador_zero_retorna_zero(self, db_session):
        """Sem humanos ativos → adoção 0%, sem ZeroDivisionError."""
        usuarios = [_usuario("svc_x@emp.com", mfa=True)]
        detalhe = calcular_adocao_mfa(usuarios, db_session)
        assert detalhe.denominador == 0
        assert detalhe.adocao_pct == 0.0

    def test_upn_vazio_ignorado(self, db_session):
        """Usuários sem UPN são ignorados silenciosamente."""
        usuarios = [
            {"userPrincipalName": "", "displayName": "Vazio", "mfa_enabled": True, "accountEnabled": True},
            _usuario("humano1@emp.com", mfa=True),
        ]
        detalhe = calcular_adocao_mfa(usuarios, db_session)
        assert detalhe.denominador == 1


# ── Testes de override do banco ───────────────────────────────────────────────


class TestOverrideBanco:
    def test_override_sobrepoe_regra(self, db_session):
        """Override do banco vence a régua automática."""
        # svc_ seria non_human pela régua, mas override marca como human
        gravar_override("svc_ceo@emp.com", Classificacao.humano, "admin@emp.com", db_session)

        usuarios = [_usuario("svc_ceo@emp.com", mfa=True)]
        detalhe = calcular_adocao_mfa(usuarios, db_session)

        assert detalhe.denominador == 1
        assert detalhe.total_nao_humanos == 0
        assert detalhe.adocao_pct == 100.0

    def test_override_para_sala(self, db_session):
        """Override classifica humano como sala (fora do denominador)."""
        gravar_override("diretor@emp.com", Classificacao.sala, "admin@emp.com", db_session)

        usuarios = [_usuario("diretor@emp.com", mfa=True)]
        detalhe = calcular_adocao_mfa(usuarios, db_session)

        assert detalhe.denominador == 0
        assert detalhe.total_salas == 1

    def test_override_atualiza_classificacao(self, db_session):
        """Segunda chamada de gravar_override atualiza o registro existente."""
        gravar_override("u@emp.com", Classificacao.nao_humano, "a@emp.com", db_session)
        gravar_override("u@emp.com", Classificacao.humano, "b@emp.com", db_session)

        row = db_session.get(AccountClassificationOverride, "u@emp.com")
        assert row is not None
        assert row.classificacao == "human"
        assert row.revisado_por == "b@emp.com"

    def test_override_normaliza_upn_maiusculo(self, db_session):
        """UPN em maiúsculo é normalizado antes de gravar."""
        gravar_override("USER@EMP.COM", Classificacao.nao_humano, "admin@emp.com", db_session)
        row = db_session.get(AccountClassificationOverride, "user@emp.com")
        assert row is not None

    def test_override_invalido_ignorado(self, db_session):
        """Override com classificação inválida no banco é ignorado (conta usa régua)."""
        # Insere diretamente no banco com valor inválido
        db_session.add(
            AccountClassificationOverride(
                upn="x@emp.com",
                classificacao="invalido_xyz",
                revisado_por="teste",
            )
        )
        db_session.commit()

        usuarios = [_usuario("x@emp.com", mfa=True)]
        # Não deve lançar exceção — deve usar classificação pela régua
        detalhe = calcular_adocao_mfa(usuarios, db_session)
        assert detalhe.denominador == 1  # cai em humano pela régua


# ── Mock do Graph (sem rede) ──────────────────────────────────────────────────


class TestMFAGraphClientMock:
    def test_get_users_with_mfa_combina_dados(self):
        """get_users_with_mfa enriquece usuários com mfa_enabled sem rede real."""
        import asyncio

        from itgov.services.mfa_graph_client import MFAGraphClient

        usuarios_mock = [
            {"userPrincipalName": "Ana@emp.com", "displayName": "Ana", "accountEnabled": True},
            {"userPrincipalName": "Bot@emp.com", "displayName": "Bot", "accountEnabled": True},
        ]
        mfa_map_mock = {"ana@emp.com": True, "bot@emp.com": False}

        async def _usuarios(*_):
            return usuarios_mock

        async def _mfa(*_):
            return mfa_map_mock

        client = MFAGraphClient()
        with (
            patch.object(client, "get_users", side_effect=_usuarios),
            patch.object(client, "get_mfa_registration_details", side_effect=_mfa),
        ):
            resultado = asyncio.run(client.get_users_with_mfa("tenant-id"))

        assert resultado[0]["mfa_enabled"] is True
        assert resultado[1]["mfa_enabled"] is False

    def test_paginacao_nextlink(self):
        """_paginate segue @odata.nextLink até esgotar as páginas."""
        import asyncio

        from itgov.services.mfa_graph_client import _paginate

        pagina1 = {"value": [{"id": "1"}], "@odata.nextLink": "http://graph/page2"}
        pagina2 = {"value": [{"id": "2"}]}

        async def coletar():
            import httpx

            client_mock = MagicMock(spec=httpx.AsyncClient)

            async def _get_mock(url: str, headers: dict):
                resp = MagicMock()
                resp.status_code = 200
                if "page2" in url:
                    resp.json.return_value = pagina2
                else:
                    resp.json.return_value = pagina1
                resp.raise_for_status = MagicMock()
                return resp

            client_mock.get = _get_mock

            with patch("itgov.services.mfa_graph_client._get") as mock_get:

                async def _get_side(c, url, token):
                    return pagina2 if "page2" in url else pagina1

                mock_get.side_effect = _get_side

                items = []
                async for item in _paginate(client_mock, "http://graph/start", "token", "tenant", "test"):
                    items.append(item)
            return items

        itens = asyncio.run(coletar())
        assert len(itens) == 2
        assert itens[0]["id"] == "1"
        assert itens[1]["id"] == "2"


# ── Fixtures para testes de cache e auth ──────────────────────────────────────

_DADOS_FAKE = {
    "adocao_pct": 87.0,
    "denominador": 100,
    "com_mfa": 87,
    "sem_mfa": 13,
    "total_humanos": 100,
    "total_nao_humanos": 5,
    "total_salas": 3,
    "total_pendente_revisao": 2,
    "pendentes_revisao": [],
}


@pytest.fixture
def flask_app(db_session):
    """App Flask mínimo com o namespace governance_mfa registrado."""
    from flask import Flask
    from flask_restx import Api

    app = Flask(__name__)
    app.config["TESTING"] = True
    # Desabilita JWT e HTTPS forced no teste
    app.config["SECRET_KEY"] = "test-only"

    api = Api(app, prefix="/api/v1")

    # Importa depois do app criado para evitar problemas de contexto
    from itgov.api.v1.governance_mfa import ns as mfa_ns

    api.add_namespace(mfa_ns)

    return app


@pytest.fixture
def cliente(flask_app):
    with flask_app.test_client() as c:
        yield c


@pytest.fixture(autouse=False)
def limpar_cache():
    """Invalida o cache antes e depois de cada teste que precise."""
    from itgov.api.v1.governance_mfa import invalidar_cache_mfa

    invalidar_cache_mfa()
    yield
    invalidar_cache_mfa()


# ── Testes de Cache ────────────────────────────────────────────────────────────


class TestCacheMFA:
    def test_dois_gets_chamam_graph_uma_vez(self, cliente, limpar_cache):
        """2 GETs consecutivos → Graph chamado só 1x (cache hit na 2ª chamada)."""
        with patch("itgov.api.v1.governance_mfa._buscar_do_graph", return_value=_DADOS_FAKE) as mock_graph:
            r1 = cliente.get("/api/v1/governance/mfa")
            r2 = cliente.get("/api/v1/governance/mfa")

        assert r1.status_code == 200
        assert r2.status_code == 200
        assert mock_graph.call_count == 1

    def test_cache_expirado_chama_graph_novamente(self, cliente, limpar_cache, monkeypatch):
        """Após TTL expirar, próximo GET busca do Graph novamente."""
        import itgov.api.v1.governance_mfa as mod

        with patch("itgov.api.v1.governance_mfa._buscar_do_graph", return_value=_DADOS_FAKE) as mock_graph:
            cliente.get("/api/v1/governance/mfa")
            # Simula expiração forçando _cache_ts para 0
            monkeypatch.setattr(mod, "_cache_ts", 0.0)
            cliente.get("/api/v1/governance/mfa")

        assert mock_graph.call_count == 2

    def test_reclassify_invalida_cache(self, cliente, limpar_cache):
        """GET → POST /reclassify → GET: Graph chamado 2x (cache invalidado pelo POST)."""
        with (
            patch("itgov.api.v1.governance_mfa._buscar_do_graph", return_value=_DADOS_FAKE) as mock_graph,
            patch("itgov.api.v1.governance_mfa._pin_configurado", return_value="pin-teste"),
            patch("itgov.services.mfa_service.gravar_override"),
        ):
            # 1º GET — popula cache (call_count = 1)
            cliente.get("/api/v1/governance/mfa")

            # POST /reclassify — deve invalidar cache
            cliente.post(
                "/api/v1/governance/reclassify",
                json={"upn": "user@emp.com", "classificacao": "human"},
                headers={"X-Gov-Mfa-Pin": "pin-teste", "X-User-Email": "admin@emp.com"},
            )

            # 2º GET — deve buscar do Graph novamente (call_count = 2)
            cliente.get("/api/v1/governance/mfa")

        assert mock_graph.call_count == 2

    def test_cache_thread_safe(self, limpar_cache):
        """Cache suporta leituras/escritas concorrentes sem race condition."""
        import threading

        from itgov.api.v1.governance_mfa import _gravar_cache, _ler_cache, invalidar_cache_mfa

        erros = []

        def _worker():
            try:
                for _ in range(50):
                    _gravar_cache(_DADOS_FAKE.copy())
                    _ler_cache()
                    invalidar_cache_mfa()
            except Exception as exc:
                erros.append(exc)

        threads = [threading.Thread(target=_worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert erros == [], f"Race conditions detectadas: {erros}"


# ── Testes de Auth (decorator requer_admin) ───────────────────────────────────


class TestAuthReclassify:
    def test_post_sem_pin_retorna_403(self, cliente, limpar_cache):
        """POST sem X-Gov-Mfa-Pin → 403."""
        with patch("itgov.api.v1.governance_mfa._pin_configurado", return_value="pin-secreto"):
            resp = cliente.post(
                "/api/v1/governance/reclassify",
                json={"upn": "u@emp.com", "classificacao": "human"},
            )
        assert resp.status_code == 403

    def test_post_pin_errado_retorna_403(self, cliente, limpar_cache):
        """POST com PIN incorreto → 403."""
        with patch("itgov.api.v1.governance_mfa._pin_configurado", return_value="pin-secreto"):
            resp = cliente.post(
                "/api/v1/governance/reclassify",
                json={"upn": "u@emp.com", "classificacao": "human"},
                headers={"X-Gov-Mfa-Pin": "errado"},
            )
        assert resp.status_code == 403

    def test_post_sem_pin_configurado_retorna_503(self, cliente, limpar_cache):
        """POST quando GOV_MFA_PIN não está configurado → 503."""
        with patch("itgov.api.v1.governance_mfa._pin_configurado", return_value=""):
            resp = cliente.post(
                "/api/v1/governance/reclassify",
                json={"upn": "u@emp.com", "classificacao": "human"},
                headers={"X-Gov-Mfa-Pin": "qualquer"},
            )
        assert resp.status_code == 503

    def test_post_com_pin_correto_retorna_200(self, cliente, limpar_cache):
        """POST com PIN correto → 200 e dados atualizados."""
        with (
            patch("itgov.api.v1.governance_mfa._buscar_do_graph", return_value=_DADOS_FAKE),
            patch("itgov.api.v1.governance_mfa._pin_configurado", return_value="pin-correto"),
            patch("itgov.services.mfa_service.gravar_override") as mock_override,
        ):
            resp = cliente.post(
                "/api/v1/governance/reclassify",
                json={"upn": "user@emp.com", "classificacao": "non_human"},
                headers={"X-Gov-Mfa-Pin": "pin-correto", "X-User-Email": "admin@emp.com"},
            )

        assert resp.status_code == 200
        assert mock_override.called

    def test_reviewed_por_vem_do_header_x_user_email(self, cliente, limpar_cache):
        """reviewed_por é extraído do header X-User-Email — nunca do body."""
        capturado: dict = {}

        def _fake_override(upn, classificacao, revisado_por, session):
            capturado["revisado_por"] = revisado_por

        with (
            patch("itgov.api.v1.governance_mfa._buscar_do_graph", return_value=_DADOS_FAKE),
            patch("itgov.api.v1.governance_mfa._pin_configurado", return_value="pin-ok"),
            patch("itgov.services.mfa_service.gravar_override", side_effect=_fake_override),
        ):
            cliente.post(
                "/api/v1/governance/reclassify",
                json={"upn": "user@emp.com", "classificacao": "human"},
                headers={"X-Gov-Mfa-Pin": "pin-ok", "X-User-Email": "operador@emp.com"},
            )

        assert capturado.get("revisado_por") == "operador@emp.com"

    def test_reviewed_por_fallback_para_ip_sem_header(self, cliente, limpar_cache):
        """Sem X-User-Email → reviewed_por usa IP (nunca null)."""
        capturado: dict = {}

        def _fake_override(upn, classificacao, revisado_por, session):
            capturado["revisado_por"] = revisado_por

        with (
            patch("itgov.api.v1.governance_mfa._buscar_do_graph", return_value=_DADOS_FAKE),
            patch("itgov.api.v1.governance_mfa._pin_configurado", return_value="pin-ok"),
            patch("itgov.services.mfa_service.gravar_override", side_effect=_fake_override),
        ):
            cliente.post(
                "/api/v1/governance/reclassify",
                json={"upn": "user@emp.com", "classificacao": "human"},
                headers={"X-Gov-Mfa-Pin": "pin-ok"},
            )

        revisado = capturado.get("revisado_por", "")
        assert revisado  # nunca vazio / null
        assert revisado.startswith("ip:"), f"Esperado ip:..., recebido: {revisado!r}"
