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
  let key = 0;

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
        <ul key={key++} className="list-disc space-y-0.5 pl-5 text-sm text-foreground/90">
          {list.map((item, j) => (
            <li key={j}>{inline(item, `l${key}-${j}`)}</li>
          ))}
        </ul>,
      );
      list = [];
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
    if (/^[-*]\s+/.test(trimmed)) {
      flushPara();
      list.push(trimmed.replace(/^[-*]\s+/, ""));
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
