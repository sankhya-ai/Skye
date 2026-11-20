import json
from pathlib import Path
import pytest
from PIL import Image
from skye.studio.mask_editor import read,change
from skye.media.model import MediaError

class Studio:
    def __init__(self,root):
        self.root=root;self.rev='r1';self.track={'clips':[{'id':'mask','src':'a.mp4','time':[0,1],'matte':'assets/mask/manifest.json'}]}
    def request(self,*args):return {'rev':self.rev,'project':{'path':str(self.root)},'value':{'tracks':[self.track]}}

@pytest.fixture
def masks(tmp_path):
    folder=tmp_path/'assets/mask';folder.mkdir(parents=True)
    image=Image.new('RGBA',(30,20),'white');alpha=Image.new('L',(30,20),0)
    for x in range(10,20):
        for y in range(5,15):alpha.putpixel((x,y),255)
    image.putalpha(alpha);image.save(folder/'a.png');(folder/'manifest.json').write_text(json.dumps({'fps':1,'start':0,'frames':['a.png']}))
    return Studio(tmp_path)

def test_edge_correction_is_new_version_and_native_operation(masks):
    original=(masks.root/'assets/mask/a.png').read_bytes();before=masks.track['clips'][0].copy()
    result=change(masks,'demo',{'base':'r1','clip':'mask','grow':2,'feather':0})
    path=masks.root/result['ops'][0]['fields']['matte'];data=json.loads(path.read_text());alpha=Image.open(path.parent/data['frames'][0]).getchannel('A')
    assert alpha.getpixel((8,5))==255 and alpha.getpixel((7,5))==0
    assert (masks.root/'assets/mask/a.png').read_bytes()==original
    assert masks.track['clips'][0]==before
    assert read(masks,'demo')['rows'][0]['preview'].startswith('data:image/png;base64,')
    import subprocess
    helper=Path(__file__).parents[1]/'skye/studio/check.mjs'
    film={'stage':{'w':30,'h':20},'tracks':[masks.track]}
    updated=json.loads(subprocess.check_output(['node',str(helper)],input=json.dumps({'film':film,'ops':result['ops']}).encode()))
    assert updated['tracks'][0]['clips'][0]['matte']==str(path.relative_to(masks.root))

@pytest.mark.parametrize('options',[{'grow':1.5},{'grow':True},{'grow':21},{'feather':float('nan')},{'feather':-1},{'grow':0,'feather':0}])
def test_invalid_edges_no_files(masks,options):
    with pytest.raises(MediaError):change(masks,'demo',{'base':'r1','clip':'mask',**options})
    assert len(list((masks.root/'assets').iterdir()))==1

def test_locked_stale_unsafe_and_failed_frame(masks):
    masks.track['locked']=True
    with pytest.raises(MediaError,match='locked'):change(masks,'demo',{'base':'r1','clip':'mask','grow':1})
    masks.track['locked']=False
    with pytest.raises(MediaError,match='changed'):change(masks,'demo',{'base':'old','clip':'mask','grow':1})
    path=masks.root/'assets/mask/manifest.json';path.write_text(json.dumps({'fps':1,'start':0,'frames':['../../../escape.png']}))
    with pytest.raises(MediaError,match='relative'):read(masks,'demo')
    path.write_text(json.dumps({'fps':1,'start':0,'frames':['a.png']}));(path.parent/'a.png').write_bytes(b'bad png')
    with pytest.raises(Exception):change(masks,'demo',{'base':'r1','clip':'mask','grow':1})
    assert len(list((masks.root/'assets').iterdir()))==1
