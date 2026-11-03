# One container = website + API + arduino-cli (AVR core only; no ESP32 core) + the avr8js simulator. Deployed to Cloud Run.

# 1. Build the website (the browser twin imports the same simulator modules as the backend test, from src/sim)
FROM node:22-slim AS web
WORKDIR /app/src/web
COPY src/web/package*.json ./
RUN npm ci
COPY src/web/ ./
COPY src/sim/ /app/src/sim/
RUN npm run build

# 2. Headless simulator (avr8js on Node), used by the API's simulation test
FROM node:22-slim AS sim
WORKDIR /sim
COPY src/sim/package*.json ./
RUN npm ci --omit=dev
COPY src/sim/ ./

# 3. API + compiler + simulator
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh | BINDIR=/usr/local/bin sh \
    && arduino-cli core update-index \
    && arduino-cli core install arduino:avr
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/siliconloop/ src/siliconloop/
RUN uv pip install --system --no-cache .
# Every library a part card allows, installed at build time so compiles never wait on downloads.
RUN python -m siliconloop.compiler --preinstall
COPY --from=web /app/src/web/dist /app/static
COPY --from=sim /usr/local/bin/node /usr/local/bin/node
COPY --from=sim /sim /app/sim
ENV STATIC_DIR=/app/static \
    SIM_DIR=/app/sim \
    COST_LOG=/data/costs.jsonl \
    BUILD_DIR=/data/builds \
    CACHE_DIR=/data/cache
CMD ["sh", "-c", "exec uvicorn siliconloop.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
