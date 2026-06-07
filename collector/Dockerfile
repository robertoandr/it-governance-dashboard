FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates \
    && curl -sSL https://github.com/gitleaks/gitleaks/releases/download/v8.21.2/gitleaks_8.21.2_linux_x64.tar.gz \
       | tar -xz -C /usr/local/bin gitleaks \
    && apt-get purge -y curl \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

HEALTHCHECK --interval=5m --timeout=10s --start-period=30s --retries=2 \
    CMD python -c "import jobs.github_pr_collector, jobs.github_pats, jobs.gitleaks_scan, jobs.zabbix_collector" || exit 1

CMD ["python", "-u", "main.py"]
