import { ArrowLeft, ArrowRight, Check, CheckCircle2, Loader2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, type R2Status } from "@/lib/api";
import type { ChamberSummary } from "@/lib/board";
import { Button } from "@/components/ui/button";
import { ChamberIcon, relayHost } from "@/components/ChamberSwitcher";
import { FileStorageSetup } from "@/components/FileStorageSetup";
import { cn } from "@/lib/utils";

// Every option is end-to-end encrypted: the relay, wherever it runs, stores only ciphertext.
// Chamber Cloud (we run the relay) isn't open yet: shown, not choosable.
const HOSTS = [
  { id: "cloudflare", title: "Your Cloudflare account", copy: "Free. Chamber sets up a relay in your Cloudflare account; you sign in to Cloudflare once in your browser." },
  { id: "server", title: "Your own server", copy: "Free. Run the relay on a VM or any Linux server, and keep every message and file there." },
  { id: "cloud", title: "Chamber Cloud", copy: "We run it for you, nothing to set up. Coming soon.", soon: true },
] as const;

// Creating on Cloudflare takes three steps after the name: sign in, file storage (optional), create.
const CF_STEPS = [
  { id: "cf-account", label: "Cloudflare" },
  { id: "cf-storage", label: "File storage" },
  { id: "cf-create", label: "Create" },
] as const;
type CfStep = (typeof CF_STEPS)[number]["id"];

// What deployRelay reports as it goes ("3/5 Deploying…"), in the words shown on the create step.
const DEPLOY_STAGES = ["Checking your Cloudflare sign-in", "Setting up file storage", "Deploying your relay", "Generating encryption keys", "Finishing setup on this Mac"];

const input = "w-full rounded-lg border bg-background p-2.5 text-sm outline-none focus:border-ring focus:ring-1 focus:ring-ring";

/**
 * Create or join a chamber. With chambers on this machine already it opens like Slack's launch page:
 * pick one of yours, or add another. Adding one from the app can go back to the one you were in.
 */
