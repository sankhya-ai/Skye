// Re-shoots docs/video/screenshots with synthetic footage. No model calls.
import {chromium} from '../skye/media/node_modules/playwright/index.mjs';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {spawn, spawnSync} from 'node:child_process';
const out=process.argv[2]??'docs/video/screenshots';
const state=await fs.mkdtemp(path.join(os.tmpdir(),'skye-shoot-'));
const fixture=path.join(state,'Product demo.webm');
spawnSync('ffmpeg',['-hide_banner','-loglevel','error','-y','-f','lavfi','-i','testsrc2=s=640x360:r=30:d=4','-an','-c:v','libvpx-vp9','-deadline','realtime',fixture]);
const port=String(18000+Math.floor(Math.random()*10000));
const server=spawn('python3',['-m','skye.cli','ui','--state-dir',state,'--port',port],{stdio:'ignore'});
let b;
try{
  let token;
  for(let i=0;i<100;i++){try{token=(await fs.readFile(path.join(state,'ui-token'),'utf8')).trim();await fetch(`http://127.0.0.1:${port}/health`);break;}catch{await new Promise(r=>setTimeout(r,100));}}
  b=await chromium.launch({headless:true});
  for(const [name,vp] of [['editor',{width:1500,height:900}],['mobile',{width:390,height:844}]]){
    const p=await b.newPage({viewport:vp});
    await p.goto(`http://127.0.0.1:${port}/?token=${token}#/video`);
    await p.getByLabel('Find cuts').uncheck();
    await p.locator('input[type=file]').setInputFiles(fixture);
    await p.getByRole('heading',{name:'Product demo',exact:true}).waitFor({timeout:60000});
    await p.waitForFunction(()=>document.querySelector('video')?.readyState>=2);
    await p.getByLabel('Playhead').fill('45');
    await p.keyboard.press('s');
    await p.getByRole('tab',{name:'Captions',exact:true}).click();
    await p.getByRole('button',{name:'Caption',exact:true}).click();await p.locator('textarea,input[type=text]').first().waitFor({timeout:5000}).catch(()=>{});
    await p.waitForTimeout(1500);
    await p.screenshot({path:path.join(out,name+'.png'),clip:name==='editor'?{x:250,y:0,width:1250,height:720}:undefined});
    await p.close();
  }
}finally{await b?.close();server.kill();await fs.rm(state,{recursive:true,force:true});}
