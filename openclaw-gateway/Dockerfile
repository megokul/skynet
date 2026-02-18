FROM python:3.12-slim

LABEL maintainer="OpenClaw"
LABEL description="OpenClaw Gateway Server v2 — AI Development Platform"

WORKDIR /app

# Install system dependencies.
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (cache layer).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code.
COPY . .

# Create data directory for SQLite.
RUN mkdir -p /app/data

# Expose WebSocket (8765) and HTTP API (8766).
EXPOSE 8765 8766

# Health check via HTTP API.
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8766/status')" || exit 1

CMD ["python", "main.py"]
