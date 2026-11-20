import {useEffect, useRef, useState} from 'react';
import {api} from '../api';
import {post} from './video/net';
import {VideoView} from './VideoView';
import './studio.css';
import {Storyboard} from './Storyboard';
import {MotionWords} from './MotionWords';
import {MotionComposer} from './MotionComposer';

type Session = {url: string; origin: string};
type Selection = {projectId: string; time: number; refs: {kind: string; id?: string; clipId?: string; label?: string}[]};
type Result = {project: string; base: string; ops: unknown[]; answer?: string};
type Job = {id: string; status: string; error?: string; result?: Result};

export function StudioView() {
  const [words,setWords]=useState(false);
  const [compose,setCompose]=useState(false);
  const [preflight,setPreflight]=useState<{name:string;stage:{w:number;h:number};duration:number;mediaFiles:number;mediaBytes:number;cacheBytes:number;freeBytes:number;warning:string}|null>(null);
  const [projectDialog,setProjectDialog]=useState(false);
  const [projectName,setProjectName]=useState('');
  const [shape,setShape]=useState('portrait');
  const [projectBusy,setProjectBusy]=useState(false);
  const [projects,setProjects]=useState<{id:string;name:string}[]>([]);
  const [projectRefresh,setProjectRefresh]=useState(0);
  const upload=useRef<HTMLInputElement>(null);
  const [storyboard,setStoryboard]=useState(true);
  const [boardRefresh,setBoardRefresh]=useState(0);
  const [legacy, setLegacy] = useState(false);
  const [session, setSession] = useState<Session | null>(null);
  const [error, setError] = useState('');
  const [selection, setSelection] = useState<Selection | null>(null);
  const [goal, setGoal] = useState('');
  const [adapter, setAdapter] = useState('local');
  const [agents, setAgents] = useState<{name: string; available: boolean}[]>([]);
  const [job, setJob] = useState<Job | null>(null);
  const [answer, setAnswer] = useState('');
  const [applying, setApplying] = useState(false);
  const frame = useRef<HTMLIFrameElement>(null);
  const request = useRef('');
  const selected = [...new Set((selection?.refs ?? []).flatMap(r => r.kind === 'clip' && r.id ? [r.id] : r.kind === 'layer' && r.clipId ? [r.clipId] : []))];
  const busy = job?.status === 'running' || applying;
  const projectCommand=(body:Record<string,unknown>)=>{if(!session)return;setProjectBusy(true);setError('');frame.current?.contentWindow?.postMessage({type:'skye:project',request:'project',...body},session.origin);};
  useEffect(()=>{void post<{projects:{id:string;name:string}[]}>('/api/studio/projects',{}).then(r=>setProjects(r.projects??[])).catch(()=>{});},[session,projectRefresh]);
  const start = () => post<Session>('/api/studio/start', {}).then(s => {setSession(s); setError('');}).catch(e => setError(e.message));
  useEffect(() => {void start(); void api<{agents: {name: string; available: boolean}[]}>('/api/media/agents').then(r => setAgents(r.agents)).catch(() => undefined);}, []);
  useEffect(() => {
    const receive = (e: MessageEvent) => {
      if (!session || e.origin !== session.origin || e.source !== frame.current?.contentWindow) return;
      if(e.data?.type==='skye:project-result'){setProjectBusy(false);if(e.data.error)setError(String(e.data.error));else{setProjectDialog(false);setBoardRefresh(n=>n+1);setProjectRefresh(n=>n+1);}}
      if (e.data?.type === 'skye:selection') setSelection(e.data.selection);
      if (e.data?.type === 'skye:ask') setGoal(String(e.data.request?.text ?? '').slice(0, 4000));
      if (e.data?.type === 'skye:applied' && e.data.request === request.current) {
        setApplying(false);
        if (e.data.error) setError(String(e.data.error));
        else {setBoardRefresh(n=>n+1);setError(''); setAnswer('Applied');}
      }
    };
    window.addEventListener('message', receive);
    return () => window.removeEventListener('message', receive);
  }, [session]);
  useEffect(() => {
    if (!job || job.status !== 'running') return;
    let live = true;
    const timer = window.setInterval(async () => {
      try {
        const next = await api<Job>(`/api/media/jobs/${job.id}`);
        if (!live) return;
        setJob(next);
        if (next.status === 'failed') setError(next.error ?? 'Agent edit failed');
        if (next.status === 'done' && next.result && session) {
          setAnswer('Applying...'); setApplying(true);
          request.current = job.id;
          frame.current?.contentWindow?.postMessage({type: 'skye:apply', request: job.id, ...next.result}, session.origin);
        }
      } catch (e) {if (live) {setError(e instanceof Error ? e.message : String(e)); setJob(null);}}
    }, 500);
    return () => {live = false; window.clearInterval(timer);};
  }, [job?.id, job?.status, session]);
  // A closed/reloaded project must not leave an applying spinner indefinitely.
  useEffect(() => {
    if (!applying) return;
    const timer = window.setTimeout(() => {setApplying(false); setError('Editor did not confirm the edit. Check the timeline before retrying.');}, 15000);
    return () => window.clearTimeout(timer);
  }, [applying]);
  const send = async () => {
    if (!selection || !selected.length || busy) return;
    setError(''); setAnswer('');
    try {setJob(await post<Job>('/api/studio/ask', {project: selection.projectId, selected, goal, adapter}));}
    catch (e) {setError(e instanceof Error ? e.message : String(e));}
  };
  if (legacy) return <><div className="studio-legacy-bar"><button onClick={() => setLegacy(false)}>Back to Video</button></div><VideoView /></>;
  return <div className="skye-studio">
    <header className="studio-bar"><div><strong>Video</strong></div>
      <div><select aria-label="Current video project" disabled={projectBusy} value={selection?.projectId??''} onChange={e=>projectCommand({action:'open',project:e.target.value})}><option value="" disabled>Choose project</option>{projects.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select><button disabled={!session||projectBusy} onClick={()=>setProjectDialog(true)}>New project</button><button disabled={!selection?.projectId||projectBusy} onClick={()=>upload.current?.click()}>{projectBusy?'Working...':'Import media'}</button><input ref={upload} type="file" multiple accept="video/*,image/*,audio/*" hidden onChange={e=>{const files=[...(e.target.files??[])];if(files.length)projectCommand({action:'import',project:selection?.projectId,files});e.target.value='';}}/><button disabled={!selection?.projectId||busy} onClick={()=>setCompose(true)}>Compose</button><button disabled={!selection?.projectId||busy} onClick={()=>setWords(true)}>Words</button><button disabled={!selection?.projectId} onClick={()=>void post<NonNullable<typeof preflight>>('/api/studio/preflight',{project:selection?.projectId}).then(setPreflight).catch(e=>setError(e.message))}>Export check</button><button aria-pressed={storyboard} onClick={()=>{setStoryboard(s=>!s);setBoardRefresh(n=>n+1);}}>{storyboard?"Editor":"Back to storyboard"}</button><button onClick={() => setLegacy(true)}>Legacy projects</button></div></header>
    {preflight&&<div className="studio-board-dialog"><strong>Export check · {preflight.name}</strong><p>{preflight.stage.w}×{preflight.stage.h} project · {preflight.duration.toFixed(2)} seconds</p><p>{preflight.mediaFiles} source files · {(preflight.mediaBytes/1048576).toFixed(1)} MB<br/>Existing cache: {(preflight.cacheBytes/1048576).toFixed(1)} MB<br/>Disk available: {(preflight.freeBytes/1073741824).toFixed(1)} GB</p><button onClick={()=>{setPreflight(null);setStoryboard(false);}}>Export</button><button onClick={()=>setPreflight(null)}>Close</button></div>}
    {words&&selection?.projectId&&<MotionWords project={selection.projectId} onClose={()=>setWords(false)} onError={setError} onApply={r=>{if(!session)return;request.current='words-'+Date.now();setApplying(true);frame.current?.contentWindow?.postMessage({type:'skye:apply',request:request.current,...r},session.origin);}}/>}
    {compose&&selection?.projectId&&<MotionComposer project={selection.projectId} onClose={()=>setCompose(false)} onError={setError} onApply={r=>{if(!session)return;request.current='compose-'+Date.now();setApplying(true);frame.current?.contentWindow?.postMessage({type:'skye:apply',request:request.current,...r},session.origin);}}/>}
    {projectDialog&&<div className="studio-board-dialog"><strong>New video project</strong><input aria-label="Project name" value={projectName} placeholder="Untitled" onChange={e=>setProjectName(e.target.value)}/><select aria-label="Project aspect" value={shape} onChange={e=>setShape(e.target.value)}><option value="portrait">9:16 · Vertical reel</option><option value="landscape">16:9 · Landscape</option><option value="square">1:1 · Square</option></select><button disabled={projectBusy} onClick={()=>projectCommand({action:'new',name:projectName,shape})}>Create project</button><button disabled={projectBusy} onClick={()=>setProjectDialog(false)}>Cancel</button></div>}
    {error && <div role="alert" className="studio-error">{error}<button onClick={() => setError('')}>Dismiss</button></div>}
    <div className="studio-body">
      <main className={"studio-editor"+(storyboard?" with-board":"")}><div className="studio-frame">{session ? <iframe ref={frame} title="Skye video editor" referrerPolicy="origin" src={session.url} allow="clipboard-read; clipboard-write; fullscreen" /> : <div className="studio-start"><p>{error ? 'Editor not ready' : 'Opening editor...'}</p><button onClick={() => void start()}>Retry</button><code>python -m skye.studio.setup</code></div>}</div>{storyboard&&selection?.projectId&&<Storyboard key={selection.projectId} project={selection.projectId} refresh={boardRefresh} selected={selected} onError={setError} onSelect={id=>{setStoryboard(false);frame.current?.contentWindow?.postMessage({type:"skye:select-shot",id},session!.origin);}} onUndo={()=>{request.current="undo-"+Date.now();frame.current?.contentWindow?.postMessage({type:"skye:undo",request:request.current},session!.origin);}} onApply={result=>{request.current="storyboard-"+Date.now();frame.current?.contentWindow?.postMessage({type:"skye:apply",request:request.current,...result},session!.origin);}} />}</main>
      <aside className={"studio-agent"+(storyboard?" board-hidden":"")}><div><h2>Video editor</h2></div>
        <div className="studio-selection"><span>Scope</span><strong>{selected.length ? `${selected.length} selected clip${selected.length === 1 ? '' : 's'} + links` : 'Select clips'}</strong></div>
        <label>Agent<select value={adapter} onChange={e => setAdapter(e.target.value)}><option value="local">Local commands · free</option><option value="claude-code">Claude Code</option><option value="codex">Codex</option></select></label>
        {adapter !== 'local' && !agents.some(a => a.name === adapter && a.available) && <p className="studio-muted">Not installed</p>}
        <textarea aria-label="Ask the video agent" maxLength={4000} rows={5} value={goal} onChange={e => setGoal(e.target.value)} placeholder={adapter === 'local' ? 'caption "Your words"\npunch · shake · volume 50%' : 'Describe the selected shot edit...'} disabled={busy} />
        <button className="studio-send" disabled={busy || !goal.trim() || !selected.length || adapter !== 'local' && !agents.some(a => a.name === adapter && a.available)} onClick={() => void send()}>{busy ? applying ? 'Applying...' : 'Editing...' : 'Send'}</button>
        {answer && <p role="status" className="studio-answer">{answer}</p>}


      </aside>
    </div>
  </div>;
}
