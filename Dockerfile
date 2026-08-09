# Throughline Dockerfile
# Universal AI CLI memory layer
# Supports: Claude Code, Cursor, Zed, Codex, Hermes, Continue, Cline, Windsurf, Vibe

# Use official Python image with slim variant
FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install system dependencies for PostgreSQL and common tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    # PostgreSQL client
    postgresql-client \
    libpq-dev \
    # Build tools
    build-essential \
    # Utilities
    curl \
    git \
    # Cleanup
    && rm -rf /var/lib/apt/lists/*

# Create and set working directory
WORKDIR /app

# Copy requirements first to leverage Docker cache
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the entire application
COPY . .

# Create directories for vendor-specific data (mounted from host)
RUN mkdir -p \
    /claude-projects \
    /cursor-sessions \
    /zed-sessions \
    /codex-sessions \
    /hermes-sessions \
    /continue-sessions \
    /cline-tasks \
    /windsurf-plans \
    /vibe-sessions

# Expose ports
EXPOSE 8501  # Streamlit GUI
EXPOSE 8000  # MCP server (optional)

# Health check for GUI
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
    CMD curl -sf http://localhost:8501/_stcore/health || exit 1

# Default command: Run the Streamlit GUI
CMD ["streamlit", "run", "gui/app.py", \
     "--server.headless=true", \
     "--server.port=8501", \
     "--server.address=0.0.0.0"]

# Alternative: Run MCP server
# CMD ["python", "-m", "throughline.mcp"]
