// Validate a proposed batch with the exact same core used by manual editing.
import {readFileSync} from 'node:fs';
import {applyOps} from './vendor/openfilm/studio/server/ops.mjs';
import {readFilm} from './vendor/openfilm/src/film-doc.mjs';
const {film, ops} = JSON.parse(readFileSync(0, 'utf8'));
const value = applyOps(structuredClone(film), ops);
const {doc, problems} = readFilm(value);
if (!doc) throw new Error(problems.join('\n'));
process.stdout.write(JSON.stringify(value));
