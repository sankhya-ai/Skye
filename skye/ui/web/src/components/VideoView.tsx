import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft, Download, Film, Loader2, Play, Plus, Redo2, Undo2, Upload } from "lucide-react";
import { api } from "../api";
import { AskTab } from "./video/AskTab";
import { AudioTab } from "./video/AudioTab";
import { CaptionsTab } from "./video/CaptionsTab";
import { CodeTab } from "./video/CodeTab";
import { Filmstrip, Thumb } from "./video/Filmstrip";
import { Stage } from "./video/Stage";
import { base, mediaBlob, post, upload } from "./video/net";
import { FPS, parse, seconds, typing, type Agent, type Audio, type Caption, type Files, type Hist, type Job, type LogEntry, type Project, type Row } from "./video/types";
import "./video.css";

type Tab = "ask" | "captions" | "audio" | "code";
const TABS: [Tab, string][] = [["ask", "Ask"], ["captions", "Captions"], ["audio", "Audio"], ["code", "Code"]];
const FRESH: Audio = { gain: 1, mute: false, fade_frames: 0 };

export function VideoView() {
  const [rows, setRows] = useState<Row[]>([]);
  const [project, setProject] = useState<Project | null>(null);
  const [shot, setShot] = useState("");
  const [draft, setDraft] = useState<Files | null>(null);
  const [saved, setSaved] = useState("");
  const [cache, setCache] = useState<Record<string, Files>>({});
  const [tab, setTab] = useState<Tab>("ask");
  const [error, setError] = useState(""), [previewError, setPreviewError] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const [sending, setSending] = useState<number | null>(null);
  const [detect, setDetect] = useState(true), [profile, setProfile] = useState("preview");
  const [source, setSource] = useState(""), [render, setRender] = useState("");
  const [mode, setMode] = useState<"live" | "render">("live");
  const [harness, setHarness] = useState("");
  const [frame, setFrame] = useState(0);
  const [agents, setAgents] = useState<Agent[]>([]), [log, setLog] = useState<LogEntry[]>([]), [hist, setHist] = useState<Hist>({ undo: null, redo: null });
  const input = useRef<HTMLInputElement>(null), video = useRef<HTMLVideoElement | null>(null), [videoEl, setVideoEl] = useState<HTMLVideoElement | null>(null);
  const timer = useRef<number>();
  const running = job?.status === "running";
  const units = project?.units ?? [];
  const selected = units.find(u => u.id === shot), number = units.findIndex(u => u.id === shot) + 1;
  const active = units.find(u => frame >= u.start && frame < u.end) ?? units[units.length - 1];
  const dirty = Boolean(draft && JSON.stringify(draft) !== saved);
  const captions = parse<Caption[]>(draft?.["captions.json"] ?? "[]", []);
  const audio = parse<Audio>(draft?.["audio.json"] ?? "", FRESH);

  const refresh = () => api<{ projects: Row[] }>(base).then(r => setRows(r.projects));
  const side = (id: string) => Promise.all([
    api<Hist>(`${base}/${id}/history`).then(setHist),
    api<{ entries: LogEntry[] }>(`${base}/${id}/agent-log`).then(r => setLog(r.entries))]);
  const open = async (id: string, keep = shot) => {
    const p = await api<Project>(`${base}/${id}`);
    setProject(p); setCache({}); void side(id);
    setShot(p.units.some(u => u.id === keep) ? keep : p.units[0].id);
    return p;
  };
  const loadShot = async (id: string, pid: string) => {
    const r = await api<{ files: Files; revision: string }>(`${base}/${pid}/units/${id}`);
    return r;
  };
  useEffect(() => {
    void refresh().catch(e => setError(e.message));
    void mediaBlob(`${base}/harness.js`, "text").then(setHarness).catch(e => setError(e.message));
    void api<{ agents: Agent[] }>(`${base}/agents`).then(r => setAgents(r.agents)).catch(() => undefined);
  }, []);
  useEffect(() => {
    if (!project || !shot) return;
    let live = true; setDraft(null);
    void loadShot(shot, project.id).then(r => { if (!live) return; setDraft(r.files); setSaved(JSON.stringify(r.files)); setProject(p => p ? { ...p, revision: r.revision } : p); }).catch(e => setError(e.message));
    return () => { live = false; };
  }, [project?.id, shot, project?.units.length, hist.undo, hist.redo]);
  useEffect(() => {
    if (!project || !active || active.id === shot || cache[active.id]) return;
    let live = true;
    void loadShot(active.id, project.id).then(r => live && setCache(c => ({ ...c, [active.id]: r.files }))).catch(() => undefined);
    return () => { live = false; };
  }, [project?.id, active?.id, shot]);
  useEffect(() => {
    if (!project) return;
    let live = true, object = ""; setSource("");
    void mediaBlob(`${base}/${project.id}/source`).then(u => { object = u; live ? setSource(u) : URL.revokeObjectURL(u); }).catch(e => setError(e.message));
    return () => { live = false; if (object) URL.revokeObjectURL(object); };
  }, [project?.id]);
  useEffect(() => {
    let live = true, object = ""; setRender("");
    if (!project?.latest) return;
    void mediaBlob(`${base}/${project.id}/exports/${project.latest.file}`).then(u => { object = u; live ? setRender(u) : URL.revokeObjectURL(u); }).catch(() => undefined);
    return () => { live = false; if (object) URL.revokeObjectURL(object); };
  }, [project?.id, project?.latest?.file]);

  const flush = useCallback(async () => {
    if (!project || !draft || JSON.stringify(draft) === saved) return;
    window.clearTimeout(timer.current);
    try {
      const body = { shot, revision: project.revision, files: draft, coalesce: true };
      const r = await post<{ revision: string }>(`${base}/${project.id}/save`, body);
      setSaved(JSON.stringify(draft)); setProject(p => p ? { ...p, revision: r.revision, stale: Boolean(p.latest) } : p);
      setCache(c => ({ ...c, [shot]: draft })); setError(""); void api<Hist>(`${base}/${project.id}/history`).then(setHist);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  }, [project, draft, saved, shot]);
  useEffect(() => { if (!dirty) return; timer.current = window.setTimeout(() => void flush(), 700); return () => window.clearTimeout(timer.current); }, [draft, dirty, flush]);

  useEffect(() => {
    if (!job || job.status !== "running") return;
    let live = true;
    const t = setInterval(() => void api<Job>(`${base}/jobs/${job.id}`).then(async cur => {
      if (!live) return;
      setJob(cur);
      if (cur.status === "failed") { setError(cur.error ?? "Video job failed"); if (project) void side(project.id); }
      if (cur.status === "done") {
        if (cur.kind === "import" && (cur.result as unknown as Project)?.id) { setFrame(0); await open((cur.result as unknown as Project).id, ""); }
        else if (project) { await open(project.id); if (cur.kind === "render") setMode("render"); }
        void refresh();
      }
    }).catch(e => live && setError(e.message)), 800);
    return () => { live = false; clearInterval(t); };
  }, [job?.id, job?.status]);

  async function act(task: () => Promise<unknown>) {
    setError("");
    try { await flush(); await task(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  }
  const seek = useCallback((f: number) => {
    const to = Math.max(0, Math.min((project?.frames ?? 1) - 1, f));
    if (video.current) video.current.currentTime = to / FPS;
    setFrame(to);
    const unit = project?.units.find(u => to >= u.start && to < u.end);
    if (unit && unit.id !== shot) void flush().then(() => setShot(unit.id));
  }, [project, shot, flush]);
  const choose = (id: string) => { const u = units.find(x => x.id === id); if (u) seek(u.start); };
  const split = () => act(async () => {
    if (!project || !active) return;
    const r = await post<{ shot: string }>(`${base}/${project.id}/split`, { revision: project.revision, shot: active.id, frame });
    await open(project.id, r.shot);
  });
  const join = (id: string) => act(async () => {
    if (!project) return;
    const r = await post<{ shot: string; notes: string[] }>(`${base}/${project.id}/merge`, { revision: project.revision, shot: id });
    await open(project.id, r.shot);
    if (r.notes.length) setError(r.notes.join(" "));
  });
  const step = (dir: "undo" | "redo") => act(async () => {
    if (!project) return;
    await post(`${base}/${project.id}/${dir}`, { revision: project.revision }); await open(project.id);
  });
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      const mod = e.metaKey || e.ctrlKey;
      if (mod && e.key.toLowerCase() === "z" && !typing(t)) { e.preventDefault(); void step(e.shiftKey ? "redo" : "undo"); }
      if (typing(t) || mod) return;
      if (e.key === "s" && !running) void split();
      if (e.key === "[" || e.key === "]") { const i = units.findIndex(u => u.id === shot) + (e.key === "]" ? 1 : -1); if (units[i]) choose(units[i].id); }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  });
  async function pick(file: File) {
    if (file.size > 512 * 1024 * 1024) { setError("Choose a video under 512 MB"); return; }
    setError(""); setSending(0);
    try { setJob(await upload(file, detect, setSending)); setProject(null); setShot(""); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setSending(null); }
  }
  const setFile = (name: keyof Files, value: string) => setDraft(d => d ? { ...d, [name]: value } : d);
  const activeFiles = active && (active.id === shot ? draft : cache[active.id]);
  const progress = job?.kind === "import" ? "Preparing video" : job?.kind === "edit" ? "Working" : "Rendering";

  return <div className="video-workspace">
    <input ref={input} type="file" accept="video/*" hidden onChange={e => { const f = e.target.files?.[0]; if (f) void pick(f); e.target.value = ""; }} />
    <header className="video-header">
      <div className="video-heading">
        {project && <button className="icon" aria-label="Library" disabled={running} onClick={() => void flush().then(() => { setProject(null); setShot(""); })}><ArrowLeft size={15} /></button>}
        <h1>{project?.name ?? "Video"}</h1></div>
      <div className="video-actions">
        {project && <><button className="icon" aria-label="Undo" title={hist.undo ? `Undo ${hist.undo}` : "Nothing to undo"} disabled={!hist.undo || running} onClick={() => void step("undo")}><Undo2 size={15} /></button>
          <button className="icon" aria-label="Redo" title={hist.redo ? `Redo ${hist.redo}` : "Nothing to redo"} disabled={!hist.redo || running} onClick={() => void step("redo")}><Redo2 size={15} /></button>
          <select aria-label="Quality" value={profile} onChange={e => setProfile(e.target.value)}><option value="preview">Preview</option><option value="final">Full quality</option></select>
          {render && project.latest && <a className="btn" href={render} download={`${project.name}.webm`}><Download size={14} /> Download</a>}
          <button className="video-primary" disabled={running} onClick={() => void act(async () => setJob(await post<Job>(`${base}/${project!.id}/render`, { profile })))}><Play size={13} /> Render</button></>}
        {!project && <button disabled={running || sending !== null} onClick={() => input.current?.click()}><Plus size={15} /> Import</button>}
      </div>
    </header>
    {error && <div className="video-error" role="alert"><span>{error}</span><button onClick={() => setError("")}>Dismiss</button></div>}
    {(running || sending !== null) && <div className="video-progress" role="status"><Loader2 size={14} className="animate-spin" />
      {sending !== null ? `Uploading ${Math.round(sending * 100)}%` : (job?.stage ?? (job?.total ? `${progress} ${job.current} of ${job.total}` : progress))}
      <i style={{ width: `${sending !== null ? sending * 100 : job?.total ? (job.current ?? 0) / job.total * 100 : 0}%` }} /></div>}
    {!project ? <>
      <div className="video-import" onDragOver={e => e.preventDefault()} onDrop={e => { e.preventDefault(); const f = e.dataTransfer.files[0]; if (f && !running) void pick(f); }}>
        <div className="video-import-icon"><Film size={22} /></div>
        <h2>Drop a video</h2><p>It becomes shots you can edit one at a time.</p>
        <button className="video-primary" disabled={running || sending !== null} onClick={() => input.current?.click()}><Upload size={15} /> Choose video</button>
        <label><input type="checkbox" checked={detect} onChange={e => setDetect(e.target.checked)} /> Find cuts</label>
        <small>10 minutes · 512 MB · stays on this machine</small>
      </div>
      {!!rows.length && <section className="video-library"><h2>Recent</h2><div>{rows.map(r => <button key={r.id} onClick={() => void act(() => open(r.id, ""))}>
        <span className="video-lib-thumb"><Thumb project={r.id} shot={r.first} nonce={0} /></span><strong>{r.name}</strong><span>{seconds(r.frames)} · {r.shots} {r.shots === 1 ? "shot" : "shots"}</span></button>)}</div></section>}
    </> : <div className="video-grid">
      <section className="video-main">
        <Stage project={project} source={source} render={render} mode={mode} setMode={setMode} harness={harness}
          active={active && activeFiles ? { unit: active, html: activeFiles["visual.html"], captions: parse<Caption[]>(activeFiles["captions.json"], []) } : undefined}
          frame={frame} seek={seek} onFrame={setFrame} onPreviewError={setPreviewError} onVideo={el => { video.current = el; setVideoEl(el); }} busy={Boolean(running)} />
        <Filmstrip project={project} selected={shot} frame={frame} busy={Boolean(running)} select={choose} split={() => void split()} join={id => void join(id)} />
      </section>
      <aside className="video-inspector">
        <div className="video-tabs" role="tablist">{TABS.map(([id, label]) => <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? "on" : ""} onClick={() => setTab(id)}>{label}</button>)}
          <span>{number ? `Shot ${number}` : ""}{selected ? ` · ${seconds(selected.end - selected.start)}` : ""}</span></div>
        {!draft || !selected ? <div className="video-empty"><Loader2 size={14} className="animate-spin" /></div> : <>
          {tab === "ask" && <AskTab agents={agents} log={log} shot={shot} shotNumber={number} running={Boolean(running)} stage={job?.stage} canUndo={hist.undo} undo={() => void step("undo")}
            send={(goal, adapter) => void act(async () => setJob(await post<Job>(`${base}/${project.id}/ask`, { shot, goal, adapter, revision: project.revision })))} />}
          {tab === "captions" && <CaptionsTab captions={captions} frames={selected.end - selected.start} local={frame - selected.start} set={c => setFile("captions.json", JSON.stringify(c, null, 2))} />}
          {tab === "audio" && <AudioTab audio={audio} frames={selected.end - selected.start} hasAudio set={a => setFile("audio.json", JSON.stringify(a))} />}
          {tab === "code" && <CodeTab html={draft["visual.html"]} set={v => setFile("visual.html", v)} error={previewError} disabled={Boolean(running)} />}
          <div className="video-status">{dirty ? "Saving" : "Saved"}{project.stale && project.latest ? " · render out of date" : ""}
            {project.latest && !project.stale ? ` · ${project.latest.rendered.length} rendered, ${project.latest.reused.length} reused` : ""}</div></>}
      </aside>
    </div>}
  </div>;
}
