import json,math,subprocess,sys,wave
from pathlib import Path
import pytest
from skye.media.model import MediaError
from skye.media.tts import ENGINE,validate,generate
from skye.studio.timing import align,analyze

@pytest.mark.parametrize('kw',[{'engine':'cloud'},{'text':''},{'language':'hi','voice':'Danny'},{'speed':float('nan')},{'speed':2},{'text':'x'*1601},{'text':'x'*241}])
def test_voice_input_rejects_before_launch(tmp_path,kw,monkeypatch):
    args={'text':'hello','output':tmp_path/'out.wav','voice':'Danny','language':'en','speed':1,'engine':ENGINE};args.update(kw)
    monkeypatch.setattr(subprocess,'run',lambda *a,**k:pytest.fail('must not launch'))
    with pytest.raises(MediaError):generate(**args)
    assert not (tmp_path/'out.wav').exists()

def test_validate_preserves_unicode_groups():
    assert validate('अपनी कहानी|अपनी आवाज़।\nनया सफ़र।','Arjun','hi',1,ENGINE)==['अपनी कहानी','अपनी आवाज़।','नया सफ़र।']

def test_generate_runs_worker_and_saves_provenance(tmp_path,monkeypatch):
    def run(args,**kw):
        r=json.loads(kw['input']);assert args==[sys.executable,'-m','skye.media.tts','--worker']
        with wave.open(r['output'],'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(24000);w.writeframes(b'\0\0'*24000)
        return subprocess.CompletedProcess(args,0,'info\n'+json.dumps({'sampleRate':24000,'timingKind':'synthesis-segments','segments':[{'text':'hello','start':0,'end':1}]}),'')
    monkeypatch.setattr(subprocess,'run',run)
    r=generate('hello',tmp_path/'out.wav');assert r['elapsed']>=0
    assert json.loads((tmp_path/'out.voice.json').read_text())['timingKind']=='synthesis-segments'
    with pytest.raises(MediaError,match='exists'):generate('hello',tmp_path/'out.wav')

def test_failed_voice_cleans_output(tmp_path,monkeypatch):
    def run(args,**kw):
        Path(json.loads(kw['input'])['output']).write_bytes(b'partial')
        return subprocess.CompletedProcess(args,1,'','missing optional package')
    monkeypatch.setattr(subprocess,'run',run)
    with pytest.raises(MediaError,match='Local voice failed'):generate('hello',tmp_path/'out.wav')
    assert not (tmp_path/'out.wav').exists()

def test_generated_phrase_alignment_not_asr_words(tmp_path):
    root=tmp_path;(root/'assets').mkdir();p=root/'assets/v.wav'
    with wave.open(str(p),'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(24000);w.writeframes(b'\0\0'*96000)
    p.with_suffix('.voice.json').write_text(json.dumps({'timingKind':'synthesis-segments','segments':[{'text':'YOUR SHOTS.','start':0,'end':1.2},{'text':'YOUR STORY.','start':1.35,'end':2.5},{'text':'MAKE THE CUT.','start':2.65,'end':4}]}))
    r=analyze(root,'assets/v.wav','voice');out=align([{'words':['YOUR SHOTS.','YOUR STORY.']},{'words':['MAKE THE CUT.']}],r)
    assert out[0]['_delays']==[0,1.35] and out[1]['_delays']==pytest.approx([.12])
    assert sum(x['duration'] for x in out)==pytest.approx(4)
    assert 'words' not in r
    with pytest.raises(MediaError,match='must match'):align([{'words':['DIFFERENT']}],r)
    with pytest.raises(MediaError,match='every'):align([{'words':['YOUR SHOTS.']}],r)

def test_pinned_sdk_rejects_int8(monkeypatch,tmp_path):
    import types
    from skye.media import tts
    monkeypatch.setenv('SKYE_KITTEN_MODEL',str(tmp_path))
    class Model:
        weights='int8'
        def __init__(self,*a,**kw):assert kw['weights']=='int4' and kw['device']=='cpu'
    monkeypatch.setitem(sys.modules,'kittenml',types.SimpleNamespace(KittenTTS=Model))
    monkeypatch.setitem(sys.modules,'soundfile',types.SimpleNamespace())
    with pytest.raises(MediaError,match='ignored int4'):tts._worker({'text':'hello','voice':'Danny','language':'en','speed':1,'engine':ENGINE,'output':str(tmp_path/'x.wav')})


def test_compose_generated_voice_is_native_audio_and_appends(tmp_path):
    from skye.studio.motion import prepare
    from test_studio_motion import Host
    h=Host(tmp_path);(tmp_path/'assets').mkdir();p=tmp_path/'assets/v.wav'
    with wave.open(str(p),'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(24000);w.writeframes(b'\0\0'*96000)
    p.with_suffix('.voice.json').write_text(json.dumps({'timingKind':'synthesis-segments','segments':[{'text':'YOUR SHOTS','start':0,'end':1.9},{'text':'YOUR STORY','start':2.05,'end':4}]}))
    r=prepare(tmp_path,h,'demo',{'base':'r1','recipe':{'timing':{'mode':'voice','source':'assets/v.wav'},'scenes':[{'words':['YOUR SHOTS']},{'words':['YOUR STORY']}]}})
    audio=next(o['clip'] for o in r['ops'] if o.get('clip',{}).get('src')=='assets/v.wav')
    assert audio['time']==[0,4] and audio['at']==0 and 'style' not in audio
    h.apply(r)
    r2=prepare(tmp_path,h,'demo',{'base':'r1','recipe':{'timing':{'mode':'voice','source':'assets/v.wav'},'scenes':[{'words':['YOUR SHOTS']},{'words':['YOUR STORY']}]}})
    audio2=next(o['clip'] for o in r2['ops'] if o.get('clip',{}).get('src')=='assets/v.wav')
    assert audio2['at']==4


def test_hindi_phrase_key_keeps_vowel_marks():
    from skye.studio.timing import _phrase_key
    assert _phrase_key('अपनी कहानी।')!=_phrase_key('अपनी कहनी।')
