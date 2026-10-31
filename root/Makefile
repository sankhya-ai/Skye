# root — build, test, and run the three parts together.
#
#   runtime/  kimi-agent-rs (Rust): the agent loop, tools, Jev director
#   server/   tura_code (Python): sessions, approvals, Dhee memory, API
#   ui/       React/Vite: the Tura-styled web UI

PYTHON ?= python3
VENV := server/.venv
RUNTIME_BIN := runtime/target/release/kimi-agent

.PHONY: all build runtime server ui test test-runtime test-server test-ui run dev doctor clean

all: build

build: runtime server ui

runtime: $(RUNTIME_BIN)

$(RUNTIME_BIN): $(shell find runtime -name '*.rs' -not -path '*/target/*') runtime/Cargo.toml runtime/Cargo.lock
	cargo build --release --manifest-path runtime/Cargo.toml --bin kimi-agent

$(VENV)/bin/python:
	$(PYTHON) -m venv $(VENV)

server: $(VENV)/bin/python
	$(VENV)/bin/python -m pip install -q -e "./server[memory,dev]"

ui:
	cd ui && npm install --no-fund --no-audit && npm run build

test: test-runtime test-server test-ui

test-runtime:
	cargo test --manifest-path runtime/Cargo.toml --workspace

test-server: runtime server
	cd server && .venv/bin/python -m pytest -q

test-ui:
	cd ui && npm test && npm run typecheck

# The built UI served by the Python host on http://localhost:8787
run: build
	$(VENV)/bin/tura-code --open

# Hot-reloading UI on :5173 against the host on :8787
dev: runtime server
	./scripts/dev.sh

doctor: server
	$(VENV)/bin/tura-code doctor

clean:
	rm -rf ui/dist runtime/target
