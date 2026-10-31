// How each tool call is named in the transcript: a verb and its subject.

import { parseArgs } from "./transcript.ts";

export type ToolIcon =
  | "terminal"
  | "file"
  | "edit"
  | "new"
  | "search"
  | "folder"
  | "globe"
  | "todo"
  | "agent"
  | "memory"
  | "image"
  | "plug";

export interface ToolView {
  icon: ToolIcon;
  verb: string;
  subject: string;
}

function short(text: unknown, max = 90): string {
  const value = String(text ?? "").replace(/\s+/g, " ").trim();
  return value.length > max ? `${value.slice(0, max - 1)}…` : value;
}

export function relativePath(path: string, workspace?: string): string {
  if (workspace && path.startsWith(`${workspace}/`)) return path.slice(workspace.length + 1);
  return path;
}

export function describeTool(name: string, rawArgs: string, workspace?: string): ToolView {
  const args = parseArgs(rawArgs) ?? {};
  const path = args.path ? relativePath(String(args.path), workspace) : "";
  switch (name) {
    case "Shell":
      return { icon: "terminal", verb: "Run", subject: short(args.command, 120) };
    case "ReadFile":
      return { icon: "file", verb: "Read", subject: path };
    case "ReadMediaFile":
      return { icon: "image", verb: "View", subject: path };
    case "WriteFile":
      return { icon: "new", verb: args.mode === "append" ? "Append" : "Write", subject: path };
    case "StrReplaceFile": {
      const edits = Array.isArray(args.edit) ? args.edit.length : args.edit ? 1 : 0;
      return { icon: "edit", verb: "Edit", subject: edits > 1 ? `${path} · ${edits} edits` : path };
    }
    case "Glob":
      return { icon: "folder", verb: "Find", subject: short(args.pattern) + (args.directory ? ` in ${relativePath(String(args.directory), workspace)}` : "") };
    case "Grep":
      return { icon: "search", verb: "Search", subject: short(args.pattern) + (args.path ? ` in ${relativePath(String(args.path), workspace)}` : "") };
    case "SearchWeb":
      return { icon: "globe", verb: "Search the web", subject: short(args.query) };
    case "FetchURL":
      return { icon: "globe", verb: "Fetch", subject: short(args.url) };
    case "SetTodoList": {
      const todos = Array.isArray(args.todos) ? args.todos : [];
      const done = todos.filter((t: any) => String(t.status).toLowerCase() === "done").length;
      return { icon: "todo", verb: "Update plan", subject: todos.length ? `${done}/${todos.length} done` : "" };
    }
    case "Task":
      return { icon: "agent", verb: `Delegate to ${args.subagent_name ?? "subagent"}`, subject: short(args.description) };
    case "CreateSubagent":
      return { icon: "agent", verb: "Create subagent", subject: short(args.name) };
    case "memory_recall":
      return { icon: "memory", verb: "Recall", subject: short(args.query) };
    case "memory_remember":
      return { icon: "memory", verb: "Remember", subject: short(args.content) };
    case "memory_checkpoint":
      return { icon: "memory", verb: "Checkpoint", subject: short(args.summary) };
    case "Think":
      return { icon: "agent", verb: "Think", subject: short(args.thought) };
    case "process": {
      const id = String(args.process_id ?? "");
      switch (args.action) {
        case "start":
          return { icon: "terminal", verb: "Start in the background", subject: short(args.command, 120) };
        case "list":
          return { icon: "terminal", verb: "List background processes", subject: "" };
        case "kill":
          return { icon: "terminal", verb: "Stop process", subject: id };
        case "write":
        case "submit":
        case "close":
          return { icon: "terminal", verb: "Send input to", subject: id };
        default:
          return { icon: "terminal", verb: "Check process", subject: id };
      }
    }
    case "coding_flow": {
      const id = String(args.flow_id ?? "");
      const verbs: Record<string, string> = {
        create: "Prepare a coding flow",
        start: "Run coding flow",
        status: "Check coding flow",
        list: "List coding flows",
        review: "Review coding flow",
        stop: "Stop coding flow",
        cancel: "Discard coding flow",
        apply: "Apply coding flow",
        rollback: "Roll back coding flow",
      };
      return { icon: "agent", verb: verbs[String(args.operation)] ?? "Coding flow", subject: args.operation === "create" ? short(args.task) : id };
    }
    case "read_document":
      return { icon: "file", verb: "Read", subject: path + (args.pages ? ` · pages ${args.pages}` : args.sheet ? ` · ${args.sheet}` : "") };
    case "edit_spreadsheet":
    case "edit_pdf": {
      const ops = Array.isArray(args.operations) ? args.operations.map((op: any) => String(op?.op ?? "")).filter(Boolean) : [];
      return { icon: "edit", verb: "Edit", subject: `${path}${ops.length ? ` · ${[...new Set(ops)].join(", ")}` : ""}` };
    }
    case "session_search":
      return { icon: "search", verb: "Search past sessions", subject: short(args.query) };
    case "github": {
      const action = String(args.action ?? "");
      const verbs: Record<string, string> = {
        repo: "Look up repository",
        list_issues: "List issues",
        get_issue: "Read issue",
        list_prs: "List pull requests",
        get_pr: "Read pull request",
        pr_diff: "Read the diff of",
        checks: "Check CI",
        create_issue: "Open an issue",
        update_issue: "Update issue",
        comment: "Comment on",
        create_pr: "Open a pull request",
        review_pr: "Review pull request",
      };
      const subject = args.number ? `#${args.number}` : short(args.title ?? args.query ?? args.ref ?? args.repo ?? "");
      return { icon: "globe", verb: `GitHub · ${verbs[action] ?? action}`, subject };
    }
    case "gmail": {
      const verbs: Record<string, string> = { search: "Search mail", read: "Read email", thread: "Read thread", draft: "Draft a reply" };
      return { icon: "globe", verb: `Gmail · ${verbs[String(args.action)] ?? args.action}`, subject: short(args.query ?? args.subject ?? args.to ?? args.id ?? "") };
    }
    case "schedule":
      return { icon: "todo", verb: args.action === "create" ? "Schedule" : args.action === "delete" ? "Delete schedule" : "List schedules", subject: short(args.name ?? args.when ?? args.id ?? "") };
    default:
      return { icon: "plug", verb: name, subject: short(Object.values(args)[0] ?? "") };
  }
}
