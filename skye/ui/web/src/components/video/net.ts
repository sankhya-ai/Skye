import { api } from "../../api";
import type { Job } from "./types";

export const base = "/api/media";
const token = new URLSearchParams(window.location.search).get("token") ?? "";
export const post = <T,>(path: string, data: unknown) => api<T>(path, { method: "POST", body: JSON.stringify(data) });

export async function mediaBlob(path: string, as = "") {
  const res = await fetch(path, { headers: { "X-Skye-Token": token } });
  if (!res.ok) throw new Error((await res.json()).error ?? "Video unavailable");
  return as === "text" ? await res.text() : URL.createObjectURL(await res.blob());
}

/** XHR, not fetch: the browser reports upload progress only here. */
export function upload(file: File, detect: boolean, onProgress: (ratio: number) => void): Promise<Job> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${base}/import`);
    xhr.setRequestHeader("X-Skye-Token", token);
    xhr.setRequestHeader("X-Video-Name", encodeURIComponent(file.name.replace(/\.[^.]+$/, "")));
    xhr.setRequestHeader("X-Scene-Detection", String(detect));
    xhr.upload.onprogress = e => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onerror = () => reject(new Error("Upload failed. Is Skye still running?"));
    xhr.onload = () => {
      let body: any = {};
      try { body = JSON.parse(xhr.responseText); } catch { /* handled below */ }
      xhr.status < 300 ? resolve(body) : reject(new Error(body.error ?? "Upload failed"));
    };
    xhr.send(file);
  });
}
