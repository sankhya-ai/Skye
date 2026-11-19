// Only called with locally generated effects, never raw worker operations.
import {readFileSync} from 'node:fs';
import {readFilmFile,readFilm} from './vendor/openfilm/src/film-doc.mjs';
import {applyOps} from './vendor/openfilm/studio/server/ops.mjs';
const {before,text,selected}=JSON.parse(readFileSync(0,'utf8'));
const {value:after,doc,problems}=readFilmFile(text);
if(!doc) throw new Error(problems.join('\n'));
// Missing `at` is native shorthand for zero. The local generator may make
// that default explicit while normalizing unrelated clips. Do not mistake it
// for an edit, and do not weaken comparisons for other fields.
const sameField=(a,b,k)=>JSON.stringify(k==='at'?(a[k]??0):a[k])===JSON.stringify(k==='at'?(b[k]??0):b[k]);
const old=new Map(before.tracks.flatMap(t=>t.clips.map(c=>[c.id,c])));
const ops=[];
for(const t of after.tracks) for(const c of t.clips){
  const prev=old.get(c.id);
  if(prev){
    if(c.id!==selected && Object.keys({...prev,...c}).some(k=>!sameField(prev,c,k))) {
      throw new Error('Effect changed an unselected clip');
    }
    if(c.id===selected){
      const fields={};for(const k of Object.keys({...prev,...c})) if(k!=='id' && !sameField(prev,c,k)) fields[k]=c[k]??null;
      if(Object.keys(fields).length) ops.push({op:'fields',clip:c.id,fields});
    }
  }else{
    // Each generated overlay gets its own top track, retaining existing track order/flags.
    ops.push({op:'insert',clip:c,newTrack:0});
  }
}
const set={};for(const [name,body] of Object.entries(after.keyframes??{})) if(before.keyframes?.[name]!==body) set[name]=body;
if(Object.keys(set).length) ops.push({op:'keyframes',set});
if(!ops.length) throw new Error('No effect operations generated');
const checked=readFilm(applyOps(structuredClone(before),ops));
if(!checked.doc) throw new Error(checked.problems.join('\n'));
process.stdout.write(JSON.stringify(ops));
