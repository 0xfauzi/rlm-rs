FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
ENV UV_CACHE_DIR=/tmp/uv-cache

WORKDIR /app

RUN python -m pip install --no-cache-dir uv

COPY . /app
RUN uv sync --frozen

CMD ["uv", "run", "python", "-m", "rlm_rs.worker_entrypoint"]
