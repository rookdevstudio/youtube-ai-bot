FROM node:22-bookworm-slim AS node-runtime

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    BOT_DATA_DIR=/data \
    FORWARDED_ALLOW_IPS=*
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates libstdc++6 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY requirements.txt requirements-lock.txt ./
RUN pip install --no-cache-dir -r requirements-lock.txt && pip check

COPY main.py youtube_service.py ai_service.py media_tools.py settings_store.py runtime_config.py start.py ./
COPY templates/ ./templates/
RUN mkdir -p /data && ffmpeg -version && ffprobe -version && node --version
EXPOSE 8000
CMD ["python", "start.py"]
