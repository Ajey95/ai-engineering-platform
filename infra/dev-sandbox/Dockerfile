# Development fixture runner only. Customer repositories require a dedicated VM.
FROM mcr.microsoft.com/playwright/python:v1.63.0-noble

RUN pip install --no-cache-dir \
    fastapi==0.116.1 \
    uvicorn==0.35.0 \
    pydantic==2.11.7 \
    pytest==8.4.2 \
    httpx==0.28.1

COPY platform_app/browser_runner.py /opt/platform/browser_runner.py
USER pwuser
WORKDIR /workspace
