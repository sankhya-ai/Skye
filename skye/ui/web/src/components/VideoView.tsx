import { useEffect, useRef, useState } from "react";
import { ArrowLeft, Check, Code2, Download, Film, Loader2, Play, Plus, Scissors, Upload, Volume2 } from "lucide-react";
import { api } from "../api";
import "./video.css";

type Unit = { id: string; start: number; end: number };
type Row = { id: string; name: string; frames: number; width: number; height: number };
type Project = Row & { fps: number; revision: string; units: Unit[]; stale: boolean; latest?: {file: string; revision: string; rendered: string[]; reused: string[]} };
type Job = {id: string; kind?: string; status: string; error?: string; stage?: string; current?: number; total?: number; shot?: string; result?: Project};
type Files = Record<"visual.html" | "captions.json" | "audio.json", string>;
type Proposal = {proposal: string; path: string; job: string};
const base = "/api/media";
const token = new URLSearchParams(window.location.search).get("token") ?? "";
const time = (frame: number) => `${(frame / 30).toFixed(2)}s`;
const post = <T,>(path: string, data: unknown) => api<T>(path, {method: "POST", body: JSON.stringify(data)});
async function mediaBlob(path: string) {
  const res = await fetch(path, {headers: {"X-Skye-Token": token}});
  if (!res.ok) throw new Error((await res.json()).error ?? "Video unavailable");
  return URL.createObjectURL(await res.blob());
}
function Thumbnail({project, shot}: {project: string; shot: string}) {
  const [url, setUrl] = useState("");
  useEffect(() => {
    let active = true, object = "";
    void mediaBlob(`${base}/${project}/thumb/${shot}`).then(value => {
      object = value; if (active) setUrl(value); else URL.revokeObjectURL(value);
    }).catch(() => undefined);
    return () => { active = false; if (object) URL.revokeObjectURL(object); };
  }, [project, shot]);
  return url ? <img src={url} alt="Shot" /> : <div className="shot-thumb-loading" />;
}

