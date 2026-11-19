import {readFileSync} from 'node:fs';
import {readFilmFile} from './vendor/openfilm/src/film-doc.mjs';
const text = readFileSync(0, 'utf8');
const {doc, problems} = readFilmFile(text);
if (!doc) { console.error(problems.join('\n')); process.exit(1); }
process.stdout.write(JSON.stringify(doc));
