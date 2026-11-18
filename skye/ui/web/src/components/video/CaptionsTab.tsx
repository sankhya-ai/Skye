import { useRef } from "react";
import { FileText, Plus, X } from "lucide-react";
import { FPS, type Caption } from "./types";

type Props = { captions: Caption[]; frames: number; local: number; set: (c: Caption[]) => void; importFile: (file: File) => void };
const sec = (f: number) => (f / FPS).toFixed(1);
const clamp = (f: number, frames: number) => Math.min(frames, Math.max(0, Math.round(f)));

export function CaptionsTab({ captions, frames, local, set, importFile }: Props) {
  const pick = useRef<HTMLInputElement>(null);
  const edit = (i: number, patch: Partial<Caption>) => set(captions.map((c, n) => n === i ? { ...c, ...patch } : c));
  const add = () => {
    const start = clamp(local, frames - 1);
    set([...captions, { start, end: Math.min(frames, start + 45), text: "" }].sort((a, b) => a.start - b.start));
  };
  return <div className="video-list">
    {!captions.length && <div className="video-empty"><strong>No captions</strong><p>Add one at the playhead.</p></div>}
    {captions.map((c, i) => <div className="video-caption" key={i}>
      <div><label>In<input type="number" step="0.1" min="0" value={sec(c.start)} onChange={e => edit(i, { start: clamp(Number(e.target.value) * FPS, frames) })} /></label>
        <label>Out<input type="number" step="0.1" min="0" value={sec(c.end)} onChange={e => edit(i, { end: clamp(Number(e.target.value) * FPS, frames) })} /></label>
        <button aria-label="Remove caption" onClick={() => set(captions.filter((_, n) => n !== i))}><X size={13} /></button></div>
      <textarea aria-label="Caption text" rows={2} maxLength={500} placeholder="Text" value={c.text} onChange={e => edit(i, { text: e.target.value })} />
      {c.end <= c.start && <small className="bad">Out must be after In</small>}
    </div>)}
    <div className="video-add-row"><button className="video-add" onClick={add}><Plus size={13} /> Caption</button>
      <button className="video-add" title="Adds to every shot it overlaps" onClick={() => pick.current?.click()}><FileText size={13} /> Import SRT</button></div>
    <input ref={pick} type="file" accept=".srt,.vtt,text/vtt" hidden onChange={e => { const f = e.target.files?.[0]; if (f) importFile(f); e.target.value = ""; }} />
  </div>;
}
