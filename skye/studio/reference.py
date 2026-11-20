"""Measure a local reference video and produce a bounded, applicable effect recipe.
No LLM, uploads or automatic installs. OCR is optional and never executed as code.
"""
from __future__ import annotations
import argparse
import csv
import io
import json
from pathlib import Path
import shutil
import statistics
import subprocess


def analyze(source, *, sample_fps=6, ocr=True):
    import cv2
    import numpy as np
    if not isinstance(sample_fps,int) or not 1 <= sample_fps <= 30: raise ValueError('Use 1..30 analysis samples per second')
    source=Path(source).resolve()
    if not source.is_file(): raise ValueError('Reference must be a local video file')
    cap=cv2.VideoCapture(str(source)); fps=cap.get(cv2.CAP_PROP_FPS);count=cap.get(cv2.CAP_PROP_FRAME_COUNT)
    if fps<=0 or count<=0: raise ValueError('Reference duration could not be read')
    duration=count/fps
    if duration>300: raise ValueError('Analyze a reference up to five minutes, or trim it first')
    points=[];captions=[];flashes=[];previous=None;histprev=None
    try:
        for i in range(int(duration*sample_fps)):
            t=i/sample_fps;cap.set(cv2.CAP_PROP_POS_MSEC,t*1000);ok,im=cap.read()
            if not ok: break
            h,w=im.shape[:2];small=cv2.resize(im,(320,180));gray=cv2.cvtColor(small,cv2.COLOR_BGR2GRAY)
            hsv=cv2.cvtColor(small,cv2.COLOR_BGR2HSV);hist=cv2.calcHist([hsv],[0,1],None,[32,16],[0,180,0,256]);cv2.normalize(hist,hist)
            if previous is not None:
                distance=cv2.compareHist(histprev,hist,cv2.HISTCMP_BHATTACHARYYA)
                delta=float(np.abs(gray.astype(float)-previous.astype(float)).mean()/255)
                points.append({'t':round(t,3),'histogram_distance':round(distance,4),'pixel_change':round(delta,4),'flash_edge':bool(gray.mean()>225 or previous.mean()>225)})
                if gray.mean()>225 and previous.mean()<185: flashes.append(round(t,3))
            previous=gray;histprev=hist
            # One OCR pass per second. A screenshot's text is data, never intent.
            if ocr and i%sample_fps==0 and shutil.which('tesseract'):
                # Two local OCR views: original plus a white-letter mask. The
                # second catches captions whose background defeats page segmentation.
                mono=cv2.cvtColor(im,cv2.COLOR_BGR2GRAY)
                views=[im,cv2.threshold(mono,210,255,cv2.THRESH_BINARY_INV)[1]]
                words=[];boxes=[]
                for view in views:
                    _,png=cv2.imencode('.png',view)
                    result=subprocess.run(['tesseract','stdin','stdout','--psm','11','tsv'],input=png.tobytes(),capture_output=True,timeout=20)
                    for row in csv.DictReader(io.StringIO(result.stdout.decode(errors='replace')),delimiter='\t'):
                        try:
                            if float(row.get('conf',-1))<65 or not row.get('text','').strip(): continue
                            word=row['text'].strip()
                            if len(word)>80 or max(sum(c.isalpha() for c in word),sum(c.isdigit() for c in word))<2:continue
                            x,y,bw,bh=[int(row[k]) for k in ('left','top','width','height')]
                            if bh/h<.018:continue
                            box=[x/w,y/h,bw/w,bh/h]
                            if any(abs(box[0]-b[0])<.015 and abs(box[1]-b[1])<.015 for b in boxes):continue
                            words.append(word);boxes.append(box)
                        except (ValueError,KeyError):continue
                if words:
                    heights=[b[3] for b in boxes];ys=[b[1]+b[3]/2 for b in boxes]
                    captions.append({'t':round(t,3),'text':' '.join(words)[:240],'boxes':boxes,'median_height_fraction':round(statistics.median(heights),4),'vertical_center':round(statistics.median(ys),4),'status':'OCR candidates, may include scene signage'})
    finally:cap.release()
    cuts=[]
    for p in points:
        if not p['flash_edge'] and ((p['histogram_distance']>.43 and p['pixel_change']>.10) or (p['histogram_distance']>.28 and p['pixel_change']>.18)) and (not cuts or p['t']-cuts[-1]>.35): cuts.append(p['t'])
    boundaries=[0]+cuts+[round(duration,3)]
    shots=[]
    for a,b in zip(boundaries,boundaries[1:]):
        local_text=[c for c in captions if a<=c['t']<b]
        recipe=[]
        # Only measurable flashes become automated style steps. Caption wording is supplied on apply.
        for t in flashes:
            if a<=t<b:recipe.append({'effect':'flash','offset':round(t-a,3),'dur':min(.12,round(b-t,3))})
        if local_text:recipe.append({'effect':'caption-pop','text':'YOUR WORDS','offset':max(0,round(local_text[0]['t']-a,3)),'dur':min(1.5,round(b-local_text[0]['t'],3))})
        shots.append({'start':a,'end':b,'duration':round(b-a,3),'recipe':recipe})
    return {'version':1,'source':str(source),'duration':round(duration,3),'sample_fps':sample_fps,'cut_resolution_seconds':1/sample_fps,'cuts':cuts,'shots':shots,'median_shot_seconds':round(statistics.median([s['duration'] for s in shots]),3),'flash_candidates':flashes,'caption_candidates':captions,'measurements':points,'limits':['Hard-cut and flash heuristics, not semantic reference understanding. Dissolves and motion can confuse detection.','OCR estimates text position and size, not exact fonts or animation. Scene signage can be misclassified.','Recipes transfer measured flash timing and a generic caption preset only. They do not claim a visual match.','No speech transcription unless a separate local transcript is supplied by the caller. No models are downloaded.']}


