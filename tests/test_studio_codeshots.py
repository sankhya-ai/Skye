import copy,json,subprocess
from pathlib import Path
import pytest
from skye.studio.storyboard import plan_change,describe
from skye.studio.codeshots import read_code,template,validate_code
from skye.media.model import MediaError

class Studio:
    def __init__(self,root):self.root=root;self.film={'stage':{'w':360,'h':640},'tracks':[]}
    def request(self,route,body=None,method=None):
        if route.endswith('/page'):
            p=self.root/body['path'];p.parent.mkdir(exist_ok=True,parents=True);p.write_text(body['text']);return {}
        return {'value':self.film,'rev':'r1','project':{'path':str(self.root)}}
    def apply(self,result):
        helper=Path(__file__).parents[1]/'skye/studio/check.mjs'
        self.film=json.loads(subprocess.check_output(['node',str(helper)],input=json.dumps({'film':self.film,'ops':result['ops']}).encode()))

@pytest.mark.parametrize('name',['orbit','kinetic','liquid','morph','chrome'])
def test_code_shot_insert_edit_versions_and_native_undo_model(tmp_path,name):
    studio=Studio(tmp_path)
    result=plan_change(tmp_path,studio,'demo',{'base':'r1','action':'insert','index':0,'kind':'code','template':name,'duration':2})
    studio.apply(result);board=describe(tmp_path,'demo',studio.request(''));shot=board['shots'][0]
    assert shot['code'];original=read_code(studio,'demo',shot['id'])['code']
    assert 'window.film' in original
    if name=='orbit':assert (tmp_path/'assets/skye-code/three-0.187.0/three.module.js').exists()
    before=copy.deepcopy(studio.film)
    replacement=original.replace('</body>','<!-- edited --></body>')
    studio.apply(plan_change(tmp_path,studio,'demo',{'base':'r1','action':'code','clip':shot['id'],'code':replacement}))
    assert read_code(studio,'demo',shot['id'])['code']==replacement
    studio.film=before
    assert read_code(studio,'demo',shot['id'])['code']==original


def test_code_templates_cannot_replace_conflicting_user_library(tmp_path):
    dest=tmp_path/'assets/skye-code/three-0.187.0';dest.mkdir(parents=True);(dest/'three.module.js').write_text('existing user asset')
    with pytest.raises(MediaError,match='different'):template(tmp_path,'orbit',2)
    assert (dest/'three.module.js').read_text()=='existing user asset'


def test_code_requires_clock_contract():
    with pytest.raises(MediaError):validate_code('<html>not clock driven</html>')


def test_agent_gets_selected_code_and_cannot_edit_other_code_shot(tmp_path):
    from skye.studio.agent import propose
    from skye.adapters.base import Result
    studio=Studio(tmp_path)
    for i in range(2):
        studio.apply(plan_change(tmp_path,studio,'demo',{'base':'r1','action':'insert','index':i,'kind':'code','template':'kinetic','duration':2}))
    ids=[c['id'] for t in studio.film['tracks'] for c in t['clips']]
    def worker(spec):
        assert 'MAKE' in spec['brief'] and 'Selected code pages' in spec['brief']
        Path(spec['repo'],'ops.json').write_text(json.dumps({'storyboard':{'action':'code','clip':ids[1],'code':'<script>window.film={duration:2,frame(t){}}</script>'}}))
        return Result(ok=True,output='Prepared code.',returncode=0)
    with pytest.raises(MediaError,match='Select the code'):propose(tmp_path,studio,'demo',[ids[0]],'Edit the code shot',runner=worker)
