// Connections: the social accounts Skye can post to. Posting still needs an approved backlog
// item and an owner action; nothing here posts. Secrets are written once and never shown back.
import { useCallback, useEffect, useState } from "react";
import {
  checkSocial,
  connectSocial,
  disconnectSocial,
  getSocial,
  saveSocialApp,
  selectSocialPage,
  startSocialOauth,
  type SocialRow,
} from "../api";
import { humanError } from "../lib";
import { Button } from "./ui/Button";

const FIELD =
  "w-full rounded-md border border-input bg-background px-3 py-2 text-sm outline-none transition-colors focus:border-foreground/40";

const LABEL: Record<string, string> = {
  handle: "Handle",
  app_password: "App password",
  service: "Server (optional)",
  server: "Server",
  access_token: "Access token",
  author: "Author URN",
  page_id: "Page ID",
  page_token: "Page token",
  client_id: "Client ID",
  client_secret: "Client secret",
};
const SECRET = new Set(["app_password", "access_token", "page_token", "client_secret"]);

function expiry(row: SocialRow): string {
  if (!row.expires_at || row.refreshable) return ""; // refreshable tokens renew themselves
  const days = Math.round((row.expires_at * 1000 - Date.now()) / 86400000);
  if (days < 0) return "expired";
  return days < 1 ? "expires today" : `${days}d left`;
}

function Row({ row, onChanged }: { row: SocialRow; onChanged: () => void }) {
  const [open, setOpen] = useState(false);
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const needsApp = row.oauth && !row.app_configured;
  const formFields = needsApp ? ["client_id", "client_secret"] : row.oauth ? [] : row.fields;

  const run = async (key: string, fn: () => Promise<unknown>, okText = "") => {
    setBusy(key);
    setMsg(null);
    try {
      await fn();
      if (okText) setMsg({ ok: true, text: okText });
      onChanged();
    } catch (err) {
      setMsg({ ok: false, text: humanError(err, "action") });
    } finally {
      setBusy(null);
    }
  };

  const signIn = () =>
    run("connect", async () => {
      const { url } = await startSocialOauth(row.id);
      window.open(url, "_blank", "noopener");
      setOpen(false);
    });

  const submit = () =>
    run("connect", async () => {
      if (needsApp) {
        await saveSocialApp(row.id, values.client_id ?? "", values.client_secret ?? "");
        setValues({});
        const { url } = await startSocialOauth(row.id);
        window.open(url, "_blank", "noopener");
      } else {
        await connectSocial(row.id, values);
        setValues({});
      }
      setOpen(false);
    });

  const status = !row.implemented ? "Soon" : row.connected ? "Connected" : "Not connected";
  const exp = row.connected ? expiry(row) : "";

  return (
    <li className="space-y-3 px-4 py-3" title={row.review}>
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-sm">
          <span className="font-medium">{row.name}</span>
          <span
            className={`rounded-xs px-2 py-0.5 text-xs ${
              row.connected ? "bg-success/20 text-foreground" : "bg-muted text-muted-foreground"
            }`}
          >
            {status}
          </span>
          {exp ? <span className="text-xs text-muted-foreground">{exp}</span> : null}
          {row.last_check && !row.last_check.ok ? (
            <span className="text-xs text-destructive">check failed</span>
          ) : null}
        </div>
        {row.implemented ? (
          <div className="flex gap-2">
            {row.connected ? (
              <>
                <Button size="sm" variant="outline" loading={busy === "check"}
                  onClick={() => void run("check", () => checkSocial(row.id), "Account OK")}>
                  Check
                </Button>
                <Button size="sm" variant="ghost" loading={busy === "off"}
                  onClick={() => void run("off", () => disconnectSocial(row.id))}>
                  Disconnect
                </Button>
              </>
            ) : row.oauth && row.app_configured ? (
              <Button size="sm" loading={busy === "connect"} onClick={() => void signIn()}>
                Connect
              </Button>
            ) : (
              <Button size="sm" variant={open ? "outline" : "primary"} onClick={() => setOpen(!open)}>
                {open ? "Cancel" : "Connect"}
              </Button>
            )}
          </div>
        ) : null}
      </div>
      {row.connected && row.pages.length > 1 ? (
        <select
          className={FIELD}
          value={row.page_id ?? ""}
          onChange={(e) => void run("page", () => selectSocialPage(e.target.value))}
        >
          {row.pages.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      ) : null}
      {open && formFields.length ? (
        <div className="space-y-2">
          {formFields.map((f) => (
            <label key={f} className="block space-y-1 text-xs text-muted-foreground">
              {LABEL[f] ?? f}
              <input
                className={FIELD}
                type={SECRET.has(f) ? "password" : "text"}
                autoComplete="off"
                value={values[f] ?? ""}
                onChange={(e) => setValues((v) => ({ ...v, [f]: e.target.value }))}
              />
            </label>
          ))}
          <div className="flex justify-end">
            <Button size="sm" loading={busy === "connect"} onClick={() => void submit()}>
              {needsApp ? "Save and sign in" : "Save"}
            </Button>
          </div>
        </div>
      ) : null}
      {msg ? <p className={`text-xs ${msg.ok ? "text-muted-foreground" : "text-destructive"}`}>{msg.text}</p> : null}
    </li>
  );
}

export function ConnectionsView() {
  const [rows, setRows] = useState<SocialRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      setRows((await getSocial()).platforms);
      setError(null);
    } catch (err) {
      setError(humanError(err, "load"));
    }
  }, []);
  useEffect(() => {
    void load();
    const t = setInterval(() => void load(), 3000); // picks up a sign-in finished in another tab
    return () => clearInterval(t);
  }, [load]);
  return (
    <div className="space-y-4">
      <h2 className="text-lg font-medium">Connections</h2>
      {error ? <p className="text-xs text-destructive">{error}</p> : null}
      <ul className="divide-y divide-border rounded-xl border border-border bg-card/80 backdrop-blur">
        {rows.map((r) => (
          <Row key={r.id} row={r} onChanged={() => void load()} />
        ))}
      </ul>
    </div>
  );
}
