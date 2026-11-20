// Shared-core transition/export probe. Synthetic media only.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import {filmHtml,clipFade,readFilmFile} from '../skye/studio/vendor/openfilm/src/film-doc.mjs';
import {applyOps} from '../skye/studio/vendor/openfilm/studio/server/ops.mjs';
import {planTransition} from '../skye/studio/vendor/openfilm/studio/ui/src/lib/transitions.ts';
import {render} from '../skye/studio/vendor/openfilm/src/render.mjs';
import {closeBrowsers} from '../skye/studio/vendor/openfilm/src/host.mjs';
const root=process.argv[2];
let value=readFilmFile(fs.readFileSync(path.join(root,'film.html'),'utf8')).value;
const facts=new Map();
const model=value.tracks.map((t,index)=>({index,role:t.clips.every(c=>c.sound)?'audio':'video',clips:t.clips.map(c=>{
  const at=c.at||0;const [from,to]=c.time;const fade=clipFade(c)||[0,0];
  facts.set(c.id,{picture:!c.sound,fade:fade.map(n=>n*1000),src:c.src});
  return {id:c.id,startMs:at*1000,endMs:(at+to-from)*1000,inMs:from*1000,speed:1,sourceMs:4000,...(c.attrs?.['data-link']?{link:c.attrs['data-link']}:{})};
})}));
const plan=planTransition(model,id=>facts.get(id),'dissolve',{a:'opening',b:'detail'},500,{ripple:true,linked:true});
assert.ok(!('error' in plan),JSON.stringify(plan));
value=applyOps(structuredClone(value),plan.ops);
fs.writeFileSync(path.join(root,'assets/captions.vtt'),'WEBVTT\n\n00:00:02.000 --> 00:00:05.000\nA shared edit model.\n');
value.tracks.unshift({clips:[],captions:{src:'assets/captions.vtt',kind:'subtitles',srclang:'en',label:'Captions'}});
fs.writeFileSync(path.join(root,'film.html'),filmHtml(value));
console.log('TRANSITION',JSON.stringify(plan));
try {const result=await render([root],{out:path.join(root,'verified.mp4'),fps:30,workers:1});console.log('EXPORT',JSON.stringify(result));}
finally {await closeBrowsers();}
