# One image for the three processes (EPIC-11 spec, section 2.8).
# API:       python -m app.main   (the default command)
# Worker:    python -m app.worker
# Scheduler: python -m app.scheduler
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1

WORKDIR /srv

COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev

COPY app ./app

RUN useradd --system --uid 10001 tripmate
USER tripmate

EXPOSE 8000
CMD ["python", "-m", "app.main"]
