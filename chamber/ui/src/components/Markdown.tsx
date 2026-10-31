import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * Renders a memory file (copied from supermemory's memoryrepo). [[wiki-links]] open the linked
 * file; `[source: talk/<id>#<msg>; by: …; added: …]` becomes a small chip opening that talk.
 */
export function Markdown({ text, onLink, onSource }: { text: string; onLink?: (target: string) => void; onSource?: (talk: string) => void }) {
  const source = text
    .replace(/\[source:\s*talk\/([\w-]+)(?:#([\w-]+))?\s*(?:;\s*by:\s*([\w.-]+))?\s*(?:;\s*added:\s*([0-9-]+))?[^\]]*\]/gi, (_, talk: string, _msg?: string, by?: string, added?: string) =>
      ` [↗${by ? ` ${by}` : ""}${added ? ` · ${added}` : ""}](src:${talk})`,
    )
    .replace(/\[\[([^\]]+)\]\]/g, (_, target: string) => `[${target}](wiki:${encodeURIComponent(target)})`);
  return (
    <div className="prose-memory">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        urlTransform={(url) => url}
        components={{
          a: ({ href, children }) => {
            if (href?.startsWith("src:")) {
              return (
                <a href="#" title="Open the talk this came from" onClick={(e) => (e.preventDefault(), onSource?.(href.slice(4)))} className="ml-1 inline-flex rounded border px-1 align-middle font-mono text-[10px] text-muted-foreground no-underline hover:text-foreground">
                  {children}
                </a>
              );
            }
            if (href?.startsWith("wiki:")) {
              const target = decodeURIComponent(href.slice(5));
              return (
                <a href="#" onClick={(e) => (e.preventDefault(), onLink?.(target))} className="text-dot underline decoration-dot/30 underline-offset-2 hover:decoration-dot">
                  {children}
                </a>
              );
            }
            return <a href={href} target="_blank" rel="noreferrer" className="underline underline-offset-2">{children}</a>;
          },
        }}
      >
        {source}
      </ReactMarkdown>
    </div>
  );
}
