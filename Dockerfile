# CineCut web app for Render: Linux, CPU only, public mode (the website, library player, accounts, the creator upload
# flow and the partner API). The studio's GPU tools and local AI models stay on the studio PC.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    CINECUT_PUBLIC=1 CINECUT_LIBRARY_BUILDER=0
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .

# Render passes the port to listen on in $PORT.
CMD ["sh", "-c", "uvicorn backend.app:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips='*'"]
