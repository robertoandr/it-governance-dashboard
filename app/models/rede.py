"""SQLAlchemy model dos IPs vistos pela descoberta de rede.

A descoberta do Zabbix só diz quem responde agora; o dashboard guarda aqui
quando cada IP apareceu pela primeira vez (para destacar dispositivo novo) e a
sugestão da IA para os hosts que as regras não souberam classificar.

A primeira carga marca tudo como ``baseline``: o que já existia antes do
dashboard começar a olhar não é "novo".
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.extensions import db


class RedeVisto(db.Model):
    """Um IP visto pela descoberta de rede."""

    __tablename__ = "rede_vistos"

    ip: str = db.Column(db.String(45), primary_key=True)
    primeiro_visto: datetime = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    ultimo_visto: datetime = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    baseline: bool = db.Column(db.Boolean, nullable=False, default=False)
    # Sugestão da IA; ``ia_assinatura`` resume os sinais usados, para refazer só quando mudarem
    ia_tipo: str = db.Column(db.String(20), nullable=False, default="")
    ia_nome: str = db.Column(db.String(100), nullable=False, default="")
    ia_motivo: str = db.Column(db.String(255), nullable=False, default="")
    ia_assinatura: str = db.Column(db.String(64), nullable=False, default="")
    ia_em: datetime | None = db.Column(db.DateTime(timezone=True), nullable=True)


class ClassificacaoDispositivo(db.Model):
    """Classificação de dispositivo cadastrada na página Rede, além das nativas.

    ``palavras`` guarda, separadas por vírgula, os trechos que fazem um host
    descoberto cair nesta classificação sozinho (ver
    ``itgov.api.v1.rede_descoberta.aplicar_classificacoes``).
    """

    __tablename__ = "rede_classificacoes"

    chave: str = db.Column(db.String(20), primary_key=True)
    rotulo: str = db.Column(db.String(60), nullable=False)
    sigla: str = db.Column(db.String(4), nullable=False)
    palavras: str = db.Column(db.String(500), nullable=False, default="")
    criado_por: str = db.Column(db.String(255), nullable=False, default="")
    criado_em: datetime = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))

    @property
    def lista_palavras(self) -> tuple[str, ...]:
        """Palavras-chave sem espaços extras e sem itens vazios."""
        return tuple(p.strip() for p in self.palavras.split(",") if p.strip())


class RedeMonitoramento(db.Model):
    """Decisão sobre monitorar no Zabbix um IP descoberto (item r2).

    ``decisao``: ``auto`` (criado sozinho), ``confirmado`` (alguém escolheu o
    template), ``recusado`` (não monitorar) ou ``erro`` (a criação falhou;
    tenta de novo depois de um tempo).
    """

    __tablename__ = "rede_monitoramento"

    ip: str = db.Column(db.String(45), primary_key=True)
    decisao: str = db.Column(db.String(12), nullable=False)
    template: str = db.Column(db.String(120), nullable=False, default="")
    hostid: str = db.Column(db.String(20), nullable=False, default="")
    erro: str = db.Column(db.String(255), nullable=False, default="")
    por: str = db.Column(db.String(255), nullable=False, default="")
    em: datetime = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))


class RedeConfig(db.Model):
    """Preferências da página Rede (chave → valor)."""

    __tablename__ = "rede_config"

    chave: str = db.Column(db.String(40), primary_key=True)
    valor: str = db.Column(db.String(255), nullable=False, default="")
