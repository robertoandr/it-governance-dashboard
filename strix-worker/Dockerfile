# Worker dedicado de pentest (Strix). Único container com acesso ao socket do
# Docker — o Strix sobe seus próprios containers-sandbox para rodar as varreduras.
FROM python:3.12-slim

# CLI do Docker (o daemon é o do host, via socket montado) + pipx para o Strix.
RUN apt-get update && apt-get install -y --no-install-recommends \
        docker-cli docker.io pipx ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="/root/.local/bin:${PATH}"
RUN pipx install strix-agent

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

CMD ["python", "-u", "worker.py"]
