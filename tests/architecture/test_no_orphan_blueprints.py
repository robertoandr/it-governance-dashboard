"""Guard de arquitetura: detecta blueprints e namespaces órfãos.

Definição de "órfão":
- Flask Blueprint: registrado no app mas sem nenhuma rota em url_map.
- Flask-RESTX Namespace: adicionado à Api mas sem nenhum Resource registrado.

Ambos os casos indicam dead code ou artefato de refactor incompleto.
"""

from __future__ import annotations

import pytest
from flask import Blueprint, Flask

# ── Detection helpers (testáveis independentemente do app real) ────────────

_INTERNAL_BLUEPRINTS: frozenset[str] = frozenset({"restx_doc"})
_INTERNAL_NAMESPACES: frozenset[str] = frozenset({"default"})


def find_orphan_blueprints(app: Flask) -> list[str]:
    """Retorna nomes de blueprints registrados sem nenhuma rota.

    Args:
        app: Instância Flask com blueprints já registrados.

    Returns:
        Lista de nomes de blueprints órfãos (vazia se tudo OK).
    """
    return [
        name
        for name, _bp in app.blueprints.items()
        if name not in _INTERNAL_BLUEPRINTS
        and not any(r.endpoint.startswith(name + ".") for r in app.url_map.iter_rules())
    ]


def find_orphan_namespaces(api) -> list[str]:
    """Retorna nomes de namespaces Flask-RESTX sem nenhum Resource.

    Args:
        api: Instância flask_restx.Api com namespaces configurados.

    Returns:
        Lista de nomes de namespaces órfãos (vazia se tudo OK).
    """
    return [ns.name for ns in api.namespaces if ns.name not in _INTERNAL_NAMESPACES and not ns.resources]


# ── Unit tests das funções de detecção (caso negativo sintético) ───────────


class TestOrphanDetectionLogic:
    """Valida que as funções de detecção capturam casos órfãos reais."""

    def test_detects_blueprint_with_no_routes(self) -> None:
        """Blueprint registrado sem @bp.route() deve ser detectado."""
        test_app = Flask(__name__)
        orphan_bp = Blueprint("orphan_bp", __name__, url_prefix="/orphan")
        test_app.register_blueprint(orphan_bp)

        orphans = find_orphan_blueprints(test_app)
        assert "orphan_bp" in orphans

    def test_does_not_flag_blueprint_with_routes(self) -> None:
        """Blueprint com pelo menos 1 rota não deve ser flagado."""
        test_app = Flask(__name__)
        healthy_bp = Blueprint("healthy_bp", __name__, url_prefix="/healthy")

        @healthy_bp.route("/ping")
        def _ping():
            return "ok"

        test_app.register_blueprint(healthy_bp)

        orphans = find_orphan_blueprints(test_app)
        assert "healthy_bp" not in orphans

    def test_ignores_internal_restx_blueprint(self) -> None:
        """restx_doc é blueprint interno do Flask-RESTX — não deve ser flagado."""
        test_app = Flask(__name__)
        internal_bp = Blueprint("restx_doc", __name__, url_prefix="/swaggerui")
        test_app.register_blueprint(internal_bp)

        orphans = find_orphan_blueprints(test_app)
        assert "restx_doc" not in orphans

    def test_empty_app_has_no_orphans(self) -> None:
        """App sem blueprints registrados não deve ter órfãos."""
        test_app = Flask(__name__)
        assert find_orphan_blueprints(test_app) == []


# ── Integration tests contra o app real ───────────────────────────────────


@pytest.mark.integration
class TestNoOrphanBlueprintsInApp:
    """Garante que o app de produção não tem blueprints ou namespaces órfãos."""

    def test_no_orphan_blueprints(self) -> None:
        """Todo blueprint registrado deve ter ao menos 1 rota."""
        from app import app

        with app.app_context():
            orphans = find_orphan_blueprints(app)
            assert not orphans, (
                f"Blueprints registrados sem rotas detectados: {orphans}\n"
                "Verifique se o blueprint tem @bp.route() definidos e foi importado corretamente."
            )

    def test_no_orphan_restx_namespaces(self) -> None:
        """Todo namespace Flask-RESTX deve ter ao menos 1 Resource."""
        from app import itgov_api

        with itgov_api.app.app_context():
            orphans = find_orphan_namespaces(itgov_api)
            assert not orphans, (
                f"Namespaces Flask-RESTX sem endpoints detectados: {orphans}\n"
                "Verifique se o namespace tem @ns.route() + Resource e foi adicionado via add_namespace()."
            )

    def test_blueprint_count_matches_expectation(self) -> None:
        """Documentar blueprints conhecidos — falha se um novo for adicionado sem atualizar este teste.

        Ao adicionar um blueprint, incremente o contador E adicione-o ao set.
        Isso força revisão consciente de todo novo blueprint registrado.
        """
        from app import app

        known_blueprints = {
            "governance",
            "maintenance",
            "dashboards",  # added: Sprint 11 foundation-5-pillars (app/views/dashboards.py)
            "auth",  # added: Sprint 12 shell-v12 (app/auth/routes.py)
            "users",  # added: Sprint 12 shell-v12 (app/views/users.py)
            "restx_doc",  # internal Flask-RESTX
        }

        with app.app_context():
            registered = set(app.blueprints.keys())
            unknown = registered - known_blueprints
            assert (
                not unknown
            ), f"Novos blueprints detectados: {unknown}\nAdicione-os ao set known_blueprints neste teste após revisão."
