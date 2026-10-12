FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy repository packages
COPY backend/packages /app/backend/packages
COPY backend/api /app/backend/api
COPY backend/workers /app/backend/workers
COPY pyproject.toml /app/

# Install all internal packages
RUN pip install --no-cache-dir \
    -e backend/packages/contracts \
    -e backend/packages/evidence \
    -e backend/packages/policy \
    -e backend/packages/orchestrator \
    -e backend/packages/adapter-base \
    -e backend/packages/adapter-http-api \
    -e backend/api \
    -e backend/workers/runner

EXPOSE 8000

CMD ["uvicorn", "uitp_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
