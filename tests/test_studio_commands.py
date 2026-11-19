import json
from pathlib import Path
import pytest
from skye.studio.commands import parse_command, local_propose
from skye.media.model import MediaError
from skye import agents, roles, gateway

@pytest.mark.parametrize('text,plan', [('caption "hello world"', {'effect':'caption-pop','text':'hello world'}), ('punch',{'effect':'punch'}), ('volume 35%',{'volume':.35}), ('mute',{'volume':0})])
def test_parse(text,plan):
    assert parse_command(text)==plan

@pytest.mark.parametrize('text',['delete all files','volume 150%','do whatever','caption hello', ''])
def test_no_guess(text):
    with pytest.raises(MediaError): parse_command(text)

def test_video_team_role():
    assert 'video-editor' in roles.known()
    assert agents.definition_for('video-editor')['instructions']
    assert 'Bash' in gateway.resolve('video-editor')['tools']

FILM='''<!doctype html><html><head><style>body {width:640px;height:360px}</style></head><body><section><video id="v1" src="assets/a.mp4#t=0,4" at="0" volume="0"></video></section></body></html>'''
class Studio:
    def __init__(self):
        import subprocess
        helper=Path(__file__).parents[1]/'skye/studio/vendor/openfilm/src/film-doc.mjs'
        code="import {readFilmFile} from "+json.dumps(helper.as_uri())+"; console.log(JSON.stringify(readFilmFile("+json.dumps(FILM)+").value))"
        self.film=json.loads(subprocess.check_output(['node','--input-type=module','-e',code]))
        self.pages=[]
    def request(self,route,body=None,method=None):
        if route.endswith('/page'):
            self.pages.append(body); return {}
        return {'value':self.film,'text':FILM,'rev':'base'}

@pytest.mark.parametrize('text',['caption "Hello"', 'punch','shake','glitch','mirror','grain'])
def test_effect_becomes_native_ops(tmp_path,text):
    studio=Studio()
    result=local_propose(tmp_path,studio,'demo',['v1'],text)
    assert result['base']=='base' and result['ops']
    if 'caption' in text: assert studio.pages

def test_local_locked(tmp_path):
    studio=Studio(); studio.film['tracks'][0]['locked']=True
    with pytest.raises(MediaError,match='locked'): local_propose(tmp_path,studio,'demo',['v1'],'punch')


def test_extended_grammar():
    assert parse_command('track "PLANTED"') == {'effect':'track-text','text':'PLANTED'}
    assert parse_command('reference "assets/ref.mp4" text "New words"') == {'reference':'assets/ref.mp4','text':'New words'}

def test_reference_path_cannot_escape(tmp_path):
    studio=Studio(); studio.project_root=tmp_path
    original=studio.request
    def request(route,body=None,method=None):
        result=original(route,body,method)
        if result: result['project']={'path':str(tmp_path)}
        return result
    studio.request=request
    with pytest.raises(MediaError,match='local asset'):
        local_propose(tmp_path,studio,'demo',['v1'],'reference "../outside.mp4"')


def test_recipe_tracks_selected_clip_after_new_sections(tmp_path):
    from skye.studio.edit import apply_recipe, read_shots
    (tmp_path/'film.html').write_text(FILM)
    apply_recipe(tmp_path,1,[{'effect':'flash','offset':1,'dur':.12},{'effect':'caption-pop','text':'one|two','offset':0}],track=1)
    assert len(list((tmp_path/'pages').glob('*.html')))==2
    shots=read_shots(tmp_path)
    assert len(shots)==1 and shots[0].clip_id=='v1'


def test_caption_then_other_shot_punch_keeps_zero_start_and_unselected(tmp_path):
    """Real multi-shot command sequence: absent at=0 must not trip the guard."""
    import subprocess, copy
    helper=Path(__file__).parents[1]/'skye/studio/vendor/openfilm/src/film-doc.mjs'
    ops_helper=Path(__file__).parents[1]/'skye/studio/check.mjs'
    class MutableStudio:
        def __init__(self):
            self.film={'stage':{'w':640,'h':360},'tracks':[{'clips':[{'id':'v1','src':'assets/a.mp4','time':[0,2]}, {'id':'v2','src':'assets/a.mp4','time':[2,4],'at':2}]}]}
        def request(self,route,body=None,method=None):
            if route.endswith('/page'): return {}
            code="import {filmHtml} from "+json.dumps(helper.as_uri())+";let s='';for await(const c of process.stdin)s+=c;process.stdout.write(filmHtml(JSON.parse(s)));"
            text=subprocess.check_output(['node','--input-type=module','-e',code],input=json.dumps(self.film).encode()).decode()
            return {'value':self.film,'text':text,'rev':'base','project':{'path':str(tmp_path)}}
        def apply(self,result):
            self.film=json.loads(subprocess.check_output(['node',str(ops_helper)],input=json.dumps({'film':self.film,'ops':result['ops']}).encode()))
    studio=MutableStudio();studio.apply(local_propose(tmp_path,studio,'demo',['v1'],'caption "Hello"'))
    before=copy.deepcopy(studio.film)
    studio.apply(local_propose(tmp_path,studio,'demo',['v2'],'punch'))
    old={c['id']:c for t in before['tracks'] for c in t['clips']}
    new={c['id']:c for t in studio.film['tracks'] for c in t['clips']}
    assert new['v2']!=old['v2']
    assert {k:v for k,v in new.items() if k!='v2'}=={k:v for k,v in old.items() if k!='v2'}


def test_effect_diff_still_rejects_unselected_move():
    import subprocess
    helper=Path(__file__).parents[1]/'skye/studio/effect-diff.mjs'
    before={'stage':{'w':640,'h':360},'tracks':[{'clips':[{'id':'a','src':'a.mp4','time':[0,2]}, {'id':'b','src':'a.mp4','time':[0,2],'at':2}]}]}
    text='<html><head><meta name="viewport" content="width=640,height=360"></head><body><section><video id="a" src="a.mp4#t=0,2" at="1"></video><video id="b" src="a.mp4#t=0,2" at="2"></video></section></body></html>'
    result=subprocess.run(['node',str(helper)],input=json.dumps({'before':before,'text':text,'selected':'b'}),capture_output=True,text=True)
    assert result.returncode and 'unselected clip' in result.stderr
