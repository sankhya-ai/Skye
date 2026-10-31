# The chamber relay as a container: what Chamber Cloud runs on Maritime, and a way to self-host it
# anywhere that runs Docker. Keep /data on a volume: every chamber, message and file lives there.
#
#   docker build -t chamber-relay . && docker run -p 8787:8787 -v chamber:/data chamber-relay
FROM node:22-slim
WORKDIR /app
COPY relay/package.json relay/package-lock.json relay/
RUN npm --prefix relay ci --no-audit --no-fund && npm cache clean --force
COPY bin bin
COPY lib lib
COPY board board
COPY package.json package.json
COPY relay/src relay/src
COPY relay/wrangler.toml relay/serve.mjs relay/backup.sh relay/
ENV HOST=0.0.0.0 PORT=8787 CHAMBER_RELAY_DATA=/data/relay HOME=/tmp
EXPOSE 8787
CMD ["node", "relay/serve.mjs"]
