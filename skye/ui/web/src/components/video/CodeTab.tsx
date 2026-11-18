type Props = { html: string; set: (v: string) => void; error: string; disabled: boolean };

export function CodeTab({ html, set, error, disabled }: Props) {
  return <div className="video-code">
    <div className="video-code-head"><span>visual.html</span><span>window.frame(t)</span></div>
    <textarea spellCheck={false} aria-label="Shot code" value={html} disabled={disabled} onChange={e => set(e.target.value)} />
    {error && <div className="video-code-error" role="alert">{error}</div>}
  </div>;
}
