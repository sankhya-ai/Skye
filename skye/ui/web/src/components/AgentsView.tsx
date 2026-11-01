// Agents: the team's real, dispatchable roles - builtins plus owner-defined specialists.
// Creating a role here writes the owner-managed registry the contract validator reads.
import { Plus } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Button } from "./ui/Button";
import { createRole, getRoles, type RoleRow } from "../api";
import { Badge } from "./ui/Badge";

export function AgentsView() {
  const [roles, setRoles] = useState<RoleRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [tools, setTools] = useState("Read, Grep, Glob");
  const [writable, setWritable] = useState(false);
  const [adding, setAdding] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setRoles((await getRoles()).roles);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const add = async () => {
    setError(null);
    try {
      await createRole({
        name: name.trim(),
        description: description.trim(),
        catalogue: {
          tools: tools.split(",").map((t) => t.trim()).filter(Boolean),
          writable,
        },
      });
      setName("");
      setDescription("");
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-medium">Agents</h2>
        <p className="text-sm text-muted-foreground">
          Tool lists scope a worker's tools; they are not a security boundary.
        </p>
      </div>
      {error ? (
        <p className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">
          {error}
        </p>
      ) : null}
      <div className="grid gap-3 sm:grid-cols-2">
        {roles.map((role) => (
          <div key={role.name} className="shadow-card rounded-xl border border-border bg-card p-4">
            <div className="flex items-center gap-2">
              <span className="font-mono text-sm font-medium">{role.name}</span>
              <Badge variant={role.source === "builtin" ? "secondary" : "info"}>
                {role.source === "registry-override" ? "custom (refines builtin)" : role.source}
              </Badge>
              {role.catalogue.writable ? (
                <Badge variant="info">Write/Edit tools</Badge>
              ) : (
                <Badge variant="secondary">no Write/Edit tools</Badge>
              )}
            </div>
            {role.description ? (
              <p className="mt-1 text-xs text-muted-foreground">{role.description}</p>
            ) : null}
            <p className="mt-2 font-mono text-xs text-muted-foreground">
              {role.catalogue.tools.join(" · ") || "no tools"}
            </p>
            <p className="mt-2 text-xs text-muted-foreground">
              {role.steps} steps run · {role.done} done · {role.failed} failed
            </p>
          </div>
        ))}
      </div>
      <div className="shadow-card rounded-xl border border-border bg-card p-4">
        <h3 className="mb-3 flex items-center gap-1 text-sm font-medium">
          <Plus className="size-4" /> New specialist
        </h3>
        <div className="grid gap-2 sm:grid-cols-2">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="name, e.g. security-auditor"
            className="rounded-md border border-border bg-background px-3 py-1.5 font-mono text-sm outline-none"
          />
          <input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="what this specialist does"
            className="rounded-md border border-border bg-background px-3 py-1.5 text-sm outline-none"
          />
          <input
            value={tools}
            onChange={(e) => setTools(e.target.value)}
            placeholder="tools, comma separated"
            className="rounded-md border border-border bg-background px-3 py-1.5 font-mono text-sm outline-none sm:col-span-2"
          />
        </div>
        <label className="mt-2 flex items-center gap-2 text-xs text-muted-foreground">
          <input
            type="checkbox"
            checked={writable}
            onChange={(e) => setWritable(e.target.checked)}
          />
          Offer Write/Edit tools (otherwise read tools only)
        </label>
        <Button
          size="sm"
          className="mt-3"
          disabled={!name.trim()}
          loading={adding}
          onClick={() => {
            setAdding(true);
            void add().finally(() => setAdding(false));
          }}
        >
          Add role
        </Button>
      </div>
    </div>
  );
}
