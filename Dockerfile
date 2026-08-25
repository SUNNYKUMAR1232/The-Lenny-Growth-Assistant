# syntax=docker/dockerfile:1
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install -r /app/backend/requirements.txt

COPY backend /app/backend
COPY skills /app/skills
COPY data/transcripts /app/data/transcripts
COPY render-start.sh /app/render-start.sh

RUN chmod +x /app/render-start.sh \
    && useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app

ENV TRANSCRIPTS_DIR=/app/data/transcripts \
    SKILLS_DIR=/app/skills

USER appuser

EXPOSE 10000

CMD ["/app/render-start.sh"]
