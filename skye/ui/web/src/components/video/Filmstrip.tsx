import { useEffect, useState } from "react";
import { Scissors } from "lucide-react";
import { base, mediaBlob } from "./net";
import { FPS, type Project } from "./types";

export function Thumb({ project, shot, nonce }: { project: string; shot: string; nonce: number }) {
  const [url, setUrl] = useState("");
  useEffect(() => {
    let live = true, object = ""; setUrl("");
    void mediaBlob(`${base}/${project}/thumb/${shot}`).then(v => { object = v; live ? setUrl(v) : URL.revokeObjectURL(v); }).catch(() => undefined);
    return () => { live = false; if (object) URL.revokeObjectURL(object); };
  }, [project, shot, nonce]);
  return url ? <img src={url} alt="" /> : <i className="video-thumb-wait" />;
}

type Props = { project: Project; selected: string; frame: number; busy: boolean; select: (id: string) => void;
  split: () => void; join: (id: string) => void };

export function Filmstrip({ project, selected, frame, busy, select, split, join }: Props) {
  const head = frame / project.frames * 100;
  return <div className="video-film">
    <div className="video-film-bar"><span>{project.units.length} {project.units.length === 1 ? "shot" : "shots"}</span>
      <button disabled={busy} onClick={split} title="Split at playhead (S)"><Scissors size={13} /> Split</button></div>
    <div className="video-strip" role="listbox" aria-label="Shots">
      {project.units.map((u, i) => <div key={u.id} className="video-strip-item" style={{ flexGrow: Math.max(1, u.end - u.start), flexBasis: 0 }}>
        <button role="option" aria-label={`Shot ${i + 1}`} aria-selected={selected === u.id} className={selected === u.id ? "shot on" : "shot"} onClick={() => select(u.id)}
          title={`Shot ${i + 1} · ${((u.end - u.start) / FPS).toFixed(1)}s`}>
          <Thumb project={project.id} shot={u.id} nonce={u.end - u.start} /><span>{String(i + 1).padStart(2, "0")}</span>
        </button>
        {i < project.units.length - 1 && <button className="cut" disabled={busy} aria-label={`Join shots ${i + 1} and ${i + 2}`} title="Join shots" onClick={() => join(u.id)} />}
      </div>)}
      <div className="video-playhead" style={{ left: `${head}%` }} />
    </div>
  </div>;
}