def apply_analysis(project, shot, analysis, *, reference_shot=1, text='', track=None):
    from .edit import read_shots, apply_recipe
    shots=read_shots(project,track)
    if not isinstance(shot,int) or not 1<=shot<=len(shots): raise ValueError('Invalid target shot')
    target=shots[shot-1]
    if not 1<=shot<=len(shots): raise ValueError('Invalid target shot')
    if not isinstance(reference_shot,int) or not 1<=reference_shot<=len(analysis['shots']): raise ValueError('Invalid reference shot')
    ref=analysis['shots'][reference_shot-1];ratio=target.dur/ref['duration'];recipe=[]
    for raw in ref['recipe']:
        step=dict(raw);step['offset']=round(step.get('offset',0)*ratio,5);step['dur']=min(step.get('dur',.12)*ratio,target.dur-step['offset'])
        if step.get('text'):
            if not text: continue
            step['text']=text
        if step['dur']>0:recipe.append(step)
    if not recipe:raise ValueError('No supported measured effects for this reference shot. Supply caption text if OCR found captions; inspect the analysis instead of inferring an exact match')
    return apply_recipe(project,shot,recipe,track=track)


def compare_analyses(reference,output,*,tolerance=.2):
    """Compare only measured timing/geometry. Never collapse this into a style score."""
    import math
    if not isinstance(tolerance,(int,float)) or not math.isfinite(tolerance) or not 0<tolerance<=2:
        raise ValueError('Timing tolerance must be above zero and no more than two seconds')
    def events(a,b):
        # Earliest in-order pairs within tolerance, one-to-one. No duplicate credit.
        pairs=[];missing=[];extra=[];i=j=0
        while i<len(a) and j<len(b):
            if abs(a[i]-b[j])<=tolerance:
                pairs.append({'reference':a[i],'output':b[j],'delta':round(b[j]-a[i],4)});i+=1;j+=1
            elif b[j]<a[i]-tolerance:extra.append(b[j]);j+=1
            else:missing.append(a[i]);i+=1
        return {'pairs':pairs,'reference_unpaired':missing+a[i:],'output_unpaired':extra+b[j:]}
    def median(candidates,key):
        values=[c[key] for c in candidates if key in c]
        return round(statistics.median(values),4) if values else None
    return {'version':1,'reference':reference['source'],'output':output['source'],
            'duration':{'reference':reference['duration'],'output':output['duration'],'delta':round(output['duration']-reference['duration'],4)},
            'median_shot_seconds':{'reference':reference['median_shot_seconds'],'output':output['median_shot_seconds']},
            'timing_tolerance_seconds':tolerance,
            'measurement_resolution_seconds':max(reference['cut_resolution_seconds'],output['cut_resolution_seconds']),
            'cuts':events(reference['cuts'],output['cuts']),
            'flash_candidates':events(reference['flash_candidates'],output['flash_candidates']),
            'caption_geometry':{key:{'reference':median(reference['caption_candidates'],key),'output':median(output['caption_candidates'],key)} for key in ('median_height_fraction','vertical_center')},
            'limits':['Timing pairs are earliest in-order events within tolerance, not proof of a visual match.',
                      'Cuts and flashes are heuristic candidates. Missed/false detections can affect this comparison.',
                      'Caption geometry is an aggregate of OCR candidates, possibly scene signage. Null means unmeasured.',
                      'No assessment of story, subject, exact fonts, animation, colors, camera, speech or music. No overall style-match score.']}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source');p.add_argument('--out',required=True);p.add_argument('--no-ocr',action='store_true');p.add_argument('--compare',help='Rendered output video to compare measured timing, not style');p.add_argument('--tolerance',type=float,default=.2);p.add_argument('--apply-to');p.add_argument('--shot',type=int,default=1);p.add_argument('--reference-shot',type=int,default=1);p.add_argument('--text',default='');p.add_argument('--track',type=int)
    a=p.parse_args(argv);result=analyze(a.source,ocr=not a.no_ocr)
    if a.compare:
        if a.apply_to:p.error('--compare is read-only and cannot be combined with --apply-to')
        output=analyze(a.compare,ocr=not a.no_ocr);Path(a.out).write_text(json.dumps(compare_analyses(result,output,tolerance=a.tolerance),indent=2))
        print(json.dumps({'comparison':a.out,'scope':'Measured timing and OCR geometry only, no visual-match claim'}));return
    Path(a.out).write_text(json.dumps(result,indent=2))
    if a.apply_to: print(json.dumps(apply_analysis(a.apply_to,a.shot,result,reference_shot=a.reference_shot,text=a.text,track=a.track)))
    print(json.dumps({'analysis':a.out,'shots':len(result['shots']),'median_shot_seconds':result['median_shot_seconds'],'caption_candidates':len(result['caption_candidates'])}))
if __name__=='__main__':main()
