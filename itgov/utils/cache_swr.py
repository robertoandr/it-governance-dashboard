"""Cache em memória que serve o dado anterior enquanto atualiza em segundo plano.

Para dados de APIs externas lentas (Zendesk leva 7–15 s): depois da primeira
carga, ninguém espera a busca — quando o prazo vence, quem pede recebe o valor
anterior e uma única thread busca o novo. Se a busca falhar, o valor anterior
continua valendo até a próxima tentativa.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Generic, TypeVar

import structlog

log = structlog.get_logger(__name__)

T = TypeVar("T")


class CacheSWR(Generic[T]):  # noqa: UP046 — o CI também roda em Python 3.11 (sem PEP 695)
    """Cache de um valor com atualização em segundo plano (stale-while-revalidate).

    Args:
        nome: Identificador usado nos logs.
        ttl: Segundos em que o valor é considerado novo.
        valido: Diz se um resultado deve ser guardado; resultado inválido
            (ex.: ``{}`` de uma busca que falhou) não substitui um valor bom.
        ttl_falha: Segundos até tentar de novo quando não há valor bom.
    """

    def __init__(
        self,
        nome: str,
        ttl: float,
        valido: Callable[[T], bool] = lambda _v: True,
        ttl_falha: float = 60.0,
    ) -> None:
        self.nome = nome
        self.ttl = ttl
        self.ttl_falha = ttl_falha
        self._valido = valido
        self._lock = threading.Lock()
        self._carga_inicial = threading.Lock()
        self._valor: T | None = None
        # Já houve uma carga (mesmo que o resultado tenha sido None/inválido)?
        # Não dá para usar "_valor is not None": a falha guardada pode ser None.
        self._carregado = False
        self._expira = 0.0
        self._bom = False
        self._atualizando = False

    def get(self, carregar: Callable[[], T]) -> T:
        """Devolve o valor em cache, buscando ou agendando atualização se preciso.

        Args:
            carregar: Função que busca o valor na origem.

        Returns:
            O valor novo, ou o anterior enquanto a atualização roda.
        """
        with self._lock:
            if self._carregado:
                if time.monotonic() >= self._expira and not self._atualizando:
                    self._atualizando = True
                    threading.Thread(target=self._atualizar, args=(carregar,), daemon=True).start()
                return self._valor  # type: ignore[return-value]
        # Sem valor ainda: uma única carga; quem chega junto espera por ela.
        with self._carga_inicial:
            with self._lock:
                if self._carregado:
                    return self._valor  # type: ignore[return-value]
            log.info("cache_swr.carga_inicial", cache=self.nome)
            valor = carregar()
            self._guardar(valor)
            return valor

    @property
    def carregado(self) -> bool:
        """Já houve alguma carga (boa ou não)? Útil para não bloquear a tela."""
        with self._lock:
            return self._carregado

    def aquecer(self, carregar: Callable[[], T]) -> None:
        """Faz a carga inicial em segundo plano (ex.: na subida do worker)."""

        def _rodar() -> None:
            try:
                self.get(carregar)
            except Exception as exc:
                log.warning("cache_swr.aquecer_falhou", cache=self.nome, error=str(exc))

        threading.Thread(target=_rodar, daemon=True).start()

    def atualizar_agora(self, carregar: Callable[[], T]) -> T:
        """Busca na origem já, esperando o resultado (ex.: botão "Atualizar").

        Falha da origem não apaga o valor anterior; devolve o que ficou valendo.
        """
        with self._lock:
            self._atualizando = True
        self._atualizar(carregar)
        with self._lock:
            return self._valor  # type: ignore[return-value]

    def limpar(self) -> None:
        """Esquece o valor guardado (usado em testes)."""
        with self._lock:
            self._valor, self._expira, self._bom, self._atualizando = None, 0.0, False, False
            self._carregado = False

    def _atualizar(self, carregar: Callable[[], T]) -> None:
        try:
            self._guardar(carregar())
            log.info("cache_swr.atualizado", cache=self.nome)
        except Exception as exc:
            log.warning("cache_swr.atualizacao_falhou", cache=self.nome, error=str(exc))
            with self._lock:
                self._expira = time.monotonic() + self.ttl_falha
        finally:
            with self._lock:
                self._atualizando = False

    def _guardar(self, valor: T) -> None:
        with self._lock:
            self._carregado = True
            if self._valido(valor):
                self._valor, self._bom = valor, True
                self._expira = time.monotonic() + self.ttl
                return
            # Resultado ruim nunca apaga um valor bom; sem valor bom, fica valendo
            # por pouco tempo (evita cada requisição esperar uma origem fora do ar).
            if not self._bom:
                self._valor = valor
            self._expira = time.monotonic() + self.ttl_falha
