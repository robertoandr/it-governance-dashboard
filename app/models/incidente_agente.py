"""SQLAlchemy model das propostas do agente de incidentes do módulo Cyber.

Para cada incidente do EDR do Acronis em aberto, o agente grava uma proposta:
encerrar, marcar como falso positivo, executar uma ação de resposta ou
deixar para alguém investigar. Nada é feito no Acronis sem aprovação: a
proposta só é executada quando um super admin aprova (direto ou pela fila de
aprovação), ver ``app.services.incidentes_agente``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from app.extensions import db

# Decisões que o agente pode propor
ENCERRAR = "encerrar"
FALSO_POSITIVO = "falso_positivo"
ACAO = "acao"
INVESTIGAR = "investigar"
DECISOES = (ENCERRAR, FALSO_POSITIVO, ACAO, INVESTIGAR)

# Situação da proposta
PROPOSTA = "proposta"  # esperando decisão
EXECUTADA = "executada"
FALHOU = "falhou"
REJEITADA = "rejeitada"
OBSOLETA = "obsoleta"  # o incidente foi resolvido/encerrado por fora antes da decisão
STATUS = (PROPOSTA, EXECUTADA, FALHOU, REJEITADA, OBSOLETA)


def _agora() -> datetime:
    return datetime.now(UTC)


class PropostaIncidente(db.Model):
    """O que o agente propõe fazer com um incidente do EDR."""

    __tablename__ = "cyber_propostas_incidente"

    id: int = db.Column(db.Integer, primary_key=True)
    incident_id: str = db.Column(db.String(64), nullable=False, unique=True)
    customer_id: str = db.Column(db.String(64), nullable=False)
    maquina: str = db.Column(db.String(200), nullable=False, default="")
    criado_no_acronis: str = db.Column(db.String(40), nullable=False, default="")
    atualizado_no_acronis: str = db.Column(db.String(40), nullable=False, default="")
    severidade: str = db.Column(db.String(20), nullable=False, default="")
    veredito: str = db.Column(db.String(20), nullable=False, default="")
    categorias: str = db.Column(db.String(300), nullable=False, default="")
    link: str = db.Column(db.String(500), nullable=False, default="")
    deteccoes_json: str = db.Column(db.Text, nullable=False, default="[]")
    acoes_oferecidas: str = db.Column(db.String(500), nullable=False, default="")
    decisao: str = db.Column(db.String(20), nullable=False, index=True)
    acao: str = db.Column(db.String(60), nullable=False, default="")
    justificativa: str = db.Column(db.String(800), nullable=False, default="")
    evidencias_json: str = db.Column(db.Text, nullable=False, default="[]")
    fonte: str = db.Column(db.String(10), nullable=False, default="regra")  # "regra" ou "ia"
    status: str = db.Column(db.String(12), nullable=False, default=PROPOSTA, index=True)
    resultado: str = db.Column(db.String(500), nullable=False, default="")
    activity_id: str = db.Column(db.String(80), nullable=False, default="")
    acao_status: str = db.Column(db.String(40), nullable=False, default="")
    analisado_em: datetime = db.Column(db.DateTime(timezone=True), nullable=False, default=_agora)
    decidido_por: str = db.Column(db.String(120), nullable=False, default="")
    decidido_em: datetime | None = db.Column(db.DateTime(timezone=True), nullable=True)

    @property
    def deteccoes(self) -> list[dict[str, object]]:
        """Detecções resumidas do incidente."""
        return json.loads(self.deteccoes_json or "[]")

    @property
    def evidencias(self) -> list[str]:
        """Evidências que sustentam a proposta."""
        return json.loads(self.evidencias_json or "[]")

    def para_dict(self) -> dict[str, object]:
        """Formato da API e da tela."""
        return {
            "id": self.id,
            "incident_id": self.incident_id,
            "maquina": self.maquina,
            "criado_no_acronis": self.criado_no_acronis,
            "severidade": self.severidade,
            "veredito": self.veredito,
            "categorias": [c for c in self.categorias.split(",") if c],
            "link": self.link,
            "deteccoes": self.deteccoes,
            "acoes_oferecidas": [a for a in self.acoes_oferecidas.split(",") if a],
            "decisao": self.decisao,
            "acao": self.acao,
            "justificativa": self.justificativa,
            "evidencias": self.evidencias,
            "fonte": self.fonte,
            "status": self.status,
            "resultado": self.resultado,
            "acao_status": self.acao_status,
            "analisado_em": self.analisado_em.isoformat() if self.analisado_em else None,
            "decidido_por": self.decidido_por,
            "decidido_em": self.decidido_em.isoformat() if self.decidido_em else None,
        }
