# Throughline — universal AI CLI memory layer
# Supported sources: Claude Code, Cursor, Zed, Codex, Hermes, Continue, Cline, Windsurf, Vibe

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# postgresql-client for psql/pg_isready, libpq + build tools for psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client \
    libpq-dev \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Requirements first to leverage the Docker layer cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN pip install -e .

# No Node stage: the built frontend is committed at throughline/web/ and ships
# inside the package, so the image never needs a JavaScript toolchain.

# Adapters resolve their source directories from $HOME at import time.
# docker-compose mounts the host tool directories under /root (read-only),
# e.g. ~/.claude -> /root/.claude, so discovery works unchanged in-container.

ENV THROUGHLINE_HOST=0.0.0.0 \
    THROUGHLINE_PORT=8787
# The API has no authentication, so binding a non-loopback address is refused
# unless this is set. Inside a container it *must* bind 0.0.0.0 or published
# ports cannot reach it — the isolation boundary is the container plus how the
# port is published. docker-compose publishes on 127.0.0.1 only; if you change
# that, put authentication in front of it first.
ENV THROUGHLINE_ALLOW_REMOTE=1

EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
    CMD curl -sf http://localhost:8787/api/health || exit 1

CMD ["throughline", "serve"]
