"""Análise da foto de um canal de câmera: tem imagem de verdade?

Calibrado em 14/10/2026 com a conferência no local (25 canais "sem imagem"
e 30 com imagem boa, em 7 gravadores Intelbras):

- canal sem imagem que o gravador ainda grava (analógica sem sinal, câmera
  com defeito) entrega uma tela chapada: ~99% dos pixels com o mesmo tom.
  Nos canais bons, o máximo foi 29%;
- câmera IP que parou de mandar vídeo nem entrega foto (timeout ou erro) —
  isso é tratado por quem tira a foto, não aqui.

Imagem fora de foco, lente tapada com algo que não seja uniforme ou câmera
virada para o lado errado não são detectadas.
"""

from __future__ import annotations

import io

from PIL import Image, ImageStat, UnidentifiedImageError
from pydantic import BaseModel

# Tela chapada: fração de pixels a até ``_TOLERANCIA`` tons da média
LIMITE_CHAPADO = 0.95
_TOLERANCIA = 8
_TAMANHO_ANALISE = (176, 120)
_TAMANHO_MINIATURA = (320, 240)


class AnaliseImagem(BaseModel):
    """Números da foto (cinza, 0–255)."""

    brilho: float
    desvio: float
    chapado: float


def analisar(jpeg: bytes) -> AnaliseImagem:
    """Brilho, contraste e quanto da foto é do mesmo tom.

    Args:
        jpeg: Foto do canal.

    Returns:
        A análise.

    Raises:
        ValueError: O conteúdo não é uma imagem.
    """
    try:
        cinza = Image.open(io.BytesIO(jpeg)).convert("L").resize(_TAMANHO_ANALISE)
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("não é uma imagem") from exc
    stat = ImageStat.Stat(cinza)
    media = stat.mean[0]
    # Histograma em vez de pixel a pixel: conta os tons perto da média
    hist = cinza.histogram()
    perto = sum(hist[max(0, int(media) - _TOLERANCIA) : min(255, int(media) + _TOLERANCIA) + 1])
    return AnaliseImagem(brilho=round(media, 1), desvio=round(stat.stddev[0], 1), chapado=round(perto / sum(hist), 3))


def sem_imagem(analise: AnaliseImagem) -> bool:
    """A foto é uma tela chapada (preta, azul, cinza) em vez de uma cena?"""
    return analise.chapado >= LIMITE_CHAPADO


def miniatura(jpeg: bytes) -> bytes:
    """Foto reduzida (JPEG) para conferir na página."""
    imagem = Image.open(io.BytesIO(jpeg)).convert("RGB")
    imagem.thumbnail(_TAMANHO_MINIATURA)
    saida = io.BytesIO()
    imagem.save(saida, format="JPEG", quality=70)
    return saida.getvalue()
