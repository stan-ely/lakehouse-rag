# Query API image: FastAPI + fastembed (ONNX, CPU) on Python 3.12, matching local development.
FROM python:3.12-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON=/usr/local/bin/python3 \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH=/opt/venv/bin:$PATH \
    FASTEMBED_CACHE_PATH=/opt/fastembed \
    RAG_EMBED_MODEL=BAAI/bge-small-en-v1.5

COPY --from=ghcr.io/astral-sh/uv:0.12.13 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --only-group api --no-install-project --no-cache

RUN useradd --create-home --uid 10001 api

# Bake the embedding model in: containers start without network access to Hugging Face, and
# every replica embeds queries with exactly the model version the indexer used.
RUN python -c "import os; from fastembed import TextEmbedding; TextEmbedding(os.environ['RAG_EMBED_MODEL'])" \
    && chown -R api /opt/fastembed
ENV HF_HUB_OFFLINE=1

# The API reuses only the stdlib PII masker from the ingestion package.
COPY ingestion/__init__.py ingestion/pii.py ./ingestion/
COPY app ./app

USER api
EXPOSE 8000

# One worker: the ONNX model is loaded per process; scale with replicas, not workers.
CMD ["python", "-m", "uvicorn", "app.api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
