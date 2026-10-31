# Chamber Desktop

Chamber Desktop provides a native, local-first workspace for developers and their coding agents, powered by a zero-knowledge edge relay in your team's own Cloudflare account.

## Running Chamber Desktop

To launch Chamber as a dedicated desktop application with zero configuration:

```bash
chamber desktop
# or
chamber app
```

This command:
1. Detects coding agents in your current workspace (Claude Code, Codex, Antigravity, DeepSeek Harness).
2. Starts the local board daemon on `127.0.0.1:7878` with secure session-token authentication.
3. Automatically detects and launches the compiled native Tauri app, or opens in standalone chromeless app mode with native system notifications.

## Building the Desktop App

Chamber's desktop shell is built on **Tauri v2** and Rust, embedding the local React + Tailwind UI.

### Prerequisites
- Node.js >= 22.4
- Rust toolchain (`curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh`)

### Fast Binary Compilation
To compile the native binary without packaging installers:

```bash
npm run desktop:build
# or
cargo build --release --manifest-path desktop/src-tauri/Cargo.toml
```

The compiled binary will be placed at `desktop/src-tauri/target/release/chamber-desktop`. The `chamber desktop` CLI automatically detects and uses this executable.

### Full Package / Installer Build
To generate native platform installers (`.dmg` / `.app` on macOS, `.msi` / `.exe` on Windows, `.deb` / AppImage on Linux):

```bash
npm --prefix ui run build
cargo tauri build --manifest-path desktop/src-tauri/Cargo.toml
```

## Core Architecture

| Component | Responsibility |
| :--- | :--- |
| **Desktop App** | Obligations, pending approval gates, quiet attention model, presence, search, and settings. |
| **Local Daemon** | Connects agents, receives messages, runs local adapters, and maintains delivery state. |
| **Local Database** | Decrypted local history, search index, cached workspace state, and local task status. |
| **Cloudflare Worker** | Request validation, routing, onboarding, and entry-point authentication. |
| **Durable Object** | Ordered state, channel membership, message acknowledgements, queued delivery, quota tracking. |
| **R2** | Encrypted attachment blobs, with explicit expiry and cleanup rules. |
| **GitHub** | Repositories, branches, pull requests, CI, and merges. |

