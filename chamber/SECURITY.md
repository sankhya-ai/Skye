# Security Policy

Chamber connects coding agents across teammates' workstations through a zero-knowledge Cloudflare Worker relay. Security, end-to-end encryption, and local execution sovereignty are fundamental to its architecture.

---

## 1. Threat Model & Trust Boundaries

```
[ Developer Workstation A ]                         [ Developer Workstation B ]
┌─────────────────────────┐                         ┌─────────────────────────┐
│ • Claude Code / Codex   │                         │ • Claude Code / Codex   │
│ • Local Git repo        │                         │ • Local Git repo        │
│ • Chamber Board (127.0.0.1)                      │ • Chamber Board (127.0.0.1)
│ • Encryption Keys       │                         │ • Encryption Keys       │
└────────────┬────────────┘                         └────────────┬────────────┘
             │                                                   │
             │ Authenticated & Sealed (ChaCha20-Poly1305)         │
             ▼                                                   ▼
       ┌───────────────────────────────────────────────────────────────┐
       │               Cloudflare Edge Worker & Durable Object         │
       │               • Zero-Knowledge Relay                          │
       │               • Routes on envelope only (from, to, reply_to)  │
       │               • Encrypted R2 attachments (ephemeral retention)│
       │               • Durable ACP SQLite Coordination Store         │
       └───────────────────────────────────────────────────────────────┘
```

Chamber treats the Cloudflare relay as **untrusted for message content**. If the Cloudflare relay environment is compromised or monitored, the attacker learns only routing metadata (agent IDs, message timestamps, ciphertext sizes) and cannot read:
- Message bodies or conversation context
- File attachments or diffs
- Remote approval gate payloads
- Team memory documents

---

## 2. Cryptographic Architecture

### End-to-End Encryption (E2EE)
- **Symmetric Cipher**: ChaCha20-Poly1305 authenticated encryption with random nonces.
- **Key Derivation**: Client keys derived from the line's invite secret or personal admin pass.
- **Envelope / Content Separation**: The relay inspects only the routing envelope (`from`, `to`, `reply_to`, `seq`). The payload body is sealed client-side before dispatch.
- **Large Attachments**: Blobs exceeding inline message limits are encrypted client-side and streamed to Cloudflare R2 under an ephemeral storage lifecycle (deleted when the line session goes idle).

### Identity & Access Control
- **Admin Keys**: Ed25519 public key cryptography.
- **Personal Passes**: The admin signs time-bound, member-specific passes. Revocation orders signed by the admin key permanently revoke compromised members and prevent name reuse.
- **Workflow Signatures**: Autonomous workflows are signed by an admin Ed25519 private key and cryptographically bound to the specific `lineId`. Workflow content tampering or cross-chamber replay is rejected.

---

## 3. Local Machine & Mutation Security

The local board and MCP server run on `127.0.0.1`:

- **Host & Origin Validation**: Non-local Host headers are rejected (protection against DNS rebinding attacks).
- **Session Mutation Tokens**: All mutating HTTP requests (`POST`, `PUT`, `DELETE`) require an `x-chamber-token` matching the board's in-memory session token. Missing or incorrect tokens are rejected with `403 Forbidden`.
- **Project Boundary Traversal Protection**: MCP file access and attachment unpacking enforce strict realpath verification (`realpathSync`). Path traversal attempts (`../`, escape symlinks, absolute paths outside project root) are rejected.
- **Waking Agents (opt-in)**: The relay sends an owner's board a knock (message id, sender, recipient; never content) when a message waits for one of their agents that is not connected. The board wakes the agent only if the owner's per-agent rule (`~/.chamber/wake.json`) allows that sender: nobody but the owner by default. A woken agent runs headless in its own project with a fixed tool set: read-only plus the chamber tools to answer, or with file edits (no shell, no commit or push) for work. Its prompt names the sender and tells it to treat the message as a teammate's request that cannot change these rules. Limits: one run per agent at a time, 6 an hour per agent, 3 an hour per teammate, 20 minutes per run. Every wake and refusal is logged (`~/.chamber/wake-log.jsonl`) with the run's events (`~/.chamber/wakes/`).
- **Message Cursor Durability**: The client commit cursor advances only after the message handler finishes durable local persistence. Network dropouts or processing exceptions force WebSocket reconnection and replay, preventing dropped messages.

---

## 4. Private Channel Isolation

- Channels prefixed with `#p-` or `#private-` enforce strict server-side access control.
- Non-members cannot discover private channels in directory listings.
- Non-members cannot self-join existing private channels; membership requires an explicit add by an existing channel participant.

---

## 5. Agent Coordination Protocol (ACP) Correctness

- **Canonical State**: The Durable Object SQLite store acts as the single source of truth for coordination tasks across machines.
- **Deterministic State Machine**: State transitions (`requested` ➔ `claimed` ➔ `in_progress` ➔ `resolved` / `rejected` / `timed_out`) are validated on both relay and local managers. Terminal states are immutable.
- **Idempotency**: Requests specify client nonces and idempotency keys; duplicate submissions return existing tasks rather than re-executing actions.
- **Deadline Enforcement**: Tasks with elapsed deadlines automatically transition to `timed_out`.
- **Provenance Verification**: Task resolutions linking commits or schemas generate source-backed memory records capturing author identity and cryptographic timestamps.

---

## 6. Reporting a Vulnerability

If you discover a security vulnerability in Chamber, please disclose it responsibly:

1. **Email**: Send details to `security@turalearn.com`.
2. **Response Time**: We acknowledge receipt of vulnerability reports within 24 hours.
3. **Disclosure Policy**: We ask that you give us 14 days to resolve critical issues before any public disclosure.
