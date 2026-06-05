# ADR-012 — SSO Test Isolation: Dynamic Env Check (Tech Debt)

**Status:** Accepted (tech debt)
**Date:** 2026-06-05
**Sprint:** 6 (SSO Microsoft Entra ID)

## Context

The `_require_login` `before_request` hook in `app.py` needs to be a no-op during
tests that don't concern authentication. The challenge is that Python caches
imported modules: once `config.py` is imported with `AZURE_SSO_ENABLED=True`, that
value is frozen even if env vars change later.

The cleaner solution would be an **app factory** (`create_app(testing=True)`) where
each test suite gets a fresh Flask app instance with its own config. However,
introducing an app factory requires refactoring all 514 existing tests that import
`from app import app` directly.

## Decision (Sprint 6 MVP)

The `before_request` hook checks `os.getenv("AZURE_*")` **at request time** rather
than `config.AZURE_SSO_ENABLED` (which is evaluated once at import). This means:

- The hook is always registered (no conditional `if SSO_ENABLED` at module level)
- At each request, it checks whether all three credentials are present in the env
- The `tests/auth/conftest.py` uses `monkeypatch.setenv` (function-scoped) so
  AZURE vars are set only for auth tests and removed immediately after

This approach works correctly and keeps all 514 tests green. It is slightly less
efficient per-request (one `os.getenv` triple-check vs. one bool lookup) but
negligible in practice.

## Tech Debt

The correct long-term fix is an app factory:

```python
# app_factory.py
def create_app(config_override: dict | None = None) -> Flask:
    app = Flask(__name__)
    cfg = {**load_config(), **(config_override or {})}
    app.config.from_mapping(cfg)
    if cfg.get("AZURE_SSO_ENABLED"):
        from itgov.auth.routes import auth_bp
        app.register_blueprint(auth_bp)
        register_auth_middleware(app)
    ...
    return app
```

Test fixtures would then use `create_app({"AZURE_SSO_ENABLED": False})` for
non-auth tests and `create_app({"AZURE_SSO_ENABLED": True, ...})` for auth tests,
eliminating the dynamic env-var check entirely.

**Tracking:** Implement app factory in a dedicated sprint (estimated 2-3h refactor).
