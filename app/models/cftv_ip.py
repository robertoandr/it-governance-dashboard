"""SQLAlchemy model das correções de IP de câmera feitas no Zabbix pelo agente (``cftv_ip_sync``)."""

from __future__ import annotations

from datetime import UTC, datetime

import structlog
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db

log = structlog.get_logger(__name__)


def _agora() -> datetime:
    return datetime.now(UTC)


class CftvIpCorrecao(db.Model):
    """Uma troca (ou tentativa) de IP de câmera no Zabbix, a partir do que o gravador usa no canal."""

    __tablename__ = "cftv_ip_correcoes"

    id: int = db.Column(db.Integer, primary_key=True)
    quando: datetime = db.Column(db.DateTime(timezone=True), nullable=False, default=_agora, index=True)
    hostid: str = db.Column(db.String(20), nullable=False, index=True)
    host: str = db.Column(db.String(128), nullable=False, default="")
    nome: str = db.Column(db.String(200), nullable=False, default="")
    gravador: str = db.Column(db.String(120), nullable=False, default="")
    canal: int = db.Column(db.Integer, nullable=False)
    ip_antigo: str = db.Column(db.String(45), nullable=False, default="")
    ip_novo: str = db.Column(db.String(45), nullable=False)
    fonte: str = db.Column(db.String(10), nullable=False, default="")
    situacao: str = db.Column(db.String(12), nullable=False)
    detalhe: str = db.Column(db.String(255), nullable=False, default="")

    def para_dict(self) -> dict[str, object]:
        """Formato da tela."""
        return {
            "quando": self.quando.replace(tzinfo=UTC) if self.quando and self.quando.tzinfo is None else self.quando,
            "host": self.host,
            "nome": self.nome,
            "gravador": self.gravador,
            "canal": self.canal,
            "ip_antigo": self.ip_antigo,
            "ip_novo": self.ip_novo,
            "fonte": self.fonte,
            "situacao": self.situacao,
            "detalhe": self.detalhe,
        }


def ultimas_correcoes(n: int = 15) -> list[dict[str, object]]:
    """Últimas correções registradas, para a tela. Requer app context.

    Fica no modelo (e não em ``cftv_ip_sync``) para a view não importar o agente,
    que importa a view: seria um ciclo.
    """
    try:
        linhas = CftvIpCorrecao.query.order_by(CftvIpCorrecao.quando.desc()).limit(n).all()
    except SQLAlchemyError as exc:
        log.warning("cftv_ip.consulta_falhou", erro=str(exc)[:200])
        return []
    return [r.para_dict() for r in linhas]
