import {
  Bot,
  Check,
  Cloud,
  Copy,
  Download,
  Info,
  FolderGit2,
  KeyRound,
  Keyboard,
  Moon,
  Palette,
  Loader2,
  ShieldCheck,
  Sun,
  User,
} from "lucide-react";
import { useEffect, useState } from "react";
import { api, type QuotaInfo, type R2Status, type StorageInfo } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { FileStorageSetup } from "@/components/FileStorageSetup";
import { KIND, type BoardData } from "@/lib/board";
import { cn } from "@/lib/utils";

const STATUS_PRESETS = [
  { emoji: "💬", text: "In a code review" },
  { emoji: "⚡", text: "Deep focus" },
  { emoji: "🌴", text: "Away" },
  { emoji: "🎧", text: "Coding" },
  { emoji: "🚀", text: "Shipping release" },
];

function SettingsSectionHeader({
  title,
  description,
  action,
}: {
  title: React.ReactNode;
  description?: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-4 pb-1">
      <div>
        <h2 className="text-base font-semibold tracking-tight text-foreground">{title}</h2>
        {description && <p className="text-xs text-muted-foreground mt-0.5">{description}</p>}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}

function SettingsOptionGroup({
  title,
  children,
}: {
  title?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      {title && (
        <div className="px-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/75">
          {title}
        </div>
      )}
      <div className="divide-y divide-border/60 overflow-hidden rounded-xl border border-border/80 bg-card shadow-xs">
        {children}
      </div>
    </div>
  );
}

function SettingsOptionRow({
  label,
  description,
  children,
  className,
  stacked = false,
}: {
  label: React.ReactNode;
  description?: React.ReactNode;
  children?: React.ReactNode;
  className?: string;
  /** Wide controls (a row of chips) go under the label, not beside it, so neither gets squeezed. */
  stacked?: boolean;
}) {
  return (
    <div className={cn("flex min-h-[50px] gap-6 px-4 py-3 text-xs", stacked ? "flex-col items-stretch gap-2.5" : "items-center justify-between", className)}>
      <div className="min-w-0 flex-1">
        <p className="text-xs font-medium text-foreground">{label}</p>
        {description && <p className="text-[11px] text-muted-foreground mt-0.5 leading-normal">{description}</p>}
      </div>
      {children && <div className={stacked ? "min-w-0" : "shrink-0"}>{children}</div>}
    </div>
  );
}

function ToggleSwitch({
  checked,
  onChange,
  disabled = false,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        "relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50",
        checked ? "bg-primary" : "bg-muted-foreground/25"
      )}
    >
      <span
        className={cn(
          "pointer-events-none inline-block size-4 transform rounded-full bg-background shadow-sm ring-0 transition duration-200 ease-in-out",
          checked ? "translate-x-4" : "translate-x-0"
        )}
      />
    </button>
  );
}

function SegmentedControl<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T;
  options: { label: string; value: T; icon?: React.ReactNode }[];
  onChange: (val: T) => void;
}) {
  return (
    <div className="inline-flex rounded-lg border bg-muted/40 p-0.5 text-xs">
      {options.map((opt) => (
        <button
          key={opt.value}
          type="button"
          onClick={() => onChange(opt.value)}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium transition-all",
            value === opt.value
              ? "bg-background text-foreground shadow-xs font-semibold"
              : "text-muted-foreground hover:text-foreground"
          )}
        >
          {opt.icon}
          {opt.label}
        </button>
      ))}
    </div>
  );
}

