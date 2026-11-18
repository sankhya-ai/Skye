// Original Skye implementation of the page-per-shot frame(t) convention.
// Runs shot code only in a fresh, sandboxed Chromium context, never in Node.
import fs from 'node:fs/promises';
import { once } from 'node:events';
import { chromium } from 'playwright';

const spec = JSON.parse(await fs.readFile(process.argv[2], 'utf8'));
const browser = await chromium.launch({headless: true, chromiumSandbox: true,
  ...(process.env.SKYE_CHROMIUM ? {executablePath: process.env.SKYE_CHROMIUM} : {})});
try {
  const context = await browser.newContext({viewport: {width: spec.width, height: spec.height},
    deviceScaleFactor: 1, serviceWorkers: 'block', acceptDownloads: false, offline: true});
  await context.route('**/*', route => route.abort());
  // Installed before the page exists: setContent reuses the blank document, so a later script would not run.
  await context.addInitScript(() => {
    window.__skyeTime = 0;
    const NativeDate = Date;
    window.Date = class extends NativeDate {
      constructor(...args) { super(...(args.length ? args : [window.__skyeTime * 1000])); }
      static now() { return window.__skyeTime * 1000; }
    };
    performance.now = () => window.__skyeTime * 1000;
    Math.random = () => 0.5;
    window.requestAnimationFrame = () => 0;
    window.setInterval = () => 0;
    window.setTimeout = () => 0;
  });
  const page = await context.newPage();
  context.on('page', popup => { if (popup !== page) void popup.close(); });
  page.setDefaultTimeout(10000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  // No source media path, API token or persistent profile is supplied to this page.
  // Canvas is inserted after shot HTML, so shot scripts cannot intercept raw host data.
  await page.setContent(`<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; font-src data:; connect-src 'none'; frame-src 'none'; worker-src 'none'; base-uri 'none'; form-action 'none'">
<style>html,body{margin:0;width:100%;height:100%;overflow:hidden;background:transparent}*{animation:none!important;transition:none!important}</style>${spec.html}`, {waitUntil:'load'});
  const composite = spec.mode === 'composite';
  await page.evaluate(({width, height, designWidth, designHeight, composite}) => {
    document.body.style.cssText = `width:${designWidth}px;height:${designHeight}px;transform-origin:0 0;transform:scale(${width/designWidth})`;
    if (composite) {
    const source = document.createElement('canvas');
    source.id = 'source'; source.width = width; source.height = height;
    source.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;z-index:-1';
    document.body.prepend(source);
    }
    const caption = document.createElement('div'); caption.id = 'skye-caption';
    caption.style.cssText = 'position:absolute;left:8%;right:8%;bottom:8%;text-align:center;color:white;font:600 28px system-ui;white-space:pre-wrap;text-shadow:0 2px 5px black;background:#0008;border-radius:8px;padding:8px;z-index:2147483647';
    document.body.append(caption);
    if (typeof window.frame !== 'function') throw new Error('Define window.frame(t) in visual.html');
    // Disable clocks and random values, while the renderer supplies explicit local time.
    Math.random = () => 0.5;
    window.requestAnimationFrame = () => 0;
    window.setInterval = () => 0;
    window.setTimeout = () => 0;
  }, {...spec, composite});
  const showCaption = async (index) => {
    const caption = spec.captions.filter(c => c.start <= index && index < c.end).map(c => c.text).join('\n');
    await page.evaluate(async ({caption, t}) => {
      window.__skyeTime = t;
      await window.frame(t);
      const node = document.getElementById('skye-caption');
      node.textContent = caption; node.style.display = caption ? '' : 'none';
    }, {caption, t: index / 30});
    if (errors.length) throw new Error(errors[0]);
  };
  const emit = async () => {
    const png = await page.screenshot({type:'png', timeout:10000, omitBackground: !composite});
    if (!process.stdout.write(png)) await once(process.stdout, 'drain');
  };
  if (!composite) {
    // Overlay mode: only the transparent graphics layer is drawn here. FFmpeg joins it to the footage.
    for (let index = 0; index < spec.frames; index++) { await showCaption(index); await emit(); }
  } else {
    const size = spec.width * spec.height * 4;
    let pending = Buffer.alloc(0), index = 0;
    for await (const data of process.stdin) {
      pending = Buffer.concat([pending, data]);
      while (pending.length >= size) {
        const rgba = pending.subarray(0, size).toString('base64');
        pending = pending.subarray(size);
        await page.evaluate(({rgba, width, height}) => {
          const bytes = Uint8ClampedArray.from(atob(rgba), c => c.charCodeAt(0));
          document.getElementById('source').getContext('2d').putImageData(new ImageData(bytes, width, height), 0, 0);
        }, {rgba, width: spec.width, height: spec.height});
        await showCaption(index); await emit(); index++;
      }
    }
    if (index !== spec.frames || pending.length) throw new Error(`Decoded ${index} frames, expected ${spec.frames}`);
  }
} finally {
  await browser.close();
}
