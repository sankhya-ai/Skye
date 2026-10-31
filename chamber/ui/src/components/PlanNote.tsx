import { useEffect, useState } from "react";
import { api, type CloudPlan } from "@/lib/api";
import { cn } from "@/lib/utils";

const DAY = 24 * 3600 * 1000;

/** On Chamber Cloud: the trial's days left, or that the plan needs the admin, with the way to billing. */
export function PlanNote() {
  const [plan, setPlan] = useState<CloudPlan | null>(null);
  useEffect(() => {
    const load = () => api.plan().then((r) => setPlan(r.plan), () => undefined);
    void load();
    const timer = setInterval(load, 10 * 60 * 1000);
    return () => clearInterval(timer);
  }, []);
  if (!plan?.registered || plan.status === "enterprise" || (plan.status === "active" && plan.access)) return null;

  const days = Math.max(0, Math.ceil((plan.trialEnds - Date.now()) / DAY));
  const blocked = !plan.access;
  const text = blocked
    ? plan.status === "trial" ? "Your free trial has ended." : "Your plan isn't active."
    : `Free trial: ${days} day${days === 1 ? "" : "s"} left.`;
  return (
    <div className={cn("mx-1 mb-2 rounded-lg border px-2.5 py-2 text-[11px] leading-snug", blocked ? "border-destructive/40 bg-destructive/10 text-destructive" : "bg-muted/40 text-muted-foreground")}>
      <span>{text} </span>
      {plan.admin && plan.billing ? (
        <button type="button" onClick={() => void api.openExternal(plan.billing!)} className="font-medium text-foreground underline-offset-2 hover:underline">
          {blocked ? "Subscribe" : "Choose a plan"}
        </button>
      ) : blocked ? <span>Ask your admin to subscribe.</span> : null}
    </div>
  );
}
