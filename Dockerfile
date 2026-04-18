FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    postgresql-client \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=3s --start-period=15s \
  CMD curl -sf http://localhost:8501/_stcore/health || exit 1

CMD ["streamlit", "run", "gui/app.py", \
     "--server.headless=true", \
     "--server.port=8501", \
     "--server.address=0.0.0.0"]
