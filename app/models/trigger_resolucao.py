"""SQLAlchemy model das marcações "resolvido" feitas na página /triggers.

A maioria dos triggers do Zabbix não permite fechamento manual
(``manual_close=0``): o problema só some lá quando a condição normaliza.
Quem marca como resolvido no dashboard registra aqui o evento, para que ele
saia da aba "Em aberto" e apareça em "Resolvidos" (com o selo "Zabbix ainda
ativo" enquanto o Zabbix não confirmar). Um novo disparo do mesmo trigger gera
outro ``eventid`` e volta para "Em aberto" sozinho.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.extensions import db


class TriggerResolucao(db.Model):
    """Marca de resolução de um evento de problema do Zabbix."""

    __tablename__ = "trigger_resolucoes"

    id: int = db.Column(db.Integer, primary_key=True)
    eventid: str = db.Column(db.String(20), nullable=False, unique=True, index=True)
    triggerid: str = db.Column(db.String(20), nullable=False, default="")
    host: str = db.Column(db.String(255), nullable=False, default="")
    name: str = db.Column(db.Text, nullable=False, default="")
    severity: int = db.Column(db.Integer, nullable=False, default=0)
    fechado_no_zabbix: bool = db.Column(db.Boolean, nullable=False, default=False)
    nota: str = db.Column(db.Text, nullable=False, default="")
    resolvido_por: str = db.Column(db.String(255), nullable=False)
    resolvido_em: datetime = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
