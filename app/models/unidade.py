"""SQLAlchemy models de unidades (sites) da empresa e vínculo DVR → unidade.

Unidades formam uma árvore rasa: raízes como "Shopping" ou "Obras" e filhas
como "Obras / Residencial X". As faixas de IP de cada unidade serão usadas
pela descoberta de rede para classificar o site de um ativo encontrado.

Os DVRs/NVRs de CFTV vêm do Zabbix (tag ``dvr`` das câmeras ou host do
gravador); ``DvrUnidade`` guarda a qual unidade cada um pertence, editável
pela própria página /cftv.
"""

from __future__ import annotations

import ipaddress
from datetime import UTC, datetime

import structlog
from sqlalchemy.exc import IntegrityError

from app.extensions import db

log = structlog.get_logger(__name__)

UNIDADES_PADRAO: tuple[str, ...] = ("Shopping", "Autoshop", "Sede Centro", "Triunfo Fábrica", "Obras")

# DVRs cuja unidade é óbvia pelo nome; os demais ficam "Sem unidade" até
# alguém definir na página /cftv.
DVRS_PADRAO: dict[str, str] = {
    "Shopping 1": "Shopping",
    "Shopping novo": "Shopping",
    "Triunfo 2": "Triunfo Fábrica",
    "Triunfo 4": "Triunfo Fábrica",
    "DVR-1": "Sede Centro",
    "DVR-2": "Sede Centro",
    "DVR-3": "Sede Centro",
}


class Unidade(db.Model):
    """Unidade (site) da empresa, opcionalmente filha de outra unidade."""

    __tablename__ = "unidades"
    __table_args__ = (
        db.UniqueConstraint("parent_id", "nome", name="uq_unidade_parent_nome"),
        # NULLs não colidem no UNIQUE acima: raízes precisam de índice parcial próprio
        db.Index(
            "uq_unidade_raiz_nome",
            "nome",
            unique=True,
            sqlite_where=db.text("parent_id IS NULL"),
            postgresql_where=db.text("parent_id IS NULL"),
        ),
    )

    id: int = db.Column(db.Integer, primary_key=True)
    nome: str = db.Column(db.String(120), nullable=False)
    parent_id: int | None = db.Column(db.Integer, db.ForeignKey("unidades.id"), nullable=True)
    faixas_ip: str = db.Column(db.Text, nullable=False, default="")
    ativo: bool = db.Column(db.Boolean, nullable=False, default=True)
    created_at: datetime = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    updated_at: datetime = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    parent = db.relationship("Unidade", remote_side=[id], backref="filhas")

    @property
    def caminho(self) -> str:
        """Nome completo na hierarquia, ex.: ``"Obras / Residencial X"``."""
        return f"{self.parent.nome} / {self.nome}" if self.parent else self.nome

    @property
    def faixas(self) -> list[str]:
        """Faixas CIDR cadastradas, uma por item."""
        return parse_faixas(self.faixas_ip)

    def ids_subarvore(self) -> set[int]:
        """IDs desta unidade e de todas as descendentes."""
        ids = {self.id}
        for filha in self.filhas:
            ids |= filha.ids_subarvore()
        return ids


class DvrUnidade(db.Model):
    """Vínculo de um DVR/NVR (nome da tag ``dvr`` ou host) com uma unidade."""

    __tablename__ = "dvr_unidades"

    id: int = db.Column(db.Integer, primary_key=True)
    dvr: str = db.Column(db.String(120), nullable=False, unique=True)
    unidade_id: int | None = db.Column(db.Integer, db.ForeignKey("unidades.id", ondelete="SET NULL"), nullable=True)

    unidade = db.relationship("Unidade")


def parse_faixas(texto: str) -> list[str]:
    """Separa faixas por vírgula, espaço ou quebra de linha e normaliza.

    Args:
        texto: Faixas digitadas pelo usuário, ex.: ``"10.41.0.0/16, 172.29.0.0/22"``.

    Returns:
        Lista de redes em notação CIDR normalizada.

    Raises:
        ValueError: Se alguma faixa não for um endereço/rede IPv4 ou IPv6 válido.
    """
    faixas: list[str] = []
    for bruto in texto.replace(",", " ").split():
        try:
            rede = ipaddress.ip_network(bruto, strict=False)
        except ValueError as exc:
            raise ValueError(f"faixa de IP inválida: {bruto}") from exc
        if str(rede) not in faixas:
            faixas.append(str(rede))
    return faixas


def seed_unidades() -> None:
    """Cria as unidades e vínculos de DVR padrão quando a tabela está vazia.

    Idempotente: se já existe qualquer unidade, não faz nada — o cadastro
    passa a ser do usuário. Com vários workers subindo juntos, só o primeiro
    grava; os demais batem nos índices únicos e desistem sem erro.
    """
    if Unidade.query.first() is not None:
        return
    try:
        por_nome = {nome: Unidade(nome=nome) for nome in UNIDADES_PADRAO}
        db.session.add_all(por_nome.values())
        db.session.flush()
        for dvr, unidade in DVRS_PADRAO.items():
            if DvrUnidade.query.filter_by(dvr=dvr).first() is None:
                db.session.add(DvrUnidade(dvr=dvr, unidade_id=por_nome[unidade].id))
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        log.info("unidades.seed_concorrente_ignorado")
