# API and trusted control workers only. Never execute a customer repository here.
FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app
RUN python -m pip install --no-cache-dir uv==0.11.3 \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin aip

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY platform_app ./platform_app
COPY migrations ./migrations
COPY scripts ./scripts
COPY alembic.ini ./alembic.ini
RUN uv sync --frozen --no-dev \
    && mkdir -p /var/lib/aip/artifacts \
    && chown -R aip:aip /var/lib/aip

USER aip
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/v1/health', timeout=2)" || exit 1
CMD ["python", "-m", "uvicorn", "platform_app.api:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
