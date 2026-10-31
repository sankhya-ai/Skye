#!/bin/sh
# Build the runtime as a static Linux binary (musl) for benchmark containers,
# on the host's architecture, into runtime/target-linux/release/kimi-agent.
# Alpine's Rust is musl-native, so nothing is cross-compiled and the binary
# runs on any task image (Ubuntu 22.04, Debian slim, Fedora, …).
set -eu
cd "$(dirname "$0")/.."
docker run --rm -v "$PWD/runtime:/src" -w /src -e CARGO_TARGET_DIR=/src/target-linux rust:1.93-alpine \
  sh -c 'apk add -q musl-dev >/dev/null && cargo build --release --bin kimi-agent'
file runtime/target-linux/release/kimi-agent
