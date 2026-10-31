import { useState } from "react";

import { api } from "../lib/api.ts";
import { Brand } from "./TuraBrand.tsx";

/** A hosted server's front door: the token its operator gave you. */
export function SignIn() {
  const [token, setToken] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <div className="signin">
      <form
        className="card signinCard"
        onSubmit={(e) => {
          e.preventDefault();
          setBusy(true);
          api.login(token.trim()).then(
            () => location.reload(),
            (err) => {
              setError(String(err.message));
              setBusy(false);
            },
          );
        }}
      >
        <Brand />
        <h1 className="greeting" style={{ fontSize: 28 }}>
          Sign in
        </h1>
        <p className="help">Paste the token you were given. It stays in this browser as a secure cookie.</p>
        <input className="input mono" type="password" autoComplete="current-password" placeholder="rt_…" value={token} onChange={(e) => setToken(e.target.value)} autoFocus />
        {error ? <p className="small" style={{ color: "var(--err)" }}>{error}</p> : null}
        <button className="pill ink" type="submit" disabled={busy || !token.trim()}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
