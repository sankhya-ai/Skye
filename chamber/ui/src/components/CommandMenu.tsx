import { BookOpen, Folder, Hash, Inbox, Layers, MessageSquarePlus, Moon, UserPlus } from "lucide-react";

import { Avatar } from "@/components/Avatar";
import { CommandDialog, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList, CommandShortcut } from "@/components/ui/command";
import { people, type BoardData, type View } from "@/lib/board";

/** ⌘K: go anywhere, do anything (Linear's and Slack's quick switcher). */
export function CommandMenu({ open, onOpenChange, data, onView, onTalk, onNewTalk, onNewChannel, onInvite, onTheme }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  data: BoardData | null;
  onView: (view: View) => void;
  onTalk: (id: string) => void;
  onNewTalk: () => void;
  onNewChannel: () => void;
  onInvite: () => void;
  onTheme: () => void;
}) {
  const run = (work: () => void) => () => {
    onOpenChange(false);
    work();
  };
  return (
    <CommandDialog open={open} onOpenChange={onOpenChange} showCloseButton={false} title="Go to" description="Jump to a channel, person, agent or talk">
      <CommandInput placeholder="Go to a channel, person, agent or talk…" />
      <CommandList>
        <CommandEmpty>Nothing found.</CommandEmpty>
        <CommandGroup heading="Actions">
          <CommandItem onSelect={run(onNewTalk)}><MessageSquarePlus /> New talk <CommandShortcut>C</CommandShortcut></CommandItem>
          <CommandItem onSelect={run(onNewChannel)}><Hash /> New or join a channel</CommandItem>
          {data?.admin ? <CommandItem onSelect={run(onInvite)}><UserPlus /> Invite a teammate</CommandItem> : null}
          <CommandItem onSelect={run(onTheme)}><Moon /> Switch theme</CommandItem>
        </CommandGroup>
        <CommandGroup heading="Go to">
          <CommandItem onSelect={run(() => onView({ kind: "inbox" }))}><Inbox /> Inbox</CommandItem>
          <CommandItem onSelect={run(() => onView({ kind: "all" }))}><Layers /> All talks</CommandItem>
          <CommandItem onSelect={run(() => onView({ kind: "memory" }))}><BookOpen /> Memory</CommandItem>
          {(data?.channels ?? []).filter((c) => c.joined).map((c) => (
            <CommandItem key={c.name} value={c.name} onSelect={run(() => onView({ kind: "channel", name: c.name }))}><Hash /> {c.name.slice(1)}</CommandItem>
          ))}
          {people(data).map(({ owner, online }) => (
            <CommandItem key={owner} value={`@${owner}`} onSelect={run(() => onView({ kind: "person", owner }))}>
              <Avatar name={owner} size={18} online={online} /> <span className="capitalize">{owner}</span>
            </CommandItem>
          ))}
          {(data?.projects ?? []).filter((p) => p.path).map((p) => (
            <CommandItem key={p.path} value={`${p.name} ${p.path}`} onSelect={run(() => onView({ kind: "project", path: p.path }))}>
              <Folder /> {p.name}
            </CommandItem>
          ))}
        </CommandGroup>
        <CommandGroup heading="Talks">
          {(data?.cards ?? []).slice(0, 50).map((c) => (
            <CommandItem key={c.id} value={`${c.title} ${c.id}`} onSelect={run(() => onTalk(c.id))}>
              <span className="truncate">{c.title}</span>
              <CommandShortcut>{c.channel ?? ""}</CommandShortcut>
            </CommandItem>
          ))}
        </CommandGroup>
      </CommandList>
    </CommandDialog>
  );
}
