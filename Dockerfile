FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/usr/local/bin:${PATH}" \
    LANG=C.UTF-8

RUN useradd -r -u 10001 -m app && mkdir -p /app && chown -R app:app /app
WORKDIR /app

RUN python -m pip install --upgrade pip && \
    pip install \
      "PyYAML>=6.0.1,<7" \
      "PyMySQL>=1.1.1,<2" \
      "python-logging-loki>=0.3.1,<0.4" \
      "aiohttp>=3.10,<4" \
      "prometheus_client>=0.20,<1"

COPY --chown=app:app ./perf_digest2loki.py /app/perf_digest2loki.py
RUN printf 'name: perf-digest\nperiod: 120\nlisten_port: 3162\nmysql:\n  query: "SELECT 1 AS info"\n  log_column: info\n  extra_tags: []\n  instances: []\n' > /app/perf_digest2loki-config.yml

EXPOSE 3162
USER app

HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD python - <<'PY'\nimport urllib.request, sys\nurl='http://127.0.0.1:3162/metrics'\ntry:\n  with urllib.request.urlopen(url, timeout=2) as r:\n    sys.exit(0 if r.status==200 else 1)\nexcept Exception:\n  sys.exit(1)\nPY

CMD ["python", "perf_digest2loki.py"]