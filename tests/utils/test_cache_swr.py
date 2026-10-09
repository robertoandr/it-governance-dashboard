"""CacheSWR: serve o valor anterior enquanto atualiza em segundo plano."""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest

from itgov.utils.cache_swr import CacheSWR


class Origem:
    """Fonte de dados falsa que conta chamadas e pode travar ou falhar."""

    def __init__(self, valores: list[object]) -> None:
        self.valores = list(valores)
        self.chamadas = 0
        self.liberar = threading.Event()
        self.liberar.set()

    def __call__(self) -> object:
        self.chamadas += 1
        self.liberar.wait(5)
        v = self.valores.pop(0)
        if isinstance(v, Exception):
            raise v
        return v


def _esperar(cond, timeout: float = 2.0) -> None:
    fim = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < fim, "condição não atingida"
        time.sleep(0.01)


def _vencer(cache: CacheSWR) -> None:
    cache._expira = 0.0


def test_primeira_carga_busca_e_depois_usa_cache() -> None:
    cache: CacheSWR[str] = CacheSWR("t", ttl=300)
    origem = Origem(["a"])
    assert cache.get(origem) == "a"
    assert cache.get(origem) == "a"
    assert origem.chamadas == 1


def test_vencido_serve_anterior_e_atualiza_em_fundo() -> None:
    cache: CacheSWR[str] = CacheSWR("t", ttl=300)
    origem = Origem(["a", "b"])
    cache.get(origem)
    _vencer(cache)
    origem.liberar.clear()  # atualização fica presa: quem pede não pode esperar

    inicio = time.monotonic()
    assert cache.get(origem) == "a"
    assert cache.get(origem) == "a"  # segunda chamada não dispara outra thread
    assert time.monotonic() - inicio < 0.5

    origem.liberar.set()
    _esperar(lambda: cache.get(origem) == "b")
    assert origem.chamadas == 2


def test_falha_na_atualizacao_mantem_valor_anterior() -> None:
    cache: CacheSWR[str] = CacheSWR("t", ttl=300, ttl_falha=60)
    origem = Origem(["a", RuntimeError("zendesk fora")])
    cache.get(origem)
    _vencer(cache)
    assert cache.get(origem) == "a"
    _esperar(lambda: not cache._atualizando)
    assert cache.get(origem) == "a"
    assert cache._expira - time.monotonic() <= 60  # tenta de novo mais cedo


def test_resultado_invalido_nao_substitui_valor_bom() -> None:
    cache: CacheSWR[dict] = CacheSWR("t", ttl=300, valido=bool)
    origem = Origem([{"csat": 90}, {}])
    cache.get(origem)
    _vencer(cache)
    cache.get(origem)
    _esperar(lambda: not cache._atualizando)
    assert cache.get(origem) == {"csat": 90}


def test_sem_valor_bom_resultado_invalido_vale_pouco_tempo() -> None:
    cache: CacheSWR[dict] = CacheSWR("t", ttl=300, valido=bool, ttl_falha=60)
    origem = Origem([{}, {"csat": 90}])
    assert cache.get(origem) == {}
    assert cache.get(origem) == {}  # não fica batendo na origem a cada pedido
    assert origem.chamadas == 1
    assert cache._expira - time.monotonic() <= 60


def test_carga_inicial_unica_com_pedidos_simultaneos() -> None:
    cache: CacheSWR[str] = CacheSWR("t", ttl=300)
    origem = Origem(["a"])
    origem.liberar.clear()
    resultados: list[str] = []
    threads = [threading.Thread(target=lambda: resultados.append(cache.get(origem))) for _ in range(5)]
    for th in threads:
        th.start()
    time.sleep(0.05)
    origem.liberar.set()
    for th in threads:
        th.join(2)
    assert resultados == ["a"] * 5
    assert origem.chamadas == 1


def test_erro_na_carga_inicial_propaga() -> None:
    cache: CacheSWR[str] = CacheSWR("t", ttl=300)
    with pytest.raises(RuntimeError):
        cache.get(Origem([RuntimeError("boom")]))


def test_aquecer_carrega_em_fundo_e_engole_erro() -> None:
    cache: CacheSWR[str] = CacheSWR("t", ttl=300)
    origem = Origem(["a"])
    cache.aquecer(origem)
    _esperar(lambda: cache._valor == "a")

    ruim: CacheSWR[str] = CacheSWR("r", ttl=300)
    with patch("itgov.utils.cache_swr.log") as log:
        ruim.aquecer(Origem([RuntimeError("boom")]))
        _esperar(lambda: log.warning.called)


def test_limpar_esquece_valor() -> None:
    cache: CacheSWR[str] = CacheSWR("t", ttl=300)
    origem = Origem(["a", "b"])
    cache.get(origem)
    cache.limpar()
    assert cache.get(origem) == "b"


def test_falha_que_devolve_none_nao_faz_cada_pedido_esperar() -> None:
    # m365_uso devolve None quando o Graph recusa: a falha tem de valer por
    # ttl_falha, senão toda página espera a origem de novo (lentidão do g4)
    origem = Origem([None, None, "ok"])
    cache: CacheSWR[object] = CacheSWR("t", ttl=60, valido=lambda v: v is not None, ttl_falha=60)
    assert cache.get(origem) is None
    assert cache.get(origem) is None
    assert origem.chamadas == 1
    # Vencida a falha, tenta de novo em segundo plano e quem pede não espera
    _vencer(cache)
    assert cache.get(origem) is None
    _esperar(lambda: origem.chamadas == 2 and not cache._atualizando)
    _vencer(cache)
    cache.get(origem)
    _esperar(lambda: cache.get(origem) == "ok")
