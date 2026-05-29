"""Domain exceptions for the IT Governance Dashboard services."""


class FornecedorNotFound(Exception):
    """Raised when a fornecedor with the given ID does not exist or was deleted."""

    def __init__(self, fornecedor_id: object) -> None:
        super().__init__(f"Fornecedor '{fornecedor_id}' não encontrado")
        self.fornecedor_id = fornecedor_id


class FornecedorAlreadyExists(Exception):
    """Raised when a fornecedor with the same CNPJ already exists."""

    def __init__(self, cnpj: str) -> None:
        super().__init__(f"Fornecedor com CNPJ '{cnpj}' já cadastrado")
        self.cnpj = cnpj


class InvalidCNPJ(Exception):
    """Raised when a CNPJ fails structural or check-digit validation."""

    def __init__(self, cnpj: str, reason: str = "") -> None:
        msg = f"CNPJ inválido: '{cnpj}'"
        if reason:
            msg += f" — {reason}"
        super().__init__(msg)
        self.cnpj = cnpj


class ContratoNotFound(Exception):
    """Raised when a contrato with the given ID does not exist or was deleted."""

    def __init__(self, contrato_id: object) -> None:
        super().__init__(f"Contrato '{contrato_id}' não encontrado")
        self.contrato_id = contrato_id


class ContratoAlreadyExists(Exception):
    """Raised when a contrato with the same numero_contrato already exists."""

    def __init__(self, numero_contrato: str) -> None:
        super().__init__(f"Contrato com número '{numero_contrato}' já cadastrado")
        self.numero_contrato = numero_contrato
