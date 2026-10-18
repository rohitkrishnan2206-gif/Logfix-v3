# Stage 1: build the React UI
FROM node:22-slim AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# Stage 2: API + prebuilt UI
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 LOGFIX_DATA_DIR=/data HOST=0.0.0.0 PORT=8000
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 10001 logfix && mkdir /data && chown logfix /data
WORKDIR /srv
COPY backend/requirements.txt backend/
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/app backend/app
COPY sample_repo sample_repo
COPY --from=ui /ui/dist frontend/dist
USER logfix
WORKDIR /srv/backend
EXPOSE 8000
VOLUME /data
HEALTHCHECK CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health')" || exit 1
CMD ["python", "-m", "app"]
