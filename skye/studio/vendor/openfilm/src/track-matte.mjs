/** A video clip's prepared matte, shared by preview and exact export frames.
 * The manifest maps source seconds to alpha PNGs in the clip box's coordinates.
 * No model runs in the browser. Missing/out-of-range mattes fail, never reveal
 * the original unmasked image as a successful export. */
const manifests = new Map();
const images = new Map();
export function matteFrame(manifest, sourceTime) {
  if (!manifest || !Number.isFinite(manifest.fps) || manifest.fps <= 0 ||
      !Number.isFinite(manifest.start) || !Array.isArray(manifest.frames) || !manifest.frames.length ||
      manifest.frames.some(p => typeof p !== 'string' || !p || /(^\/|:|\\|\.\.)/.test(p))) {
    throw new Error('matte manifest needs fps, source start, and relative alpha PNG frame paths');
  }
  const frame = Math.floor((sourceTime - manifest.start) * manifest.fps + 1e-5);
  if (frame < 0 || frame >= manifest.frames.length) throw new Error(`matte has no frame for source time ${sourceTime}`);
  return manifest.frames[frame];
}
async function loadedImage(url) {
  let job = images.get(url);
  if (!job) {
    job = new Promise((resolve, reject) => {
      const image = new Image();
      image.onload = () => resolve(image);
      image.onerror = () => reject(new Error(`matte image could not load: ${url}`));
      image.src = url;
    });
    images.set(url, job);
    if (images.size > 6) images.delete(images.keys().next().value);
  }
  return job;
}
export async function applyTrackMatte(c, sourceTime) {
  if (!c.row.matte) {
    if (c.el.dataset.filmMatte) {
      for (const p of ['mask-image', 'mask-size', 'mask-position', 'mask-repeat', 'mask-mode']) c.el.style.removeProperty(p);
      delete c.el.dataset.filmMatte;
    }
    return;
  }
  const url = new URL(c.row.matte, document.baseURI);
  if (url.origin !== location.origin) throw new Error('matte manifest must be in the project');
  if (!manifests.has(url.href)) manifests.set(url.href, fetch(url).then(async r => {
    if (!r.ok) throw new Error(`matte manifest failed: ${r.status}`);
    return r.json();
  }));
  const manifest = await manifests.get(url.href);
  const frame = new URL(matteFrame(manifest, sourceTime), url).href;
  await loadedImage(frame);
  c.el.dataset.filmMatte = '1';
  c.el.style.maskImage = `url(${JSON.stringify(frame)})`;
  c.el.style.maskSize = '100% 100%';
  c.el.style.maskPosition = 'center';
  c.el.style.maskRepeat = 'no-repeat';
  c.el.style.maskMode = 'alpha';
}
