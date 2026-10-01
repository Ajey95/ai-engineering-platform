# Development fixture runner only. Customer repositories require a dedicated VM.
FROM python:3.12-slim-bookworm

RUN pip install --no-cache-dir \
    playwright==1.63.0 \
    fastapi==0.116.1 \
    uvicorn==0.35.0 \
    pydantic==2.11.7 \
    pytest==8.4.2 \
    httpx==0.28.1 \
    && python -m playwright install --with-deps chromium \
    && useradd -m -u 10001 pwuser

RUN mv /root/.cache/ms-playwright /ms-playwright \
    && chmod -R a+rX /ms-playwright
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

COPY platform_app/browser_runner.py /opt/platform/browser_runner.py
COPY platform_app/verifier.py /opt/platform/verifier.py
COPY platform_app/container_verifier.py /opt/platform/container_verifier.py
USER pwuser
WORKDIR /workspace
