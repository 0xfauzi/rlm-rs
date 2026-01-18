FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
ENV UV_CACHE_DIR=/tmp/uv-cache

WORKDIR /app

RUN python -m pip install --no-cache-dir uv

COPY . /app
RUN uv sync --frozen

ENV PARSER_HOST=0.0.0.0
ENV PARSER_PORT=8081

EXPOSE 8081

CMD ["sh", "-c", "exec uv run uvicorn rlm_rs.parser.service:app --host ${PARSER_HOST:-0.0.0.0} --port ${PARSER_PORT:-8081}"]
