"""Local planar text tracking. Optional OpenCV, no model or network calls."""
from __future__ import annotations
import argparse
import html
import json
from pathlib import Path
import uuid


def track_text(project, shot, text, *, quad=None, roi=None, track=None, fps=15):
    import cv2
    import numpy as np
    from .edit import read_shots, _canonical_text, _insert_overlay, _write_film, _page_clip, EditError
    root=Path(project)
    body=_canonical_text((root/'film.html').read_text())
    shots=read_shots(root,track,_text=body)
    if not 1<=shot<=len(shots): raise EditError('Invalid shot')
    s=shots[shot-1]
    if s.speed!=1 or s.style.replace(' ', '').replace('object-fit:cover','').strip('; '):
        raise EditError('Tracking requires an unsped centered-cover shot without existing styles or animation')
    if not 1<=fps<=30 or s.dur>30: raise EditError('Use 1..30 tracking fps and a shot no longer than 30 seconds')
    if not text.strip() or len(text)>160: raise EditError('Text needs 1..160 characters')
    # Project paths must remain local and inside the project.
    source=(root/s.src).resolve()
    if not source.is_relative_to(root.resolve()) or not source.is_file(): raise EditError('Tracking source must be a local project asset')
    from .edit import _canonical_text
    import subprocess
    stage=json.loads(subprocess.check_output(['node',str(Path(__file__).with_name('effect-check.mjs'))],input=body.encode()))['stage']
    w,h=stage['w'],stage['h']
    scale=min(1,720/max(w,h)); aw,ah=round(w*scale),round(h*scale)
    q=np.array(quad or [[.2,.69],[.8,.69],[.91,.87],[.09,.87]],np.float32)
    if q.shape!=(4,2) or not np.isfinite(q).all() or (q<0).any() or (q>1).any(): raise EditError('Quad is four normalized x,y corners, clockwise from top-left')
    q*=np.array([aw,ah])
    region=roi or [0,.52,1,.98]
    if len(region)!=4 or not 0<=region[0]<region[2]<=1 or not 0<=region[1]<region[3]<=1: raise EditError('ROI is normalized x0,y0,x1,y1')
    cap=cv2.VideoCapture(str(source))
    def frame(t):
        cap.set(cv2.CAP_PROP_POS_MSEC,(s.time[0]+t)*1000)
        ok,im=cap.read()
        if not ok: raise EditError(f'Cannot decode tracking frame at {t:.2f}s')
        ih,iw=im.shape[:2]; r=max(aw/iw,ah/ih)
        im=cv2.resize(im,(round(iw*r),round(ih*r))); y=(im.shape[0]-ah)//2;x=(im.shape[1]-aw)//2
        return cv2.cvtColor(im[y:y+ah,x:x+aw],cv2.COLOR_BGR2GRAY)
    samples=[]; quality=[]
    try:
        prev=frame(0)
        mask=np.zeros_like(prev);x0,y0,x1,y1=region;mask[round(y0*ah):round(y1*ah),round(x0*aw):round(x1*aw)]=255
        points=cv2.goodFeaturesToTrack(prev,maxCorners=500,qualityLevel=.01,minDistance=5,mask=mask)
        if points is None or len(points)<16: raise EditError('Not enough ground features. Choose a textured stationary plane with --roi')
        original=points.copy(); current=points.copy()
        samples.append({'t':0,'quad':(q/np.array([aw,ah])).tolist()});quality.append({'t':0,'inliers':len(points),'error':0})
        import math
        for i in range(1,math.ceil(s.dur*fps)):
            t=i/fps; gray=frame(t)
            nxt,st,_=cv2.calcOpticalFlowPyrLK(prev,gray,current,None,winSize=(25,25),maxLevel=3)
            back,st2,_=cv2.calcOpticalFlowPyrLK(gray,prev,nxt,None,winSize=(25,25),maxLevel=3)
            good=(st.ravel()==1)&(st2.ravel()==1)&(np.linalg.norm(current-back,axis=2).ravel()<1.5)
            original=original[good];nxt=nxt[good]
            if len(nxt)<12: raise EditError(f'Tracking lost at {t:.2f}s. Shorten the shot or choose another ground ROI')
            H,inliers=cv2.findHomography(original,nxt,cv2.RANSAC,2.5)
            count=0 if inliers is None else int(inliers.sum())
            if H is None or count<10 or count/len(nxt)<.45: raise EditError(f'No reliable plane at {t:.2f}s')
            error=float(np.median(np.linalg.norm(cv2.perspectiveTransform(original,H)-nxt,axis=2)[inliers.astype(bool)]))
            corners=cv2.perspectiveTransform(q.reshape(-1,1,2),H).reshape(4,2)
            if not np.isfinite(corners).all() or abs(cv2.contourArea(corners))<100: raise EditError('Tracked plane collapsed')
            samples.append({'t':round(t,5),'quad':(corners/np.array([aw,ah])).tolist()}); quality.append({'t':round(t,5),'inliers':count,'error':round(error,3)})
            keep=inliers.ravel().astype(bool)
            original=original[keep]
            prev=gray;current=nxt[keep].reshape(-1,1,2)
    finally: cap.release()
    # Homographies are resolved ahead of time, so preview and exact export use identical transforms.
    transforms=[]
    src=np.float32([[0,0],[1000,0],[1000,220],[0,220]])
    for sample in samples:
        dst=np.float32(sample['quad'])*np.float32([w,h])
        H=cv2.getPerspectiveTransform(src,dst); H/=H[2,2]
        transforms.append([sample['t'],[float(H[0,0]),float(H[1,0]),0,float(H[2,0]),float(H[0,1]),float(H[1,1]),0,float(H[2,1]),0,0,1,0,float(H[0,2]),float(H[1,2]),0,1]])
    name='tracked-'+uuid.uuid4().hex[:10]
    data={'version':1,'method':'LK forward-backward + RANSAC homography','shot':s.clip_id,'source':s.src,'range':list(s.time),'fps':fps,'text':text,'roi':region,'samples':samples,'quality':quality}
    page='<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;background:transparent;overflow:hidden}#text{position:absolute;left:0;top:0;width:1000px;height:220px;display:flex;align-items:center;justify-content:center;color:white;font:900 150px Arial,sans-serif;white-space:nowrap;transform-origin:0 0;text-shadow:0 3px 1px #0006;opacity:.92}</style></head><body><div id="text">'+html.escape(text)+'</div><script>const frames='+json.dumps(transforms)+';window.film={duration:'+str(s.dur)+',frame(t){const i=Math.min(frames.length-1,Math.max(0,Math.round(t*'+str(fps)+')));document.getElementById("text").style.transform="matrix3d("+frames[i][1].join(",")+")"}};window.film.frame(0);</script></body></html>'
    pages=root/'pages';pages.mkdir(exist_ok=True)
    (pages/(name+'.html')).write_text(page);(pages/(name+'.json')).write_text(json.dumps(data,indent=2))
    el,at,dur=_page_clip({'src':'pages/'+name+'.html','len':s.dur},s.at,s.dur)
    _write_film(root,_insert_overlay(body,el,at,dur))
    return {'page':'pages/'+name+'.html','tracking':'pages/'+name+'.json','frames':len(samples),'min_inliers':min(v['inliers'] for v in quality),'max_median_error':max(v['error'] for v in quality)}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('project');p.add_argument('--shot',type=int,required=True);p.add_argument('--text',required=True);p.add_argument('--track',type=int);p.add_argument('--quad',help='eight normalized corner coordinates');p.add_argument('--roi',help='normalized x0,y0,x1,y1');p.add_argument('--fps',type=int,default=15)
    a=p.parse_args(argv);q=None
    if a.quad:
        v=list(map(float,a.quad.split(',')));q=[v[i:i+2] for i in range(0,len(v),2)]
    print(json.dumps(track_text(a.project,a.shot,a.text,track=a.track,quad=q,roi=list(map(float,a.roi.split(','))) if a.roi else None,fps=a.fps)))
if __name__=='__main__': main()
