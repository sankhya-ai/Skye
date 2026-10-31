# root on Maritime (maritime.sh): the runtime, the host and the built UI in one image.
# Maritime builds this from the repository root; state lives in /data, which survives
# sleep and redeploys. See "Run root on Maritime" in README.md.

FROM rust:1-bookworm AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends cmake clang && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY runtime ./runtime
RUN cargo build --release --manifest-path runtime/Cargo.toml --bin kimi-agent

FROM node:22-bookworm-slim AS ui
WORKDIR /src/ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci --no-fund --no-audit
COPY ui ./
RUN npm run build

FROM python:3.12-slim-bookworm
# ca-certificates: outbound HTTPS; git: checkpoints and repos; ripgrep: the runtime's Grep tool.
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates git ripgrep && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY agent ./agent
COPY server ./server
RUN python -m venv /app/server/.venv && /app/server/.venv/bin/pip install --no-cache-dir -e "./server[memory]"
COPY runtime/kimi-agent/src/skills ./runtime/kimi-agent/src/skills
COPY --from=runtime /src/runtime/target/release/kimi-agent ./runtime/target/release/kimi-agent
COPY --from=ui /src/ui/dist ./ui/dist
COPY scripts/maritime-start.py ./scripts/maritime-start.py
ENV TURA_CODE_HOME=/data/root \
    DHEE_MODELS_DIR=/data/models \
    TURA_CODE_MARITIME=1 \
    TURA_CODE_BOOTSTRAP=0 \
    PYTHONUNBUFFERED=1
CMD ["/app/server/.venv/bin/python", "/app/scripts/maritime-start.py"]
