FROM python:3.12-slim AS base

RUN pip install --no-cache-dir uv==0.11.16

WORKDIR /app
COPY agent/pyproject.toml agent/uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY agent/src ./src
RUN uv sync --frozen --no-dev

FROM python:3.12-slim

RUN useradd --create-home --uid 10001 appuser
WORKDIR /app
COPY --from=base /app/.venv /app/.venv
COPY --from=base /app/src /app/src
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

USER appuser
EXPOSE 8000

CMD ["uvicorn", "agent.main:app", "--host", "0.0.0.0", "--port", "8000"]
