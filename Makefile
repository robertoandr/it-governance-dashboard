# ============================================
# IT Gov Dashboard — Makefile
# Uso: make <comando>
# ============================================

.PHONY: help install install-dev test test-fast test-cov lint format \
        security audit clean run pre-commit setup all check

CYAN := \033[36m
GREEN := \033[32m
YELLOW := \033[33m
RESET := \033[0m

help: ## Mostra esta ajuda
	@echo ""
	@echo "$(CYAN)IT Governance Dashboard — Comandos$(RESET)"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$
' $(MAKEFILE_LIST) | \ 		awk 'BEGIN {FS = ":.*?## "}; {printf "  $(GREEN)%-18s$(RESET) %s\n",
$$1, $$2}'
	@echo ""

# ===== Instalação =====
install: ## Instala deps de produção
	pip install -r requirements.txt

install-dev: ## Instala deps de prod + dev
	pip install -r requirements.txt
	pip install -r requirements-dev.txt

setup: install-dev ## Setup completo (deps + pre-commit hooks)
	pre-commit install
	@echo "$(GREEN)✓ Setup completo!$(RESET)"

# ===== Testes =====
test: ## Roda todos os testes com cobertura
	pytest

test-fast: ## Roda testes em paralelo (sem cobertura)
	pytest -n auto --no-cov

test-cov: ## Roda testes e indica relatório HTML
	pytest
	@echo "$(CYAN)→ Relatório: htmlcov/index.html$(RESET)"

test-smoke: ## Roda apenas testes smoke
	pytest -m smoke

test-unit: ## Roda apenas testes unitários
	pytest -m unit

# ===== Qualidade =====
lint: ## Verifica código com ruff (sem alterar)
	ruff check .
	ruff format --check .

format: ## Formata código automaticamente
	ruff check --fix .
	ruff format .
	@echo "$(GREEN)✓ Código formatado$(RESET)"

typecheck: ## Verifica tipos com mypy
	mypy . --ignore-missing-imports

# ===== Segurança =====
security: ## Análise de segurança (bandit)
	bandit -c pyproject.toml -r . -ll

audit: ## Auditoria de vulnerabilidades
	pip-audit -r requirements.txt
	pip-audit -r requirements-dev.txt

# ===== Pipeline =====
check: lint security test ## Pipeline completo (CI local)
	@echo "$(GREEN)✓ Todos os checks passaram!$(RESET)"

all: format check ## Format + check completo

# ===== Pre-commit =====
pre-commit: ## Roda pre-commit em todos os arquivos
	pre-commit run --all-files

pre-commit-update: ## Atualiza versões dos hooks
	pre-commit autoupdate

# ===== Execução =====
run: ## Roda servidor Flask (dev)
	python app.py

run-prod: ## Roda com gunicorn (produção)
	gunicorn -w 4 -b 0.0.0.0:5000 app:app

# ===== Limpeza =====
clean: ## Remove caches e temporários
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -delete 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage coverage.xml
	@echo "$(GREEN)✓ Limpeza concluída$(RESET)"

clean-all: clean ## Remove tudo + venv
	rm -rf venv .venv
	@echo "$(YELLOW)⚠ venv removido. Execute 'make setup' novamente$(RESET)"
