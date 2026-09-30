FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

FROM python:3.12-slim-bookworm
WORKDIR /app
RUN useradd -m -u 1000 appuser && mkdir -p /data && chown -R appuser:appuser /app /data
COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv
COPY --chown=appuser:appuser ./app ./app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 CREDENTIALS_PATH=/data/credentials.json
USER appuser
EXPOSE 8000
ENTRYPOINT ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
