# Skye on Maritime

Two ways to run Skye on a [Maritime](https://maritime.sh) VM, both from the repo-root `Dockerfile`.
Everything lives in `/data`, including `$HOME` (`/data/home`), so Claude Code's sign-in (`~/.claude`)
and GitHub's (`~/.config/gh`) survive sleep and redeploys.

## 1. Hosted: everyone who texts Skye gets their own machine

```
their phone ─▶ Keiki (iMessage, Telegram, ...) ─▶ skye-desk ─▶ their own Skye VM (POST /chat)
their VM's news (needs you / done) ─▶ skye-desk /notify ─▶ Keiki notice ─▶ their phone
```

- **The front desk** (`SKYE_ROLE=front-desk`, `skye/frontdesk.py`) is one public, always-on agent.
  - It claims turns from Keiki's inbox.
  - On a person's first message it creates their worker. The worker's Maritime `externalId` is a hash of the conversation.
  - It passes each message to the worker through Maritime's chat API, and sends the reply back.
  - It holds no one's credentials.
- **A worker** (`SKYE_ROLE=worker`, `skye/chat.py`) is a private agent that belongs to one person.
  - It answers Maritime's `POST /chat` with the same commands as the Telegram bot: `claude`, `github`, `repo`, a goal, `status`, `approve`, `pr`.
  - Anything it says later goes to the desk at `SKYE_DESK_NOTIFY_URL`, which carries a token signed for that worker only.

Desk variables:

| Variable | What |
|---|---|
| `SKYE_ROLE` | `front-desk` |
| `KEIKI_HARNESS_KEY` (secret) | the Keiki agent's harness API key (Harness → External → inbox mode) |
| `KEIKI_API_KEY` (secret), `KEIKI_AGENT_ID` | a Keiki developer key with `agents:write`, for news a worker sends when no conversation is open. Without them, news waits for the person's next message. |
| `MARITIME_API_KEY` (secret) | `maritime keys create --name skye-desk --scopes provision,deploy,secrets,manage` |
| `SKYE_WORKER_IMAGE` | the Skye image, by digest: `ghcr.io/maritime-sh/maritime-agent-builds@sha256:...` from a Skye build's log |
| `SKYE_DESK_URL` | the desk's own `publicUrl` |
| `SKYE_DESK_SECRET` (secret) | signs the workers' notify tokens; keep it stable |
| `SKYE_WORKER_IDLE_SECONDS` | how long an idle worker stays awake (default 3600) |

**Updating Skye for everyone:**
1. Build once, for example by redeploying a test agent.
2. Point the desk's `SKYE_WORKER_IMAGE` at the new image digest.

New workers start on the new image. Existing workers keep their image until they are redeployed.

## 2. Your own: one VM, your own Telegram bot

1. Create an agent from this repo and keep it private.
2. Make a bot with @BotFather and set its token:
   ```
   maritime agents env set <agent> SKYE_TELEGRAM_TOKEN=<token> --secret --reload
   maritime agents env set <agent> SKYE_TELEGRAM_PAIR_CODE=<a code you choose> --secret --reload
   ```
3. Send your bot `/start <code>`.

From then on the bot answers only you: `/claude`, `/github`, `/repo owner/name`, then any goal.
