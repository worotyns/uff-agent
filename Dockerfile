FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    jq \
    ripgrep \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install core dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Discover and install per-skill dependencies
COPY scripts/build-requirements.py scripts/build-requirements.py
COPY skills/ skills/
RUN python scripts/build-requirements.py > /tmp/requirements-merged.txt \
    && pip install --no-cache-dir -r /tmp/requirements-merged.txt \
    && rm -rf /tmp/requirements-merged.txt

# Copy application code (skills stay in image for runtime loading)
COPY skills/ skills/
COPY agents/ agents/
COPY entities/ entities/
COPY src/ src/
COPY tests/ tests/
COPY main.py config.yaml SOUL.md welcome*.md mcp_servers.default.json ./

ENV AGENT_HOME=/app/brain
ENV PYTHONUNBUFFERED=1
ENV TZ=UTC

CMD ["python", "main.py"]
