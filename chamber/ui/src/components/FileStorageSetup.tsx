import { CheckCircle2, ExternalLink, Loader2, RefreshCw } from "lucide-react";
import { api, type R2Status } from "@/lib/api";
import { Button } from "@/components/ui/button";

/**
 * How to turn on R2 (Cloudflare's file storage) for files up to 25 MB, shown in onboarding and in
 * Settings. Cloudflare only lets the account owner turn it on, in the dashboard, so Chamber can't do it.
 */
export function FileStorageSetup({ status, checking, onCheck }: { status: R2Status | null; checking: boolean; onCheck: () => void }) {
  if (!status && checking) {
    return (
      <div className="flex items-center gap-2 rounded-lg border p-3 text-xs text-muted-foreground">
        <Loader2 className="size-3.5 animate-spin" /> Checking your Cloudflare account…
      </div>
    );
  }

  if (status?.enabled) {
    return (
      <div className="flex gap-2.5 rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-3 text-xs">
        <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-600 dark:text-emerald-400" />
        <div>
          <p className="font-medium text-foreground">R2 is on for this account</p>
          <p className="mt-0.5 leading-relaxed text-muted-foreground">Teammates can share files up to 25 MB. They're encrypted before they leave your machine and deleted after 7 days.</p>
        </div>
      </div>
    );
  }

  const steps = [
    <>Open R2 in your Cloudflare dashboard.</>,
    <>Turn R2 on. Cloudflare asks for a payment method, but the free tier covers 10 GB and Chamber deletes files after 7 days, so a team stays well inside it.</>,
    <>Come back here and click <span className="font-medium text-foreground">Check again</span>.</>,
  ];

  return (
    <div className="space-y-3 rounded-lg border p-3.5 text-xs">
      <p className="leading-relaxed text-muted-foreground">
        {status?.reason || "R2 isn't turned on for this Cloudflare account yet."} Only you can turn it on, from the Cloudflare dashboard:
      </p>
      <ol className="space-y-2">
        {steps.map((step, i) => (
          <li key={i} className="flex gap-2.5">
            <span className="flex size-4.5 shrink-0 items-center justify-center rounded-full bg-muted text-[10px] font-semibold text-foreground">{i + 1}</span>
            <span className="leading-relaxed text-muted-foreground">{step}</span>
          </li>
        ))}
      </ol>
      <div className="flex gap-2 pt-1">
        <Button type="button" size="sm" variant="outline" className="h-8 gap-1.5 text-xs" onClick={() => status && void api.openExternal(status.dashboard).catch(() => window.open(status.dashboard, "_blank"))} disabled={!status}>
          Open Cloudflare <ExternalLink className="size-3" />
        </Button>
        <Button type="button" size="sm" variant="outline" className="h-8 gap-1.5 text-xs" onClick={onCheck} disabled={checking}>
          {checking ? <Loader2 className="size-3 animate-spin" /> : <RefreshCw className="size-3" />} Check again
        </Button>
      </div>
    </div>
  );
}
