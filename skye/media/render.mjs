// Original Skye implementation of the page-per-shot frame(t) convention.
// Runs shot code only in a fresh, sandboxed Chromium context, never in Node.
import fs from 'node:fs/promises';
import { once } from 'node:events';
import { chromium } from 'playwright';

const spec = JSON.parse(await fs.readFile(process.argv[2], 'utf8'));
const harness = await fs.readFile(new URL('./harness.js', import.meta.url), 'utf8');
const browser = await chromium.launch({headless: true, chromiumSandbox: true,
  ...(process.env.SKYE_CHROMIUM ? {executablePath: process.env.SKYE_CHROMIUM} : {})});
try {
  const context = await browser.newContext({viewport: {width: spec.width, height: spec.height},
    deviceScaleFactor: 1, serviceWorkers: 'block', acceptDownloads: false, offline: true});
  await context.route('**/*', route => route.abort());
  const page = await context.newPage();
  context.on('page', popup => { if (popup !== page) void popup.close(); });
  page.setDefaultTimeout(10000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  // No source media path, API token or persistent profile is supplied to this page.
  // Canvas is inserted after shot HTML, so shot scripts cannot intercept raw host data.
  await page.setContent(`<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; font-src data:; connect-src 'none'; frame-src 'none'; worker-src 'none'; base-uri 'none'; form-action 'none'">
<style>html,body{margin:0;width:100%;height:100%;overflow:hidden;background:transparent}*{animation:none!important;transition:none!important}</style><script>${harness}</script>${spec.html}`, {waitUntil:'load'});
  const composite = spec.mode === 'composite';
  await page.evaluate(options => window.__skye.setup(options), {width: spec.width, height: spec.height,
    designWidth: spec.designWidth, designHeight: spec.designHeight, composite});
  const showCaption = async (index) => {
    const caption = spec.captions.filter(c => c.start <= index && index < c.end).map(c => c.text).join('\n');
    await page.evaluate(([t, text]) => window.__skye.frame(t, text), [index / 30, caption]);
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
        await page.evaluate(([data, w, h]) => window.__skye.drawRgba(data, w, h), [rgba, spec.width, spec.height]);
        await showCaption(index); await emit(); index++;
      }
    }
    if (index !== spec.frames || pending.length) throw new Error(`Decoded ${index} frames, expected ${spec.frames}`);
  }
} finally {
  await browser.close();
}
