"""Prepare source-timed alpha PNG mattes locally, without an alpha video codec."""
from __future__ import annotations
import json
import math
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path


def prepare_matte(root: Path, shot, at: float, dur: float, *, fps: int = 30) -> dict:
    from .edit import EditError
    if shot.speed != 1 or shot.style.strip().strip(';') not in ('', 'object-fit:cover'):
        raise EditError('prepared matte requires an un-sped, plain object-fit:cover shot')
    if not math.isfinite(at) or not math.isfinite(dur) or dur <= 0 or at < shot.at or at+dur > shot.at+shot.dur+1e-6:
        raise EditError('matte window must fit inside the selected shot')
    try:
        from rembg import remove, new_session
        import numpy as np
        from PIL import Image, ImageFilter
    except ImportError as e:
        raise EditError('local matte needs rembg, NumPy and Pillow') from e
    stage = re.search(r'width=(\d+),?\s*height=(\d+)', (root/'film.html').read_text())
    if not stage:
        raise EditError('prepared matte needs an explicit width/height viewport')
    w,h=map(int,stage.groups())
    source=(root/shot.src).resolve()
    if not source.is_file():
        raise EditError('prepared matte requires a local source file')
    start=round(shot.time[0]+at-shot.at,3)
    ident=uuid.uuid4().hex[:10]
    rel=f'assets/matte-{ident}'
    out=root/rel
    work=root/'.skye-effects'/f'matte-input-{ident}'
    out.mkdir(parents=True);work.mkdir(parents=True)
    before=time.monotonic()
    try:
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-ss',str(start),'-i',str(source),
                        '-t',str(dur),'-vf',f'fps={fps},scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}',
                        str(work/'f%06d.png'),'-y'],check=True)
        frames=sorted(work.glob('f*.png'))
        if not frames:
            raise EditError('matte extraction produced no frames')
        session=new_session('u2netp'); prev=None
        names=[]
        for i,fp in enumerate(frames):
            with Image.open(fp) as image:
                mask=np.asarray(remove(image.convert('RGB'),session=session,only_mask=True,post_process_mask=True),dtype=np.float32)/255
            if prev is not None:
                mask=.65*mask+.35*prev
            prev=mask
            alpha=Image.fromarray((mask*255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(2.2))
            rgba=Image.new('RGBA',(w,h),'white');rgba.putalpha(alpha)
            name=f'f{i:06d}.png';rgba.save(out/name);names.append(name)
        (out/'manifest.json').write_text(json.dumps({'fps':fps,'start':round(start,6),'frames':names,'geometry':[w,h],
                                                    'model':'u2netp','smoothing':.35,'feather':2.2}))
        return {'matte':rel+'/manifest.json','frames':len(names),'elapsed_s':round(time.monotonic()-before,2),
                'source_start':start,'duration':len(names)/fps}
    except Exception:
        shutil.rmtree(out,ignore_errors=True)
        raise
    finally:
        shutil.rmtree(work,ignore_errors=True)
