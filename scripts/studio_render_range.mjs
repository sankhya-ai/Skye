// Render one time range of a film over assets/long.webm, for the frame-parity gate. No model calls.
import {filmHtml} from '../skye/studio/vendor/openfilm/src/film-doc.mjs';
import {render} from '../skye/studio/vendor/openfilm/src/render.mjs';
import {closeBrowsers} from '../skye/studio/vendor/openfilm/src/host.mjs';
import fs from 'node:fs';
const [root, start, end, out, fps = '30', durArg] = process.argv.slice(2);
const duration = Number(durArg);
if (!root || !start || !end || !out || !duration) {
  console.error('usage: node studio_render_range.mjs <projectRoot> <start> <end> <out.mp4> [fps] <duration>');
  process.exit(2);
}
const film = {stage: {w: 160, h: 90}, tracks: [{clips: [{id: 'long', src: 'assets/long.webm', time: [0, duration]}]}]};
fs.writeFileSync(`${root}/film.html`, filmHtml(film));
try {
  const result = await render([root, `${start}-${end}`], {out, fps: Number(fps), workers: 1});
  console.log('EXPORT', JSON.stringify(result));
} finally {
  await closeBrowsers();
}
