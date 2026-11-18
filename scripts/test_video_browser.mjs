// Optional full browser scenario. Requires the media runtime and Python on PATH.
// Uses only synthetic footage and a disposable state directory, no model calls.
import {chromium} from '../skye/media/node_modules/playwright/index.mjs';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {spawn, spawnSync} from 'node:child_process';
import assert from 'node:assert/strict';

const state=await fs.mkdtemp(path.join(os.tmpdir(),'skye-video-browser-'));
const fixture=path.join(state,'Browser scenario.webm');
const made=spawnSync('ffmpeg',['-hide_banner','-loglevel','error','-y','-f','lavfi','-i',
  'testsrc2=s=480x270:r=30:d=2','-an','-c:v','libvpx-vp9','-deadline','realtime',fixture]);
assert.equal(made.status,0,made.stderr.toString());
const port=String(18000+Math.floor(Math.random()*10000));
const server=spawn(process.env.PYTHON??'python3',['-m','skye.cli','ui','--state-dir',state,'--port',port],{stdio:['ignore','pipe','pipe']});
let browser;
try {
  let token;
  for(let i=0;i<100;i++) {
    try {token=(await fs.readFile(path.join(state,'ui-token'),'utf8')).trim();await fetch(`http://127.0.0.1:${port}/health`);break;} catch {await new Promise(r=>setTimeout(r,100));}
  }
  assert.ok(token,'server did not start');
  browser=await chromium.launch({headless:true,chromiumSandbox:true});
  const page=await browser.newPage({viewport:{width:1500,height:1050}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(`http://127.0.0.1:${port}/?token=${token}#/video`);
  await page.getByLabel('Find cuts').uncheck();
  await page.locator('input[type=file]').setInputFiles(fixture);
  await page.getByRole('heading',{name:'Browser scenario',exact:true}).waitFor({timeout:60000});
  await page.waitForFunction(()=>document.querySelector('video')?.readyState>=2);
  // A user scrubs to the one-second mark and presses S. Both halves keep working.
  await page.getByLabel('Playhead').fill('30');
  await page.waitForFunction(()=>{const v=document.querySelector('video');return v&&!v.seeking&&Math.abs(v.currentTime-1)<0.04;});
  await page.keyboard.press('s');
  await page.getByRole('option',{name:/Shot 2/}).waitFor();
  assert.equal(await page.getByRole('listbox',{name:'Shots'}).getByRole('option').count(),2);
  // Captions tab: add one at the playhead, see it live in the sandboxed overlay without rendering.
  await page.getByRole('tab',{name:'Captions',exact:true}).click();
  await page.getByRole('button',{name:'Caption',exact:true}).click();
  await page.getByLabel('Caption text').fill('Live caption');
  const overlay=()=>page.frames().find(f=>f!==page.mainFrame());
  await page.waitForFunction(()=>true);
  await (async()=>{for(let i=0;i<50;i++){const f=overlay();if(f&&await f.evaluate(()=>document.getElementById('skye-caption')?.textContent).catch(()=>'')==='Live caption')return;await new Promise(r=>setTimeout(r,100));}throw new Error('live caption never appeared');})();
  await page.getByText('Saved',{exact:false}).first().waitFor();
  // Code tab: the preview shows runtime errors as you type.
  await page.getByRole('tab',{name:'Code',exact:true}).click();
  const code=page.getByLabel('Shot code');
  await code.fill('<div id="t" style="position:absolute;left:8%;top:8%;color:white;font:700 40px sans-serif"></div><script>window.frame=(t)=>{document.getElementById("t").textContent="One changed shot"; throw new Error("boom")}</script>');
  await page.getByRole('alert').filter({hasText:'boom'}).waitFor();
  await code.fill('<div id="t" style="position:absolute;left:8%;top:8%;color:white;font:700 40px sans-serif"></div><script>window.frame=(t)=>{document.getElementById("t").textContent="One changed shot"}</script>');
  await (async()=>{for(let i=0;i<50;i++){const f=overlay();if(f&&await f.evaluate(()=>document.getElementById('t')?.textContent).catch(()=>'')==='One changed shot')return;await new Promise(r=>setTimeout(r,100));}throw new Error('live code never appeared');})();
  await page.getByText('Saved',{exact:true}).waitFor();
  await page.screenshot({path:process.env.SHOT_DESKTOP??'/tmp/video-desktop.png',clip:{x:224,y:0,width:1276,height:720}});
  // Render, then undo the burst of edits in one step.
  await page.getByRole('button',{name:'Render',exact:true}).click();
  await page.getByRole('link',{name:'Download'}).waitFor({timeout:90000});
  await page.getByText(/2 rendered|1 rendered, 1 reused|rendered/).first().waitFor();
  await page.getByRole('button',{name:'Undo'}).click();
  await page.getByText('render out of date').waitFor();
  assert.equal(await page.getByRole('listbox',{name:'Shots'}).getByRole('option').count(),2);
  // No agent is installed in the test machine: the panel says so instead of failing on send.
  await page.getByRole('tab',{name:'Ask',exact:true}).click();
  await page.getByText('Install Claude Code or Codex').waitFor();
  assert.equal(errors.length,0,errors.join('\n'));
  await page.waitForFunction(()=>[...document.querySelectorAll('.video-strip img')].length===2&&[...document.querySelectorAll('.video-strip img')].every(i=>i.complete&&i.naturalWidth>0));
  for (const width of [1500,768,390,320]) {
    await page.setViewportSize({width,height:1000});
    const ok=await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth);
    if(!ok){console.log(await page.evaluate(()=>[...document.querySelectorAll("body *")].filter(e=>e.getBoundingClientRect().right>innerWidth+1).map(e=>({tag:e.tagName,cls:String(e.className),right:e.getBoundingClientRect().right})).slice(-12)));}
    assert.ok(ok,`overflow at ${width}`);
    if(width===390)await page.screenshot({path:process.env.SHOT_MOBILE??'/tmp/video-mobile.png'});
  }
  console.log('PASS: upload, split, live captions and code, errors shown, autosave, render, undo; no model called');
} finally {
  if(browser)await browser.close();server.kill('SIGTERM');
  await new Promise(resolve=>server.once('exit',resolve));
  await fs.rm(state,{recursive:true,force:true});
}
