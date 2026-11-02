// Minimal markdown renderer for artifact previews. Artifact content is untrusted
// worker output, so this never uses dangerouslySetInnerHTML: every line is plain
// React text (auto-escaped), formatting is a small allow-list, and links render
// as styled text with the URL shown, never as navigation - the artifacts UI has
// no open-in-tab/download path by design (scripts/svg_security_probe.py).

function inline(text: string, keyBase: string): React.ReactNode[] {
  // **bold**, *italic*, `code`, [label](url) - applied as non-overlapping splits.
  const out: React.ReactNode[] = [];
  const pattern = /(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/g;
  let last = 0;
  let i = 0;
  for (const m of text.matchAll(pattern)) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    const k = `${keyBase}-${i++}`;
    if (tok.startsWith("**")) {
      out.push(<strong key={k} className="font-medium text-foreground">{tok.slice(2, -2)}</strong>);
    } else if (tok.startsWith("`")) {
      out.push(<code key={k} className="rounded-sm bg-muted px-1 font-mono text-[11px]">{tok.slice(1, -1)}</code>);
    } else if (tok.startsWith("[")) {
      const close = tok.indexOf("](");
      const label = tok.slice(1, close);
      const url = tok.slice(close + 2, -1);
      out.push(
        <span key={k} className="text-muted-foreground">
          {label} <span className="font-mono text-[11px]">({url})</span>
        </span>,
      );
    } else {
      out.push(<em key={k}>{tok.slice(1, -1)}</em>);
    }
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function MarkdownView({ source }: { source: string }) {
  const lines = source.split("\n");
  const blocks: React.ReactNode[] = [];
  let para: string[] = [];
  let list: string[] = [];
  let code: string[] | null = null;
  let table: string[][] = [];
  let ordered = false;
  let key = 0;

  const cells = (row: string) =>
    row.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
  const flushTable = () => {
    if (!table.length) return;
    const [head, ...body] = table;
    blocks.push(
      <div key={key++} className="overflow-x-auto rounded-md border border-border">
        <table className="w-full border-collapse text-left text-xs">
          <thead className="bg-muted/60">
            <tr>
              {head.map((c, j) => (
                <th key={j} className="px-2.5 py-1.5 font-medium text-foreground">{inline(c, `th${key}-${j}`)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {body.map((row, r) => (
              <tr key={r} className="border-t border-border align-top">
                {row.map((c, j) => (
                  <td key={j} className="px-2.5 py-1.5 text-foreground/85">{inline(c, `td${key}-${r}-${j}`)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>,
    );
    table = [];
  };

  const flushPara = () => {
    if (para.length) {
      blocks.push(
        <p key={key++} className="text-sm leading-relaxed text-foreground/90">
          {inline(para.join(" "), `p${key}`)}
        </p>,
      );
      para = [];
    }
  };
  const flushList = () => {
    if (list.length) {
      blocks.push(
        ordered ? (
          <ol key={key++} className="list-decimal space-y-0.5 pl-5 text-sm text-foreground/90">
            {list.map((item, j) => (
              <li key={j}>{inline(item, `l${key}-${j}`)}</li>
            ))}
          </ol>
        ) : (
          <ul key={key++} className="list-disc space-y-0.5 pl-5 text-sm text-foreground/90">
            {list.map((item, j) => (
              <li key={j}>{inline(item, `l${key}-${j}`)}</li>
            ))}
          </ul>
        ),
      );
      list = [];
      ordered = false;
    }
  };

  for (const line of lines) {
    const trimmed = line.trim();
    if (trimmed.startsWith("```")) {
      if (code === null) {
        flushPara();
        flushList();
        code = [];
      } else {
        blocks.push(
          <pre key={key++} className="overflow-x-auto rounded-sm bg-muted p-2 font-mono text-xs text-foreground/90">
            {code.join("\n")}
          </pre>,
        );
        code = null;
      }
      continue;
    }
    if (code !== null) {
      code.push(line);
      continue;
    }
    if (trimmed.startsWith("|") && trimmed.endsWith("|") && trimmed.length > 1) {
      flushPara();
      flushList();
      // the |---|---| separator row carries no content
      if (!/^\|[\s:|-]+\|$/.test(trimmed)) table.push(cells(trimmed));
      continue;
    }
    flushTable();
    const heading = /^(#{1,4})\s+(.*)$/.exec(trimmed);
    if (heading) {
      flushPara();
      flushList();
      const level = heading[1].length;
      const cls =
        level === 1
          ? "text-base font-semibold text-foreground"
          : level === 2
            ? "text-sm font-semibold text-foreground"
            : "text-sm font-medium text-foreground";
      blocks.push(
        <p key={key++} className={cls}>
          {inline(heading[2], `h${key}`)}
        </p>,
      );
      continue;
    }
    if (/^[-*]\s+/.test(trimmed) || /^\d+[.)]\s+/.test(trimmed)) {
      flushPara();
      const isOrdered = /^\d+[.)]\s+/.test(trimmed);
      if (list.length && isOrdered !== ordered) flushList();
      ordered = isOrdered;
      list.push(trimmed.replace(/^([-*]|\d+[.)])\s+/, ""));
      continue;
    }
    if (/^(-{3,}|\*{3,})$/.test(trimmed)) {
      flushPara();
      flushList();
      blocks.push(<hr key={key++} className="border-border" />);
      continue;
    }
    if (trimmed === "") {
      flushPara();
      flushList();
      continue;
    }
    list.length === 0 && para.push(trimmed);
    if (list.length > 0) {
      flushList();
      para.push(trimmed);
    }
  }
  flushPara();
  flushList();
  flushTable();
  if (code !== null) {
    blocks.push(
      <pre key={key++} className="overflow-x-auto rounded-sm bg-muted p-2 font-mono text-xs text-foreground/90">
        {code.join("\n")}
      </pre>,
    );
  }
  if (blocks.length === 0) {
    return <p className="text-xs text-muted-foreground">Empty document.</p>;
  }
  return <div className="space-y-2">{blocks}</div>;
}
