# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /srv

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install ".[openai]"

COPY jobagent ./jobagent
COPY web ./web

RUN useradd -u 10001 -m jobagent && mkdir -p /srv/data && chown -R jobagent /srv
USER jobagent
ENV JOBAGENT_DSN=sqlite:////srv/data/jobagent.db

EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8090/healthz',timeout=2).status==200 else 1)"

CMD ["uvicorn", "jobagent.api:app", "--host", "0.0.0.0", "--port", "8090"]
