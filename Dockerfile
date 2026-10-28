# syntax=docker/dockerfile:1
#
# GuideGen — FastAPI app image.
# Runtime state (SQLite + job artifacts) lives on the mounted volume at /app/data,
# never in the image: a rebuild must not wipe job history.
#
# Build:  docker build -t guidegen:local .
# Run:    docker compose up -d --build      (see docker-compose.yml)

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies in their own layer so editing application code does not re-run pip.
COPY requirements.txt ./
RUN pip install -r requirements.txt

# Application code, tests (the image doubles as its own verification harness),
# and the golden specs used by `python3 -m guidegen.verify`.
COPY guidegen ./guidegen
COPY tests ./tests
COPY data/golden ./data/golden

# Non-root runtime user. data/ is created here so a fresh named volume inherits
# this ownership; for a bind mount the deploy script chowns the host dir to match.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin guidegen \
 && mkdir -p /app/data/jobs \
 && chown -R guidegen:guidegen /app/data
USER guidegen

EXPOSE 8765

# stdlib-only probe: python:3.12-slim has no curl, and adding one for a healthcheck
# is not worth an extra package. urlopen raises on any non-2xx, so no status check.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=4).read()"]

# 0.0.0.0 inside the container only; the published port is bound to host loopback
# (docker-compose.yml) and Caddy is the only thing that talks to it.
CMD ["uvicorn", "guidegen.web.app:app", "--host", "0.0.0.0", "--port", "8765", "--no-server-header"]
