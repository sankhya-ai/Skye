import {useEffect, useRef, useState} from 'react';
import {api} from '../api';
import {post} from './video/net';
import {VideoView} from './VideoView';
import './studio.css';

type Session = {url: string; origin: string};
type Selection = {projectId: string; time: number; refs: {kind: string; id?: string; clipId?: string; label?: string}[]};
type Result = {project: string; base: string; ops: unknown[]; answer?: string};
type Job = {id: string; status: string; error?: string; result?: Result};

export function StudioView() {
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
  const start = () => post<Session>('/api/studio/start', {}).then(s => {setSession(s); setError('');}).catch(e => setError(e.message));
  useEffect(() => {void start(); void api<{agents: {name: string; available: boolean}[]}>('/api/media/agents').then(r => setAgents(r.agents)).catch(() => undefined);}, []);
  useEffect(() => {
    const receive = (e: MessageEvent) => {
      if (!session || e.origin !== session.origin || e.source !== frame.current?.contentWindow) return;
      if (e.data?.type === 'skye:selection') setSelection(e.data.selection);
      if (e.data?.type === 'skye:ask') setGoal(String(e.data.request?.text ?? '').slice(0, 4000));
      if (e.data?.type === 'skye:applied' && e.data.request === request.current) {
        setApplying(false);
        if (e.data.error) setError(String(e.data.error));
        else {setError(''); setAnswer(a => a + ' Applied. Use Undo to revert this edit.');}
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
          setAnswer(next.result.answer ?? 'Edit ready'); setApplying(true);
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
  if (legacy) return <><div className="studio-legacy-bar"><button onClick={() => setLegacy(false)}>Back to Video</button><span>Legacy shot projects. Kept unchanged.</span></div><VideoView /></>;
  return <div className="skye-studio">
    <header className="studio-bar"><div><strong>Video</strong><span>Your team's editor</span></div>
      <button onClick={() => setLegacy(true)}>Legacy projects</button></header>
    {error && <div role="alert" className="studio-error">{error}<button onClick={() => setError('')}>Dismiss</button></div>}
    <div className="studio-body">
      <main className="studio-editor">{session ? <iframe ref={frame} title="Skye video editor" referrerPolicy="origin" src={session.url} allow="clipboard-read; clipboard-write; fullscreen" /> : <div className="studio-start"><p>{error ? 'Editor not ready' : 'Opening editor...'}</p><button onClick={() => void start()}>Retry</button><code>python -m skye.studio.setup</code></div>}</main>
      <aside className="studio-agent"><div><h2>Video editor</h2><p>One edit at a time. Select clips in the timeline. Linked audio is included.</p></div>
        <div className="studio-selection"><span>Scope</span><strong>{selected.length ? `${selected.length} selected clip${selected.length === 1 ? '' : 's'} + links` : 'Select clips'}</strong><small>{selected.join(', ') || 'Your agent reads a film snapshot, not media files or service keys.'}</small></div>
        <label>Agent<select value={adapter} onChange={e => setAdapter(e.target.value)}><option value="local">Local commands · free</option><option value="claude-code">Claude Code</option><option value="codex">Codex</option></select></label>
        {adapter !== 'local' && !agents.some(a => a.name === adapter && a.available) && <p className="studio-muted">Not detected here. Set up your local agent to use Ask. Manual editing works without it.</p>}
        <textarea aria-label="Ask the video agent" maxLength={4000} rows={5} value={goal} onChange={e => setGoal(e.target.value)} placeholder={adapter === 'local' ? 'caption "Your words"\npunch · shake · volume 50%' : 'Describe the selected shot edit...'} disabled={busy} />
        <button className="studio-send" disabled={busy || !goal.trim() || !selected.length || adapter !== 'local' && !agents.some(a => a.name === adapter && a.available)} onClick={() => void send()}>{busy ? applying ? 'Applying...' : 'Editing...' : 'Send'}</button>
        {answer && <p role="status" className="studio-answer">{answer}</p>}
        <details className="studio-muted"><summary>Commands</summary><p>caption "Words" · text "Words" · punch · shake · flash · grain · glitch · mirror · volume 50% · mute</p><p>track "Words" uses a textured ground plane. reference "assets/ref.mp4" text "New words" measures a local reference. Both need OpenCV; OCR uses Tesseract when installed.</p></details>
        <div className="studio-note">Canvas, timeline and color use the same film. Agent edits use the same operations and Undo as manual edits.<br /><br />Local commands need no model. Claude Code and Codex use your own installed agent. Remote generation and automatic updates are off.</div>
      </aside>
    </div>
  </div>;
}
