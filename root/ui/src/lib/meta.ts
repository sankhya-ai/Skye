// The short note at the end of a tool row — "598 lines", "+12 −3", "exit 1",
// "no matches" — read from what the runtime returned.

import { lineDiff } from "./diff.ts";
import { outputText, type ToolBlock } from "./transcript.ts";

export interface StepMeta {
  text: string;
  added?: number;
  removed?: number;
  error?: boolean;
}

function lineCount(text: string): number {
  const trimmed = text.replace(/\n+$/, "");
  return trimmed ? trimmed.split("\n").length : 0;
}

function plural(n: number, one: string, many = `${one}s`): string {
  return `${n.toLocaleString()} ${n === 1 ? one : many}`;
}

/** Seconds between the call and its result, when long enough to be worth saying. */
export function stepDuration(step: ToolBlock): string {
  if (step.started == null || step.finished == null) return "";
  const seconds = step.finished - step.started;
  if (seconds < 1) return "";
  if (seconds < 60) return `${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds)}s`;
  return `${Math.floor(seconds / 60)}m ${String(Math.round(seconds % 60)).padStart(2, "0")}s`;
}

export function stepMeta(step: ToolBlock): StepMeta {
  const result = step.result;
  if (step.approval?.status === "reject") return { text: "rejected" };
  if (!result) return { text: "" };
  const brief = result.display?.find((d) => d.type === "brief")?.text ?? "";
  const message = result.message ?? "";
  if (result.is_error) {
    const exit = /exit code:? (\d+)/i.exec(message);
    if (exit) return { text: `exit ${exit[1]}`, error: true };
    return { text: (brief || "failed").toLowerCase(), error: true };
  }
  switch (step.name) {
    case "ReadFile": {
      if (/repeated read/i.test(brief)) return { text: "already read" };
      const lines = /(\d+) lines? read/.exec(message);
      return { text: lines ? plural(Number(lines[1]), "line") : "" };
    }
    case "WriteFile":
    case "StrReplaceFile": {
      let added = 0;
      let removed = 0;
      for (const block of result.display ?? []) {
        if (block.type !== "diff") continue;
        const diff = lineDiff(String(block.old_text ?? ""), String(block.new_text ?? ""));
        added += diff.added;
        removed += diff.removed;
      }
      return added || removed ? { text: "", added, removed } : { text: "" };
    }
    case "Shell": {
      const lines = lineCount(outputText(result));
      return { text: lines ? plural(lines, "line") : "" };
    }
    case "Grep": {
      if (/no matches/i.test(message)) return { text: "no matches" };
      const lines = lineCount(outputText(result));
      return { text: lines ? plural(lines, "result") : "" };
    }
    case "Glob": {
      if (/no matches/i.test(message)) return { text: "no matches" };
      const lines = lineCount(outputText(result));
      return { text: lines ? plural(lines, "file") : "" };
    }
    case "SearchWeb":
      return { text: brief.replace(/^Searched via /i, "via ") };
    default:
      return { text: "" };
  }
}

/** A model's thinking made readable. Some models stream reasoning with a line
 * break between every word; rejoined, it reads as the prose it is. Runs of
 * blank lines collapse to one paragraph break. */
export function tidyThought(text: string): string {
  let tidy = text.replace(/\r/g, "").trim();
  const lines = tidy.split("\n").filter((line) => line.trim());
  const words = tidy.split(/\s+/).filter(Boolean).length;
  if (lines.length >= 4 && words / lines.length < 2) tidy = tidy.replace(/[ \t]*\n\s*/g, " ");
  return tidy.replace(/\n{3,}/g, "\n\n");
}
