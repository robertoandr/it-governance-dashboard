"""Credenciais dos usuários criados pelo teste de ponta a ponta."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Usuario:
    """E-mail e senha (aleatória por execução) de um usuário de teste."""

    email: str
    senha: str
