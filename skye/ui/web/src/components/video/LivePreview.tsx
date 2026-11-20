import { useEffect, useMemo, useRef, useState } from "react";
import { FPS, type Caption, usesSource } from "./types";

const CSP = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; font-src data:; connect-src 'none'; frame-src 'none'; worker-src 'none'; base-uri 'none'; form-action 'none'";

type Props = { harness: string; html: string; captions: Caption[]; width: number; height: number; frame: number;
  start: number; video: HTMLVideoElement | null; onError: (message: string) => void };

/** Shot code runs in an opaque-origin sandboxed iframe with no network. Only frames and text cross. */
export function LivePreview({ harness, html, captions, width, height, frame, start, video, onError }: Props) {
  const box = useRef<HTMLDivElement>(null), view = useRef<HTMLIFrameElement>(null);
  const [ready, setReady] = useState(false), [scale, setScale] = useState(1);
  const composite = usesSource(html);
  const doc = useMemo(() => `<meta http-equiv="Content-Security-Policy" content="${CSP}">
<style>html,body{margin:0;width:100%;height:100%;overflow:hidden;background:transparent}*{animation:none!important;transition:none!important}</style>
<script>${harness}</script>${html}<script>__skye.listen(m => parent.postMessage({skye: 1, ...m}, '*'))</script>`, [harness, html]);
  const errors = useRef(onError); errors.current = onError;
  useEffect(() => {
    setReady(false);
    const listen = (e: MessageEvent) => {
      if (e.source !== view.current?.contentWindow || e.data?.skye !== 1) return;
      if (e.data.type === "ready") {
        view.current?.contentWindow?.postMessage({ type: "setup", options: { width, height, designWidth: width, designHeight: height, composite } }, "*");
        setReady(true); errors.current("");
      }
      if (e.data.type === "error") errors.current(String(e.data.message).slice(0, 300));
    };
    window.addEventListener("message", listen);
    return () => window.removeEventListener("message", listen);
  }, [doc, width, height, composite]);
  useEffect(() => {
    if (!ready) return;
    let live = true;
    void (async () => {
      const local = Math.max(0, frame - start);
      const caption = captions.filter(c => c.start <= local && local < c.end).map(c => c.text).join("\n");
      const image = composite && video && video.readyState >= 2 ? await createImageBitmap(video) : undefined;
      if (live) view.current?.contentWindow?.postMessage({ type: "frame", t: local / FPS, caption, image, id: local }, "*", image ? [image] : []);
    })();
    return () => { live = false; };
  }, [ready, frame, start, captions, composite, video]);
  useEffect(() => {
    const el = box.current; if (!el) return;
    const fit = () => setScale(el.clientWidth / width);
    fit(); const observer = new ResizeObserver(fit); observer.observe(el);
    return () => observer.disconnect();
  }, [width]);
  return <div className="video-overlay" ref={box}>
    <iframe ref={view} title="Shot preview" sandbox="allow-scripts" srcDoc={doc} style={{ width, height, transform: `scale(${scale})` }} />
  </div>;
}
