FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Europe/Berlin \
    DATA_DIR=/data \
    STATIC_DIR=/app/frontend

WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt

COPY backend/app backend/app
COPY frontend frontend

# Dateien für den Pi (Compose, Einrichtung, Updates). update.sh kopiert sie von hier.
COPY deploy deploy
COPY .env.example deploy/.env.example
ARG IMAGE_NAME=""
RUN chmod +x deploy/*.sh \
 && if [ -n "$IMAGE_NAME" ]; then sed -i "s#^IMAGE=.*#IMAGE=${IMAGE_NAME}#" deploy/.env.example; fi

RUN useradd --system --uid 1000 --create-home app && mkdir -p /data && chown app /data
USER app
WORKDIR /app/backend

EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
