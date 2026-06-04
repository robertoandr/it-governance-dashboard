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
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt && \
    pip install --no-cache-dir --prefix=/install gunicorn


# ─── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM python:3.12-slim-bookworm AS runtime

LABEL org.opencontainers.image.title="IT Governance Dashboard" \
      org.opencontainers.image.description="5-pillar COBIT governance dashboard" \
      org.opencontainers.image.url="https://github.com/robertoandr/it-governance-dashboard" \
      org.opencontainers.image.source="https://github.com/robertoandr/it-governance-dashboard"

# Runtime libs required by ldap3
RUN apt-get update && apt-get install -y --no-install-recommends \
    libldap-2.5-0 \
    libsasl2-2 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Non-root user UID/GID 1000
RUN groupadd --gid 1000 appgroup \
    && useradd --uid 1000 --gid 1000 --no-create-home --shell /bin/false appuser

WORKDIR /app

# Copy wheels from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY --chown=appuser:appgroup . .

# Writable runtime dirs (data, logs, tmp)
RUN mkdir -p /app/data /app/logs /app/tmp \
    && chown -R appuser:appgroup /app/data /app/logs /app/tmp

USER appuser

EXPOSE 5000

# Healthcheck against the /health endpoint added in the factory app
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fs http://localhost:5000/health || exit 1

# gunicorn: 4 workers × gthread, timeout 30s
ENTRYPOINT ["python", "-m", "gunicorn"]
CMD ["--bind", "0.0.0.0:5000", \
     "--workers", "4", \
     "--worker-class", "gthread", \
     "--threads", "2", \
     "--timeout", "30", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "--log-level", "info", \
     "wsgi:application"]
