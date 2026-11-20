import copy,json,subprocess
from pathlib import Path
import pytest
from skye.studio.audio import change,read
from skye.media.model import MediaError
from skye.studio.storyboard import plan_change

class Studio:
    def __init__(self,root):self.root=root;self.film={'stage':{'w':360,'h':640},'tracks':[{'clips':[{'id':'one','src':'assets/video.mp4','time':[0,2]},{'id':'two','src':'assets/video.mp4','at':2,'time':[2,4]}]}]}
    def request(self,route,*args,**kwargs):return {'value':self.film,'project':{'path':str(self.root)},'rev':'base'}
    def apply(self,r):
        helper=Path(__file__).parents[1]/'skye/studio/check.mjs'
        self.film=json.loads(subprocess.check_output(['node',str(helper)],input=json.dumps({'film':self.film,'ops':r['ops']}).encode()))

@pytest.fixture
def studio(tmp_path):
    p=tmp_path/'assets';p.mkdir();(p/'bed.wav').write_bytes(b'sound')
    return Studio(tmp_path)

def probe(monkeypatch,duration=10):
    monkeypatch.setattr('skye.studio.audio.subprocess.run',lambda *a,**k:type('P',(),{'stdout':json.dumps({'format':{'duration':duration},'streams':[{'codec_type':'audio'}]})})())

def test_bed_and_shot_sound_are_native_and_anchor_predictably(tmp_path,studio,monkeypatch):
    probe(monkeypatch)
    bed=change(tmp_path,studio,'demo',{'base':'base','action':'insert','source':'assets/bed.wav','target':'bed'})
    assert bed['ops'][0]['clip']['time']==[0,4]
    assert 'sound' not in bed['ops'][0]['clip']
    # Release ffprobe stub before native operations use subprocess.check_output.
    monkeypatch.undo();studio.apply(bed)
    probe(monkeypatch)
    shot=change(tmp_path,studio,'demo',{'base':'base','action':'insert','source':'assets/bed.wav','target':'two','sourceIn':1})
    assert shot['ops'][0]['at']==2 and shot['ops'][0]['clip']['time']==[1,3]
    monkeypatch.undo();studio.apply(shot)
    move=plan_change(tmp_path,studio,'demo',{'base':'base','action':'reorder','order':['two','one'],'spanning':'anchor'})
    studio.apply(move)
    rows=read(tmp_path,studio,'demo')['rows']
    assert next(r for r in rows if r['bed'])['at']==0
    assert next(r for r in rows if r['sound'] and not r['bed'])['at']==0

def test_levels_fades_native_validation_and_scope(tmp_path,studio):
    studio.film['tracks'].append({'clips':[{'id':'bed','src':'assets/bed.wav','time':[0,4]}]})
    studio.apply(change(tmp_path,studio,'demo',{'base':'base','action':'levels','clip':'bed','volume':.4,'fadeIn':.2,'fadeOut':.3}))
    row=next(r for r in read(tmp_path,studio,'demo')['rows'] if r['id']=='bed')
    assert row['volume']==.4 and row['fade']==[.2,.3]
    with pytest.raises(MediaError):change(tmp_path,studio,'demo',{'base':'old','action':'levels','clip':'bed'})
    with pytest.raises(MediaError):change(tmp_path,studio,'demo',{'base':'base','action':'levels','clip':'bed','fadeIn':3,'fadeOut':2})
    studio.film['tracks'][-1]['locked']=True
    with pytest.raises(MediaError):change(tmp_path,studio,'demo',{'base':'base','action':'levels','clip':'bed'})
    with pytest.raises(MediaError):change(tmp_path,studio,'demo',{'base':'base','action':'remove','clip':'one'})

def test_no_silent_audio_loop_or_external_sources(tmp_path,studio,monkeypatch):
    probe(monkeypatch,1)
    with pytest.raises(MediaError,match='too short'):change(tmp_path,studio,'demo',{'base':'base','action':'insert','source':'assets/bed.wav','target':'bed'})
    with pytest.raises(MediaError,match='imported'):change(tmp_path,studio,'demo',{'base':'base','action':'insert','source':'https://example.org/bed.wav'})