export function VideoView() {
  const [rows, setRows] = useState<Row[]>([]);
  const [project, setProject] = useState<Project | null>(null);
  const [shot, setShot] = useState("");
  const [files, setFiles] = useState<Files | null>(null);
  const [saved, setSaved] = useState("");
  const [tab, setTab] = useState<keyof Files>("visual.html");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [job, setJob] = useState<Job | null>(null);
  const [detect, setDetect] = useState(true);
  const [source, setSource] = useState("");
  const [output, setOutput] = useState("");
  const [showSource, setShowSource] = useState(false);
  const [profile, setProfile] = useState("preview");
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [goal, setGoal] = useState("");
  const [adapter, setAdapter] = useState("claude-code");
  const [playhead, setPlayhead] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const player = useRef<HTMLVideoElement>(null);
  const dirty = Boolean(files && JSON.stringify(files) !== saved);
  const selected = project?.units.find(u => u.id === shot);
  const running = busy || job?.status === "running";

  const refresh = () => api<{projects: Row[]}>(base).then(r => setRows(r.projects));
  const open = async (id: string) => {
    const p = await api<Project>(`${base}/${id}`); setProject(p);
    setShot(current => p.units.some(u => u.id === current) ? current : p.units[0].id);
    return p;
  };
  useEffect(() => { void refresh().catch(e => setError(String(e.message))); }, []);
  useEffect(() => {
    if (!project || !shot) return;
    let active = true;
    setFiles(null); setProposal(null);
    void api<{files: Files; revision: string}>(`${base}/${project.id}/units/${shot}`).then(r => {
      if (!active) return;
      setFiles(r.files); setSaved(JSON.stringify(r.files));
      setProject(p => p ? {...p, revision:r.revision} : p);
    }).catch(e => setError(e.message));
    return () => { active = false; };
  }, [project?.id, shot]);
  useEffect(() => {
    if (!project) return;
    let active = true, object = "";
    setSource("");
    void mediaBlob(`${base}/${project.id}/source`).then(url => {
      object = url; if (active) setSource(url); else URL.revokeObjectURL(url);
    }).catch(e => setError(e.message));
    return () => {active = false; if (object) URL.revokeObjectURL(object);};
  }, [project?.id]);
  useEffect(() => {
    let active = true, object = ""; setOutput("");
    if (!project?.latest) return;
    void mediaBlob(`${base}/${project.id}/exports/${project.latest.file}`).then(url => {
      object = url; if (active) setOutput(url); else URL.revokeObjectURL(url);
    }).catch(e => setError(e.message));
    return () => {active = false; if (object) URL.revokeObjectURL(object);};
  }, [project?.id, project?.latest?.file]);
  useEffect(() => {
    if (!job || job.status !== "running") return;
    let active = true;
    const timer = setInterval(() => {
      void api<Job>(`${base}/jobs/${job.id}`).then(async current => {
        if (!active) return;
        setJob(current);
        if (current.status === "failed") setError(current.error ?? "Video job failed");
        if (current.status === "done") {
          if (current.result?.id) await open(current.result.id);
          else if (project) {
            await open(project.id);
            if (current.kind === "edit") {
              const fresh = await api<{files: Files}>(`${base}/${project.id}/units/${shot}`);
              setFiles(fresh.files); setSaved(JSON.stringify(fresh.files)); setProposal(null);
            }
          }
          await refresh();
        }
      }).catch(e => {if (active) setError(e.message);});
    }, 1000);
    return () => {active = false; clearInterval(timer);};
  }, [job?.id, job?.status, project?.id]);

  async function act(task: () => Promise<unknown>) {
    setBusy(true); setError("");
    try {await task();} catch (e) {setError(e instanceof Error ? e.message : String(e));}
    finally {setBusy(false);}
  }
  async function upload(file: File) {
    if (file.size > 512 * 1024 * 1024) {setError("Choose a video under 512 MB"); return;}
    await act(async () => {
      const res = await fetch(`${base}/import`, {method:"POST", headers:{"X-Skye-Token":token,
        "X-Video-Name":encodeURIComponent(file.name.replace(/\.[^.]+$/, "")), "X-Scene-Detection":String(detect)}, body:file});
      const result = await res.json(); if (!res.ok) throw new Error(result.error);
      setJob(result); setProject(null); setShot("");
    });
  }
  async function save() {
    if (!project || !files) return;
    await post(`${base}/${project.id}/save`, {shot, revision:project.revision, files});
    setSaved(JSON.stringify(files)); await open(project.id);
  }
  const select = (id: string) => {
    if (dirty && !confirm("Discard unsaved changes?")) return;
    setShot(id);
    const next = project?.units.find(u => u.id === id);
    if (next && player.current) player.current.currentTime = next.start / 30;
  };
  async function recut(mode: "split" | "merge") {
    if (!project || !selected) return;
    const boundaries = [0, ...project.units.map(u => u.end)];
    const frame = Math.round((player.current?.currentTime ?? 0) * 30);
    if (mode === "split" && !(frame > selected.start && frame < selected.end)) throw new Error("Move the playhead inside the selected shot first");
    const cuts = mode === "split" ? [...boundaries, frame].sort((a,b) => a-b) : boundaries.filter(n => n !== selected.end);
    await post(`${base}/${project.id}/recut`, {revision:project.revision, cuts});
    const p = await open(project.id); setShot(""); setTimeout(() => setShot(p.units[0].id), 0);
  }

  return <div className="video-workspace">
    <input ref={input} type="file" accept="video/*" hidden onChange={e => {const f=e.target.files?.[0]; if(f) void upload(f); e.target.value="";}} />
    <header className="video-header">
      <div className="video-heading"><span className="video-eyebrow"><Film size={14}/> STUDIO</span>
        <h1>{project?.name ?? "Video"}</h1></div>
      <div className="video-actions">
        {project && <button disabled={!!running} onClick={() => {if (!dirty || confirm("Discard unsaved changes?")) {setProject(null); setShot("");}}}><ArrowLeft size={14}/> Library</button>}
        <button disabled={!!running} onClick={() => input.current?.click()}><Plus size={15}/> Import</button>
        {project && <><select aria-label="Render quality" value={profile} onChange={e=>setProfile(e.target.value)}><option value="preview">Preview · 640px</option><option value="final">Final · source size</option></select>
          <button className="video-primary" disabled={!!running || dirty} onClick={() => void act(async()=>{setShowSource(false);setJob(await post<Job>(`${base}/${project.id}/render`,{profile}));})}><Play size={13}/>{running ? "Working" : "Render"}</button></>}
      </div>
    </header>
    {error && <div className="video-error" role="alert">{error}<button onClick={()=>setError("")}>Dismiss</button></div>}
    {job?.status === "running" && <div className="video-progress" role="status"><Loader2 size={14} className="animate-spin"/>{job.stage ?? (job.kind === "import" ? "Indexing video" : `Rendering ${job.current ?? 0} / ${job.total ?? "…"}`)}<span>Local processing. You can keep using Skye.</span></div>}
    {!project ? <>
      <div className="video-import" onDragOver={e=>e.preventDefault()} onDrop={e=>{e.preventDefault(); const f=e.dataTransfer.files[0];if(f&&!running)void upload(f);}}>
        <div className="video-import-icon"><Film size={24}/></div><h2>A video. Separate, editable shots.</h2>
        <p>Keep your footage. Change the code around it.</p>
        <button className="video-primary" disabled={!!running} onClick={()=>input.current?.click()}><Upload size={15}/> Choose video</button>
        <label><input type="checkbox" checked={detect} onChange={e=>setDetect(e.target.checked)}/> Detect cuts</label>
        <small>Up to 10 minutes · 512 MB · processed on this machine</small>
      </div>
      {!!rows.length && <section className="video-library"><h2>Recent</h2><div>{rows.map(row=><button key={row.id} onClick={()=>void act(()=>open(row.id))}>
        <Film size={20}/><strong>{row.name}</strong><span>{time(row.frames)} · {row.width} × {row.height}</span></button>)}</div></section>}
    </> : <>
      <div className="video-grid">
        <section className="video-preview">
          <div className="video-panel-header"><span>{showSource || !output ? "Source" : "Render"}</span><div>
            {output && <button className="video-text-button" onClick={()=>setShowSource(v=>!v)}>{showSource ? "Show render" : "Show source"}</button>}
            {project.stale && !showSource && output && <span className="video-stale">Changes not rendered</span>}
            <span>{project.width} × {project.height} · 30 fps</span></div></div>
          <div className="video-stage"><video ref={player} key={(showSource || !output) ? source : output} src={(showSource || !output) ? source : output} controls playsInline onTimeUpdate={e=>setPlayhead(e.currentTarget.currentTime)}/></div>
          <div className="video-preview-footer"><span className="video-mono">{playhead.toFixed(2)} / {(project.frames/30).toFixed(2)}s</span>
            {output && <a href={output} download={`${project.name}-${project.latest?.file.endsWith("final.webm") ? "final" : "preview"}.webm`}><Download size={14}/> Download render</a>}
          </div>
          <div className="video-timeline">{project.units.map((u,i)=><button key={u.id} style={{flex:Math.max(1,u.end-u.start)}} className={shot===u.id?"selected":""} onClick={()=>select(u.id)} title={`${time(u.start)} – ${time(u.end)}`}><span>{String(i+1).padStart(2,"0")}</span><i/></button>)}</div>
          <div className="video-section-label"><h2>Shots <span>{project.units.length}</span></h2><span>Fixed cuts · local time</span></div>
          <div className="video-shots">{project.units.map((u,i)=><button key={u.id} onClick={()=>select(u.id)} className={shot===u.id?"selected":""}>
            <div className="shot-image"><Thumbnail project={project.id} shot={u.id}/><span>{String(i+1).padStart(2,"0")}</span></div>
            <strong>Shot {String(i+1).padStart(2,"0")}</strong><small>{time(u.start)} – {time(u.end)}</small>
          </button>)}</div>
          <div className="video-cut-actions"><button disabled={!!running || dirty} onClick={()=>void act(()=>recut("split"))}><Scissors size={13}/> Split at playhead</button>
            <button disabled={!!running || dirty || selected?.end===project.frames} onClick={()=>void act(()=>recut("merge"))}>Merge next</button><span>Adjust cuts before editing</span></div>
        </section>
        <aside className="video-inspector">
          <div className="video-panel-header"><span>{`Shot ${String(Number(shot.replace("shot-", ""))).padStart(2, "0")}`}</span><span className="video-mono">{selected ? time(selected.end-selected.start) : ""}</span></div>
          <div className="video-tabs">{(["visual.html","captions.json","audio.json"] as const).map(name=><button key={name} className={tab===name?"active":""} onClick={()=>setTab(name)}>{name==="visual.html"?<Code2 size={13}/>:name==="audio.json"?<Volume2 size={13}/>:null}{name==="visual.html"?"Code":name==="captions.json"?"Captions":"Audio"}</button>)}</div>
          <div className="video-code-label"><span>{tab}</span><span>{tab === "visual.html" ? "frame(t)" : "local frames"}</span></div>
          <textarea spellCheck={false} aria-label={tab} value={files?.[tab]??""} disabled={!files || !!running} onChange={e=>setFiles(f=>f?{...f,[tab]:e.target.value}:f)} />
          <div className="video-save"><span>{!files ? "Loading" : dirty ? "Unsaved changes" : <><Check size={12}/> Saved</>}</span><button disabled={!dirty || !!running} onClick={()=>void act(save)}>Save shot</button></div>
          <div className="video-agent"><h3>Edit with your agent</h3>
            <input aria-label="Shot edit request" placeholder="What should change in this shot?" value={goal} onChange={e=>setGoal(e.target.value)}/>
            <div><select aria-label="Video agent" value={adapter} onChange={e=>setAdapter(e.target.value)}><option value="claude-code">Claude Code</option><option value="codex">Codex</option></select>
              <button disabled={!!running || dirty} onClick={()=>void act(async()=>setProposal(await post<Proposal>(`${base}/${project.id}/prepare`,{shot,goal,adapter})))}>Prepare edit</button></div>
            {proposal && <div className="video-handoff"><p>Runs {adapter === "codex" ? "Codex" : "Claude Code"} using your own account. Reviews these files, then renders locally.</p>
              <button className="video-primary" disabled={!!running || dirty} onClick={()=>void act(async()=>{setShowSource(false);setJob(await post<Job>(`${base}/${project.id}/run-edit`,{proposal:proposal.proposal}));})}>Approve and run</button>
              <details><summary>Run manually</summary><code>skye run {proposal.job}</code><button onClick={()=>void navigator.clipboard.writeText(`skye run ${proposal.job}`).catch(()=>setError("Copy unavailable. Select the command instead."))}>Copy command</button>
              <button disabled={!!running} onClick={()=>void act(async()=>{await post(`${base}/${project.id}/apply`,{proposal:proposal.proposal});await open(project.id);setShot("");setTimeout(()=>setShot(shot),0);setProposal(null);})}>Apply proposal</button></details></div>}
            <p>Only this shot's files enter the project. Rendering stays local.</p>
          </div>
        </aside>
      </div>
      <footer className="video-note"><span>Footage stays video. Code owns the layers.</span><span>{project.latest && !project.stale ? `${project.latest.rendered.length} rendered · ${project.latest.reused.length} reused` : "Captions and audio stop at each cut."}</span></footer>
    </>}
  </div>;
}
