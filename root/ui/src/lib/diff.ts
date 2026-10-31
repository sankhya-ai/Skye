// Line diffs for the runtime's `diff` display blocks (old_text → new_text) and
// a parser for git's unified diff, both producing the same row shape.

export type DiffRow =
  | { kind: "context" | "add" | "del"; text: string; oldNo?: number; newNo?: number }
  | { kind: "gap"; text: string };

export interface FileDiff {
  path: string;
  rows: DiffRow[];
  added: number;
  removed: number;
  binary?: boolean;
}

const CONTEXT = 3;
const MAX_CELLS = 4_000_000;

function splitLines(text: string): string[] {
  if (!text) return [];
  const lines = text.split("\n");
  if (lines[lines.length - 1] === "") lines.pop();
  return lines;
}

/** Rows for old → new, with unchanged runs folded down to CONTEXT lines. */
export function lineDiff(oldText: string, newText: string): { rows: DiffRow[]; added: number; removed: number } {
  const a = splitLines(oldText);
  const b = splitLines(newText);
  let start = 0;
  while (start < a.length && start < b.length && a[start] === b[start]) start += 1;
  let endA = a.length;
  let endB = b.length;
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) {
    endA -= 1;
    endB -= 1;
  }
  const midA = a.slice(start, endA);
  const midB = b.slice(start, endB);
  const ops: { kind: "context" | "add" | "del"; text: string }[] = [];
  for (let i = 0; i < start; i += 1) ops.push({ kind: "context", text: a[i] });
  if (midA.length * midB.length > MAX_CELLS) {
    for (const line of midA) ops.push({ kind: "del", text: line });
    for (const line of midB) ops.push({ kind: "add", text: line });
  } else {
    ops.push(...lcsOps(midA, midB));
  }
  for (let i = endA; i < a.length; i += 1) ops.push({ kind: "context", text: a[i] });

  const rows: DiffRow[] = [];
  let oldNo = 1;
  let newNo = 1;
  let added = 0;
  let removed = 0;
  const numbered = ops.map((op) => {
    const row: DiffRow = { ...op };
    if (op.kind === "context") {
      row.oldNo = oldNo++;
      row.newNo = newNo++;
    } else if (op.kind === "del") {
      row.oldNo = oldNo++;
      removed += 1;
    } else {
      row.newNo = newNo++;
      added += 1;
    }
    return row;
  });
  // Fold unchanged runs longer than 2*CONTEXT.
  let i = 0;
  while (i < numbered.length) {
    if (numbered[i].kind !== "context") {
      rows.push(numbered[i]);
      i += 1;
      continue;
    }
    let j = i;
    while (j < numbered.length && numbered[j].kind === "context") j += 1;
    const run = numbered.slice(i, j);
    const leading = i === 0;
    const trailing = j === numbered.length;
    const keepHead = leading ? 0 : CONTEXT;
    const keepTail = trailing ? 0 : CONTEXT;
    if (run.length > keepHead + keepTail + 1) {
      rows.push(...run.slice(0, keepHead));
      const hidden = run.length - keepHead - keepTail;
      rows.push({ kind: "gap", text: `${hidden} unchanged line${hidden === 1 ? "" : "s"}` });
      rows.push(...run.slice(run.length - keepTail));
    } else {
      rows.push(...run);
    }
    i = j;
  }
  return { rows, added, removed };
}

function lcsOps(a: string[], b: string[]): { kind: "context" | "add" | "del"; text: string }[] {
  const n = a.length;
  const m = b.length;
  const table: Uint32Array[] = [];
  for (let i = 0; i <= n; i += 1) table.push(new Uint32Array(m + 1));
  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) {
      table[i][j] = a[i] === b[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }
  const ops: { kind: "context" | "add" | "del"; text: string }[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      ops.push({ kind: "context", text: a[i] });
      i += 1;
      j += 1;
    } else if (table[i + 1][j] >= table[i][j + 1]) {
      ops.push({ kind: "del", text: a[i] });
      i += 1;
    } else {
      ops.push({ kind: "add", text: b[j] });
      j += 1;
    }
  }
  while (i < n) ops.push({ kind: "del", text: a[i++] });
  while (j < m) ops.push({ kind: "add", text: b[j++] });
  return ops;
}

/** Files from `git diff` output. */
export function parseUnifiedDiff(text: string): FileDiff[] {
  const files: FileDiff[] = [];
  let current: FileDiff | null = null;
  let oldNo = 0;
  let newNo = 0;
  for (const line of text.split("\n")) {
    if (line.startsWith("diff --git ")) {
      const match = / b\/(.+)$/.exec(line);
      current = { path: match ? match[1] : line.slice(11), rows: [], added: 0, removed: 0 };
      files.push(current);
      continue;
    }
    if (!current) continue;
    if (line.startsWith("Binary files")) {
      current.binary = true;
      continue;
    }
    if (line.startsWith("+++ ") || line.startsWith("--- ") || line.startsWith("index ") || line.startsWith("new file") || line.startsWith("deleted file") || line.startsWith("similarity") || line.startsWith("rename ")) {
      continue;
    }
    const hunk = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$/.exec(line);
    if (hunk) {
      oldNo = Number(hunk[1]);
      newNo = Number(hunk[2]);
      current.rows.push({ kind: "gap", text: hunk[3].trim() || `line ${newNo}` });
      continue;
    }
    if (line.startsWith("+")) {
      current.rows.push({ kind: "add", text: line.slice(1), newNo: newNo++ });
      current.added += 1;
    } else if (line.startsWith("-")) {
      current.rows.push({ kind: "del", text: line.slice(1), oldNo: oldNo++ });
      current.removed += 1;
    } else if (line.startsWith(" ")) {
      current.rows.push({ kind: "context", text: line.slice(1), oldNo: oldNo++, newNo: newNo++ });
    }
  }
  return files;
}
