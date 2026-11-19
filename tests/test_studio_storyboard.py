import copy,json
from pathlib import Path
import pytest
from skye.studio.storyboard import describe,save_note,plan_change
from skye.media.model import MediaError

FILM={'stage':{'w':640,'h':360},'tracks':[{'clips':[{'id':'one','src':'assets/a.mp4','time':[0,3]},{'id':'two','src':'assets/a.mp4','at':3,'time':[3,5]}]}]}
class Studio:
    def __init__(self,root):self.film=copy.deepcopy(FILM);self.root=root;self.pages=[]
    def request(self,route,body=None,method=None):
        if route.endswith('/page'):self.pages.append(body);return {}
        return {'value':self.film,'rev':'r1','project':{'path':str(self.root)}}


def test_storyboard_current_timing_notes(tmp_path):
    studio=Studio(tmp_path);save_note(tmp_path,studio,'demo','one','Opening action')
    board=describe(tmp_path,'demo',studio.request(''))
    assert len(board['shots'])==2
    assert board['shots'][0]['note']=='Opening action'
    assert board['shots'][1]['duration']==2


def test_insert_placeholder_ripples_and_validates(tmp_path):
    studio=Studio(tmp_path)
    result=plan_change(tmp_path,studio,'demo',{'base':'r1','action':'insert','index':1,'duration':2,'note':'new beat'})
    assert result['ops'][0]=={'op':'move','clip':'two','track':0,'at':5.0}
    assert result['ops'][-1]['clip']['attrs']['data-skye-shot']=='placeholder'
    assert studio.pages


def test_reorder_maps_each_shot(tmp_path):
    studio=Studio(tmp_path)
    result=plan_change(tmp_path,studio,'demo',{'base':'r1','action':'reorder','order':['two','one']})
    assert result['ops']==[{'op':'move','clip':'one','at':2},{'op':'move','clip':'two','at':0}]


def test_delete_ripples(tmp_path):
    studio=Studio(tmp_path)
    result=plan_change(tmp_path,studio,'demo',{'base':'r1','action':'delete','clip':'one'})
    assert result['ops']==[{'op':'remove','clip':'one'},{'op':'move','clip':'two','at':0}]


@pytest.mark.parametrize('action',[{'action':'insert','index':0},{'action':'reorder','order':['two','one']},{'action':'delete','clip':'one'}])
def test_locked_protected(tmp_path,action):
    studio=Studio(tmp_path);studio.film['tracks'][0]['locked']=True
    with pytest.raises(MediaError,match='locked'):plan_change(tmp_path,studio,'demo',{'base':'r1',**action})


def test_stale_and_spanning_refused(tmp_path):
    studio=Studio(tmp_path)
    with pytest.raises(MediaError,match='changed'):plan_change(tmp_path,studio,'demo',{'base':'old','action':'delete','clip':'one'})
    studio.film['tracks'].append({'clips':[{'id':'music','src':'assets/bed.mp3','time':[0,5]}]})
    with pytest.raises(MediaError,match='spans'):plan_change(tmp_path,studio,'demo',{'base':'r1','action':'insert','index':1})


@pytest.mark.parametrize('effect',['flash','film-burn','glitch','whip-out','fade-black','cut'])
def test_transition_native_ops(tmp_path,effect):
    studio=Studio(tmp_path)
    result=plan_change(tmp_path,studio,'demo',{'base':'r1','action':'transition','clip':'one','effect':effect})
    assert result['ops']
    if effect!='cut':assert 'data-skye-transition' in result['ops'][-1]['fields']['attrs']


def test_agent_reads_storyboard_note(tmp_path):
    from skye.studio.agent import propose
    from skye.adapters.base import Result
    studio=Studio(tmp_path)
    save_note(tmp_path,studio,'demo','one','Keep the opening quiet')
    def worker(spec):
        assert 'Keep the opening quiet' in spec['brief']
        Path(spec['repo'],'ops.json').write_text('[{"op":"set","clip":"one","field":"volume","value":0.3}]')
        return Result(ok=True,output='Lowered the shot.',returncode=0)
    assert propose(tmp_path,studio,'demo',['one'],'Lower the volume',runner=worker)['ops'][0]['value']==.3


def test_replace_transition_with_cut_restores_original(tmp_path):
    import subprocess
    studio=Studio(tmp_path)
    first=plan_change(tmp_path,studio,'demo',{'base':'r1','action':'transition','clip':'one','effect':'whip-out'})
    helper=Path(__file__).parents[1]/'skye/studio/check.mjs'
    studio.film=json.loads(subprocess.check_output(['node',str(helper)],input=json.dumps({'film':studio.film,'ops':first['ops']}).encode()))
    second=plan_change(tmp_path,studio,'demo',{'base':'r1','action':'transition','clip':'one','effect':'cut'})
    final=json.loads(subprocess.check_output(['node',str(helper)],input=json.dumps({'film':studio.film,'ops':second['ops']}).encode()))
    clip=final['tracks'][0]['clips'][0]
    assert not clip.get('style') and not clip.get('attrs',{}).get('data-skye-transition')


