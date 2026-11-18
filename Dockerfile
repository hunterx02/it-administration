# IT Administration — server image (VPS). Data, secrets key and encrypted secrets live in volumes, never in the image.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    ITADM_DATA_DIR=/data ITADM_SECRETS_PATH=/secrets/secrets.enc.json ITADM_FRONTEND_DIR=/app/frontend
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/app backend/app
COPY backend/templates backend/templates
COPY frontend frontend
RUN useradd --system --uid 10001 itadm && mkdir -p /data /secrets && chown itadm /data /secrets
USER itadm
WORKDIR /app/backend
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health')" || exit 1
CMD ["python", "-m", "uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
