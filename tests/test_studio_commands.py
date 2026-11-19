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
    assert 'Bash' not in gateway.resolve('video-editor')['tools']

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
