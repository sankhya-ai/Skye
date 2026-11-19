import {boot} from './lib.mjs';import fs from 'node:fs/promises';import path from 'node:path';
import {applyOps} from '/home/sandbox/Skye/skye/studio/vendor/openfilm/studio/server/ops.mjs';
import {filmHtml} from '/home/sandbox/Skye/skye/studio/vendor/openfilm/src/film-doc.mjs';
import {readProjectFilm} from '/home/sandbox/Skye/skye/studio/vendor/openfilm/studio/server/film.mjs';
const LIB='/home/sandbox/Skye/skye/studio/vendor/openfilm/library/effects';
const {project:p}=JSON.parse(await fs.readFile('/tmp/w/proj.json','utf8'));
const R=p.path;
await fs.mkdir(R+'/assets/fx',{recursive:true});await fs.mkdir(R+'/looks',{recursive:true});await fs.mkdir(R+'/assets/sfx',{recursive:true});
for(const n of ['vignette','grain','light-leak'])await fs.copyFile(`${LIB}/${n}/files/${n}.html`,`${R}/assets/fx/${n}.html`);
await fs.copyFile('/tmp/w/pa/kicker.html',R+'/assets/fx/kicker.html');
for(const n of ['gta','paper'])await fs.copyFile(`/tmp/w/pa/${n}.cube`,`${R}/looks/${n}.cube`);
for(const n of ['whoosh','boom'])await fs.copyFile(`/tmp/w/pa/${n}.wav`,`${R}/assets/sfx/${n}.wav`);
await fs.copyFile('/tmp/w/film0.html',R+'/film.html');const text0=await fs.readFile(R+'/film.html','utf8');await fs.mkdir('/tmp/w/steps',{recursive:true});await fs.writeFile('/tmp/w/steps/00-baseline.html',text0);
let {value}=await readProjectFilm(R);
const clone=x=>structuredClone(x);
value.keyframes={...(value.keyframes||{}),
 'push-in':'from { scale: 1 } to { scale: 1.05 }',
 'push-soft':'from { scale: 1 } to { scale: 1.03 }',
 'pull-out':'from { scale: 1.035 } to { scale: 1 }',
 'drift-l':'from { scale: 1.025; translate: 10px 0 } to { scale: 1.025; translate: -10px 0 }',
 'drift-r':'from { scale: 1.025; translate: -10px 0 } to { scale: 1.025; translate: 10px 0 }'};
