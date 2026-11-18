// Free, synthetic local integration checks. No provider or model calls.
import {chromium} from '../skye/studio/vendor/openfilm/node_modules/playwright-core/index.mjs';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {spawn, spawnSync} from 'node:child_process';
import assert from 'node:assert/strict';
import {filmHtml} from '../skye/studio/vendor/openfilm/src/film-doc.mjs';

const state = process.env.STUDIO_TEST_STATE || await fs.mkdtemp(path.join(os.tmpdir(), 'skye-studio-browser-'));
await fs.mkdir(state,{recursive:true});
const port=String(18000+Math.floor(Math.random()*10000));
const log=await fs.open(path.join(state,'skye.log'),'a');
const server=spawn(process.env.PYTHON || 'python3',['-m','skye.cli','ui','--state-dir',state,'--port',port],{stdio:['ignore',log.fd,log.fd]});
let browser;
try {
  let token;
  for(let i=0;i<100;i++) {
    try {token=(await fs.readFile(path.join(state,'ui-token'),'utf8')).trim();await fetch(`http://127.0.0.1:${port}/health`);break;}catch{await new Promise(r=>setTimeout(r,100));}
  }
  assert.ok(token,'server did not start');
  const res=await fetch(`http://127.0.0.1:${port}/api/studio/start`,{method:'POST',headers:{'X-Skye-Token':token,'Content-Type':'application/json'},body:'{}'});
  const session=await res.json();assert.ok(session.url,JSON.stringify(session));
  const key=new URL(session.url).searchParams.get('key');
  const call=async(route,body,method)=>{const r=await fetch(session.origin+route,{method:method || (body?'POST':'GET'),headers:{'x-studio-key':key,'Content-Type':'application/json'},...(body?{body:JSON.stringify(body)}:{})});const data=await r.json();assert.ok(r.ok,JSON.stringify(data));return data;};
  for(const route of ['/api/providers','/api/get','/api/skills','/api/update','/api/news']) {
    const denied=await fetch(session.origin+route,{headers:{'x-studio-key':key}});
    assert.equal(denied.status,403,route+' was not disabled');
  }
  const made=await call('/api/projects',{name:'A local film'});const project=made.project;
  const assets=path.join(project.path,'assets');await fs.mkdir(assets,{recursive:true});
  for(const [name,color,rate,tone] of [['opening','0x516d8d','24',440],['detail','0x746885','30000/1001',660],['closing','0x4f746c','25',880]]) {
    const out=spawnSync('ffmpeg',['-hide_banner','-loglevel','error','-y','-f','lavfi','-i',`color=c=${color}:s=640x360:r=${rate}:d=4`,'-f','lavfi','-i',`sine=frequency=${tone}:sample_rate=48000:duration=4`,'-shortest','-c:v','libvpx-vp9','-deadline','realtime','-cpu-used','6','-c:a','libopus',path.join(assets,`${name}.webm`)]);
    assert.equal(out.status,0,out.stderr.toString());
  }
  await fs.writeFile(path.join(assets,'title.html'),`<meta name="viewport" content="width=640,height=360"><style>body{margin:0;background:transparent;color:white;font:600 35px system-ui}h1{position:absolute;left:40px;top:90px;font-size:40px;letter-spacing:-2px}p{position:absolute;left:42px;top:163px;font-size:16px;opacity:.7}</style><h1 id="title">A quieter kind of edit.</h1><p>One film. Every view.</p><script>window.film={duration:12,frame(t){document.getElementById('title').style.opacity=Math.min(1,t*2)}};</script>`);
  const film={stage:{w:640,h:360},tracks:[{clips:[{id:'title',src:'assets/title.html',time:[0,12]}]},
    {clips:['opening','detail','closing'].map((id,i)=>({id,src:`assets/${id}.webm`,at:i*4,time:[0,4],volume:0,attrs:{'data-link':id}}))},
    {clips:['opening','detail','closing'].map((id,i)=>({id:id+'-audio',src:`assets/${id}.webm`,sound:true,at:i*4,time:[0,4],attrs:{'data-link':id}}))}]};
  await fs.writeFile(path.join(project.path,'film.html'),filmHtml(film));
  await call(`/api/projects/${project.id}/canvas`,{nodes:Array.from({length:1000},(_,i)=>({id:`shot${i}`,path:`assets/${['opening','detail','closing'][i%3]}.webm`,title:`Shot ${String(i+1).padStart(4,'0')}`,x:(i%25)*760,y:Math.floor(i/25)*460})),edges:[]},'PUT');
  browser=await chromium.launch({headless:true,chromiumSandbox:true});
  const page=await browser.newPage({viewport:{width:1920,height:1100}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(`http://127.0.0.1:${port}/?token=${token}#/video`);
  const editor=page.frameLocator('iframe[title="Skye video editor"]');
  await editor.getByText('A local film',{exact:true}).first().waitFor({timeout:30000});
  await page.waitForTimeout(2000);
  console.log('BUTTONS',await editor.getByRole('button').evaluateAll(xs=>xs.map(x=>({a:x.getAttribute('aria-label'),t:x.getAttribute('title'),text:x.textContent?.slice(0,40)}))));
  console.log('FRAME_BODY',(await editor.locator('body').innerText()).slice(0,6000));
  await page.screenshot({path:path.join(state,'studio-edit.png'),animations:'disabled',timeout:60000});
  console.log('STATE',state);console.log('PROJECT',project.id);console.log('ERRORS',JSON.stringify(errors));
  console.log('FRAME_BODY',(await editor.locator('body').innerText()).slice(0,6000));
  await fs.writeFile(path.join(state,'test-info.json'),JSON.stringify({state,project,film}));
  console.log('COUNTS',await editor.locator('video').count(),await editor.locator('.react-flow__node').count());
  const block = editor.locator('[data-block-title="opening.webm"]').first();
  console.log('BLOCKS',await editor.locator('[data-block-id]').evaluateAll(xs=>xs.map(x=>({id:x.dataset.blockId,title:x.dataset.blockTitle,rect:x.getBoundingClientRect().toJSON()}))));
  await block.click();
  await page.getByText(/selected clip.*links/).waitFor();
  console.log('SELECTION',await page.locator('.studio-selection').innerText());
  const edits=[]; page.on('request',r=>{if(r.url().endsWith('/edit')&&r.method()==='POST'){try{edits.push(r.postDataJSON());}catch{}}});
  const beforeManual = await call(`/api/projects/${project.id}`);
  const handle = await block.locator('[data-trim="end"]').boundingBox();
  assert.ok(handle);
  await page.mouse.move(handle.x+handle.width/2,handle.y+handle.height/2);
  await page.mouse.down();await page.mouse.move(handle.x-50,handle.y+handle.height/2,{steps:12});await page.mouse.up();
  await page.waitForTimeout(400);
  const afterManual = await call(`/api/projects/${project.id}`);
  assert.notEqual(afterManual.rev,beforeManual.rev,'manual ripple did not save');
  const get=(f,id)=>f.value.tracks.flatMap(t=>t.clips).find(c=>c.id===id);
  console.log('MANUAL_TIMING',JSON.stringify(afterManual.value));
  assert.deepEqual(get(afterManual,'opening').time,get(afterManual,'opening-audio').time,'linked trim differs');
  assert.equal(get(afterManual,'detail').at,get(afterManual,'detail-audio').at,'linked following clips drift');
  assert.ok(get(afterManual,'detail').at<4,'ripple did not close following clips');
  await editor.getByRole('button',{name:'Undo (Ctrl+Z)',exact:true}).click();
  await page.waitForTimeout(300);
  assert.deepEqual((await call(`/api/projects/${project.id}`)).value,beforeManual.value);
  await editor.getByRole('button',{name:'Redo (Ctrl+Shift+Z)',exact:true}).click();
  await page.waitForTimeout(300);
  assert.deepEqual((await call(`/api/projects/${project.id}`)).value,afterManual.value);
  await editor.getByRole('button',{name:'Undo (Ctrl+Z)',exact:true}).click();await page.waitForTimeout(300);
  // Deterministic fake worker uses the real Python dispatcher, then the parent applies
  // its validated result through useFilm, exactly as the Ask panel does.
  await fs.writeFile(path.join(state,'manual-ops.json'),JSON.stringify(edits[0].ops));
  const worker=spawnSync(process.env.PYTHON || 'python3',['scripts/studio_fake_proposal.py',state,session.origin,key,project.id],{encoding:'utf8'});
  assert.equal(worker.status,0,worker.stderr);
  const proposal=JSON.parse(worker.stdout);
  await page.evaluate(({origin,proposal})=>document.querySelector('iframe[title="Skye video editor"]').contentWindow.postMessage({type:'skye:apply',request:'test-worker',...proposal},origin),{origin:session.origin,proposal});
  await page.waitForTimeout(400);
  const afterAgent=await call(`/api/projects/${project.id}`);
  assert.deepEqual(afterAgent.value,afterManual.value,'manual and dispatcher fake worker produced different films');
  await editor.getByRole('button',{name:'Undo (Ctrl+Z)',exact:true}).click();await page.waitForTimeout(300);
  assert.deepEqual((await call(`/api/projects/${project.id}`)).value,beforeManual.value);
  await editor.getByRole('button',{name:'Redo (Ctrl+Shift+Z)',exact:true}).click();await page.waitForTimeout(300);
  assert.deepEqual((await call(`/api/projects/${project.id}`)).value,afterManual.value);
  await page.screenshot({path:path.join(state,'studio-linked-edit.png'),animations:'disabled',timeout:60000});
  await editor.getByRole('button',{name:'Editors',exact:true}).click();
  console.log('MENU',(await editor.locator('body').innerText()).slice(-2400));
  await editor.getByText('Canvas',{exact:true}).last().click();
  await page.waitForTimeout(1200);
  const canvasMetrics = {nodes:await editor.locator('.react-flow__node').count(),videos:await editor.locator('video').count(),heap:await page.evaluate(()=>performance.memory?.usedJSHeapSize)};
  console.log('CANVAS_METRICS',canvasMetrics);
  await page.screenshot({path:path.join(state,'studio-canvas.png'),animations:'disabled',timeout:60000});
  await editor.locator('.react-flow__pane').click({position:{x:100,y:100}});
  await page.keyboard.press('Shift+0');await page.waitForTimeout(500);
  const frame=page.frames().find(f=>f.url().startsWith(session.origin));
  const cdp=await page.context().newCDPSession(page); // Same-process iframe shares the parent CDP isolate.
  await cdp.send('Performance.enable');
  const perf=Object.fromEntries((await cdp.send('Performance.getMetrics')).metrics.map(m=>[m.name,m.value]));
  const zoomMetrics={nodes:await editor.locator('.react-flow__node').count(),videos:await editor.locator('video').count(),sharedPageHeapBytes:perf.JSHeapUsedSize};
  assert.ok(zoomMetrics.nodes<100,'offscreen canvas cards were not culled');
  console.log('ZOOM_METRICS',JSON.stringify(zoomMetrics));
  await page.screenshot({path:path.join(state,'studio-canvas-zoom.png'),animations:'disabled'});
  await editor.getByRole('button',{name:'Editors',exact:true}).click();
  await editor.getByText('Color',{exact:true}).last().click();await page.waitForTimeout(800);
  await page.screenshot({path:path.join(state,'studio-color.png'),animations:'disabled'});
  console.log('FINAL_ERRORS',JSON.stringify(errors));
  await fs.writeFile(path.join(state,'metrics.json'),JSON.stringify({canvasMetrics,zoomMetrics,errors}));

} finally {
  await browser?.close();server.kill('SIGINT');await new Promise(r=>server.once('exit',r));await log.close();
}
