import { Activity as Pulse, Bot, Brain, FilePen, FileText, GitMerge, Globe, ListTodo, Plug, Search, SquareTerminal } from "lucide-react";
import type { ReactNode } from "react";

import type { StepKind } from "../../lib/activity.ts";

const ICONS: Record<StepKind, (size: number) => ReactNode> = {
  read: (s) => <FileText size={s} />,
  search: (s) => <Search size={s} />,
  command: (s) => <SquareTerminal size={s} />,
  web: (s) => <Globe size={s} />,
  edit: (s) => <FilePen size={s} />,
  plan: (s) => <ListTodo size={s} />,
  memory: (s) => <Brain size={s} />,
  remember: (s) => <Brain size={s} />,
  agent: (s) => <Bot size={s} />,
  flow: (s) => <GitMerge size={s} />,
  process: (s) => <Pulse size={s} />,
  tool: (s) => <Plug size={s} />,
};

export function StepIcon({ kind, size = 15 }: { kind: StepKind; size?: number }) {
  return <span className="icon">{ICONS[kind](size)}</span>;
}
