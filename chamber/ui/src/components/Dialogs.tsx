import { Check, Copy, Hash, UserMinus } from "lucide-react";

import { Avatar } from "@/components/Avatar";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { api } from "@/lib/api";
import { people, type BoardData } from "@/lib/board";

/** New channel, or join one you are not in. */
export function ChannelDialog({ open, onOpenChange, data, onJoined }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  data: BoardData | null;
  onJoined: (name: string) => void;
}) {
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const others = (data?.channels ?? []).filter((c) => !c.joined);
  const join = async (raw: string) => {
    try {
      const { channel } = await api.channel("join", raw);
      setName("");
      onOpenChange(false);
      onJoined(channel);
    } catch (e) {
      setError(String((e as Error).message));
    }
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Channels</DialogTitle>
          <DialogDescription>A channel is a room for a topic. Everyone in it sees what's said; @mention someone to reach their agent.</DialogDescription>
        </DialogHeader>
        <form className="flex gap-2" onSubmit={(e) => (e.preventDefault(), name.trim() && void join(name))}>
          <div className="flex flex-1 items-center gap-1 rounded-md border px-2 focus-within:border-ring">
            <Hash className="size-3.5 text-muted-foreground" />
            <input autoFocus value={name} onChange={(e) => setName(e.target.value.toLowerCase().replace(/[^a-z0-9_#-]/g, "-"))} placeholder="backend" className="h-9 flex-1 bg-transparent outline-none" />
          </div>
          <Button type="submit" disabled={!name.trim()}>Create</Button>
        </form>
        {others.length ? (
          <div className="flex flex-col gap-1">
            <p className="text-muted-foreground">Or join one:</p>
            {others.map((c) => (
              <button key={c.name} onClick={() => void join(c.name)} className="flex items-center gap-2 rounded-md px-2 py-1.5 text-left hover:bg-accent">
                <Hash className="size-3.5" /> {c.name.slice(1)} <span className="ml-auto text-muted-foreground">{c.members.length} in it</span>
              </button>
            ))}
          </div>
        ) : null}
        {error ? <p className="text-destructive">{error}</p> : null}
      </DialogContent>
    </Dialog>
  );
}

/** The admin's join command for a teammate. */
export function InviteDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const [command, setCommand] = useState("");
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (open) api.invite().then((r) => setCommand(r.code || r.command), (e) => setCommand(String(e.message)));
  }, [open]);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Invite a teammate</DialogTitle>
          <DialogDescription>A personal invite code for one teammate: send it to them privately. They open Chamber, choose "Join a chamber" and paste it. Each time you open this, it makes a new one.</DialogDescription>
        </DialogHeader>
        <pre className="overflow-x-auto rounded-md border bg-muted p-3 font-mono text-[12px] whitespace-pre-wrap break-all">{command}</pre>
        <Button onClick={() => void navigator.clipboard.writeText(command).then(() => (setCopied(true), setTimeout(() => setCopied(false), 1500)))}>
          {copied ? <Check /> : <Copy />} {copied ? "Copied" : "Copy invite code"}
        </Button>
      </DialogContent>
    </Dialog>
  );
}

