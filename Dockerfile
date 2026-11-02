# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY src/ src/
COPY data/ data/
COPY docs/sample_kb/ docs/sample_kb/

# Run as a non-root user
RUN useradd --create-home --uid 10001 bot && mkdir -p logs && chown -R bot:bot /app
USER bot

# Prometheus metrics (also satisfies Cloud Run's "must listen on $PORT" requirement)
EXPOSE 8000

# Default: real Slack bot. Override with "simulate" or "demo" for a no-Slack demo.
ENTRYPOINT ["python", "-m", "helpdesk_faq_bot"]
CMD ["slack"]
