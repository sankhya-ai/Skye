import copy,json
from pathlib import Path
import pytest
from skye.studio.motion import prepare
from skye.media.model import MediaError
from test_studio_codeshots import Studio

class Host(Studio):
    def request(self,route,body=None,method=None):
        if route.endswith('/page'):return super().request(route,body,method)
        return {'value':self.film,'rev':'r1','project':{'path':str(self.root)}}

def test_compose_separate_editable_layers_and_native_undo(tmp_path):
    h=Host(tmp_path);before=copy.deepcopy(h.film)
    result=prepare(tmp_path,h,'demo',{'base':'r1','recipe':{'bpm':120,'scenes':[{'words':'YOUR|STORY','layout':'arc'},{'words':['NEXT'],'layout':'title'}]}})
    h.apply(result)
    clips=[c for t in h.film['tracks'] for c in t['clips']]
    texts=[c for c in clips if c['attrs']['data-skye-motion']=='text']
    assert len(texts)==3 and result['duration']==4
    assert texts[0]['overrides'][0]['at']=='#words'
    assert texts[0]['size']['w']>0
    assert h.film['keyframes']
    assert all('canvas' not in p.read_text() for p in (tmp_path/'pages').glob('*.html'))
    assert all('window.film' in p.read_text() for p in (tmp_path/'pages').glob('*.html'))
    h.film=before
    assert not h.film['tracks']

@pytest.mark.parametrize('row',[{'words':['x'],'duration':float('nan')},{'words':['x'],'layout':'bad'},{'words':['x'],'source':'../escape.mp4'},{'words':[]},{'words':['x'],'negative':1}])
def test_compose_rejects_before_writing(tmp_path,row):
    h=Host(tmp_path)
    with pytest.raises(MediaError):prepare(tmp_path,h,'demo',{'base':'r1','recipe':{'scenes':[row]}})
    assert not (tmp_path/'pages').exists()

def test_compose_stale_and_overlap_reject(tmp_path):
    h=Host(tmp_path)
    with pytest.raises(MediaError,match='changed'):prepare(tmp_path,h,'demo',{'base':'old','recipe':{'scenes':[{'words':['x']}]}})
    h.film['tracks']=[{'clips':[{'id':'a','src':'assets/a.mp4','time':[0,4]}]}]
    with pytest.raises(MediaError,match='appends'):prepare(tmp_path,h,'demo',{'base':'r1','recipe':{'start':2,'scenes':[{'words':['x']}]}})

def test_words_preserve_style_and_reject_locked_or_stale(tmp_path):
    from skye.studio.motion import text_layers
    h=Host(tmp_path)
    h.apply(prepare(tmp_path,h,'demo',{'base':'r1','recipe':{'scenes':[{'words':['HELLO']}]}}))
    rows=text_layers(h,'demo');cid=rows['rows'][0]['id'];original=copy.deepcopy(h.film)
    h.apply(text_layers(h,'demo',{'base':'r1','edits':{cid:'YOUR WORDS'}}))
    changed=next(c for t in h.film['tracks'] for c in t['clips'] if c['id']==cid)
    assert changed['overrides'][0]['text']=='YOUR WORDS'
    assert changed['overrides'][0]['style']==next(c for t in original['tracks'] for c in t['clips'] if c['id']==cid)['overrides'][0]['style']
    assert text_layers(h,'demo')['rows'][0]['text']=='YOUR WORDS'
    with pytest.raises(MediaError,match='changed'):text_layers(h,'demo',{'base':'stale','edits':{cid:'NO'}})
    next(t for t in h.film['tracks'] if any(c['id']==cid for c in t['clips']))['locked']=True
    with pytest.raises(MediaError,match='locked'):text_layers(h,'demo',{'base':'r1','edits':{cid:'NO'}})

def test_board_groups_motion_layers_into_scenes(tmp_path):
    from skye.studio.storyboard import describe
    h=Host(tmp_path)
    h.apply(prepare(tmp_path,h,'demo',{'base':'r1','recipe':{'scenes':[{'words':['ONE','TWO']},{'words':['THREE']}]}}))
    board=describe(tmp_path,'demo',h.request(''))
    assert len(board['shots'])==2
    assert not any(s['code'] for s in board['shots'])
    assert any('ONE' in s['text'] and 'TWO' in s['text'] for s in board['shots'])

def test_compose_reuses_nonoverlapping_timeline_lanes(tmp_path):
    h=Host(tmp_path)
    h.apply(prepare(tmp_path,h,'demo',{'base':'r1','recipe':{'scenes':[{'words':['ONE','TWO']}] * 7}}))
    assert len(h.film['tracks'])==4  # block overlay, two phrase lanes, background
    assert sum(len(t['clips']) for t in h.film['tracks'])==23
