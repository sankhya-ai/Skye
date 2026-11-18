// Long, mixed-frame-rate local playback and restart probe. No model calls.
import {chromium} from '../skye/studio/vendor/openfilm/node_modules/playwright-core/index.mjs';
import {filmHtml} from '../skye/studio/vendor/openfilm/src/film-doc.mjs';
import {spawn,spawnSync} from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const state=process.argv[2];await fs.mkdir(state,{recursive:true});
async function start(){
 const proc=spawn('node',['skye/studio/runtime.mjs',path.join(state,'studio')],{stdio:['pipe','pipe','inherit']});
 const session=await new Promise((resolve,reject)=>{let s='';proc.stdout.on('data',d=>{s+=d;try{resolve(JSON.parse(s.split('\n')[0]));}catch{}});proc.once('exit',c=>reject(Error('runtime exited '+c)));});
 return {proc,session,call:async(route,body)=>{const r=await fetch(session.origin+route,{headers:{'x-studio-key':session.key,'content-type':'application/json'},...(body?{method:'POST',body:JSON.stringify(body)}:{})});const j=await r.json();assert.ok(r.ok,JSON.stringify(j));return j;}};
}
let run=await start();let browser;
try{
 const {project}=await run.call('/api/projects',{name:'Long mixed fps'});const assets=path.join(project.path,'assets');await fs.mkdir(assets,{recursive:true});
 for(const [name,rate,color,freq] of [['a','24','0x516d8d',440],['b','30000/1001','0x746885',660],['c','25','0x4f746c',880]]){
  const r=spawnSync('ffmpeg',['-v','error','-y','-f','lavfi','-i',`color=c=${color}:s=160x90:r=${rate}:d=400`,'-f','lavfi','-i',`sine=frequency=${freq}:sample_rate=48000:duration=400`,'-c:v','libvpx-vp9','-deadline','realtime','-cpu-used','8','-b:v','20k','-c:a','libopus','-shortest',path.join(assets,name+'.webm')]);assert.equal(r.status,0,r.stderr.toString());
 }
 await fs.writeFile(path.join(assets,'concat.txt'),"file 'a.webm'\nfile 'b.webm'\nfile 'c.webm'\n");
 const join=spawnSync('ffmpeg',['-v','error','-y','-f','concat','-safe','0','-i',path.join(assets,'concat.txt'),'-c','copy',path.join(assets,'long.webm')]);assert.equal(join.status,0,join.stderr.toString());
 const film={stage:{w:160,h:90},tracks:[{clips:[{id:'long',src:'assets/long.webm',time:[0,1200]}]}]};
 await fs.writeFile(path.join(project.path,'film.html'),filmHtml(film));
 const saved=await run.call('/api/projects/'+project.id);
 run.proc.stdin.end();await new Promise(r=>run.proc.once('exit',r));run=await start();
 const reopened=await run.call('/api/projects/'+project.id);assert.deepEqual(reopened.value,saved.value);assert.equal(reopened.rev,saved.rev);
 browser=await chromium.launch({headless:true,chromiumSandbox:true});const page=await browser.newPage();
 const requests=[];page.on('request',r=>{if(r.url().includes('long.webm'))requests.push(r.headers().range||'no-range');});
 await page.goto(run.session.url);await page.evaluate(url=>{document.body.innerHTML='';const v=document.createElement('video');v.controls=true;v.src=url;v.preload='metadata';document.body.append(v);},new URL('assets/long.webm',reopened.folder).href);
 await page.locator('video').evaluate(v=>new Promise((r,j)=>{v.onloadedmetadata=r;v.onerror=()=>j(Error('decode error'));if(v.readyState>=1)r();}));
 const seeks=[];for(const t of [10,399.7,400.3,799.7,800.3,1198,1]){seeks.push(await page.locator('video').evaluate(async(v,t)=>{const s=performance.now();await new Promise(r=>{v.onseeked=r;v.currentTime=t;});return {t,actual:v.currentTime,ms:performance.now()-s};},t));}
 const cdp=await page.context().newCDPSession(page);await cdp.send('Performance.enable');const heap=(await cdp.send('Performance.getMetrics')).metrics.find(m=>m.name==='JSHeapUsedSize').value;
 const processes=spawnSync('ps',['-eo','rss,args'],{encoding:'utf8'}).stdout.split('\n').filter(l=>l.includes('chrome-headless-shell')&&!l.includes('ps -eo'));const rssKiB=processes.reduce((n,l)=>n+Number(l.trim().split(/\s+/)[0]),0);
 const probe=JSON.parse(spawnSync('ffprobe',['-v','error','-show_entries','stream=codec_type,duration,r_frame_rate,avg_frame_rate:format=duration,size','-of','json',path.join(assets,'long.webm')],{encoding:'utf8'}).stdout);
 const result={restartExact:true,probe,seeks,rangeRequests:requests,jsHeapBytes:heap,allHeadlessChromeRssKiB:rssKiB,caveat:'Synthetic 160x90 low-bitrate 20-minute input. Raw video element over the actual Studio range server, not timeline scrubbing. RSS sums all matching headless processes, not an isolated task memory limit. Does not prove 4K or M1 performance.'};
 await fs.writeFile(path.join(state,'long-metrics.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
}finally{await browser?.close();run.proc.stdin.end();await new Promise(r=>run.proc.once('exit',r));}
