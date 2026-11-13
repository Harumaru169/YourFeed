FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# The subscription CLIs named in CLIS (claude, codex, gemini), with the Node.js that runs them.
# Without CLIS, /opt/clis stays empty and the image does not grow.
FROM node:22-slim AS clis
ARG CLIS=""
RUN mkdir -p /opt/clis/bin && packages="" \
 && for cli in $(echo "$CLIS" | tr ',' ' '); do \
      case "$cli" in \
        claude) packages="$packages @anthropic-ai/claude-code" ;; \
        codex) packages="$packages @openai/codex" ;; \
        gemini) packages="$packages @google/gemini-cli" ;; \
        *) echo "unknown CLI in YOURFEED_CLIS: $cli (use claude, codex, gemini)" >&2; exit 1 ;; \
      esac; \
    done \
 && if [ -n "$packages" ]; then \
      npm install -g --prefix /opt/clis --no-audit --no-fund $packages \
      && npm cache clean --force && cp /usr/local/bin/node /opt/clis/bin/node; \
    fi

FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:/opt/clis/bin:$PATH"
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY alembic.ini ./
COPY yourfeed ./yourfeed
COPY --from=web /web/dist ./web/dist
COPY --from=clis /opt/clis /opt/clis
# The CLIs keep their logins in the home directory, which compose keeps in a volume.
RUN useradd --system --uid 10001 --create-home --home-dir /home/yourfeed yourfeed
USER yourfeed
EXPOSE 8000
CMD ["uvicorn", "yourfeed.main:app", "--host", "0.0.0.0", "--port", "8000"]
