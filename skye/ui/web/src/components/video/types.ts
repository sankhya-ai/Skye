export const FPS = 30;
export type Unit = { id: string; start: number; end: number };
export type Row = { id: string; name: string; frames: number; width: number; height: number; first: string; shots: number };
export type Latest = { file: string; revision: string; rendered: string[]; reused: string[]; shot?: string | null };
export type Project = Row & { revision: string; units: Unit[]; stale: boolean; latest?: Latest | null };
export type Files = Record<"visual.html" | "captions.json" | "audio.json", string>;
export type Caption = { start: number; end: number; text: string };
export type Audio = { gain: number; mute: boolean; fade_frames: number };
export type Job = { id: string; kind?: string; status: string; error?: string; stage?: string; current?: number; total?: number; result?: Latest & { answer?: string } };
export type Agent = { name: string; available: boolean };
export type LogEntry = { at: number; shot: string; goal: string; adapter: string; status: string; answer?: string; error?: string };
export type Hist = { undo: string | null; redo: string | null };
export const seconds = (frame: number) => `${(frame / FPS).toFixed(2)}s`;
export const clock = (frame: number) => {
  const total = Math.max(0, frame) / FPS;
  return `${Math.floor(total / 60)}:${(total % 60).toFixed(1).padStart(4, "0")}`;
};
export const parse = <T,>(text: string, fallback: T): T => { try { return JSON.parse(text) as T; } catch { return fallback; } };
export const usesSource = (html: string) => /source/.test(html.replace(/<!--[\s\S]*?-->|\/\*[\s\S]*?\*\/|(?<![:"'])\/\/[^\n]*/g, ""));
export const typing = (t: HTMLElement) => t.tagName === "TEXTAREA" || t.tagName === "SELECT"
  || (t.tagName === "INPUT" && !["range", "checkbox"].includes((t as HTMLInputElement).type));
