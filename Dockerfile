# -----------------------------------------------------------------------------
# Stage 1: build CSS Tailwind untuk dashboard.
# Kalau npm/build gagal, CSS lama yang sudah ada di static/ tetap dipakai.
# -----------------------------------------------------------------------------
FROM node:20-slim AS css

WORKDIR /css
COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund || true
COPY tailwind.config.js ./
COPY templates ./templates
COPY static ./static
RUN npm run build:css || echo "Peringatan: build Tailwind gagal, memakai CSS lama."

# -----------------------------------------------------------------------------
# Stage 2: aplikasi
# -----------------------------------------------------------------------------
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY templates ./templates
COPY static ./static
COPY --from=css /css/static/tailwind.css ./static/tailwind.css

RUN mkdir -p /data /secrets

ENV BOT_DATA_DIR=/data \
    BOT_TZ=Asia/Makassar

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4).status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
