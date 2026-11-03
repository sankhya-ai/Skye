# Skye on a VM of your own (built for Maritime, maritime.sh; any Docker host works).
# One image: Skye, git, the GitHub CLI and Claude Code. Everything that must survive sleep
# and redeploys - Skye's state, your repositories, and the Claude/GitHub sign-ins (they
# live under $HOME) - is kept in /data. See deploy/maritime/README.md.
FROM node:22-bookworm-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl git python3 ripgrep procps \
 && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list \
 && apt-get update && apt-get install -y --no-install-recommends gh \
 && rm -rf /var/lib/apt/lists/*

# Claude Code: the person's own install, signed in from chat with their own account.
RUN npm install -g --no-fund --no-audit @anthropic-ai/claude-code && claude --version

WORKDIR /app
COPY pyproject.toml LICENSE README.md ./
COPY skye ./skye
COPY deploy/maritime/start.py ./deploy/maritime/start.py

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    DISABLE_AUTOUPDATER=1
CMD ["python3", "/app/deploy/maritime/start.py"]
