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
import re
from datetime import UTC, datetime

import structlog
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column

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


UFS: frozenset[str] = frozenset(
    [
        "AC",
        "AL",
        "AP",
        "AM",
        "BA",
        "CE",
        "DF",
        "ES",
        "GO",
        "MA",
        "MT",
        "MS",
        "MG",
        "PA",
        "PB",
        "PR",
        "PE",
        "PI",
        "RJ",
        "RN",
        "RS",
        "RO",
        "RR",
        "SC",
        "SP",
        "SE",
        "TO",
    ]
)

LOGO_MAX_BYTES = 512 * 1024

# Só formatos raster, reconhecidos pelos bytes iniciais. SVG fica de fora:
# pode carregar script e seria servido do nosso domínio.
_LOGO_ASSINATURAS: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
)


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
    cnpj: str = db.Column(db.String(18), nullable=False, default="", server_default="")
    endereco: str = db.Column(db.String(200), nullable=False, default="", server_default="")
    cep: str = db.Column(db.String(9), nullable=False, default="", server_default="")
    cidade: str = db.Column(db.String(120), nullable=False, default="", server_default="")
    uf: str = db.Column(db.String(2), nullable=False, default="", server_default="")
    # deferred: a listagem não precisa carregar os bytes de todos os logos
    logo: Mapped[bytes | None] = mapped_column(db.LargeBinary, nullable=True, deferred=True)
    logo_mime: str | None = db.Column(db.String(32), nullable=True)
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
    def local(self) -> str:
        """Cidade e UF para exibição, ex.: ``"Joinville/SC"`` (vazio sem cidade)."""
        if not self.cidade:
            return self.uf
        return f"{self.cidade}/{self.uf}" if self.uf else self.cidade

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
    # Nome exibido no lugar do nome do Zabbix (só admin altera)
    apelido: str = db.Column(db.String(120), nullable=False, default="", server_default="")
    # Gravador principal quando este é um duplicado (só admin marca); vazio = não é
    duplicado_de: str = db.Column(db.String(120), nullable=False, default="", server_default="")

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


def normalizar_cep(texto: str) -> str:
    """Valida um CEP e devolve no formato ``00000-000``.

    Args:
        texto: CEP digitado, com ou sem hífen/ponto.

    Returns:
        CEP formatado, ou string vazia quando nada foi informado.

    Raises:
        ValueError: Se não houver exatamente 8 dígitos.
    """
    digitos = re.sub(r"[\s.\-]", "", texto)
    if not digitos:
        return ""
    if not re.fullmatch(r"\d{8}", digitos):
        raise ValueError(f"CEP inválido: {texto.strip()}")
    return f"{digitos[:5]}-{digitos[5:]}"


def _digito_cnpj(base: str) -> str:
    pesos = list(range(len(base) - 7, 1, -1)) + list(range(9, 1, -1))
    resto = sum(int(d) * p for d, p in zip(base, pesos, strict=True)) % 11
    return "0" if resto < 2 else str(11 - resto)


def normalizar_cnpj(texto: str) -> str:
    """Valida um CNPJ (dígitos verificadores) e devolve ``00.000.000/0000-00``.

    Args:
        texto: CNPJ digitado, com ou sem pontuação.

    Returns:
        CNPJ formatado, ou string vazia quando nada foi informado.

    Raises:
        ValueError: Se não tiver 14 dígitos ou os dígitos verificadores não baterem.
    """
    digitos = re.sub(r"[\s./\-]", "", texto)
    if not digitos:
        return ""
    if not re.fullmatch(r"\d{14}", digitos) or len(set(digitos)) == 1:
        raise ValueError(f"CNPJ inválido: {texto.strip()}")
    base = digitos[:12]
    dv = _digito_cnpj(base)
    dv += _digito_cnpj(base + dv)
    if digitos[12:] != dv:
        raise ValueError(f"CNPJ inválido (dígito verificador): {texto.strip()}")
    return f"{digitos[:2]}.{digitos[2:5]}.{digitos[5:8]}/{digitos[8:12]}-{digitos[12:]}"


def normalizar_uf(texto: str) -> str:
    """Valida a sigla do estado (``sc`` → ``SC``); vazio é aceito.

    Raises:
        ValueError: Se a sigla não for uma UF brasileira.
    """
    uf = texto.strip().upper()
    if uf and uf not in UFS:
        raise ValueError(f"UF inválida: {texto.strip()}")
    return uf


def tipo_logo(dados: bytes) -> str:
    """Identifica o tipo do logo pelos bytes iniciais e confere o tamanho.

    Args:
        dados: Conteúdo do arquivo enviado.

    Returns:
        MIME type (``image/png``, ``image/jpeg`` ou ``image/webp``).

    Raises:
        ValueError: Arquivo vazio, maior que ``LOGO_MAX_BYTES`` ou de outro formato.
    """
    if not dados:
        raise ValueError("Arquivo de logo vazio.")
    if len(dados) > LOGO_MAX_BYTES:
        raise ValueError(f"Logo maior que {LOGO_MAX_BYTES // 1024} KB.")
    for assinatura, mime in _LOGO_ASSINATURAS:
        if dados.startswith(assinatura):
            return mime
    if dados[:4] == b"RIFF" and dados[8:12] == b"WEBP":
        return "image/webp"
    raise ValueError("Logo deve ser PNG, JPEG ou WebP.")


# Colunas criadas depois da tabela existir em produção: create_all() não
# altera tabelas existentes, então são adicionadas aqui (idempotente).
_COLUNAS_NOVAS: tuple[tuple[str, str], ...] = (
    ("cnpj", "VARCHAR(18) NOT NULL DEFAULT ''"),
    ("endereco", "VARCHAR(200) NOT NULL DEFAULT ''"),
    ("cep", "VARCHAR(9) NOT NULL DEFAULT ''"),
    ("cidade", "VARCHAR(120) NOT NULL DEFAULT ''"),
    ("uf", "VARCHAR(2) NOT NULL DEFAULT ''"),
    ("logo", "BLOB"),
    ("logo_mime", "VARCHAR(32)"),
)


_COLUNAS_NOVAS_DVR: tuple[tuple[str, str], ...] = (
    ("apelido", "VARCHAR(120) NOT NULL DEFAULT ''"),
    ("duplicado_de", "VARCHAR(120) NOT NULL DEFAULT ''"),
)


def garantir_colunas(engine: Engine | None = None) -> None:
    """Adiciona às tabelas ``unidades`` e ``dvr_unidades`` as colunas que faltam.

    Com vários workers subindo juntos, outro pode ter criado a coluna entre a
    inspeção e o ALTER; o erro de coluna duplicada é ignorado.

    Args:
        engine: Banco a migrar; padrão é o do Flask-SQLAlchemy.
    """
    from sqlalchemy.exc import OperationalError

    engine = engine or db.engine
    inspetor = inspect(engine)
    for tabela, colunas in (
        (Unidade.__tablename__, _COLUNAS_NOVAS),
        (DvrUnidade.__tablename__, _COLUNAS_NOVAS_DVR),
    ):
        if not inspetor.has_table(tabela):
            continue
        existentes = {c["name"] for c in inspetor.get_columns(tabela)}
        for nome, ddl in colunas:
            if nome in existentes:
                continue
            try:
                with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {tabela} ADD COLUMN {nome} {ddl}"))
                log.info("unidades.coluna_adicionada", tabela=tabela, coluna=nome)
            except OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
                log.info("unidades.coluna_concorrente_ignorada", tabela=tabela, coluna=nome)


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