/** Who is in the chamber; the admin removes people (their invite and name stop working) or lets a name back. */
export function MembersDialog({ open, onOpenChange, admin, me, data: board, onChanged }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  admin: boolean;
  me: string;
  data: BoardData | null;
  onChanged: () => void;
}) {
  const [data, setData] = useState<Awaited<ReturnType<typeof api.members>> | null>(null);
  const [confirm, setConfirm] = useState<string | null>(null);
  const [error, setError] = useState("");
  const load = () => api.members().then(setData, (e) => setError(String(e.message)));
  useEffect(() => {
    if (open) {
      setConfirm(null);
      setError("");
      void load();
    }
  }, [open]);
  const act = async (work: () => Promise<unknown>) => {
    try {
      await work();
      setConfirm(null);
      await load();
      onChanged();
    } catch (e) {
      setError(String((e as Error).message));
    }
  };
  const memberNames = new Set((data?.members ?? []).map((m) => m.owner));
  const removed = new Set((data?.removed ?? []).map((r) => r.owner));
  // People seen in talks or channels who never joined with a personal invite (e.g. from before invites were personal).
  const others = people(board).filter((p) => !memberNames.has(p.owner) && !removed.has(p.owner));
  const row = (owner: string, online: boolean | undefined, note?: string) => (
    <div key={owner} className="flex h-9 items-center gap-2 rounded-md px-2 hover:bg-accent/60">
      <Avatar name={owner} size={22} online={online} />
      <span className="capitalize">{owner}</span>
      {owner === me ? <span className="text-muted-foreground">you</span> : note ? <span className="text-muted-foreground">{note}</span> : null}
      {admin && owner !== me ? (
        confirm === owner ? (
          <span className="ml-auto flex gap-1">
            <Button size="sm" variant="destructive" onClick={() => void act(() => api.revoke(owner))}>Remove {owner}</Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirm(null)}>Cancel</Button>
          </span>
        ) : (
          <Button size="sm" variant="ghost" className="ml-auto text-muted-foreground" onClick={() => setConfirm(owner)}>
            <UserMinus /> Remove
          </Button>
        )
      ) : null}
    </div>
  );
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Members</DialogTitle>
          <DialogDescription>
            {data && !data.personal ? "This chamber has no personal invites yet: anyone with its key may join." : "Everyone in this chamber. Removing someone disconnects them at once; their invite and name stop working."}
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-1">{(data?.members ?? []).map((m) => row(m.owner, m.online))}</div>
        {others.length ? (
          <div className="flex flex-col gap-1 border-t pt-3">
            <p className="text-muted-foreground">Seen here, without a personal invite</p>
            {others.map((p) => row(p.owner, p.online, "no invite"))}
          </div>
        ) : null}
        {admin && removed.size ? (
          <div className="flex flex-col gap-1 border-t pt-3">
            <p className="text-muted-foreground">Removed</p>
            {[...removed].map((owner) => (
              <div key={owner} className="flex h-9 items-center gap-2 rounded-md px-2 text-muted-foreground">
                <Avatar name={owner} size={22} />
                <span className="capitalize line-through">{owner}</span>
                <Button size="sm" variant="ghost" className="ml-auto" onClick={() => void act(() => api.revoke(owner, "restore"))}>Restore</Button>
              </div>
            ))}
          </div>
        ) : null}
        {error ? <p className="text-destructive">{error}</p> : null}
      </DialogContent>
    </Dialog>
  );
}

/** A channel's members (Slack's "People in #channel"): add teammates, or leave. */
export function ChannelMembersDialog({ open, onOpenChange, data, channel, onLeft, onChanged }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  data: BoardData | null;
  channel: string;
  onLeft: () => void;
  onChanged: () => void;
}) {
  const [error, setError] = useState("");
  const me = data?.me ?? "";
  const members = data?.channels.find((c) => c.name === channel)?.members ?? [];
  const online = new Set((data?.agents ?? []).map((a) => a.agent.split("/")[0]));
  const others = people(data).filter((p) => !members.includes(p.owner));
  const act = async (work: () => Promise<unknown>) => {
    try {
      setError("");
      await work();
      onChanged();
    } catch (e) {
      setError(String((e as Error).message));
    }
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>People in {channel}</DialogTitle>
          <DialogDescription>Everyone here sees the channel on their board; @mention someone to reach their agent.</DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-1">
          {members.map((m) => (
            <div key={m} className="group flex h-9 items-center gap-2 rounded-md px-2 hover:bg-accent/60">
              <Avatar name={m} size={22} online={online.has(m)} />
              <span className="capitalize">{m}</span>
              {m === me ? <span className="text-muted-foreground">you</span> : null}
              {m !== me && members.includes(me) ? (
                <Button size="sm" variant="ghost" className="ml-auto hidden text-muted-foreground group-hover:inline-flex" onClick={() => void act(() => api.channel("leave", channel, m))}>
                  Remove
                </Button>
              ) : null}
            </div>
          ))}
        </div>
        {others.length ? (
          <div className="flex flex-col gap-1 border-t pt-3">
            <p className="text-muted-foreground">Add a teammate</p>
            {others.map((p) => (
              <button key={p.owner} onClick={() => void act(() => api.channel("add", channel, p.owner))} className="flex h-9 items-center gap-2 rounded-md px-2 text-left hover:bg-accent">
                <Avatar name={p.owner} size={22} online={p.online} />
                <span className="capitalize">{p.owner}</span>
                <span className="ml-auto text-muted-foreground">Add</span>
              </button>
            ))}
          </div>
        ) : (
          <p className="border-t pt-3 text-muted-foreground">Everyone in the chamber is here. Invite more people to the chamber first.</p>
        )}
        {channel !== "#general" ? (
          <Button variant="ghost" className="self-start text-muted-foreground" onClick={() => void act(async () => (await api.channel("leave", channel), onOpenChange(false), onLeft()))}>
            Leave {channel}
          </Button>
        ) : null}
        {error ? <p className="text-destructive">{error}</p> : null}
      </DialogContent>
    </Dialog>
  );
}