export function OnboardingView({ onConfigured, chambers = [], adding = false }: { onConfigured: () => void; chambers?: ChamberSummary[]; adding?: boolean }) {
  const [step, setStep] = useState<"choose" | "create" | "join" | CfStep>("choose");
  const [invite, setInvite] = useState("");
  const [handle, setHandle] = useState("");
  const [chamberName, setChamberName] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [project, setProject] = useState("");
  // The stage deployRelay has reached, from its "n/5 …" progress (its other notes don't move it).
  const [stage, setStage] = useState(0);
  // Where the chamber's relay runs: Chamber Cloud (hosted, a 7-day trial), your Cloudflare, or your server.
  const [host, setHost] = useState<"cloud" | "cloudflare" | "server">("cloudflare");
  const [relay, setRelay] = useState("");
  // Cloudflare: the account signed in to (null: not yet known), its R2, and whether to use it.
  const [account, setAccount] = useState<{ authenticated: boolean; account: string | null } | null>(null);
  const [r2, setR2] = useState<R2Status | null>(null);
  const [checking, setChecking] = useState(false);
  const [storage, setStorage] = useState(true);

  // The folder the app was started in becomes your first workspace; more are added in the app.
  useEffect(() => {
    api.detectHarnesses().then((res) => setProject(res.project || ""), () => undefined);
  }, []);

  // Going back abandons an attempt in flight, so its late result (or error) never shows on another screen.
  const attempt = useRef(0);
  const run = async (action: () => Promise<unknown>, fallback: string, then: () => void = onConfigured) => {
    const mine = ++attempt.current;
    setLoading(true);
    setError("");
    try {
      await action();
      if (mine === attempt.current) then();
    } catch (err: any) {
      if (mine === attempt.current) setError(err?.message || fallback);
    } finally {
      if (mine === attempt.current) setLoading(false);
    }
  };

  const join = (e: React.FormEvent) => {
    e.preventDefault();
    void run(() => api.joinOnboarding(invite.trim(), handle.trim() || undefined, project || undefined, chamberName.trim() || undefined), "That invite code didn't work. Ask your admin for a new one.");
  };

  // While a chamber is being created, show the step the server is on (sign-in, deploy, keys…).
  useEffect(() => {
    if (!loading || step !== "cf-create") return setStage(0);
    const poll = setInterval(() => api.cloudflareProgress().then((res) => {
      const n = Number(res.progress.match(/^(\d)\/\d/)?.[1] || 0);
      if (n) setStage((s) => Math.max(s, n));
    }, () => undefined), 1000);
    return () => clearInterval(poll);
  }, [loading, step]);

  // Each Cloudflare step checks where the account stands when it opens.
  useEffect(() => {
    if (step === "cf-account" && !account) api.cloudflareAuth().then(setAccount, () => setAccount({ authenticated: false, account: null }));
    if (step === "cf-storage" && !r2) checkR2();
  }, [step]);

  const checkR2 = () => {
    setChecking(true);
    api.r2Status().then(
      (status) => {
        setR2(status);
        setStorage(status.enabled);
      },
      (err) => setError(err?.message || "Couldn't check your Cloudflare account."),
    ).finally(() => setChecking(false));
  };

  const signIn = () =>
    void run(
      async () => setAccount(await api.cloudflareLogin()),
      "Cloudflare sign-in didn't finish. Try again.",
      () => undefined,
    );

  const create = (e: React.FormEvent) => {
    e.preventDefault();
    if (host === "cloud") {
      void run(() => api.newOnboarding("cloud", handle.trim() || undefined, project || undefined, chamberName.trim() || undefined), "Couldn't reach Chamber Cloud. Check your connection and try again.");
      return;
    }
    if (host === "server") {
      void run(() => api.newOnboarding(relay.trim(), handle.trim() || undefined, project || undefined, chamberName.trim() || undefined), "Couldn't reach that relay. Check the address and that the server is running.");
      return;
    }
    setError("");
    setStep("cf-account");
  };

  const deploy = () =>
    void run(() => api.cloudflareSetup(handle.trim() || undefined, project || undefined, storage, chamberName.trim() || undefined), "Couldn't create the chamber. Check your Cloudflare sign-in and try again.");

  // Back goes one step back; from something in flight (a sign-in, a deploy) it cancels that on the server
  // too, so it never finishes behind your back.
  const goBack = () => {
    if (loading && (step === "cf-create" || step === "cf-account")) void api.cloudflareCancel().catch(() => undefined);
    attempt.current++;
    setLoading(false);
    setError("");
    const previous = { "cf-account": "create", "cf-storage": "cf-account", "cf-create": "cf-storage" } as const;
    setStep(step in previous ? previous[step as CfStep] : "choose");
  };

  const back = (
    <button type="button" onClick={goBack} className="mb-4 flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
      <ArrowLeft className="size-3.5" /> Back
    </button>
  );

  const cfIndex = CF_STEPS.findIndex((s) => s.id === step);
  const stepper =
    cfIndex >= 0 ? (
      <ol className="mb-6 flex items-center gap-2">
        {CF_STEPS.map((s, i) => (
          <li key={s.id} className="flex flex-1 items-center gap-2">
            <span
              className={cn(
                "flex size-5 shrink-0 items-center justify-center rounded-full text-[10px] font-semibold",
                i < cfIndex ? "bg-primary text-primary-foreground" : i === cfIndex ? "border-2 border-primary text-foreground" : "border text-muted-foreground",
              )}
            >
              {i < cfIndex ? <Check className="size-3" /> : i + 1}
            </span>
            <span className={cn("text-xs", i === cfIndex ? "font-medium text-foreground" : "text-muted-foreground")}>{s.label}</span>
            {i < CF_STEPS.length - 1 ? <span className="h-px flex-1 bg-border" /> : null}
          </li>
        ))}
      </ol>
    ) : null;

  return (
    <div className="flex min-h-screen w-full items-center justify-center bg-background px-4 py-8">
      <div className="w-full max-w-md rounded-2xl border bg-card p-8 shadow-xl">
        <div className="mb-8 text-center">
          <h1 className="flex items-center justify-center gap-1.5 text-2xl font-bold tracking-tight">
            chamber<span className="inline-block size-2 rounded-full bg-dot" />
          </h1>
          <p className="mt-2 text-sm text-muted-foreground">Your agents and your teammates' agents, in one place.</p>
        </div>

        {stepper}

        {error ? <div className="mb-4 whitespace-pre-wrap break-words rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-xs text-destructive">{error}</div> : null}

        {step === "choose" && chambers.length && !adding ? (
          <div className="mb-6">
            <h2 className="text-sm font-semibold">Welcome back</h2>
            <p className="mt-0.5 text-xs text-muted-foreground">Choose a chamber to get started.</p>
            <div className="mt-3 divide-y overflow-hidden rounded-xl border">
              {chambers.map((c) => (
                <button
                  key={c.id}
                  type="button"
                  disabled={loading}
                  onClick={() => void run(() => api.switchChamber(c.id), "Couldn't open that chamber.")}
                  className="flex w-full items-center gap-3 p-3 text-left transition hover:bg-muted/40"
                >
                  <ChamberIcon name={c.name} size={36} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium">{c.name}</span>
                    <span className="block truncate text-xs text-muted-foreground">
                      {relayHost(c.relay)} · {c.owner}
                      {c.admin ? " (admin)" : ""}
                    </span>
                  </span>
                  <ArrowRight className="size-4 shrink-0 text-muted-foreground" />
                </button>
              ))}
            </div>
            <p className="mt-6 text-xs font-medium text-muted-foreground">Or add another chamber</p>
          </div>
        ) : null}

        {step === "choose" && adding ? (
          <button type="button" onClick={() => void run(() => api.cancelAddChamber(), "Couldn't go back.")} className="mb-4 flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
            <ArrowLeft className="size-3.5" /> Back to your chamber
          </button>
        ) : null}

        {step === "choose" ? (
          <div className="space-y-3">
            <button type="button" onClick={() => setStep("create")} className="w-full rounded-xl border p-4 text-left transition hover:bg-muted/40">
              <div className="font-semibold">Create a chamber</div>
              <p className="mt-1 text-xs text-muted-foreground">Start one for your team. You'll be its admin and can invite everyone.</p>
            </button>
            <button type="button" onClick={() => setStep("join")} className="w-full rounded-xl border p-4 text-left transition hover:bg-muted/40">
              <div className="font-semibold">Join a chamber</div>
              <p className="mt-1 text-xs text-muted-foreground">Your admin sent you an invite code. Paste it and you're in.</p>
            </button>
          </div>
        ) : null}

        {step === "join" ? (
          <form onSubmit={join} className="space-y-4">
            {back}
            <div>
              <label className="mb-1.5 block text-xs font-medium">Invite code</label>
              <textarea rows={3} required autoFocus value={invite} onChange={(e) => setInvite(e.target.value)} placeholder="https://…workers.dev/#…" className={`${input} font-mono text-xs`} />
            </div>
            <div>
              <label className="mb-1.5 block text-xs font-medium">Chamber name</label>
              <input value={chamberName} onChange={(e) => setChamberName(e.target.value)} maxLength={60} placeholder="Optional. What you'll see it as, e.g. Acme" className={input} />
            </div>
            <div>
              <label className="mb-1.5 block text-xs font-medium">Your name</label>
              <input value={handle} onChange={(e) => setHandle(e.target.value)} placeholder="Optional. Defaults to your git name" className={input} />
            </div>
            <Button type="submit" disabled={loading || !invite.trim()} className="h-10 w-full gap-2 text-sm">
              {loading ? <><Loader2 className="size-4 animate-spin" /> Joining…</> : <>Join chamber <ArrowRight className="size-4" /></>}
            </Button>
          </form>
        ) : null}

        {step === "create" ? (
          <form onSubmit={create} className="space-y-4">
            {back}
            <div>
              <label className="mb-1.5 block text-xs font-medium">Chamber name</label>
              <input autoFocus value={chamberName} onChange={(e) => setChamberName(e.target.value)} maxLength={60} placeholder="Your team or project, e.g. Acme" className={input} />
            </div>
            <div>
              <label className="mb-1.5 block text-xs font-medium">Your name</label>
              <input value={handle} onChange={(e) => setHandle(e.target.value)} placeholder="Optional. Defaults to your git name" className={input} />
            </div>
            <fieldset className="space-y-2" disabled={loading}>
              <legend className="mb-1.5 block text-xs font-medium">Where it runs</legend>
              {HOSTS.map((h) => (
                <label key={h.id} className={`flex gap-2.5 rounded-lg border p-3 text-left transition ${"soon" in h ? "cursor-default border-dashed opacity-60" : host === h.id ? "cursor-pointer border-primary bg-primary/5" : "cursor-pointer hover:bg-muted/40"}`}>
                  <input type="radio" name="host" className="mt-0.5" disabled={"soon" in h} checked={host === h.id} onChange={() => { setHost(h.id); setError(""); }} />
                  <span>
                    <span className="block text-sm font-medium">{h.title}</span>
                    <span className="mt-0.5 block text-xs leading-relaxed text-muted-foreground">{h.copy}</span>
                  </span>
                </label>
              ))}
            </fieldset>
            {host === "server" ? (
              <div>
                <label className="mb-1.5 block text-xs font-medium">Your relay's address</label>
                <input required autoFocus value={relay} onChange={(e) => setRelay(e.target.value)} placeholder="https://relay.example.com" className={`${input} font-mono text-xs`} />
                <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">
                  Run <code className="font-mono">vm.sh</code> on a Linux server or VM, or the Docker image, and paste the address it prints.
                </p>
              </div>
            ) : null}
            <Button type="submit" disabled={loading || (host === "server" && !relay.trim())} className="h-10 w-full gap-2 text-sm">
              {loading ? <><Loader2 className="size-4 animate-spin" /> Creating your chamber…</> : host === "cloudflare" ? <>Continue <ArrowRight className="size-4" /></> : <>Create chamber <ArrowRight className="size-4" /></>}
            </Button>
          </form>
        ) : null}

        {step === "cf-account" ? (
          <div className="space-y-4">
            {back}
            <div>
              <h2 className="text-sm font-semibold">Connect your Cloudflare account</h2>
              <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                Your chamber's relay runs in your own Cloudflare account, on the free plan. It only ever sees encrypted messages. No account yet? You can make a free one while signing in.
              </p>
            </div>
            {!account ? (
              <div className="flex items-center gap-2 rounded-lg border p-3 text-xs text-muted-foreground">
                <Loader2 className="size-3.5 animate-spin" /> Checking your Cloudflare sign-in…
              </div>
            ) : account.authenticated ? (
              <div className="flex gap-2.5 rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-3 text-xs">
                <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-600 dark:text-emerald-400" />
                <div>
                  <p className="font-medium text-foreground">Signed in to Cloudflare</p>
                  <p className="mt-0.5 text-muted-foreground">{account.account}</p>
                </div>
              </div>
            ) : loading ? (
              <div className="flex items-center gap-2 rounded-lg border p-3 text-xs text-muted-foreground">
                <Loader2 className="size-3.5 animate-spin" /> Finish signing in in the browser window that opened, then come back here.
              </div>
            ) : null}
            {account?.authenticated ? (
              <Button type="button" onClick={() => setStep("cf-storage")} className="h-10 w-full gap-2 text-sm">
                Continue <ArrowRight className="size-4" />
              </Button>
            ) : (
              <Button type="button" onClick={signIn} disabled={!account || loading} className="h-10 w-full gap-2 text-sm">
                {loading ? <><Loader2 className="size-4 animate-spin" /> Waiting for Cloudflare…</> : <>Sign in with Cloudflare <ArrowRight className="size-4" /></>}
              </Button>
            )}
          </div>
        ) : null}

        {step === "cf-storage" ? (
          <div className="space-y-4">
            {back}
            <div>
              <h2 className="flex items-center gap-2 text-sm font-semibold">
                File storage <span className="rounded-full border px-1.5 py-px text-[10px] font-medium text-muted-foreground">Optional</span>
              </h2>
              <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                Messages, code and small files (up to 64 KB) work without it. To share screenshots, logs and other files up to 25 MB, the relay stores them in R2, Cloudflare's file storage.
              </p>
            </div>
            <FileStorageSetup status={r2} checking={checking} onCheck={checkR2} />
            {r2?.enabled ? (
              <label className="flex cursor-pointer items-center gap-2.5 rounded-lg border p-3 text-xs">
                <input type="checkbox" checked={storage} onChange={(e) => setStorage(e.target.checked)} />
                <span className="text-foreground">Use R2 for files up to 25 MB</span>
              </label>
            ) : null}
            <Button type="button" onClick={() => setStep("cf-create")} disabled={!r2 && checking} className="h-10 w-full gap-2 text-sm">
              Continue <ArrowRight className="size-4" />
            </Button>
            {r2 && !r2.enabled ? (
              <button type="button" onClick={() => { setStorage(false); setStep("cf-create"); }} className="w-full text-center text-xs text-muted-foreground hover:text-foreground">
                Skip for now. You can turn it on later in Settings → Relay.
              </button>
            ) : null}
          </div>
        ) : null}

        {step === "cf-create" ? (
          <div className="space-y-4">
            {back}
            <div>
              <h2 className="text-sm font-semibold">Create your chamber</h2>
              <p className="mt-1 text-xs leading-relaxed text-muted-foreground">This takes about a minute.</p>
            </div>
            <dl className="divide-y rounded-lg border text-xs">
              {[
                ["Chamber", chamberName.trim() || "Named after its relay"],
                ["Admin", handle.trim() || "Your git name"],
                ["Cloudflare account", account?.account || "Signed in"],
                ["File storage", storage && r2?.enabled ? "Files up to 25 MB (R2)" : "Files up to 64 KB · turn on later in Settings"],
              ].map(([k, v]) => (
                <div key={k} className="flex justify-between gap-4 px-3 py-2.5">
                  <dt className="text-muted-foreground">{k}</dt>
                  <dd className="text-right font-medium text-foreground">{v}</dd>
                </div>
              ))}
            </dl>
            {loading ? (
              <ol className="space-y-1.5 rounded-lg border p-3 text-xs">
                {DEPLOY_STAGES.map((label, i) => {
                  const n = i + 1;
                  const skipped = n === 2 && !(storage && r2?.enabled);
                  return (
                    <li key={label} className={cn("flex items-center gap-2", n === stage ? "text-foreground" : "text-muted-foreground", skipped && "opacity-50")}>
                      {n < stage ? <Check className="size-3.5 text-emerald-600 dark:text-emerald-400" /> : n === stage ? <Loader2 className="size-3.5 animate-spin" /> : <span className="size-3.5" />}
                      {label}
                      {skipped ? " (skipped)" : ""}
                    </li>
                  );
                })}
              </ol>
            ) : null}
            <Button type="button" onClick={deploy} disabled={loading} className="h-10 w-full gap-2 text-sm">
              {loading ? <><Loader2 className="size-4 animate-spin" /> Creating your chamber…</> : <>Create chamber <ArrowRight className="size-4" /></>}
            </Button>
          </div>
        ) : null}
      </div>
    </div>
  );
}
