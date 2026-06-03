"""Teste de arquitetura: nenhum módulo de produção usa os.environ[] diretamente.

Padrão estabelecido nos PRs #76 e #78: todo acesso a variáveis de ambiente
deve passar pelo config.py, que valida no startup e oferece mensagens claras.

Exceções documentadas:
- config.py: a única fonte autorizada de leitura de env vars
- tests/: fixtures de teste precisam setar env vars antes do import de config
- scripts/: scripts de diagnóstico utilitários (não produção)
"""

from __future__ import annotations

import ast
from pathlib import Path

# Raiz do projeto (dois níveis acima de tests/architecture/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Diretórios de produção a auditar
PRODUCTION_DIRS = [
    PROJECT_ROOT / "itgov",
    PROJECT_ROOT / "routes",
    PROJECT_ROOT / "services",
    PROJECT_ROOT / "app_utils.py",
]

# Arquivos e padrões explicitamente excluídos
ALLOWED_FILES = {
    PROJECT_ROOT / "config.py",  # única fonte autorizada
}
ALLOWED_PREFIXES = (
    str(PROJECT_ROOT / "tests"),
    str(PROJECT_ROOT / "scripts"),
    str(PROJECT_ROOT / "venv"),
    str(PROJECT_ROOT / ".venv"),
)


def _find_python_files(paths: list) -> list[Path]:
    """Encontra todos os .py nos caminhos dados, excluindo __pycache__."""
    result = []
    for path in paths:
        p = Path(path)
        if p.is_file() and p.suffix == ".py":
            result.append(p)
        elif p.is_dir():
            result.extend(f for f in p.rglob("*.py") if "__pycache__" not in str(f))
    return result


def _has_direct_environ_access(source: str) -> list[tuple[int, str]]:
    """Retorna (linha, código) para cada acesso os.environ["KEY"] ou os.environ['KEY']."""
    violations = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return violations

    for node in ast.walk(tree):
        # Detecta os.environ["KEY"] ou os.environ['KEY'] (Subscript com constante)
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Attribute)
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "os"
            and node.value.attr == "environ"
            and isinstance(node.slice, ast.Constant)
        ):
            violations.append((node.lineno, f"os.environ[{node.slice.value!r}]"))
    return violations


class TestNoOsEnvironDirect:
    """Garante que código de produção usa config.* em vez de os.environ[] direto."""

    def test_no_subscript_access_in_production_code(self):
        """Nenhum arquivo de produção deve usar os.environ['KEY'] ou os.environ["KEY"]."""
        violations_found: dict[str, list[tuple[int, str]]] = {}

        for py_file in _find_python_files(PRODUCTION_DIRS):
            if py_file in ALLOWED_FILES:
                continue
            if any(str(py_file).startswith(prefix) for prefix in ALLOWED_PREFIXES):
                continue

            source = py_file.read_text(encoding="utf-8")
            violations = _has_direct_environ_access(source)
            if violations:
                rel = str(py_file.relative_to(PROJECT_ROOT))
                violations_found[rel] = violations

        if violations_found:
            lines = ["", "os.environ[] direto detectado em código de produção:", ""]
            for filename, hits in sorted(violations_found.items()):
                for lineno, code in hits:
                    lines.append(f"  {filename}:{lineno}  →  {code}")
            lines.append("")
            lines.append("Substitua por config.<VARIAVEL> (ver config.py e PRs #76/#78).")
            assert False, "\n".join(lines)

    def test_migrated_modules_do_not_import_os(self):
        """Módulos migrados em #76/#78 não devem mais importar 'os'."""
        migrated = [
            PROJECT_ROOT / "itgov" / "api" / "v1" / "zabbix.py",
            PROJECT_ROOT / "itgov" / "api" / "v1" / "zendesk.py",
            PROJECT_ROOT / "itgov" / "services" / "zendesk_service.py",
            PROJECT_ROOT / "routes" / "maintenance.py",
        ]
        still_importing_os = []
        for path in migrated:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import | ast.ImportFrom) and isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "os":
                            still_importing_os.append(f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}")
        assert not still_importing_os, f"Módulos migrados ainda importam 'os': {still_importing_os}"
