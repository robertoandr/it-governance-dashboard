"""SQLAlchemy models do agente SIEM do módulo Cyber.

- ``SiemEvento``: eventos de segurança lidos da memória dos FortiGate
  (guardados por ``RETENCAO_DIAS``; a memória do FortiGate gira em minutos).
- ``SiemAchado``: o que o agente concluiu (força bruta, admin falhando...),
  com evidência e recomendação. Repetições do mesmo achado somam em
  ``ocorrencias`` em vez de criar linhas novas.
- ``SiemAnalise``: cada rodada de análise (quando, quantos eventos, se a IA
  respondeu), para a tela mostrar quando o agente olhou pela última vez.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from app.extensions import db

NOVO = "novo"
VISTO = "visto"
DESCARTADO = "descartado"
RESOLVIDO = "resolvido"
STATUS_ACHADO = (NOVO, VISTO, DESCARTADO, RESOLVIDO)


def _agora() -> datetime:
    return datetime.now(UTC)


class SiemEvento(db.Model):
    """Um evento de log de segurança de um FortiGate."""

    __tablename__ = "siem_eventos"

    id: int = db.Column(db.Integer, primary_key=True)
    chave: str = db.Column(db.String(32), nullable=False, unique=True)
    fortigate: str = db.Column(db.String(40), nullable=False, index=True)
    quando: datetime = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    eventtime: int = db.Column(db.BigInteger, nullable=False)
    tipo: str = db.Column(db.String(20), nullable=False, default="")
    subtipo: str = db.Column(db.String(30), nullable=False, default="")
    nivel: str = db.Column(db.String(20), nullable=False, default="")
    descricao: str = db.Column(db.String(120), nullable=False, default="")
    origem_ip: str = db.Column(db.String(45), nullable=False, default="")
    usuario: str = db.Column(db.String(120), nullable=False, default="")
    acao: str = db.Column(db.String(40), nullable=False, default="")
    motivo: str = db.Column(db.String(80), nullable=False, default="")
    mensagem: str = db.Column(db.String(300), nullable=False, default="")


class SiemAchado(db.Model):
    """Um achado do agente SIEM."""

    __tablename__ = "siem_achados"
    __table_args__ = (db.UniqueConstraint("assinatura", "fortigate", name="uq_siem_achado"),)

    id: int = db.Column(db.Integer, primary_key=True)
    assinatura: str = db.Column(db.String(80), nullable=False)
    fortigate: str = db.Column(db.String(40), nullable=False)
    severidade: str = db.Column(db.String(10), nullable=False, index=True)
    titulo: str = db.Column(db.String(200), nullable=False)
    resumo: str = db.Column(db.String(600), nullable=False, default="")
    evidencias_json: str = db.Column(db.Text, nullable=False, default="[]")
    recomendacao: str = db.Column(db.String(600), nullable=False, default="")
    fonte: str = db.Column(db.String(10), nullable=False, default="regra")  # "regra" ou "ia"
    status: str = db.Column(db.String(12), nullable=False, default=NOVO, index=True)
    ocorrencias: int = db.Column(db.Integer, nullable=False, default=1)
    criado_em: datetime = db.Column(db.DateTime(timezone=True), nullable=False, default=_agora)
    visto_em: datetime = db.Column(db.DateTime(timezone=True), nullable=False, default=_agora, index=True)
    decidido_por: str = db.Column(db.String(120), nullable=False, default="")

    @property
    def evidencias(self) -> list[str]:
        """Evidências do achado (números, IPs, usuários)."""
        return json.loads(self.evidencias_json or "[]")

    def para_dict(self) -> dict[str, object]:
        """Formato da API e da tela."""
        return {
            "id": self.id,
            "assinatura": self.assinatura,
            "fortigate": self.fortigate,
            "severidade": self.severidade,
            "titulo": self.titulo,
            "resumo": self.resumo,
            "evidencias": self.evidencias,
            "recomendacao": self.recomendacao,
            "fonte": self.fonte,
            "status": self.status,
            "ocorrencias": self.ocorrencias,
            "criado_em": self.criado_em.isoformat() if self.criado_em else None,
            "visto_em": self.visto_em.isoformat() if self.visto_em else None,
            "decidido_por": self.decidido_por,
        }


class SiemAnalise(db.Model):
    """Uma rodada de análise do agente."""

    __tablename__ = "siem_analises"

    id: int = db.Column(db.Integer, primary_key=True)
    em: datetime = db.Column(db.DateTime(timezone=True), nullable=False, default=_agora, index=True)
    minutos: float = db.Column(db.Float, nullable=False, default=0)
    eventos: int = db.Column(db.Integer, nullable=False, default=0)
    achados: int = db.Column(db.Integer, nullable=False, default=0)
    ia: bool = db.Column(db.Boolean, nullable=False, default=False)
    erro: str = db.Column(db.String(300), nullable=False, default="")
