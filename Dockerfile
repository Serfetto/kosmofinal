# AquaFlow: сервис на готовых данных из репозитория (без скачивания снимков и обучения).
#   docker build -t aquaflow .
#   docker run -d -p 8000:8000 --name aquaflow aquaflow   →  http://localhost:8000

# ---------- фронтенд ----------
FROM node:20-alpine AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---------- сервис ----------
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# libgomp — OpenMP для xgboost/lightgbm; остальное (GDAL, PROJ) приходит в колёсах rasterio/pyproj
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# Точные версии, на которых получены метрики
COPY requirements.lock .
RUN pip install -r requirements.lock

RUN useradd --create-home --uid 1000 app

COPY configs configs
COPY pipeline pipeline
COPY backend backend
COPY --from=frontend /app/frontend/dist frontend/dist
# data/ пишется во время работы: кеш течений и зон скопления, сохранённые запросы
COPY --chown=app:app data data

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"

CMD ["python", "-m", "uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000"]
