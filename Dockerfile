# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first — the layer is cached until pyproject.toml changes.
COPY pyproject.toml README.md ./
RUN mkdir -p app && touch app/__init__.py && pip install --no-cache-dir .

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app

RUN adduser --disabled-password --gecos "" --uid 1000 swaplink \
    && mkdir -p /app/data \
    && chown -R swaplink:swaplink /app
USER swaplink

CMD ["python", "-m", "app"]
