FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    wget \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps from both requirement files
COPY requirements.txt /tmp/root_requirements.txt
COPY backend/requirements.txt /tmp/backend_requirements.txt
RUN pip install --no-cache-dir \
    -r /tmp/root_requirements.txt \
    -r /tmp/backend_requirements.txt \
    python-dotenv

# Install Playwright + Chromium (used by the scraper module)
RUN playwright install chromium --with-deps

# Copy backend source — server.py expects CWD=/app with modules/ alongside it
COPY backend/ ./

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || curl -f http://localhost:8000/docs || exit 1

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
