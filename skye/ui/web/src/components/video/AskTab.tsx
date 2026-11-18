import { useEffect, useRef, useState } from "react";
import { ArrowUp, Check, Loader2, RotateCcw } from "lucide-react";
import type { Agent, LogEntry } from "./types";

const IDEAS = ["Add a title", "Cinematic look", "Black and white", "Add a lower third"];
type Props = { agents: Agent[]; log: LogEntry[]; shot: string; shotNumber: number; running: boolean; stage?: string;
  canUndo: string | null; undo: () => void; send: (goal: string, adapter: string) => void };

export function AskTab({ agents, log, shot, shotNumber, running, stage, canUndo, undo, send }: Props) {
  const [goal, setGoal] = useState(""), [adapter, setAdapter] = useState("claude-code");
  const end = useRef<HTMLDivElement>(null);
  const mine = log.filter(e => e.shot === shot);
  const ready = agents.find(a => a.name === adapter)?.available ?? false;
  const any = agents.some(a => a.available);
  useEffect(() => { const first = agents.find(a => a.available); if (first && !agents.find(a => a.name === adapter)?.available) setAdapter(first.name); }, [agents]);
  useEffect(() => end.current?.scrollIntoView({ block: "end" }), [mine.length, running]);
  const go = () => { if (goal.trim() && ready && !running) { send(goal.trim(), adapter); setGoal(""); } };
  return <div className="video-ask">
    <div className="video-chat">
      {!mine.length && !running && <div className="video-empty"><strong>Shot {shotNumber}</strong>
        <p>One change at a time. Only this shot is edited.</p>
        <div className="video-ideas">{IDEAS.map(i => <button key={i} onClick={() => setGoal(i)}>{i}</button>)}</div></div>}
      {mine.map(e => <div key={e.at} className="video-turn">
        <p className="you">{e.goal}</p>
        <p className={e.status === "failed" ? "bot bad" : "bot"}>{e.status === "failed" ? (e.error ?? "Failed").split("\n").pop() : (e.answer || "Done").trim()}</p>
      </div>)}
      {running && <div className="video-turn"><p className="bot wait"><Loader2 size={13} className="animate-spin" /> {stage ?? "Working"}</p></div>}
      {!running && canUndo?.startsWith("Agent") && <button className="video-undo" onClick={undo}><RotateCcw size={12} /> Revert "{canUndo.slice(7, 40)}"</button>}
      <div ref={end} />
    </div>
    <div className="video-compose">
      <textarea aria-label="Ask your coding agent" rows={2} placeholder="Describe one change" value={goal} disabled={!any}
        onChange={e => setGoal(e.target.value)} onKeyDown={e => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey || !e.shiftKey)) { e.preventDefault(); go(); } }} />
      <div className="video-compose-row">
        {any && <select aria-label="Agent" value={adapter} onChange={e => setAdapter(e.target.value)}>
          {agents.map(a => <option key={a.name} value={a.name}>{a.name === "codex" ? "Codex" : "Claude Code"}{a.available ? "" : " (not found)"}</option>)}
        </select>}
        {any ? <button className="video-send" aria-label="Send" disabled={!goal.trim() || !ready || running} onClick={go}><ArrowUp size={14} /></button>
          : <span className="video-hint">Install Claude Code or Codex</span>}
      </div>
      <small><Check size={10} /> Uses your own login. Edits land in this shot only.</small>
    </div>
  </div>;
}
