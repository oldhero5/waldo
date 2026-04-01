FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg libgl1 libglib2.0-0 curl && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .python-version ./
RUN uv sync --no-dev --group app --group labeler --no-install-project

COPY lib/ lib/
COPY app/ app/
COPY labeler/ labeler/
COPY trainer/ trainer/
COPY alembic.ini .
COPY alembic/ alembic/

COPY scripts/entrypoint-app.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
