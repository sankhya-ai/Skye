// Interleave test: cut one clip into four with the editor's real split op,
// ripple later cuts with move ops, and insert a code scene, a still image and
// a second video between the cuts with insert ops. Writes the final film.html.
// No model calls. Usage: node interleave.mjs <projectRoot> <vendorDir>
// projectRoot needs film.html (one video clip, id "tura"), assets/, scenes/.
import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
const [root, vendor] = process.argv.slice(2);
if (!root || !vendor) { console.error('usage: node interleave.mjs <projectRoot> <vendorDir>'); process.exit(2); }
const imp = (p) => import(pathToFileURL(path.join(vendor, p)).href);
const {filmHtml, readFilmFile} = await imp('src/film-doc.mjs');
const {applyOps} = await imp('studio/server/ops.mjs');
const orig = fs.readFileSync(path.join(root, 'film.html'), 'utf8');
let {value} = readFilmFile(orig);
// 1) cut the 12s clip at 3, 6, 9 (editor: split at the playhead)
value = applyOps(value, [{op: 'split', clip: 'tura', at: 3, time: [0, 3], restTime: [3, 12]}]);
const id2 = value.tracks[0].clips[1].id;
value = applyOps(value, [{op: 'split', clip: id2, at: 6, time: [3, 6], restTime: [6, 12]}]);
const id3 = value.tracks[0].clips[2].id;
value = applyOps(value, [{op: 'split', clip: id3, at: 9, time: [6, 9], restTime: [9, 12]}]);
const ids = value.tracks[0].clips.map((c) => c.id);
console.log('cuts', JSON.stringify(value.tracks[0].clips.map((c) => ({id: c.id, at: c.at, time: c.time}))));
// 2) ripple later cuts right to open gaps (editor: insert moves everything after)
value = applyOps(value, [{op: 'move', clip: ids[1], at: 5}, {op: 'move', clip: ids[2], at: 10}, {op: 'move', clip: ids[3], at: 16}]);
// 3) interleave: code scene at 3, still image at 8, web clip at 13, endcard at 19
value = applyOps(value, [
  {op: 'insert', clip: {src: 'scenes/act1.html', time: [0, 2], at: 3}},
  {op: 'insert', clip: {src: 'assets/meme.png', time: [0, 2], at: 8}, track: 0},
  {op: 'insert', clip: {src: 'assets/webclip.mp4', time: [0, 3], at: 13}, track: 0},
  {op: 'insert', clip: {src: 'scenes/endcard.html', time: [0, 2], at: 19}},
]);
fs.writeFileSync(path.join(root, 'film.html'), filmHtml(value, orig));
console.log('FILM\n' + fs.readFileSync(path.join(root, 'film.html'), 'utf8'));
