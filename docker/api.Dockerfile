FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
ENV UV_CACHE_DIR=/tmp/uv-cache

WORKDIR /app

RUN python -m pip install --no-cache-dir uv

COPY . /app
RUN uv sync --frozen

ENV API_HOST=0.0.0.0
ENV API_PORT=8080

EXPOSE 8080

CMD ["sh", "-c", "exec uv run uvicorn rlm_rs.api.app:app --host ${API_HOST:-0.0.0.0} --port ${API_PORT:-8080}"]
