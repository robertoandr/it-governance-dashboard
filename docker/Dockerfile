# ─── Stage 1: builder ─────────────────────────────────────────────────────────
FROM python:3.12-slim-bookworm AS builder

WORKDIR /build

# Instala apenas o necessário para compilar dependências C (ldap3, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libldap2-dev \
    libsasl2-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Instala deps em /install (isolado do sistema)
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ─── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM python:3.12-slim-bookworm AS runtime

# Libdeps de runtime (ldap3 precisa de libldap em runtime)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libldap-2.5-0 \
    libsasl2-2 \
    && rm -rf /var/lib/apt/lists/*

# Non-root user (UID/GID 1000)
RUN groupadd --gid 1000 appgroup \
    && useradd --uid 1000 --gid 1000 --no-create-home --shell /bin/false appuser

WORKDIR /app

# Deps do builder
COPY --from=builder /install /usr/local

# Código da aplicação (chown para non-root)
COPY --chown=appuser:appgroup . .

# Diretórios graváveis para runtime (logs, cache)
RUN mkdir -p /app/tmp /app/logs \
    && chown -R appuser:appgroup /app/tmp /app/logs

USER appuser

EXPOSE 8080

# Health check via endpoint /api/health
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c \
        "import urllib.request; \
         r = urllib.request.urlopen('http://localhost:8080/api/health', timeout=4); \
         exit(0 if r.status == 200 else 1)" \
    || exit 1

# Gunicorn: 2 workers síncronos (app usa threads internas para refresh)
CMD ["python", "-m", "gunicorn", \
     "--bind", "0.0.0.0:8080", \
     "--workers", "2", \
     "--threads", "4", \
     "--timeout", "60", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "--log-level", "info", \
     "app:app"]
