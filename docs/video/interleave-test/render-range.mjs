// Render one range of the film: node render-range.mjs <projectRoot> <vendorDir> <start> <end> <out.mp4>
import path from 'node:path';
import {pathToFileURL} from 'node:url';
const [root, vendor, a, b, out] = process.argv.slice(2);
const imp = (p) => import(pathToFileURL(path.join(vendor, p)).href);
const {render} = await imp('src/render.mjs');
const {closeBrowsers} = await imp('src/host.mjs');
const t0 = Date.now();
try {
  const r = await render([root, `${a}-${b}`], {out, fps: 30, workers: 2});
  console.log('SEG', a, b, JSON.stringify(r), 'elapsed_s', ((Date.now() - t0) / 1000).toFixed(1));
} catch (e) { console.log('SEG_FAILED', a, b, e.message); }
await closeBrowsers();
