FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

FROM base AS dev
COPY requirements-dev.txt .
RUN pip install -r requirements-dev.txt
# Source is bind-mounted in docker-compose.yml; this copy keeps the image usable on its own.
COPY . .
