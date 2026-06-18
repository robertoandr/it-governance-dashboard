# ─── Stage 1: builder ─────────────────────────────────────────────────────────
FROM python:3.12-slim-bookworm AS builder

WORKDIR /build

# Build-time deps for C extensions (ldap3, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libldap2-dev \
    libsasl2-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Build wheels into /install (isolated from system)
# Removes pip from runtime; strips __pycache__ to save space
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt && \
    pip install --no-cache-dir --prefix=/install gunicorn && \
    find /install -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true && \
    find /install -name "*.pyc" -delete 2>/dev/null || true


# ─── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM python:3.12-slim-bookworm AS runtime

LABEL org.opencontainers.image.title="Governança de TI Dashboard" \
      org.opencontainers.image.description="Dashboard COBIT 5 pilares — Governança de TI" \
      org.opencontainers.image.url="https://github.com/robertoandr/it-governance-dashboard" \
      org.opencontainers.image.source="https://github.com/robertoandr/it-governance-dashboard"

# Runtime libs required by ldap3 + curl for healthcheck
# apt-get upgrade patches fixable CVEs (e.g. libgnutls30 CVE-2026-33845)
RUN apt-get update && apt-get upgrade -y --no-install-recommends && \
    apt-get install -y --no-install-recommends \
        libldap-2.5-0 \
        libsasl2-2 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Remove pip from runtime image (not needed, saves ~7MB)
RUN pip uninstall -y pip setuptools wheel 2>/dev/null || true

# Non-root user UID/GID 1000
RUN groupadd --gid 1000 appgroup \
    && useradd --uid 1000 --gid 1000 --no-create-home --shell /bin/false appuser

WORKDIR /app

# Copy wheels from builder (no pip, no cache)
COPY --from=builder /install /usr/local

# Copy application code
COPY --chown=appuser:appgroup . .

# Writable runtime dirs (data, logs, tmp)
RUN mkdir -p /app/data /app/logs /app/tmp \
    && chown -R appuser:appgroup /app/data /app/logs /app/tmp

COPY --chown=appuser:appgroup docker/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

USER appuser

EXPOSE 5000

ENV FLASK_APP=app

# Healthcheck against the /health endpoint (factory app)
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fs http://localhost:5000/health || exit 1

# Entrypoint: flask db -> flask seed-admin -> gunicorn (factory pattern)
ENTRYPOINT ["/app/entrypoint.sh"]
