import { Shield, ShieldAlert } from "lucide-react";
import { useEffect, useState } from "react";
import { api, type Gate } from "@/lib/api";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { GateCard } from "@/components/GateCard";

interface GatesDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  me?: string;
  onChanged?: () => void;
}

export function GatesDialog({ open, onOpenChange, me = "", onChanged }: GatesDialogProps) {
  const [gates, setGates] = useState<Gate[]>([]);
  const [loading, setLoading] = useState(false);

  const loadGates = async () => {
    setLoading(true);
    try {
      const res = await api.gates();
      setGates(res.gates || []);
    } catch {
      setGates([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (open) loadGates();
  }, [open]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <div className="flex items-center gap-2">
            <Shield className="size-5 text-[#8db8d5]" />
            <DialogTitle>Approval Gates</DialogTitle>
          </div>
          <DialogDescription>
            Review and approve sensitive actions requested by coding agents or workflows.
          </DialogDescription>
        </DialogHeader>

        <div className="max-h-[70vh] overflow-y-auto py-2 pr-1">
          {loading ? (
            <p className="py-4 text-center text-xs text-muted-foreground">Checking pending gates...</p>
          ) : gates.length === 0 ? (
            <div className="py-8 text-center text-xs text-muted-foreground">
              <ShieldAlert className="mx-auto mb-2 size-8 text-muted-foreground/40" />
              <p className="font-medium text-foreground">No pending approval gates</p>
              <p className="mt-1 text-muted-foreground">
                When an agent triggers an action requiring human approval, it will appear here.
              </p>
            </div>
          ) : (
            gates.map((g) => (
              <GateCard
                key={g.id}
                gateId={g.id}
                description={g.description}
                action={g.action}
                requestedBy={g.requestedBy}
                expiresAt={g.expiresAt}
                createdAt={g.createdAt}
                resolvedAt={g.resolvedAt}
                resolvedBy={g.resolvedBy}
                lineId={g.lineId}
                initialStatus={g.status}
                me={me}
                onResolved={() => {
                  loadGates();
                  onChanged?.();
                }}
              />
            ))
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