@pytest.mark.parametrize('action',[{'action':'insert','index':1},{'action':'reorder','order':['two','one']},{'action':'delete','clip':'one'}])
def test_explicit_anchor_policy_preserves_spanning_audio_and_overlay(tmp_path,action):
    studio=Studio(tmp_path)
    studio.film['tracks'] += [{'locked':True,'clips':[{'id':'music','src':'assets/bed.mp3','time':[0,5]}]},{'clips':[{'id':'title','src':'pages/title.html','time':[0,5]}]}]
    result=plan_change(tmp_path,studio,'demo',{'base':'r1','spanning':'anchor',**action})
    assert result['ops']
    assert not any(op.get('clip') in ('music','title') for op in result['ops'] if isinstance(op.get('clip'),str))


def test_import_window_validated_against_real_duration(tmp_path):
    import subprocess
    studio=Studio(tmp_path);(tmp_path/'assets').mkdir()
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=blue:s=32x32:d=6','-c:v','libx264',str(tmp_path/'assets/a.mp4')],check=True)
    args={'base':'r1','action':'insert','index':1,'source':'assets/a.mp4','sourceIn':3,'duration':2}
    result=plan_change(tmp_path,studio,'demo',args)
    assert result['ops'][-1]['clip']['time']==[3,5]
    assert result['ops'][-1]['clip']['style']=='object-fit:cover'
    with pytest.raises(MediaError,match='longer'):plan_change(tmp_path,studio,'demo',{**args,'sourceIn':5})


def test_composed_preview_uses_film_timeline_time(tmp_path,monkeypatch):
    import urllib.parse
    seen=[]
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self):return b'jpeg'
    def open_request(request,timeout):
        seen.append(urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query))
        return Response()
    monkeypatch.setattr('urllib.request.urlopen',open_request)
    studio=Studio(tmp_path);studio.origin='http://localhost:1234';studio.session={'key':'test'}
    result=describe(tmp_path,'demo',studio.request(''),thumbnails=True,studio=studio)
    assert len(result['shots'])==2
    assert [q['path'] for q in seen]==[['film.html'],['film.html']]
    assert [q['ms'] for q in seen]==[['500'],['3500']]
    assert all(s['thumbnail'].startswith('data:image/jpeg;base64,') for s in result['shots'])


def test_project_owned_notes_migrate_and_layout_survives_state_move(tmp_path):
    import shutil
    from skye.studio.storyboard import save_layout,_file
    root=tmp_path/'project';root.mkdir();state=tmp_path/'old-state';studio=Studio(root)
    _file(state,'demo').write_text(json.dumps({'one':'Old note'}))
    assert describe(state,'demo',studio.request(''))['shots'][0]['note']=='Old note'
    save_layout(state,studio,'demo',{'clip':'one','position':{'x':700,'y':550}})
    save_layout(state,studio,'demo',{'viewport':{'x':-50,'y':75,'zoom':.6}})
    save_note(state,studio,'demo','two','Project note')
    assert _file(state,'demo').read_text()==json.dumps({'one':'Old note'})
    copied=tmp_path/'copied';shutil.copytree(root,copied)
    other=Studio(copied);board=describe(tmp_path/'fresh-state','new-id',other.request(''))
    assert [s['note'] for s in board['shots']]==['Old note','Project note']
    assert board['layout']=={'positions':{'one':{'x':700,'y':550}},'viewport':{'x':-50,'y':75,'zoom':.6}}
    with pytest.raises(MediaError):save_layout(state,studio,'demo',{'clip':'one','position':{'x':float('nan'),'y':0}})
    with pytest.raises(MediaError):save_layout(state,studio,'demo',{'viewport':{'x':0,'y':0,'zoom':0}})


def test_edge_preview_is_current_composed_film(tmp_path,monkeypatch):
    import urllib.parse
    from skye.studio.storyboard import edge_preview
    seen=[]
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self):return b'jpeg'
    def fetch(request,timeout):
        seen.append(urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query));return Response()
    monkeypatch.setattr('urllib.request.urlopen',fetch)
    studio=Studio(tmp_path);studio.origin='http://localhost:1234';studio.session={'key':'test'}
    result=edge_preview(tmp_path,studio,'demo','one')
    assert result['effect']=='cut' and result['base']=='r1'
    assert [q['ms'] for q in seen]==[['2880'],['3000'],['3120']]
    assert all(q['path']==['film.html'] for q in seen)
    with pytest.raises(MediaError,match='edge'):edge_preview(tmp_path,studio,'demo','two')
    def failed(*args,**kwargs):raise OSError('missing frame')
    monkeypatch.setattr('urllib.request.urlopen',failed)
    with pytest.raises(MediaError,match='unavailable'):edge_preview(tmp_path,studio,'demo','one')
