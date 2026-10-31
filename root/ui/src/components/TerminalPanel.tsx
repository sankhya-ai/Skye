// The owner's terminal: a login shell in the session's folder (xterm.js over
// /api/sessions/{id}/terminal). Loaded on first open; kept alive while the
// drawer is hidden, so a running command isn't cut off.

import "@xterm/xterm/css/xterm.css";

import { FitAddon } from "@xterm/addon-fit";
import { Terminal, type ITheme } from "@xterm/xterm";
import { useEffect, useRef, useState } from "react";

import { bootstrap } from "../lib/api.ts";

function inkTheme(): ITheme {
  const css = getComputedStyle(document.documentElement);
  const v = (name: string) => css.getPropertyValue(name).trim();
  return { background: v("--raised"), foreground: v("--ink"), cursor: v("--ink"), cursorAccent: v("--raised"), selectionBackground: v("--selected") };
}

export default function TerminalPanel({ sessionId, hidden }: { sessionId: string; hidden: boolean }) {
  const host = useRef<HTMLDivElement>(null);
  const fitRef = useRef<() => void>(() => undefined);
  const termRef = useRef<Terminal | null>(null);
  const [status, setStatus] = useState<"connecting" | "open" | "closed">("connecting");
  const [generation, setGeneration] = useState(0);

  useEffect(() => {
    const element = host.current;
    if (!element) return;
    const css = getComputedStyle(document.documentElement);
    const term = new Terminal({
      fontFamily: css.getPropertyValue("--font-mono").trim() || "monospace",
      fontSize: 12.5,
      cursorBlink: true,
      scrollback: 5000,
      theme: inkTheme(),
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(element);
    termRef.current = term;
    let socket: WebSocket | null = null;
    let disposed = false;

    const sendSize = () => {
      if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
    };
    fitRef.current = () => {
      if (!element.offsetParent) return; // hidden: nothing to measure
      fit.fit();
      sendSize();
    };
    fitRef.current();

    void bootstrap().then(() => {
      if (disposed) return;
      const scheme = location.protocol === "https:" ? "wss" : "ws";
      socket = new WebSocket(`${scheme}://${location.host}/api/sessions/${sessionId}/terminal`);
      socket.binaryType = "arraybuffer";
      socket.onopen = () => {
        setStatus("open");
        sendSize();
        term.focus();
      };
      socket.onmessage = (event) => term.write(typeof event.data === "string" ? event.data : new Uint8Array(event.data as ArrayBuffer));
      socket.onclose = (event) => {
        setStatus("closed");
        if (event.code === 4403) term.write("\r\nThe terminal is only available on a local install.\r\n");
      };
    });
    const input = term.onData((data) => {
      if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "input", data }));
    });
    const resize = new ResizeObserver(() => fitRef.current());
    resize.observe(element);
    // Light and dark follow the app's own switch.
    const themes = new MutationObserver(() => (term.options.theme = inkTheme()));
    themes.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

    return () => {
      disposed = true;
      input.dispose();
      resize.disconnect();
      themes.disconnect();
      socket?.close();
      term.dispose();
    };
  }, [sessionId, generation]);

  useEffect(() => {
    if (hidden) return;
    requestAnimationFrame(() => {
      fitRef.current();
      termRef.current?.focus();
    });
  }, [hidden]);

  return (
    <div className="terminalPanel" hidden={hidden}>
      <div ref={host} className="terminalHost" />
      {status === "closed" ? (
        <div className="terminalBar">
          <span className="faint small">The shell has ended.</span>
          <button
            className="pill small"
            onClick={() => {
              setStatus("connecting");
              setGeneration((g) => g + 1);
            }}
          >
            New shell
          </button>
        </div>
      ) : null}
    </div>
  );
}
