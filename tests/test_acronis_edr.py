"""Cliente da API de EDR do Acronis (/api/mdr/v1) usado pelo agente de incidentes."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from itgov.services import acronis_edr as edr

_BASE = "https://acronis.test"
_MDR = f"{_BASE}/api/mdr/v1"


@pytest.fixture(autouse=True)
def credenciais(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACRONIS_BASE_URL", _BASE)
    monkeypatch.setenv("ACRONIS_CLIENT_ID", "cid")
    monkeypatch.setenv("ACRONIS_CLIENT_SECRET", "sec")
    monkeypatch.delenv("ACRONIS_CYBER_CLIENT_ID", raising=False)
    monkeypatch.delenv("ACRONIS_CYBER_CLIENT_SECRET", raising=False)


@pytest.fixture
def api() -> respx.MockRouter:
    with respx.mock(assert_all_called=False) as router:
        router.post(f"{_BASE}/api/2/idp/token").respond(json={"access_token": "tok"})
        yield router


def _item(iid: str, criado: str = "2026-10-20T10:00:00Z", estado: str = "NOT_STARTED", **extra: object) -> dict:
    return {
        "incident_id": iid,
        "customer_id": "cust",
        "host_name": f"pc-{iid}",
        "created_at": criado,
        "updated_at": criado,
        "severity": "HIGH",
        "verdict": "MALICIOUS",
        "state": estado,
        "mitigation_state": "NOT_MITIGATED",
        "incident_categories": ["URL_BLOCKED"],
        "incident_link": f"https://console/{iid}",
    } | extra


def test_listar_abertos_pagina_filtra_encerrados_e_ordena(api: respx.MockRouter) -> None:
    rota = api.get(f"{_MDR}/incidents").mock(
        side_effect=[
            httpx.Response(
                200, json={"items": [_item("a", "2026-10-01T00:00:00Z"), _item("b", estado="CLOSED")], "cursor": "c2"}
            ),
            httpx.Response(
                200, json={"items": [_item("c", "2026-10-22T00:00:00Z"), _item("d", estado="FALSE_POSITIVE")]}
            ),
        ]
    )
    abertos = edr.listar_abertos()
    assert [i.incident_id for i in abertos] == ["c", "a"]
    assert rota.calls[0].request.url.params["mitigation_state"] == "NOT_MITIGATED"
    assert rota.calls[1].request.url.params["cursor"] == "c2"
    assert rota.calls[0].request.headers["Authorization"] == "Bearer tok"


def test_detalhe_traz_acoes_e_deteccoes(api: respx.MockRouter) -> None:
    raw = _item(
        "a",
        incident_actions=[
            {"action": "WORKLOAD_ISOLATE", "workload_id": "w1"},
            {"action": "WORKLOAD_SHUTDOWN", "workload_id": "w1"},
        ],
        detections=[
            {
                "engine": "URLF",
                "event_time": "2026-10-23T23:38:26Z",
                "description": "User executes malicious link",
                "threat_name": "URL.MaliciousWebsite.C",
                "verdict": "MALICIOUS",
                "confidence": 80,
                "is_blocked": False,
                "dest_url": "api.mau.example",
            }
        ],
    )
    rota = api.get(f"{_MDR}/incidents/a").respond(json=raw)
    inc = edr.detalhe("a", "cust")
    assert inc.acoes == {"WORKLOAD_ISOLATE": "w1", "WORKLOAD_SHUTDOWN": "w1"}
    d = inc.deteccoes[0]
    assert (d.motor, d.alvo, d.bloqueado, d.confianca) == ("URLF", "api.mau.example", False, 80)
    params = rota.calls[0].request.url.params
    assert params["customer_id"] == "cust" and params["with_incident_actions"] == "true"


@pytest.mark.parametrize("codigo", [404, 410])
def test_detalhe_de_incidente_apagado(api: respx.MockRouter, codigo: int) -> None:
    api.get(f"{_MDR}/incidents/x").respond(codigo, json={"code": "gone"})
    with pytest.raises(edr.IncidenteNaoEncontradoError):
        edr.detalhe("x", "cust")


def test_atualizar_estado_envia_comentario_e_idempotencia(api: respx.MockRouter) -> None:
    rota = api.post(f"{_MDR}/incidents/a/investigation_state").respond(201)
    edr.atualizar_estado("a", "cust", "CLOSED", "encerrado pelo dashboard")
    req = rota.calls[0].request
    assert json.loads(req.content) == {"state": "CLOSED", "comment": "encerrado pelo dashboard"}
    assert req.headers["Idempotency-Key"] and req.url.params["customer_id"] == "cust"


def test_atualizar_estado_recusa_estado_fora_da_lista(api: respx.MockRouter) -> None:
    with pytest.raises(ValueError, match="não permitido"):
        edr.atualizar_estado("a", "cust", "NOT_STARTED", "x")
    with pytest.raises(ValueError, match="não permitido"):
        edr.atualizar_estado_lote([("a", "cust")], "INVESTIGATING", "x")


def test_atualizar_estado_lote_devolve_falha_por_item(api: respx.MockRouter) -> None:
    rota = api.post(f"{_MDR}/incidents/investigation_state").respond(
        207,
        json={
            "items": [
                {"item": {"incident_id": "a", "customer_id": "cust"}, "status": 201},
                {
                    "item": {"incident_id": "b", "customer_id": "cust"},
                    "status": 404,
                    "error": {"code": "nf", "message": "não existe"},
                },
            ]
        },
    )
    resultado = edr.atualizar_estado_lote([("a", "cust"), ("b", "cust")], "CLOSED", "lote")
    assert resultado == {"a": "", "b": "não existe"}
    corpo = json.loads(rota.calls[0].request.content)
    assert corpo["update"] == {"state": "CLOSED", "comment": "lote"}
    assert corpo["customer_incident_ids"][1] == {"customer_id": "cust", "incident_id": "b"}
    assert edr.atualizar_estado_lote([], "CLOSED", "x") == {}


def test_executar_acao_so_permitida_e_oferecida(api: respx.MockRouter) -> None:
    rota = api.post(f"{_MDR}/incidents/a/response_action").respond(201, json={"activity_id": "act-1"})
    inc = edr.normalizar(
        _item(
            "a",
            incident_actions=[
                {"action": "PROCESS_STOP", "workload_id": "w1"},
                {"action": "WORKLOAD_SHUTDOWN", "workload_id": "w1"},
            ],
        )
    )
    assert edr.executar_acao(inc, "PROCESS_STOP", "parar") == "act-1"
    params = rota.calls[0].request.url.params
    assert (params["action"], params["workload_id"], params["customer_id"]) == ("PROCESS_STOP", "w1", "cust")
    with pytest.raises(edr.AcaoNaoPermitidaError):
        edr.executar_acao(inc, "WORKLOAD_SHUTDOWN", "desligar")  # oferecida, mas fora da lista permitida
    with pytest.raises(edr.AcaoNaoPermitidaError):
        edr.executar_acao(inc, "WORKLOAD_ISOLATE", "isolar")  # permitida, mas não oferecida
    assert len(rota.calls) == 1


def test_status_acao(api: respx.MockRouter) -> None:
    rota = api.get(f"{_MDR}/incidents/a/response_action").respond(json={"status": "SUCCEEDED"})
    assert edr.status_acao("a", "cust", "act-1") == "SUCCEEDED"
    assert rota.calls[0].request.url.params["activity_id"] == "act-1"


def test_listas_de_acoes_e_estados_nao_incluem_destrutivas() -> None:
    proibidas = {"WORKLOAD_SHUTDOWN", "WORKLOAD_RESTART", "PROCESS_FILE_DELETE", "ADD_ALL_PROCESS_FILE_URL_TO_ALLOW_LIST",
                 "ADD_TO_ALLOW", "WORKLOAD_ROLLBACK", "WORKLOAD_PATCH"}  # fmt: skip
    assert not proibidas & set(edr.ACOES_PERMITIDAS)
    assert set(edr.ESTADOS_PERMITIDOS) == {"TRIAGED", "FALSE_POSITIVE", "CLOSED"}
