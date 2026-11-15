# Build the same-origin web bundle for the API inside the reviewer container.
FROM node:22-bookworm-slim AS web
WORKDIR /web
ENV GIGBID_API_URL=http://127.0.0.1:8000 NEXT_TELEMETRY_DISABLED=1
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY apps/web/ ./
RUN npm run build

FROM caddy:2-alpine AS caddy
FROM ghcr.io/astral-sh/uv:0.11 AS uv

FROM python:3.12-slim-bookworm
ENV PATH="/app/.venv/bin:$PATH" \
    UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_HTTP_TIMEOUT=120 \
    PYTHONUNBUFFERED=1 NEXT_TELEMETRY_DISABLED=1 NODE_ENV=production
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libstdc++6 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
COPY --from=web /usr/local/bin/node /usr/local/bin/node
COPY --from=caddy /usr/bin/caddy /usr/local/bin/caddy
RUN node --version && caddy version

COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-install-project --no-dev
COPY . .

COPY --from=web /web/.next/standalone /opt/gigbid-web
COPY --from=web /web/.next/static /opt/gigbid-web/.next/static
COPY --from=web /web/public /opt/gigbid-web/public
COPY infra/reviewer/Caddyfile /etc/gigbid/Caddyfile

EXPOSE 10000
CMD ["python", "-m", "scripts.hosted_demo"]
