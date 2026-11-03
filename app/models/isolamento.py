"""SQLAlchemy model dos isolamentos pedidos à mão no módulo Cyber ("Isolar máquina").

Cada linha é uma máquina isolada a pedido de alguém, fora das propostas do
agente de incidentes. Só pelo Acronis: a máquina precisa ter incidente aberto
no EDR que oferece ``WORKLOAD_ISOLATE`` (ver ``app.services.isolamento_maquina``).
A coluna ``fortigate`` ficou da quarentena no FortiGate, que foi retirada.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.extensions import db

ACRONIS = "acronis"
MECANISMOS = (ACRONIS,)

# Situação do isolamento
ISOLADA = "isolada"
FALHOU = "falhou"
LIBERADA = "liberada"
STATUS = (ISOLADA, FALHOU, LIBERADA)


def _agora() -> datetime:
    return datetime.now(UTC)


class IsolamentoMaquina(db.Model):
    """Uma máquina isolada a pedido (pelo Acronis)."""

    __tablename__ = "cyber_isolamentos"

    id: int = db.Column(db.Integer, primary_key=True)
    alvo: str = db.Column(db.String(200), nullable=False)  # o que foi digitado
    maquina: str = db.Column(db.String(200), nullable=False, default="")
    ip: str = db.Column(db.String(45), nullable=False, default="", index=True)
    mecanismo: str = db.Column(db.String(12), nullable=False)
    fortigate: str = db.Column(db.String(60), nullable=False, default="")
    incident_id: str = db.Column(db.String(64), nullable=False, default="")
    customer_id: str = db.Column(db.String(64), nullable=False, default="")
    link: str = db.Column(db.String(500), nullable=False, default="")
    motivo: str = db.Column(db.String(500), nullable=False, default="")
    status: str = db.Column(db.String(12), nullable=False, default=ISOLADA, index=True)
    resultado: str = db.Column(db.String(500), nullable=False, default="")
    activity_id: str = db.Column(db.String(80), nullable=False, default="")
    acao_status: str = db.Column(db.String(40), nullable=False, default="")
    liberar_activity_id: str = db.Column(db.String(80), nullable=False, default="")
    liberar_status: str = db.Column(db.String(40), nullable=False, default="")
    pedido_por: str = db.Column(db.String(200), nullable=False, default="")
    isolado_em: datetime = db.Column(db.DateTime(timezone=True), nullable=False, default=_agora)
    liberado_por: str = db.Column(db.String(200), nullable=False, default="")
    liberado_em: datetime | None = db.Column(db.DateTime(timezone=True), nullable=True)

    def para_dict(self) -> dict[str, object]:
        """Formato da API e da tela."""
        return {
            "id": self.id,
            "alvo": self.alvo,
            "maquina": self.maquina,
            "ip": self.ip,
            "mecanismo": self.mecanismo,
            "fortigate": self.fortigate,
            "incident_id": self.incident_id,
            "link": self.link,
            "motivo": self.motivo,
            "status": self.status,
            "resultado": self.resultado,
            "acao_status": self.acao_status,
            "liberar_status": self.liberar_status,
            "pedido_por": self.pedido_por,
            "isolado_em": self.isolado_em.isoformat() if self.isolado_em else None,
            "liberado_por": self.liberado_por,
            "liberado_em": self.liberado_em.isoformat() if self.liberado_em else None,
        }
