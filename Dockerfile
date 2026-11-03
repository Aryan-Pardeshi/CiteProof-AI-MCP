FROM python:3.14-slim

# PYTHONUNBUFFERED matters here: buffered stdout stalls SSE streaming.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000 \
    RESEARCH_DB_PATH=/app/data/research_state.db \
    LLM_CACHE_PATH=/app/data/llm_cache.db \
    FIGURES_DIR=/app/data/figures \
    MPLCONFIGDIR=/tmp/matplotlib

WORKDIR /app

# curl is used by the container healthcheck
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend /app/backend
COPY frontend /app/frontend

# Checkpointer, LLM cache, and generated figures live here. Mount a volume over
# it, or all three are lost on every container rebuild.
# World-writable on purpose: Hugging Face Spaces runs the container as uid 1000,
# not root, so a directory owned by root at build time is unwritable at runtime
# and the SQLite checkpointer fails on first write.
RUN mkdir -p /app/data /app/data/figures && chmod -R 777 /app/data

EXPOSE 8000

# Shell form so $PORT is expanded at runtime: hosts like Hugging Face Spaces and
# FastAPI Cloud assign the port themselves.
CMD ["sh", "-c", "uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