// tracks: top to bottom
const T=['kick','leak','grain','vig','pic','snd','sfx'];
const old=value.tracks; // [pic, snd]
value.tracks=T.map(n=>n==='pic'?old[0]:n==='snd'?old[1]:{clips:[],attrs:{id:'t-'+n}});
value.tracks[4].attrs={id:'t-pic'};value.tracks[5].attrs={id:'t-snd'};
const tr=n=>{let t=value.tracks.find(t=>t.attrs?.id==='t-'+n);if(t)return t;t={clips:[],attrs:{id:'t-'+n}};const rank=m=>T.indexOf(m.attrs.id.slice(2));let k=value.tracks.findIndex(x=>rank(x)>rank(t));if(k<0)k=value.tracks.length;value.tracks.splice(k,0,t);return t;};
const find=id=>{for(const t of value.tracks){const c=t.clips.find(c=>c.id===id);if(c)return c}throw new Error(id)};
const snapshot=()=>{const m=new Map();value.tracks.forEach((t,ti)=>t.clips.forEach(c=>m.set(c.id,{track:t.attrs?.id,full:JSON.stringify(c),noAt:JSON.stringify({...c,at:0})})));return m};
const GOLD='#f3d27a',INK='#3a2a1c';
const shots=[
 {n:'hook',lut:'gta',anim:'push-in',fade:[0.25,0],kick:['TURA PICTURES PRESENTS',0.45,1.4,'#ffffff','180px'],vig:'#000000',grain:.38},
 {n:'headline',lut:'gta',anim:'push-soft',vig:'#000000',grain:.34,boom:true,trim:0.3},
 {n:'mission',lut:'gta',anim:'push-in',vig:'#000000',grain:.34,leak:'#ff8a3c'},
 {n:'guilt',lut:'paper',anim:'drift-l',vig:'#2a1608',grain:.28,kick:['PROLOGUE',0.3,2.2,INK],whoosh:true},
 {n:'step1',lut:'paper',anim:'drift-r',vig:'#2a1608',grain:.28,kick:['ACT I',0.25,2.3,INK],whoosh:true},
 {n:'step2',lut:'paper',anim:'push-soft',vig:'#2a1608',grain:.28,kick:['ACT II',0.25,2.6,INK],whoosh:true},
 {n:'weakspot',lut:'paper',anim:'pull-out',vig:'#2a1608',grain:.3,whoosh:true},
 {n:'step3',lut:'paper',anim:'drift-l',vig:'#2a1608',grain:.28,kick:['ACT III',0.2,1.8,INK],whoosh:true},
 {n:'clock',lut:'paper',anim:'push-soft',vig:'#2a1608',grain:.28,kick:['ACT IV',0.2,1.8,INK],whoosh:true},
 {n:'goplay',lut:'paper',anim:'drift-r',vig:'#2a1608',grain:.28,kick:['EPILOGUE',0.25,1.9,INK],whoosh:true},
 {n:'endcard',lut:'paper',anim:'push-soft',vig:'#2a1608',grain:.24,leak:'#ffc27a',fade:[0,0.9],trim:-0.8,kick:['A TURA PRODUCTION',0.6,1.9,INK]},
];
const log=[];
const sh=await boot();
try{
 for(const [i,s] of shots.entries()){
  const before=snapshot();
  const pic=find('pic-'+s.n),snd=find('snd-'+s.n);
  // trim (pacing). positive: shorten tail and ripple later clips left. negative arg means shorten tail without ripple (last shot)
  if(s.trim){
    const amt=Math.abs(s.trim);const [a,b]=pic.time;const nb=+(b-amt).toFixed(3);
    const ops=[{op:'set',clip:pic.id,field:'time',value:[a,nb]},{op:'set',clip:snd.id,field:'time',value:[a,nb]}];
    if(s.trim>0)for(const t of value.tracks)for(const c of t.clips)if((c.at||0)>pic.at+1e-6&&/^(pic|snd)-/.test(c.id))ops.push({op:'move',clip:c.id,at:+((c.at)-amt).toFixed(3)});
    value=applyOps(value,ops);
  }
  const P=find('pic-'+s.n),S=find('snd-'+s.n);const at=P.at||0,len=+(P.time[1]-P.time[0]).toFixed(3);
  // grade + move (code edit on the picture clip's own style)
  P.style=`--of-lut: url(looks/${s.lut}.cube); animation: ${s.anim} var(--clip-length) cubic-bezier(.25,.1,.25,1) both`;
  if(s.fade){P.overrides=[{fade:s.fade}];S.overrides=[{fade:s.fade}];}
  const add=(track,c)=>{tr(track).clips.push(c)};
  add('grain',{id:'fx-grain-'+s.n,src:'assets/fx/grain.html',at,time:[0,len],style:`--amount: ${s.grain}`});
  add('vig',{id:'fx-vig-'+s.n,src:'assets/fx/vignette.html',at,time:[0,len],style:`--color: ${s.vig}; opacity: ${s.lut==='gta'?0.85:0.38}`});
  if(s.leak)add('leak',{id:'fx-leak-'+s.n,src:'assets/fx/light-leak.html',at,time:[0,Math.min(len,1.2)],style:`--color: ${s.leak}; mix-blend-mode: screen`,overrides:[{fade:[0.1,0.7]}]});
  if(s.kick){const [txt,off,dur,col,bottom]=s.kick;add('kick',{id:'kick-'+s.n,src:'assets/fx/kicker.html',at:+(at+off).toFixed(3),time:[0,dur],style:`--color: ${col}; --bottom: ${bottom||'150px'}`,overrides:[{at:'#k',text:txt},{fade:[0,0.45]}]});}
  if(s.whoosh)add('sfx',{id:'sfx-whoosh-'+s.n,src:'assets/sfx/whoosh.wav',sound:true,at,time:[0,0.9],volume:1.4});
  if(s.boom)add('sfx',{id:'sfx-boom-'+s.n,src:'assets/sfx/boom.wav',sound:true,at,time:[0,1.4],volume:0.8});
  const html=filmHtml(value,await fs.readFile(R+'/film.html','utf8'));
  await fs.writeFile(R+'/film.html',html);
  await fs.writeFile(`/tmp/w/steps/${String(i+1).padStart(2,'0')}-${s.n}.html`,html);
  const g=await sh.call('/api/projects/'+p.id);
  let sv;try{sv=await sh.call(`/api/projects/${p.id}/history/save`,{message:`Shot ${i+1} ${s.n}: grade ${s.lut}, ${s.anim}${s.trim?', trim '+s.trim:''}${s.kick?', title':''}${s.whoosh||s.boom?', sound':''}`});}catch(e){sv={commit:'(already saved)'}}
  const after=snapshot();const changed=[],timingOnly=[];for(const [k,v] of after){const b=before.get(k);if(!b||b.full!==v.full||b.track!==v.track){(b&&b.noAt===v.noAt&&b.track===v.track?timingOnly:changed).push(k)}}const gone=[...before.keys()].filter(k=>!after.has(k));
  log.push({shot:s.n,changed,timingOnly,gone,commit:sv.commit});console.log(s.n,'changed',changed.join(','),'| timing-only(at):',timingOnly.length?timingOnly.join(','):'-',gone.length?'gone '+gone:'');
 }
 await fs.writeFile('/tmp/w/steplog.json',JSON.stringify(log,null,1));
 const f=await sh.call('/api/projects/'+p.id);console.log('final rev',f.rev,'problems',JSON.stringify(f.problems||[]),'dur');
}finally{sh.stop()}
