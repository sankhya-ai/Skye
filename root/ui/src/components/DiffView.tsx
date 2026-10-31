import { useMemo } from "react";

import { lineDiff, type DiffRow } from "../lib/diff.ts";

export function DiffRows({ rows }: { rows: DiffRow[] }) {
  return (
    <div className="diffRows scroll">
      {rows.map((row, index) =>
        row.kind === "gap" ? (
          <div key={index} className="diffRow gap">
            {row.text}
          </div>
        ) : (
          <div key={index} className={`diffRow ${row.kind}`}>
            <span className="no">{row.oldNo ?? ""}</span>
            <span className="no">{row.newNo ?? ""}</span>
            <span className="sign">{row.kind === "add" ? "+" : row.kind === "del" ? "−" : " "}</span>
            <span className="text">{row.text || " "}</span>
          </div>
        ),
      )}
    </div>
  );
}

export function DiffHeader({ path, added, removed, extra }: { path: string; added: number; removed: number; extra?: string }) {
  return (
    <div className="diffHead">
      <span className="path">{path}</span>
      {extra ? <span className="faint small">{extra}</span> : null}
      <span className="plus">+{added}</span>
      <span className="minus">−{removed}</span>
    </div>
  );
}

export function TextDiff({ path, oldText, newText }: { path: string; oldText: string; newText: string }) {
  const { rows, added, removed } = useMemo(() => lineDiff(oldText, newText), [oldText, newText]);
  return (
    <div className="diff">
      <DiffHeader path={path} added={added} removed={removed} extra={oldText ? undefined : "new file"} />
      <DiffRows rows={rows} />
    </div>
  );
}
