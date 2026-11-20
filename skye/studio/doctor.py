"""One-command local platform check. Never installs packages, calls a model or uploads results."""
from __future__ import annotations
import argparse
import importlib.util
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile


def check(out):
    out=Path(out).expanduser().resolve();out.mkdir(parents=True,exist_ok=True)
    result={'platform':platform.platform(),'machine':platform.machine(),'python':platform.python_version(),'tools':{k:shutil.which(k) for k in ('node','ffmpeg','ffprobe','tesseract','claude','codex')},'optional_modules':{k:importlib.util.find_spec(k) is not None for k in ('cv2','numpy','rembg','pytest')},'checks':{},'not_verified':['Owner CLI authentication or quota','Interactive selection, Undo and audio sync','4K performance, proxy generation and HEVC alpha on this hardware','Mask quality on your footage; optical-flow tracking under parallax/occlusion']}
    def run(name,args,timeout=180):
        try:
            p=subprocess.run(args,capture_output=True,text=True,timeout=timeout);(out/(name+'.log')).write_text(p.stdout+p.stderr)
            result['checks'][name]={'ok':p.returncode==0,'log':str(out/(name+'.log'))};return p.returncode==0
        except (OSError,subprocess.TimeoutExpired) as e:result['checks'][name]={'ok':False,'error':str(e)};return False
    if not all(result['tools'][x] for x in ('node','ffmpeg','ffprobe')):
        result['checks']['smoke']={'ok':False,'error':'Install Node, FFmpeg and ffprobe first. This check installs nothing.'}
    else:
        root=out/'smoke';(root/'assets').mkdir(parents=True,exist_ok=True)
        ok=run('make-source',['ffmpeg','-y','-f','lavfi','-i','color=c=0x285c79:s=640x360:d=1:r=30','-c:v','libx264','-pix_fmt','yuv420p',str(root/'assets/source.mp4')])
        if ok:
            (root/'film.html').write_text('<!doctype html><html><head><meta name="viewport" content="width=640,height=360"><style></style></head><body><section><video id="smoke" src="assets/source.mp4#t=0,1" at="0" volume="0"></video></section></body></html>')
            try:
                from .edit import apply_effect
                apply_effect(root,1,'caption-pop',text='SKYE|CHECK',offset=0)
                vendor=Path(__file__).parent/'vendor/openfilm'
                script=out/'render.mjs'
                script.write_text('import {pathToFileURL} from "node:url";const vendor='+json.dumps(str(vendor))+';const {render}=await import(pathToFileURL(vendor+"/src/render.mjs"));const {closeBrowsers}=await import(pathToFileURL(vendor+"/src/host.mjs"));try{await render(['+json.dumps(str(root))+',"0-1"],{out:'+json.dumps(str(out/'smoke.mp4'))+',fps:30,workers:1})}finally{await closeBrowsers()}')
                if run('render',['node',str(script)],timeout=240):
                    run('probe',['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(out/'smoke.mp4')])
                    run('frame',['ffmpeg','-y','-ss','0.5','-i',str(out/'smoke.mp4'),'-frames:v','1',str(out/'frame.png')])
                    result['not_verified'].append('Open frame.png and smoke.mp4: verify readable animated SKYE CHECK over a blue background, no errors or black frames')
            except Exception as e:result['checks']['effect']={'ok':False,'error':str(e)}
    (out/'report.json').write_text(json.dumps(result,indent=2));return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',default='skye-platform-check');a=p.parse_args(argv);r=check(a.out);print(json.dumps(r,indent=2));sys.exit(0 if r['checks'] and all(v['ok'] for v in r['checks'].values()) else 1)
if __name__=='__main__':main()
