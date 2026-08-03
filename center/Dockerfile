FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CENTER_DATABASE_PATH=/data/telemetry.sqlite3

WORKDIR /app

COPY requirements.txt /app/center/requirements.txt
RUN pip install --no-cache-dir -r /app/center/requirements.txt \
    && groupadd --system center \
    && useradd --system --gid center --home-dir /nonexistent center \
    && mkdir -p /data \
    && chown center:center /data

COPY --chown=center:center . /app/center

USER center:center
VOLUME ["/data"]
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=2).read()"]

CMD ["uvicorn", "center.app:app", "--host", "0.0.0.0", "--port", "8765", "--workers", "1"]
