# Trusted FFmpeg worker; customer source is never executed in this image.
FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir uv==0.11.3 \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin aip

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY platform_app ./platform_app
COPY scripts ./scripts
RUN uv sync --frozen --no-dev \
    && mkdir -p /var/lib/aip/artifacts \
    && chown -R aip:aip /var/lib/aip

USER aip
CMD ["python", "-m", "scripts.dispatch_hosted_media", "--serve"]
