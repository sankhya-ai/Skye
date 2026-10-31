// The view and the open talk live in the URL (#/inbox, #/c/general, #/p/nitin, #/a/ashish/root, ?t=<id>),
// so links and the back button work.

import type { View } from "./board.ts";

export interface Route {
  view: View;
  talk: string | null; // a talk id, "new", or null
}

export function parse(hash: string): Route {
  const [path, query = ""] = hash.replace(/^#\/?/, "").split("?");
  const talk = new URLSearchParams(query).get("t");
  const [kind, ...rest] = path.split("/");
  const arg = decodeURIComponent(rest.join("/"));
  const view: View =
    kind === "work" ? { kind: "work" }
    : kind === "inbox" ? { kind: "inbox" }
    : kind === "all" ? { kind: "all" }
    : kind === "memory" ? { kind: "memory" }
    : kind === "project" && arg ? { kind: "project", path: arg }
    : kind === "c" && arg ? { kind: "channel", name: `#${arg}` }
    : kind === "p" && arg ? { kind: "person", owner: arg }
    : kind === "a" && arg ? { kind: "agent", agent: arg }
    : { kind: "work" };
  return { view, talk };
}

export function format({ view, talk }: Route): string {
  const path =
    view.kind === "work" ? "work"
    : view.kind === "all" ? "all"
    : view.kind === "memory" ? "memory"
    : view.kind === "project" ? `project/${encodeURIComponent(view.path)}`
    : view.kind === "channel" ? `c/${encodeURIComponent(view.name.slice(1))}`
    : view.kind === "person" ? `p/${encodeURIComponent(view.owner)}`
    : view.kind === "agent" ? `a/${view.agent.split("/").map(encodeURIComponent).join("/")}`
    : "inbox";
  return `#/${path}${talk ? `?t=${encodeURIComponent(talk)}` : ""}`;
}
