# Throughline — universal AI CLI memory layer
# Supported sources: Claude Code, Cursor, Zed, Codex, Hermes, Continue, Cline, Windsurf, Vibe

FROM python:3.12-slim

ARG THROUGHLINE_UID=1000
ARG THROUGHLINE_GID=1000

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
    && (getent group "$THROUGHLINE_GID" || groupadd --gid "$THROUGHLINE_GID" throughline) \
    && useradd --create-home --uid "$THROUGHLINE_UID" --gid "$THROUGHLINE_GID" \
        --shell /usr/sbin/nologin throughline \
    && install -d -m 700 -o throughline -g throughline /var/lib/throughline/backups \
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
# docker-compose mounts the host tool directories under /home/throughline
# (read-only), e.g. ~/.claude -> /home/throughline/.claude.

ENV HOME=/home/throughline \
    THROUGHLINE_HOST=0.0.0.0 \
    THROUGHLINE_PORT=8787

USER throughline

EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
    CMD curl -sf http://localhost:8787/api/health || exit 1

CMD ["throughline", "serve"]
