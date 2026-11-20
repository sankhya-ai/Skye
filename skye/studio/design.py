"""Deterministic composition direction and inspectable design rules, no model calls."""
import math
from collections import Counter

GUIDE = {
    'version': 1,
    'rules': [
        {'id':'tilt-budget','rule':'Keep at least 80% of phrases upright; tilt one emphasis phrase at a time, alternating direction.'},
        {'id':'hierarchy','rule':'One focal phrase; support phrases use less than 60% of its height.'},
        {'id':'safe-area','rule':'Keep text, including rotation, within an 8% title-safe inset.'},
        {'id':'rhythm','rule':'Alternate calm and impact scenes. Do not use a transition on every cut.'},
        {'id':'palette','rule':'One base palette, one contrast palette, one accent phrase per scene.'},
        {'id':'repetition','rule':'Avoid three identical layouts in a row; keep a quiet resolution.'},
        {'id':'timing','rule':'Let speech lead phrase entrances. Use measured beats for picture cuts, never force speech to a grid.'},
    ]
}

def direct(rows, free=False):
    import copy
    rows=copy.deepcopy(rows)
    if free:return rows,[]
    total=sum(len(r['words']) for r in rows);budget=total//5;used=0;choices=[]
    for i,row in enumerate(rows):
        role='resolve' if i==len(rows)-1 else ('hero','quiet','impact','contrast','quiet','impact')[i%6]
        row['_role']=role
        row['_tilt']=0
        if role=='impact' and used<budget:
            row['_tilt']=6 if used%2==0 else -6;used+=1
        row['palette']='mint' if row.get('source') else ('paper' if role=='resolve' else rows[0].get('palette','paper'))
        row['_blocks']=role=='impact' and i>0
        row['negative']=bool(row.get('negative',False) and role=='impact')
        if i>1 and row['layout']==rows[i-1]['layout']==rows[i-2]['layout']:row['layout']='title' if row['layout']!='title' else 'split'
        choices.append({'scene':i+1,'role':role,'tilt':row['_tilt'],'note':{'hero':'Large upright hook.','quiet':'Smaller support, gentle entrance.','impact':'Single angled emphasis, quick entrance.','contrast':'Upright type, picture rotation carries contrast.','resolve':'Still upright finish, longer hold.'}[role]})
    return rows,choices

def text_box(row,j,n):
    """Normalized boxes; hierarchy and composition vary, not arbitrary angles."""
    role=row.get('_role','hero');layout=row['layout'];focal=0 if role in ('hero','resolve') else n-1
    # Up to five lines, one dominant line. These vertical slots do not overlap.
    heights=[.10 if n==5 else .13]*n;heights[focal]=.29 if n<=3 else .23
    gap=.035;total=sum(heights)+gap*(n-1);y=(1-total)/2+sum(heights[:j])+gap*j
    w=.82 if j==focal else .56
    x=(1-w)/2
    if layout in ('split','cards') and role!='resolve':x=.09 if j==focal else .35
    if role=='quiet':w=.72 if j==focal else .44;x=.1
    r=row.get('_tilt',0) if j==focal else 0
    # Exact conservative AABB fit inside 8%, even for long/vertical formats.
    return {'x':x,'y':y,'w':min(w,.91-x),'h':heights[j],'r':r,'focal':j==focal}

def lint(clips,W,H):
    warnings=[];texts=[c for c in clips if c.get('attrs',{}).get('data-skye-motion')=='text']
    tilted=[c for c in texts if abs(c.get('box',{}).get('r',0))>.5]
    def warn(rule,ids,note):warnings.append({'rule':rule,'clips':ids,'note':note})
    if len(tilted)>len(texts)//5:warn('tilt-budget',[c['id'] for c in tilted],'Too many angled phrases. Keep at least 80% upright.')
    signs=[1 if c['box']['r']>0 else -1 for c in sorted(tilted,key=lambda c:c.get('at',0))]
    if any(a==b for a,b in zip(signs,signs[1:])):warn('tilt-direction',[c['id'] for c in tilted],'Repeated tilt direction. Alternate the few emphasis angles.')
    for c in texts:
        b=c.get('box',{});a=math.radians(b.get('r',0));ww=abs(b.get('w',0)*math.cos(a))+abs(b.get('h',0)*math.sin(a));hh=abs(b.get('w',0)*math.sin(a))+abs(b.get('h',0)*math.cos(a));cx=b.get('x',0)+b.get('w',0)/2;cy=b.get('y',0)+b.get('h',0)/2
        if cx-ww/2<W*.079 or cx+ww/2>W*.921 or cy-hh/2<H*.079 or cy+hh/2>H*.921:warn('safe-area',[c['id']],'Rotated text box crosses the 8% inset.')
    groups={}
    for c in texts:groups.setdefault(c.get('attrs',{}).get('data-skye-scene',''),[]).append(c)
    for group in groups.values():
        if len(group)>1:
            hs=sorted([c.get('box',{}).get('h',0) for c in group],reverse=True)
            if hs[1]>.6*hs[0]:warn('hierarchy',[c['id'] for c in group],'Support text competes with the focal phrase.')
    if texts:
        centers=[(c.get('box',{}).get('x',0)+c.get('box',{}).get('w',0)/2)/W for c in texts]
        if not .35<=sum(centers)/len(centers)<=.65:warn('balance',[],'The film leans to one edge. Rebalance the focal phrases.')
    for group in groups.values():
        for i,a in enumerate(group):
            for b in group[i+1:]:
                if a.get('at',0)+a['time'][1]-a['time'][0]<=b.get('at',0) or b.get('at',0)+b['time'][1]-b['time'][0]<=a.get('at',0):continue
                x,y=a.get('box',{}),b.get('box',{})
                overlap=max(0,min(x.get('x',0)+x.get('w',0),y.get('x',0)+y.get('w',0))-max(x.get('x',0),y.get('x',0)))*max(0,min(x.get('y',0)+x.get('h',0),y.get('y',0)+y.get('h',0))-max(x.get('y',0),y.get('y',0)))
                if overlap>W*H*.015:warn('collision',[a['id'],b['id']],'Concurrent text boxes overlap. Check actual glyphs before keeping it.')
    scenes=[c for c in clips if c.get('attrs',{}).get('data-skye-motion')=='scene'];layouts=[c['attrs'].get('data-skye-layout') for c in sorted(scenes,key=lambda c:c.get('at',0))]
    if any(a==b==c for a,b,c in zip(layouts,layouts[1:],layouts[2:]) if a):warn('repetition',[],'Three identical layouts in a row.')
    if len({c.get('attrs',{}).get('data-skye-palette') for c in scenes})>2:warn('palette',[],'More than two palettes in this composition.')
    blocks=[c for c in clips if c.get('attrs',{}).get('data-skye-motion')=='blocks']
    if len(blocks)>math.ceil(len(scenes)/3):warn('rhythm',[c['id'] for c in blocks],'A block transition on most cuts competes with the story.')
    return {'decisions':[{'scene':i+1,'role':c['attrs'].get('data-skye-role','custom'),'layout':c['attrs'].get('data-skye-layout','custom')} for i,c in enumerate(sorted(scenes,key=lambda c:c.get('at',0)))],'rules':GUIDE,'warnings':warnings,'metrics':{'phrases':len(texts),'tilted':len(tilted),'upright':len(texts)-len(tilted),'scenes':len(scenes),'transitions':len(blocks)}}
