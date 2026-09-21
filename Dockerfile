FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN addgroup --system mediadrop && adduser --system --ingroup mediadrop mediadrop \
    && mkdir -p /data/media /data/images /data/modern /data/legacy-media /data/legacy-images && chown -R mediadrop:mediadrop /data

COPY pyproject.toml README.md LICENSE /app/
COPY app /app/app
COPY scripts /app/scripts
RUN pip install --upgrade pip && pip install .

USER mediadrop
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3).read()"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips=*"]
