import { FPS, type Audio } from "./types";

type Props = { audio: Audio; frames: number; set: (a: Audio) => void; hasAudio: boolean };

export function AudioTab({ audio, frames, set, hasAudio }: Props) {
  const max = Math.floor(frames / 2);
  return <div className="video-list video-audio">
    {!hasAudio && <p className="video-hint">This video has no sound.</p>}
    <label>Volume <b>{audio.mute ? "Muted" : `${Math.round(audio.gain * 100)}%`}</b>
      <input type="range" min="0" max="2" step="0.05" value={audio.gain} disabled={audio.mute} onChange={e => set({ ...audio, gain: Number(e.target.value) })} /></label>
    <label className="row"><input type="checkbox" checked={audio.mute} onChange={e => set({ ...audio, mute: e.target.checked })} /> Mute this shot</label>
    <label>Fade in and out <b>{(audio.fade_frames / FPS).toFixed(1)}s</b>
      <input type="range" min="0" max={max} step="1" value={Math.min(audio.fade_frames, max)} onChange={e => set({ ...audio, fade_frames: Number(e.target.value) })} /></label>
    <p className="video-hint">Applies to this shot. Sound continues across cuts.</p>
  </div>;
}