export function SettingsDialog({
  open,
  onOpenChange,
  data,
  me,
  onChanged,
  dark = false,
  onToggleTheme,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  data: BoardData | null;
  me: string;
  onChanged: () => void;
  dark?: boolean;
  onToggleTheme?: (dark: boolean) => void;
}) {
  const [tab, setTab] = useState<"profile" | "appearance" | "relay" | "agents" | "security" | "shortcuts" | "about">("profile");
  // About: the Mac app checks for updates itself (Tauri's updater); run from a clone, git pull updates it.
  const [appVersion, setAppVersion] = useState<string | null>(null);
  const [updateState, setUpdateState] = useState<"idle" | "checking" | "current" | "installed" | "error">("idle");
  const [updateNote, setUpdateNote] = useState("");
  const [statusEmoji, setStatusEmoji] = useState("💬");
  const [statusText, setStatusText] = useState("");
  const [quota, setQuota] = useState<QuotaInfo | null>(null);
  const [copiedInvite, setCopiedInvite] = useState(false);
  const [confirmLogout, setConfirmLogout] = useState(false);
  const [copiedKey, setCopiedKey] = useState(false);
  const [copiedRelay, setCopiedRelay] = useState(false);
  const [inviteCommand, setInviteCommand] = useState("");
  const [savedNote, setSavedNote] = useState("");
  const [compactTimeline, setCompactTimeline] = useState(false);
  const [alertGates, setAlertGates] = useState(true);
  const [alertObligations, setAlertObligations] = useState(true);
  const [defaultHarness, setDefaultHarness] = useState<"claude" | "antigravity" | "codex">("claude");
  const [approvalGateEnabled, setApprovalGateEnabled] = useState(true);
  // File storage (R2): whether the relay has it, and the admin's flow for turning it on.
  const [storage, setStorage] = useState<StorageInfo | null>(null);
  const [storageSetup, setStorageSetup] = useState(false);
  const [r2, setR2] = useState<R2Status | null>(null);
  const [r2Checking, setR2Checking] = useState(false);
  const [storageBusy, setStorageBusy] = useState("");
  const [storageError, setStorageError] = useState("");

  useEffect(() => {
    if (open) {
      api.getUserStatus().then(
        (s) => {
          if (s.emoji) setStatusEmoji(s.emoji);
          if (s.text) setStatusText(s.text);
        },
        () => undefined
      );
      api.quota().then((q) => setQuota(q), () => undefined);
      api.storage().then(setStorage, () => undefined);
      if (data?.admin) {
        api.invite().then(
          (res: { command: string; code?: string }) => setInviteCommand(res.code || res.command),
          () => undefined
        );
      }
    }
  }, [open, data?.admin]);

  const tauri = (window as any).__TAURI__?.core;
  useEffect(() => {
    if (open && tauri) tauri.invoke("app_version").then(setAppVersion, () => undefined);
  }, [open]);

  const checkForUpdates = async () => {
    setUpdateState("checking");
    setUpdateNote("");
    try {
      const result: string = await tauri.invoke("check_for_updates");
      if (result.startsWith("installed")) {
        setUpdateState("installed");
        setUpdateNote(`Chamber ${result.replace("installed ", "")} is installed. Restart to use it.`);
      } else {
        setUpdateState("current");
        setUpdateNote("You're on the latest version.");
      }
    } catch (err: any) {
      setUpdateState("error");
      setUpdateNote(`Couldn't check for updates: ${String(err?.message || err)}`);
    }
  };

  const checkR2 = () => {
    setR2Checking(true);
    setStorageError("");
    api.r2Status().then(setR2, (err) => setStorageError(err?.message || "Couldn't check your Cloudflare account.")).finally(() => setR2Checking(false));
  };

  const openStorageSetup = () => {
    setStorageSetup(true);
    if (!r2) checkR2();
  };

  const signInCloudflare = async () => {
    setStorageBusy("Finish signing in in the browser window that opened…");
    setStorageError("");
    try {
      await api.cloudflareLogin();
      checkR2();
    } catch (err: any) {
      setStorageError(err?.message || "Cloudflare sign-in didn't finish. Try again.");
    } finally {
      setStorageBusy("");
    }
  };

  // Creating the bucket and redeploying the relay takes up to a minute: show what it's on meanwhile.
  const enableStorage = async () => {
    setStorageBusy("Turning on file storage…");
    setStorageError("");
    const poll = setInterval(() => api.storage().then((s) => s.progress && setStorageBusy(s.progress), () => undefined), 1000);
    try {
      await api.enableStorage();
      setStorage((s) => (s ? { ...s, files: true } : s));
      setStorageSetup(false);
    } catch (err: any) {
      setStorageError(err?.message || "Couldn't turn on file storage.");
    } finally {
      clearInterval(poll);
      setStorageBusy("");
    }
  };

  const saveStatus = async (emoji = statusEmoji, text = statusText) => {
    try {
      await api.setUserStatus(emoji, text.trim());
      setSavedNote("Saved");
      setTimeout(() => setSavedNote(""), 1800);
      onChanged();
    } catch {}
  };

  const applyPreset = (emoji: string, text: string) => {
    setStatusEmoji(emoji);
    setStatusText(text);
    saveStatus(emoji, text);
  };

  const copyInvite = () => {
    if (!inviteCommand) return;
    navigator.clipboard.writeText(inviteCommand);
    setCopiedInvite(true);
    setTimeout(() => setCopiedInvite(false), 2000);
  };

  const copyKey = () => {
    const key = `ed25519:chamber_${me || "user"}_${(data?.projects?.[0]?.name || "default").slice(0, 8)}`;
    navigator.clipboard.writeText(key);
    setCopiedKey(true);
    setTimeout(() => setCopiedKey(false), 2000);
  };

  const copyRelayHost = () => {
    navigator.clipboard.writeText(location.host || "127.0.0.1:7878");
    setCopiedRelay(true);
    setTimeout(() => setCopiedRelay(false), 2000);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="w-[840px] max-w-[94vw] sm:max-w-[840px] h-[580px] max-h-[85vh] flex flex-col p-0 overflow-hidden rounded-2xl border border-border/80 bg-background shadow-2xl">
        <DialogHeader className="px-6 py-3.5 border-b flex flex-row items-center justify-between">
          <div>
            <DialogTitle className="text-sm font-semibold text-foreground">Preferences</DialogTitle>
          </div>
        </DialogHeader>

        <div className="flex flex-1 min-h-0 overflow-hidden">
          {/* Settings Sidebar */}
          <nav className="w-52 shrink-0 border-r bg-muted/15 p-2.5 flex flex-col justify-between select-none">
            <div className="space-y-4">
              {/* Personal Section */}
              <div className="space-y-0.5">
                <div className="px-2 pb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/60">
                  Personal
                </div>
                <button
                  type="button"
                  onClick={() => setTab("profile")}
                  className={cn(
                    "w-full flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-xs font-medium transition-colors text-left",
                    tab === "profile"
                      ? "bg-accent text-accent-foreground font-semibold shadow-xs"
                      : "text-muted-foreground hover:bg-accent/50 hover:text-foreground"
                  )}
                >
                  <User className="size-3.5" /> Profile & Status
                </button>
                <button
                  type="button"
                  onClick={() => setTab("appearance")}
                  className={cn(
                    "w-full flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-xs font-medium transition-colors text-left",
                    tab === "appearance"
                      ? "bg-accent text-accent-foreground font-semibold shadow-xs"
                      : "text-muted-foreground hover:bg-accent/50 hover:text-foreground"
                  )}
                >
                  <Palette className="size-3.5" /> Appearance
                </button>
                <button
                  type="button"
                  onClick={() => setTab("shortcuts")}
                  className={cn(
                    "w-full flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-xs font-medium transition-colors text-left",
                    tab === "shortcuts"
                      ? "bg-accent text-accent-foreground font-semibold shadow-xs"
                      : "text-muted-foreground hover:bg-accent/50 hover:text-foreground"
                  )}
                >
                  <Keyboard className="size-3.5" /> Shortcuts
                </button>
                <button
                  type="button"
                  onClick={() => setTab("about")}
                  className={cn(
                    "w-full flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-xs font-medium transition-colors text-left",
                    tab === "about"
                      ? "bg-accent text-accent-foreground font-semibold shadow-xs"
                      : "text-muted-foreground hover:bg-accent/50 hover:text-foreground"
                  )}
                >
                  <Info className="size-3.5" /> About & Updates
                </button>
              </div>

              {/* Relay & Team Section */}
              <div className="space-y-0.5">
                <div className="px-2 pb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/60">
                  Relay & Team
                </div>
                <button
                  type="button"
                  onClick={() => setTab("relay")}
                  className={cn(
                    "w-full flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-xs font-medium transition-colors text-left",
                    tab === "relay"
                      ? "bg-accent text-accent-foreground font-semibold shadow-xs"
                      : "text-muted-foreground hover:bg-accent/50 hover:text-foreground"
                  )}
                >
                  <Cloud className="size-3.5" /> Cloudflare Relay
                </button>
                <button
                  type="button"
                  onClick={() => setTab("security")}
                  className={cn(
                    "w-full flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-xs font-medium transition-colors text-left",
                    tab === "security"
                      ? "bg-accent text-accent-foreground font-semibold shadow-xs"
                      : "text-muted-foreground hover:bg-accent/50 hover:text-foreground"
                  )}
                >
                  <KeyRound className="size-3.5" /> Security & Keys
                </button>
              </div>

              {/* Compute Section */}
              <div className="space-y-0.5">
                <div className="px-2 pb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/60">
                  Compute
                </div>
                <button
                  type="button"
                  onClick={() => setTab("agents")}
                  className={cn(
                    "w-full flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-xs font-medium transition-colors text-left",
                    tab === "agents"
                      ? "bg-accent text-accent-foreground font-semibold shadow-xs"
                      : "text-muted-foreground hover:bg-accent/50 hover:text-foreground"
                  )}
                >
                  <Bot className="size-3.5" /> Coding Agents
                </button>
              </div>
            </div>

            <div className="px-2 py-1.5 text-[10px] text-muted-foreground/50 border-t border-border/40">
              Chamber Desktop · v0.1.0
            </div>
          </nav>

          {/* Settings Content Area */}
          <div className="flex-1 min-h-0 overflow-y-auto p-6 space-y-6">
            {tab === "profile" && (
              <div className="space-y-5">
                <SettingsSectionHeader
                  title="Profile & Status"
                  description="Manage your team identity and availability."
                />

                <SettingsOptionGroup title="Identity">
                  <SettingsOptionRow
                    label="Handle"
                    description="Teammates and coding agents reference you by this handle."
                  >
                    <Badge variant="secondary" className="font-mono text-xs font-semibold px-2 py-0.5">
                      @{me}
                    </Badge>
                  </SettingsOptionRow>
                  <SettingsOptionRow
                    label="Account Role"
                    description="Determines permission to issue teammate invite passes."
                  >
                    <Badge variant={data?.admin ? "default" : "outline"} className="text-xs">
                      {data?.admin ? "Workspace Administrator" : "Team Member"}
                    </Badge>
                  </SettingsOptionRow>
                </SettingsOptionGroup>

                <SettingsOptionGroup title="Session">
                  <SettingsOptionRow
                    label={`Leave ${data?.chamber?.name || "this chamber"}`}
                    description="Take this chamber off this Mac; your other chambers stay. Use your invite code to come back in."
                  >
                    <Button
                      size="sm"
                      variant="outline"
                      className="h-8 px-2.5 text-xs"
                      onClick={() => {
                        if (!confirmLogout) return setConfirmLogout(true);
                        void api.logout().then(() => onChanged());
                      }}
                    >
                      {confirmLogout ? "Confirm leave" : "Leave"}
                    </Button>
                  </SettingsOptionRow>
                </SettingsOptionGroup>

                <SettingsOptionGroup title="Custom Status">
                  <SettingsOptionRow
                    label="Current Status"
                    description="Visible in team channels and task timeline headers."
                  >
                    <div className="flex items-center gap-1.5">
                      <input
                        type="text"
                        value={statusEmoji}
                        onChange={(e) => setStatusEmoji(e.target.value.slice(0, 2))}
                        className="size-8 text-center text-sm rounded-md border bg-background outline-none focus:border-ring"
                        title="Emoji icon"
                      />
                      <input
                        type="text"
                        value={statusText}
                        onChange={(e) => setStatusText(e.target.value)}
                        placeholder="What are you working on?"
                        className="w-48 h-8 rounded-md border bg-background px-2.5 text-xs outline-none focus:border-ring"
                      />
                      <Button size="sm" onClick={() => saveStatus()} className="h-8 px-2.5 text-xs">
                        {savedNote || "Save"}
                      </Button>
                    </div>
                  </SettingsOptionRow>
                  <SettingsOptionRow
                    label="Quick Presets"
                    description="One-click status shortcuts."
                    stacked
                  >
                    <div className="flex flex-wrap gap-1.5">
                      {STATUS_PRESETS.map((p) => (
                        <button
                          key={p.text}
                          type="button"
                          onClick={() => applyPreset(p.emoji, p.text)}
                          className="inline-flex items-center gap-1 rounded-full border bg-muted/40 px-2 py-0.5 text-[11px] font-medium hover:bg-accent transition-colors"
                        >
                          <span>{p.emoji}</span>
                          <span>{p.text}</span>
                        </button>
                      ))}
                    </div>
                  </SettingsOptionRow>
                </SettingsOptionGroup>
              </div>
            )}

            {tab === "appearance" && (
              <div className="space-y-5">
                <SettingsSectionHeader
                  title="Appearance"
                  description="Customize interface themes and desktop presentation."
                />

                <SettingsOptionGroup title="Interface Theme">
                  <SettingsOptionRow
                    label="Color Theme"
                    description="Switch between light and dark visual themes."
                  >
                    <SegmentedControl
                      value={dark ? "dark" : "light"}
                      options={[
                        { label: "Light", value: "light", icon: <Sun className="size-3" /> },
                        { label: "Dark", value: "dark", icon: <Moon className="size-3" /> },
                      ]}
                      onChange={(val) => onToggleTheme?.(val === "dark")}
                    />
                  </SettingsOptionRow>
                  <SettingsOptionRow
                    label="Compact Density"
                    description="Reduce vertical padding across channel message rows."
                  >
                    <ToggleSwitch
                      checked={compactTimeline}
                      onChange={setCompactTimeline}
                    />
                  </SettingsOptionRow>
                </SettingsOptionGroup>

                <SettingsOptionGroup title="Notifications">
                  <SettingsOptionRow
                    label="Approval Gate Alerts"
                    description="Notify when an agent requires human sign-off on code changes."
                  >
                    <ToggleSwitch
                      checked={alertGates}
                      onChange={setAlertGates}
                    />
                  </SettingsOptionRow>
                  <SettingsOptionRow
                    label="Task Resolutions"
                    description="Notify when a delegated obligation is marked ready for review."
                  >
                    <ToggleSwitch
                      checked={alertObligations}
                      onChange={setAlertObligations}
                    />
                  </SettingsOptionRow>
                </SettingsOptionGroup>
              </div>
            )}

            {tab === "relay" && (
              <div className="space-y-5">
                <SettingsSectionHeader
                  title="Cloudflare Relay"
                  description="Serverless Durable Object sequencing and encrypted R2 storage."
                />

                <SettingsOptionGroup title="Connection">
                  <SettingsOptionRow
                    label="Relay Connection"
                    description="Real-time WebSocket coordination via Cloudflare Durable Objects."
                  >
                    <Badge variant="outline" className="gap-1.5 border-[#a8cbbb]/40 bg-[#a8cbbb]/15 text-[#36634d] dark:text-[#a8cbbb] font-medium">
                      <span className="size-1.5 rounded-full bg-[#a8cbbb]" /> Connected
                    </Badge>
                  </SettingsOptionRow>
                  <SettingsOptionRow
                    label="Relay Host"
                    description="Endpoint coordinating ordered ciphertext streams."
                  >
                    <div className="flex items-center gap-1.5">
                      <span className="font-mono text-xs text-muted-foreground bg-muted/50 px-2 py-0.5 rounded border">
                        {location.host || "127.0.0.1:7878"}
                      </span>
                      <Button size="sm" variant="ghost" onClick={copyRelayHost} className="h-7 px-2">
                        {copiedRelay ? <Check className="size-3 text-[#36634d] dark:text-[#a8cbbb]" /> : <Copy className="size-3" />}
                      </Button>
                    </div>
                  </SettingsOptionRow>
                </SettingsOptionGroup>

                <SettingsOptionGroup title="File Storage">
                  <SettingsOptionRow
                    label="Shared files"
                    description={
                      storage?.files
                        ? "Files up to 25 MB, stored encrypted in R2 and deleted after 7 days."
                        : storage?.admin && storage.cloudflare
                          ? "Files up to 64 KB. Turn on R2, Cloudflare's file storage, to share files up to 25 MB."
                          : storage?.admin
                            ? "Files up to 64 KB."
                            : "Files up to 64 KB. Your chamber's admin can turn on larger files in their settings."
                    }
                  >
                    {!storage ? (
                      <Loader2 className="size-3.5 animate-spin text-muted-foreground" />
                    ) : storage.files ? (
                      <Badge variant="outline" className="gap-1.5 border-[#a8cbbb]/40 bg-[#a8cbbb]/15 text-[#36634d] dark:text-[#a8cbbb] font-medium">
                        <span className="size-1.5 rounded-full bg-[#a8cbbb]" /> Up to 25 MB
                      </Badge>
                    ) : storage.admin && storage.cloudflare && !storageSetup ? (
                      <Button size="sm" variant="outline" onClick={openStorageSetup} className="h-8 text-xs">
                        Set up
                      </Button>
                    ) : (
                      <Badge variant="outline" className="text-xs text-muted-foreground">Up to 64 KB</Badge>
                    )}
                  </SettingsOptionRow>
                  {storageSetup && !storage?.files ? (
                    <div className="space-y-3 px-4 py-3.5">
                      {storageError ? <div className="whitespace-pre-wrap break-words rounded-lg border border-destructive/30 bg-destructive/10 p-2.5 text-xs text-destructive">{storageError}</div> : null}
                      {r2 && !r2.authenticated ? (
                        <div className="flex items-center justify-between gap-4 rounded-lg border p-3 text-xs">
                          <span className="text-muted-foreground">Sign in to the Cloudflare account your relay runs in.</span>
                          <Button size="sm" onClick={signInCloudflare} disabled={Boolean(storageBusy)} className="h-8 shrink-0 text-xs">
                            Sign in with Cloudflare
                          </Button>
                        </div>
                      ) : (
                        <FileStorageSetup status={r2} checking={r2Checking} onCheck={checkR2} />
                      )}
                      {storageBusy ? (
                        <div className="flex items-center gap-2 text-xs text-muted-foreground">
                          <Loader2 className="size-3.5 animate-spin" /> {storageBusy}
                        </div>
                      ) : null}
                      <div className="flex justify-end gap-2">
                        <Button size="sm" variant="ghost" onClick={() => setStorageSetup(false)} disabled={Boolean(storageBusy)} className="h-8 text-xs">
                          Cancel
                        </Button>
                        <Button size="sm" onClick={enableStorage} disabled={!r2?.enabled || Boolean(storageBusy)} className="h-8 text-xs">
                          Turn on file storage
                        </Button>
                      </div>
                    </div>
                  ) : null}
                </SettingsOptionGroup>

                <SettingsOptionGroup title="Quotas & Usage">
                  <SettingsOptionRow
                    label="Daily Durable Object Requests"
                    description={`${quota?.dailyRequests?.toLocaleString() || "0"} / 100,000 daily requests used`}
                  >
                    <div className="flex items-center gap-2.5">
                      <div className="w-24 bg-muted rounded-full h-1.5 overflow-hidden">
                        <div
                          className={cn("h-full rounded-full", (quota?.requestsPercentage || 0) > 80 ? "bg-[#c5b4e3]" : "bg-primary")}
                          style={{ width: `${Math.max(2, Math.min(100, quota?.requestsPercentage || 0))}%` }}
                        />
                      </div>
                      <span className="font-mono text-xs text-muted-foreground w-8 text-right">
                        {Math.round(quota?.requestsPercentage || 0)}%
                      </span>
                    </div>
                  </SettingsOptionRow>
                  {storage?.files ? (
                  <SettingsOptionRow
                    label="R2 Encrypted Storage"
                    description={`${quota ? Math.round(quota.r2StorageBytes / (1024 * 1024)) : 0} MB / 10 GB storage used`}
                  >
                    <div className="flex items-center gap-2.5">
                      <div className="w-24 bg-muted rounded-full h-1.5 overflow-hidden">
                        <div className="h-full rounded-full bg-[#a8cbbb]" style={{ width: "2%" }} />
                      </div>
                      <span className="font-mono text-xs text-muted-foreground w-8 text-right">
                        &lt;1%
                      </span>
                    </div>
                  </SettingsOptionRow>
                  ) : null}
                </SettingsOptionGroup>
              </div>
            )}

            {tab === "agents" && (
              <div className="space-y-5">
                <SettingsSectionHeader
                  title="Coding Agents"
                  description="Local repository workspaces and agent harnesses."
                />

                <SettingsOptionGroup title="Connected Workspaces">
                  {(data?.projects || []).length > 0 ? (
                    data!.projects.map((p) => (
                      <SettingsOptionRow
                        key={p.name}
                        label={
                          <span className="inline-flex items-center gap-1.5 font-medium">
                            <FolderGit2 className="size-3.5 text-muted-foreground" />
                            {p.name}
                          </span>
                        }
                        description={<span className="font-mono text-[11px] truncate max-w-sm block">{p.path}</span>}
                      >
                        <div className="flex flex-wrap gap-1">
                          {p.agents.map((a) => (
                            <span
                              key={a.agent}
                              className={cn(
                                "inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[11px] font-medium border",
                                a.online
                                  ? "border-[#a8cbbb]/30 bg-[#a8cbbb]/10 text-[#36634d] dark:text-[#a8cbbb]"
                                  : "border-border/60 bg-muted/40 text-muted-foreground"
                              )}
                            >
                              <span className={cn("size-1.5 rounded-full", a.online ? "bg-[#a8cbbb]" : "bg-muted-foreground/40")} />
                              {KIND[a.kind] ?? a.kind}
                            </span>
                          ))}
                        </div>
                      </SettingsOptionRow>
                    ))
                  ) : (
                    <SettingsOptionRow
                      label="No workspaces connected"
                      description="Connect a repository with `chamber init` to discover local agents."
                    />
                  )}
                </SettingsOptionGroup>

                <SettingsOptionGroup title="Agent Defaults">
                  <SettingsOptionRow
                    label="Default Harness"
                    description="Preferred coding agent when delegating tasks."
                  >
                    <SegmentedControl
                      value={defaultHarness}
                      options={[
                        { label: "Claude Code", value: "claude" },
                        { label: "Antigravity", value: "antigravity" },
                        { label: "Codex", value: "codex" },
                      ]}
                      onChange={setDefaultHarness}
                    />
                  </SettingsOptionRow>
                  <SettingsOptionRow
                    label="Approval Gate"
                    description="Require human sign-off before committing filesystem changes."
                  >
                    <ToggleSwitch
                      checked={approvalGateEnabled}
                      onChange={setApprovalGateEnabled}
                    />
                  </SettingsOptionRow>
                </SettingsOptionGroup>
              </div>
            )}

            {tab === "security" && (
              <div className="space-y-5">
                <SettingsSectionHeader
                  title="Security & Keys"
                  description="Access management and end-to-end cryptographic policies."
                />

                <SettingsOptionGroup title="Team Access">
                  {data?.admin && inviteCommand ? (
                    <SettingsOptionRow
                      label="Teammate Invite Command"
                      description="Cryptographically signed personal pass for new teammates."
                    >
                      <div className="flex items-center gap-1.5">
                        <input
                          readOnly
                          value={inviteCommand}
                          className="w-56 h-8 rounded-md border bg-muted/40 px-2 font-mono text-[11px] text-muted-foreground select-all outline-none"
                        />
                        <Button size="sm" variant="outline" onClick={copyInvite} className="h-8 gap-1 text-xs">
                          {copiedInvite ? <Check className="size-3 text-emerald-500" /> : <Copy className="size-3" />}
                          {copiedInvite ? "Copied" : "Copy"}
                        </Button>
                      </div>
                    </SettingsOptionRow>
                  ) : (
                    <SettingsOptionRow
                      label="Invite Management"
                      description="Only workspace administrators can generate cryptographic passes."
                    >
                      <Badge variant="outline" className="text-xs">
                        Admin Only
                      </Badge>
                    </SettingsOptionRow>
                  )}
                </SettingsOptionGroup>

                <SettingsOptionGroup title="Cryptography">
                  <SettingsOptionRow
                    label="Payload Encryption"
                    description="Envelope-bound authenticated client-side encryption."
                  >
                    <Badge variant="outline" className="gap-1 font-mono text-[11px] border-primary/30 text-primary">
                      <ShieldCheck className="size-3" /> AES-256-GCM
                    </Badge>
                  </SettingsOptionRow>
                  <SettingsOptionRow
                    label="Relay Zero-Knowledge"
                    description="Relay stores and routes encrypted ciphertext only."
                  >
                    <Badge variant="outline" className="font-mono text-[11px] text-muted-foreground">
                      Zero-Knowledge Enforced
                    </Badge>
                  </SettingsOptionRow>
                  <SettingsOptionRow
                    label="Device Key Fingerprint"
                    description="Local Ed25519 public key identifier."
                  >
                    <div className="flex items-center gap-1.5">
                      <span className="font-mono text-[11px] text-muted-foreground bg-muted/50 px-2 py-0.5 rounded border">
                        ed25519:{me.slice(0, 4)}...{(data?.projects?.[0]?.name || "chamber").slice(0, 4)}
                      </span>
                      <Button size="sm" variant="ghost" onClick={copyKey} className="h-7 px-2">
                        {copiedKey ? <Check className="size-3 text-emerald-500" /> : <Copy className="size-3" />}
                      </Button>
                    </div>
                  </SettingsOptionRow>
                </SettingsOptionGroup>
              </div>
            )}

            {tab === "about" && (
              <div className="space-y-5">
                <SettingsSectionHeader title="About & Updates" description="Which Chamber this is, and getting the newest one." />

                <SettingsOptionGroup title="Chamber">
                  <SettingsOptionRow label="Version" description={tauri ? "The Mac app checks for updates by itself shortly after it opens, then every few hours." : "Running from the Chamber repository."}>
                    <span className="font-mono text-xs text-muted-foreground bg-muted/50 px-2 py-0.5 rounded border">{appVersion ? `v${appVersion}` : tauri ? "…" : "source"}</span>
                  </SettingsOptionRow>
                  {tauri ? (
                    <SettingsOptionRow
                      label="Updates"
                      description={updateNote || "Download and install the newest Chamber now. You choose when to restart."}
                    >
                      <Button size="sm" variant="outline" onClick={checkForUpdates} disabled={updateState === "checking"} className="h-8 gap-1.5 text-xs">
                        {updateState === "checking" ? <Loader2 className="size-3 animate-spin" /> : updateState === "current" ? <Check className="size-3" /> : <Download className="size-3" />}
                        {updateState === "checking" ? "Checking…" : "Check for updates"}
                      </Button>
                    </SettingsOptionRow>
                  ) : (
                    <SettingsOptionRow
                      label="Updates"
                      description={
                        <>
                          In the folder you run Chamber from: <code className="font-mono">git pull</code>, then <code className="font-mono">npm start</code> again.
                        </>
                      }
                    />
                  )}
                </SettingsOptionGroup>
              </div>
            )}

            {tab === "shortcuts" && (
              <div className="space-y-5">
                <SettingsSectionHeader
                  title="Keyboard Shortcuts"
                  description="Power-user keyboard navigation and shortcuts."
                />

                <SettingsOptionGroup title="Navigation">
                  <SettingsOptionRow label="Quick switcher & search">
                    <kbd className="rounded border bg-muted px-2 py-0.5 font-mono text-[11px] text-foreground font-semibold">
                      ⌘K / Ctrl+K
                    </kbd>
                  </SettingsOptionRow>
                  <SettingsOptionRow label="Start a new conversation">
                    <kbd className="rounded border bg-muted px-2 py-0.5 font-mono text-[11px] text-foreground font-semibold">
                      C
                    </kbd>
                  </SettingsOptionRow>
                  <SettingsOptionRow label="Close active modal or panel">
                    <kbd className="rounded border bg-muted px-2 py-0.5 font-mono text-[11px] text-foreground font-semibold">
                      Esc
                    </kbd>
                  </SettingsOptionRow>
                </SettingsOptionGroup>

                <SettingsOptionGroup title="Messaging">
                  <SettingsOptionRow label="Send message">
                    <kbd className="rounded border bg-muted px-2 py-0.5 font-mono text-[11px] text-foreground font-semibold">
                      Enter
                    </kbd>
                  </SettingsOptionRow>
                  <SettingsOptionRow label="Insert newline in composer">
                    <kbd className="rounded border bg-muted px-2 py-0.5 font-mono text-[11px] text-foreground font-semibold">
                      Shift + Enter
                    </kbd>
                  </SettingsOptionRow>
                </SettingsOptionGroup>

                <SettingsOptionGroup title="View Switching">
                  <SettingsOptionRow label="Switch between views">
                    <kbd className="rounded border bg-muted px-2 py-0.5 font-mono text-[11px] text-foreground font-semibold">
                      ⌘1 – ⌘4
                    </kbd>
                  </SettingsOptionRow>
                </SettingsOptionGroup>
              </div>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
