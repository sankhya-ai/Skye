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
  await page.getByLabel('Detect cuts').uncheck();
  await page.locator('input[type=file]').setInputFiles(fixture);
  await page.getByRole('heading',{name:'Browser scenario',exact:true}).waitFor({timeout:60000});
  await page.waitForFunction(()=>document.querySelector('video')?.readyState>=2);
  await page.evaluate(()=>{document.querySelector('video').currentTime=1;});
  await page.waitForFunction(()=>{const v=document.querySelector('video');return v&&!v.seeking&&v.currentTime===1;});
  await page.getByRole('button',{name:'Split at playhead'}).click();
  await page.getByRole('button',{name:/Shot 02.*1.00s/}).waitFor();
  await page.getByRole('button',{name:/Shot 02.*1.00s/}).click();
  await page.getByRole('textbox',{name:'visual.html',exact:true}).waitFor();
  await page.getByRole('button',{name:'Render',exact:true}).click();
  await page.getByRole('link',{name:'Download render'}).waitFor({timeout:60000});
  // Change only the selected shot, save, then reuse the neighbour's encoded picture.
  const field=page.getByRole('textbox',{name:'visual.html',exact:true});
  await field.fill((await field.inputValue()).replace("title.textContent = '';", "title.textContent = 'One changed shot';"));
  await page.getByRole('button',{name:'Save shot'}).click();
  await page.getByText('Changes not rendered',{exact:true}).waitFor();
  await page.getByRole('button',{name:'Render',exact:true}).click();
  await page.getByText('1 rendered · 1 reused',{exact:true}).waitFor({timeout:60000});
  await page.getByRole('button',{name:'Captions',exact:true}).click();
  await page.getByRole('textbox',{name:'captions.json'}).fill('[{"start":0,"end":9999,"text":"Invalid"}]');
  await page.getByRole('button',{name:'Save shot'}).click();
  await page.getByRole('alert').filter({hasText:'Caption crosses the shot boundary'}).waitFor();
  await page.getByRole('textbox',{name:'captions.json'}).fill('[]');
  await page.getByRole('button',{name:'Dismiss'}).click();
  await page.getByRole('button',{name:'Save shot'}).click();
  await page.getByRole('button',{name:'Prepare edit'}).waitFor({state:'visible'});
  await page.getByRole('button',{name:'Prepare edit'}).click();
  await page.getByRole('button',{name:'Approve and run'}).waitFor(); // Do not start a model.
  assert.equal(errors.length,0,errors.join('\n'));
  await page.waitForFunction(()=>[...document.querySelectorAll(".shot-image img")].length===2&&[...document.querySelectorAll(".shot-image img")].every(i=>i.complete&&i.naturalWidth>0));
  for (const width of [1500,768,390,320]) {
    await page.setViewportSize({width,height:1000});
    assert.ok(await page.locator(".shot-image").evaluateAll(es=>es.every(e=>{const i=e.querySelector("img").getBoundingClientRect(),r=e.getBoundingClientRect();return Math.abs(i.width-r.width)<1&&Math.abs(i.height-r.height)<1})),`thumbnail bounds at ${width}`);
    const ok=await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth);
    if(!ok){console.log(await page.evaluate(()=>[...document.querySelectorAll("body *")].filter(e=>e.getBoundingClientRect().right>innerWidth+1).map(e=>({tag:e.tagName,cls:e.className,right:e.getBoundingClientRect().right,text:e.textContent?.slice(0,40)})).slice(-20)));await page.screenshot({path:"/downloads/overflow-320.png",fullPage:true});}
    assert.ok(ok,`overflow at ${width}`);
  }
  console.log('PASS: browser upload -> split -> edit -> save -> render -> cache reuse -> invalid caption -> prepare-only; no model called');
} finally {
  if(browser)await browser.close();server.kill('SIGTERM');
  await new Promise(resolve=>server.once('exit',resolve));
  await fs.rm(state,{recursive:true,force:true});
}
