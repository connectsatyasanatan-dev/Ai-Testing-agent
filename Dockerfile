FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create dedicated non-root application user and group (Task P3-07 / Least Privilege)
RUN groupadd -g 10001 uitp \
    && useradd -u 10001 -g uitp -s /bin/sh -d /app uitp \
    && mkdir -p /app/artifacts \
    && chown -R uitp:uitp /app

# Copy repository packages
COPY --chown=uitp:uitp backend/packages /app/backend/packages
COPY --chown=uitp:uitp backend/api /app/backend/api
COPY --chown=uitp:uitp backend/workers /app/backend/workers
COPY --chown=uitp:uitp pyproject.toml /app/

# Install all internal packages
RUN pip install --no-cache-dir \
    -e backend/packages/contracts \
    -e backend/packages/evidence \
    -e backend/packages/policy \
    -e backend/packages/orchestrator \
    -e backend/packages/adapters/base \
    -e backend/packages/adapters/http_api \
    -e backend/packages/adapters/web \
    -e backend/api \
    -e backend/workers/runner

# Switch to non-root least-privilege user
USER 10001:10001

EXPOSE 8000

CMD ["uvicorn", "uitp_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
