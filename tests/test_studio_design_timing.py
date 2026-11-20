import array,copy,math
import pytest
from skye.studio.design import lint
from skye.studio.motion import prepare
from skye.studio.timing import beats,align,asset
from skye.media.model import MediaError
from test_studio_motion import Host

@pytest.mark.parametrize('n',[1,2,3,4,5])
@pytest.mark.parametrize('w,h',[(1920,1080),(1080,1920),(1080,1080)])
def test_guided_budget_safe_area_hierarchy(w,h,n,tmp_path):
    host=Host(tmp_path);host.film['stage']={'w':w,'h':h}
    r=prepare(tmp_path,host,'demo',{'base':'r1','recipe':{'scenes':[{'words':['ONE','FOCAL','SUPPORT','MORE','END'][:n],'layout':'arc'} for _ in range(7)]}})
    assert r['design']['metrics']['tilted']==min(2,7*n//5)
    assert not r['design']['warnings']
    assert [d['tilt'] for d in r['decisions'] if d['tilt']]==[6,-6][:min(2,7*n//5)]
    host.apply(r)
    assert not lint([c for t in host.film['tracks'] for c in t['clips']],w,h)['warnings']

def test_raw_lint_flags_repeated_tilt_and_margins(tmp_path):
    host=Host(tmp_path)
    r=prepare(tmp_path,host,'demo',{'base':'r1','recipe':{'design':'free','scenes':[{'words':['ONE','TWO'],'layout':'arc'}]*4}})
    rules={w['rule'] for w in r['design']['warnings']}
    assert {'tilt-budget','hierarchy','safe-area','repetition','rhythm'}<=rules

@pytest.mark.parametrize('bpm',[72,100,120,150])
def test_local_pulse_recovers_synthetic_clicks(bpm):
    samples=array.array('h',[0])* (16000*12)
    for t in range(int(12*bpm/60)):
        start=round(t*60/bpm*16000)
        for j in range(800):
            if start+j<len(samples):samples[start+j]=int(24000*math.exp(-j/100)*math.sin(j*.25))
    r=beats(samples)
    assert abs(r['bpm']-bpm)<=1 and r['confidence']>.7
    assert r['grid']

def test_silence_and_sparse_do_not_invent_grid():
    assert beats(array.array('h',[0])*32000)['bpm'] is None
    assert beats(array.array('h',[50])*32000)['bpm'] is None

def test_speech_exact_tokens_and_no_fabricated_match():
    report={'duration':5,'words':[{'word':w,'start':i*.6,'end':i*.6+.4,'confidence':.99} for i,w in enumerate(['your','shots','your','story','make','the','cut'])]}
    rows=[{'words':['YOUR SHOTS','YOUR STORY']},{'words':['MAKE THE CUT']}]
    out=align(copy.deepcopy(rows),report)
    assert out[0]['_delays']==[0,1.2]
    assert out[1]['_delays']==pytest.approx([.12])
    assert sum(r['duration'] for r in out)==pytest.approx(4.65)
    with pytest.raises(MediaError,match='not found'):align([{'words':['NOT PRESENT']}],report)
    report['words'][0]['confidence']=.3
    with pytest.raises(MediaError,match='Low-confidence'):align(rows,report)

def test_asset_escape_rejected(tmp_path):
    with pytest.raises(MediaError):asset(tmp_path,'assets/../../etc/passwd')

def test_timing_input_rejects_internal_delays(tmp_path):
    with pytest.raises(MediaError):prepare(tmp_path,Host(tmp_path),'demo',{'base':'r1','recipe':{'scenes':[{'words':['x'],'_delays':[0]}]}})


def test_speech_prepare_uses_native_word_starts(tmp_path,monkeypatch):
    from skye.studio import timing
    report={'duration':4,'words':[{'word':w,'start':i*.6+.1,'end':i*.6+.5,'confidence':.99} for i,w in enumerate(['your','shots','your','story'])]}
    monkeypatch.setattr(timing,'analyze',lambda *args:report)
    h=Host(tmp_path)
    result=prepare(tmp_path,h,'demo',{'base':'r1','recipe':{'timing':{'mode':'speech','source':'assets/voice.wav'},'scenes':[{'words':['YOUR SHOTS']},{'words':['YOUR STORY']}]}})
    texts=[op['clip'] for op in result['ops'] if op.get('clip',{}).get('attrs',{}).get('data-skye-motion')=='text']
    assert sorted(c['at'] for c in texts)==pytest.approx([.1,1.3])
    assert result['timing']['words']==report['words']


def test_reviewed_timing_bounds_are_checked(tmp_path,monkeypatch):
    from skye.studio import timing
    (tmp_path/'assets').mkdir();(tmp_path/'assets/voice.wav').touch()
    monkeypatch.setattr(timing,'pcm',lambda path:array.array('h',[0])*64000)
    good=[{'word':'Hello','start':.1,'end':.5},{'word':'world','start':.8,'end':1.2}]
    out=timing.reviewed(tmp_path,'assets/voice.wav',good)
    assert out['words'][0]['reviewed'] and out['words'][0]['word']=='hello'
    for words in [[{'word':'x','start':3,'end':5}],list(reversed(good)),[{'word':'x','start':float('nan'),'end':1}]]:
        with pytest.raises(MediaError):timing.reviewed(tmp_path,'assets/voice.wav',words)


def test_native_media_text_style_validates(tmp_path):
    import shutil,subprocess
    if not shutil.which('ffmpeg'):pytest.skip('ffmpeg not installed')
    (tmp_path/'assets').mkdir();media=tmp_path/'assets/shot.mp4'
    subprocess.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','color=black:s=32x32:d=2','-pix_fmt','yuv420p',str(media)],check=True)
    h=Host(tmp_path)
    r=prepare(tmp_path,h,'demo',{'base':'r1','recipe':{'scenes':[{'words':['HELLO'],'source':'assets/shot.mp4'}]}})
    text=next(o['clip'] for o in r['ops'] if o.get('clip',{}).get('attrs',{}).get('data-skye-motion')=='text')
    assert 'textShadow' in text['overrides'][0]['style']
