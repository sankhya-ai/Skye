import {readFileSync} from 'node:fs';
import {readFilmFile,clipTag} from './vendor/openfilm/src/film-doc.mjs';
const text = readFileSync(0, 'utf8');
const {doc, problems,layout} = readFilmFile(text);
if (!doc) { console.error(problems.join('\n')); process.exit(1); }
if (process.argv.includes('--normalize')) {
  // Change only clip tags, preserving the head, comments and section flags.
  // Native IDs, single quotes and implicit at=0 become unambiguous to callers.
  const edits=[];
  doc.tracks.forEach((track,ti)=>track.clips.forEach((clip,ci)=>{
    const spot=layout.tracks[ti].clips[ci];
    let tag=clipTag(clip);
    if (tag.startsWith('<video ') && !/\sat=/.test(tag)) tag=tag.replace('<video ','<video at="0" ');
    edits.push({...spot,text:tag});
  }));
  let normalized=text;
  for(const e of edits.sort((a,b)=>b.start-a.start)) normalized=normalized.slice(0,e.start)+e.text+normalized.slice(e.end);
  process.stdout.write(normalized);
} else process.stdout.write(JSON.stringify(doc));
