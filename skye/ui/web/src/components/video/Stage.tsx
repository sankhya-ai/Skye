import { useEffect, useRef, useState } from "react";
import { Pause, Play, Volume2, VolumeX } from "lucide-react";
import { LivePreview } from "./LivePreview";
import { FPS, clock, typing, type Caption, type Project, type Unit } from "./types";

type Props = { project: Project; source: string; render: string; mode: "live" | "render"; setMode: (m: "live" | "render") => void;
  harness: string; active?: { unit: Unit; html: string; captions: Caption[] }; frame: number; seek: (frame: number) => void;
  onFrame: (frame: number) => void; onPreviewError: (m: string) => void; onVideo: (v: HTMLVideoElement | null) => void; busy: boolean };

export function Stage({ project, source, render, mode, setMode, harness, active, frame, seek, onFrame, onPreviewError, onVideo, busy }: Props) {
  const player = useRef<HTMLVideoElement>(null);
  const [playing, setPlaying] = useState(false), [muted, setMuted] = useState(false);
  const url = mode === "render" && render ? render : source;
  useEffect(() => {
    const v = player.current; if (!v) return;
    onVideo(v);
    let stop = false;
    const tick = () => { if (stop) return; if (v.readyState >= 1) onFrame(Math.round(v.currentTime * FPS)); v.requestVideoFrameCallback ? v.requestVideoFrameCallback(tick) : requestAnimationFrame(tick); };
    tick();
    return () => { stop = true; onVideo(null); };
  }, [url]);
  const toggle = () => { const v = player.current; if (!v) return; v.paused ? void v.play() : v.pause(); };
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (typing(t) || e.metaKey || e.ctrlKey) return;
      if (e.key === " ") { e.preventDefault(); toggle(); }
      if (e.key === "ArrowLeft") seek(frame - (e.shiftKey ? 10 : 1));
      if (e.key === "ArrowRight") seek(frame + (e.shiftKey ? 10 : 1));
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [frame, seek]);
  const live = mode === "live";
  return <div className="video-stage-wrap">
    <div className="video-stage-bar">
      <div className="video-seg" role="tablist" aria-label="Preview">
        <button role="tab" aria-selected={live} className={live ? "on" : ""} onClick={() => setMode("live")}>Live</button>
        <button role="tab" aria-selected={!live} disabled={!render} className={!live ? "on" : ""} onClick={() => setMode("render")}>Render</button>
      </div>
      <span>{project.stale && !live ? "Out of date" : `${project.width} × ${project.height}`}</span>
    </div>
    <div className="video-stage">
      <div className="video-frame" style={{ aspectRatio: `${project.width}/${project.height}` }}>
        <video ref={player} key={url} src={url} playsInline muted={muted} disablePictureInPicture disableRemotePlayback
          onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)}
          onLoadedMetadata={e => { e.currentTarget.currentTime = frame / FPS; }} />
        {live && active && !busy && <LivePreview harness={harness} html={active.html} captions={active.captions} width={project.width}
          height={project.height} frame={frame} start={active.unit.start} video={player.current} onError={onPreviewError} />}
      </div>
    </div>
    <div className="video-transport">
      <button aria-label={playing ? "Pause" : "Play"} onClick={toggle}>{playing ? <Pause size={14} /> : <Play size={14} />}</button>
      <span className="video-mono">{clock(frame)}</span>
      <input aria-label="Playhead" type="range" min={0} max={project.frames - 1} value={Math.min(frame, project.frames - 1)} onChange={e => seek(Number(e.target.value))} />
      <span className="video-mono">{clock(project.frames)}</span>
      <button aria-label={muted ? "Unmute" : "Mute"} onClick={() => setMuted(v => !v)}>{muted ? <VolumeX size={14} /> : <Volume2 size={14} />}</button>
    </div>
  </div>;
}
