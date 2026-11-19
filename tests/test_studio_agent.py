"""Local editor safety/dispatcher checks. No model calls."""
import copy
import json
from pathlib import Path
import pytest
from skye.studio.agent import propose, validate_ops
from skye.media.model import MediaError
from skye.adapters.base import Result

FILM = {'stage': {'w': 640, 'h': 360}, 'tracks': [
    {'clips': [{'id': 'v1', 'src': 'assets/a.webm', 'time': [0, 4], 'volume': 0, 'attrs': {'data-link': 'first'}},
               {'id': 'v2', 'src': 'assets/b.webm', 'at': 4, 'time': [0, 4]}]},
    {'clips': [{'id': 'a1', 'src': 'assets/a.webm', 'sound': True, 'time': [0, 4], 'attrs': {'data-link': 'first'}}]}]}


def test_valid_operation_uses_copied_core():
    ops = [{'op': 'set', 'clip': 'a1', 'field': 'volume', 'value': 0.5}]
    assert validate_ops(FILM, ops, ['v1', 'a1']) == ops


@pytest.mark.parametrize('ops', [
    [{'op': 'set', 'clip': 'v2', 'field': 'volume', 'value': 0.2}],
    [{'op': 'set', 'clip': 'v1', 'field': 'src', 'value': 'https://example.invalid/v.mp4'}],
    [{'op': 'fields', 'clip': 'v1', 'fields': {'src': '/etc/passwd'}}],
    [{'op': 'track', 'track': 0, 'field': 'locked', 'value': False}],
    [{'op': 'insert', 'clip': {'src': 'assets/c.webm'}}],
    [{'op': 'set', 'clip': 'v1', 'field': 'at', 'value': float('nan')}],
    [{'op': 'set', 'clip': 'v1', 'field': 'overrides', 'value': [{'at': 'body', 'style': {'background': 'url(https://example.invalid)'}}]}],
    [{'op': 'props', 'edits': [{'clip': 'v1', 'prop': 'src', 'value': '/etc/passwd'}]}],
])
def test_reject_unscoped_or_executable_ops(ops):
    with pytest.raises(MediaError):
        validate_ops(FILM, ops, ['v1', 'a1'])


def test_locked_destination():
    film = copy.deepcopy(FILM)
    film['tracks'][1]['locked'] = True
    with pytest.raises(MediaError):
        validate_ops(film, [{'op': 'move', 'clip': 'v1', 'at': 0, 'track': 1}], ['v1'])


class Studio:
    def __init__(self):
        self.rev = 'base123'
    def request(self, route):
        return {'value': FILM, 'rev': self.rev}


def test_real_dispatcher_fake_worker_links_and_scope(tmp_path):
    seen = {}
    def fake(spec):
        seen.update(spec)
        assert 'Bash' not in spec['tools'] and 'Write' in spec['tools']
        folder = Path(spec['repo'])
        (folder / 'ops.json').write_text(json.dumps([{'op': 'set', 'clip': 'a1', 'field': 'volume', 'value': 0.5}]))
        return Result(ok=True, output='Lowered selected audio.', returncode=0)
    result = propose(tmp_path, Studio(), 'abc123', ['v1'], 'Lower the audio', runner=fake)
    assert result['selected'] == ['a1', 'v1']
    assert result['base'] == 'base123'
    assert not any((Path(seen['repo']) / x).exists() for x in ('key', 'source.webm', 'visual.html'))


def test_stale_worker_never_commits(tmp_path):
    studio = Studio()
    def fake(spec):
        Path(spec['repo'], 'ops.json').write_text('[{"op":"set","clip":"a1","field":"volume","value":0.3}]')
        studio.rev = 'changed'
        return Result(ok=True, output='Done', returncode=0)
    with pytest.raises(MediaError, match='Project changed'):
        propose(tmp_path, studio, 'abc123', ['v1'], 'Remove it', runner=fake)


def test_worker_tampering_rejected(tmp_path):
    def fake(spec):
        Path(spec['repo'], 'other.txt').write_text('unexpected')
        return Result(ok=True, output='Done', returncode=0)
    with pytest.raises(MediaError, match='outside ops.json'):
        propose(tmp_path, Studio(), 'abc123', ['v1'], 'Change it', runner=fake)


def test_linked_audio_cannot_be_left_behind():
    with pytest.raises(MediaError, match='out of sync'):
        validate_ops(FILM, [{'op': 'move', 'clip': 'v1', 'at': 1}], ['v1', 'a1'])


def test_existing_overrides_outside_scope_do_not_block_proposals():
    # Found editing a real reel: the editor's own fades on other
    # shots made every proposal fail with "Unsupported manual override".
    film = copy.deepcopy(FILM)
    film['tracks'][0]['clips'][1]['overrides'] = [{'fade': [0.2, 0]}]
    ops = [{'op': 'set', 'clip': 'a1', 'field': 'volume', 'value': 0.5}]
    assert validate_ops(film, ops, ['v1', 'a1']) == ops
    # In-scope clips are still checked, and a fade must be two numbers.
    bad = [{'op': 'set', 'clip': 'v1', 'field': 'overrides', 'value': [{'fade': ['x']}]}]
    with pytest.raises(MediaError):
        validate_ops(film, bad, ['v1', 'a1'])
    ok = [{'op': 'set', 'clip': 'v1', 'field': 'overrides', 'value': [{'fade': [0.2, 0.2]}]},
          {'op': 'set', 'clip': 'a1', 'field': 'overrides', 'value': [{'fade': [0.2, 0.2]}]}]
    assert validate_ops(film, ok, ['v1', 'a1']) == ok


def test_worker_effect_plan_uses_trusted_generator(tmp_path,monkeypatch):
    seen={}
    def prepare(state,studio,project,selected,plan):
        seen.update(plan)
        return {'project':project,'base':'base123','ops':[{'op':'set','clip':'a1','field':'volume','value':.5}]}
    monkeypatch.setattr('skye.studio.commands.effect_proposal',prepare)
    def fake(spec):
        assert spec['role']=='video-editor' and 'Bash' not in spec['tools']
        Path(spec['repo'],'ops.json').write_text('{"effect":"punch"}')
        return Result(ok=True,output='Prepared punch.',returncode=0)
    result=propose(tmp_path,Studio(),'abc123',['v1'],'Punch in',runner=fake)
    assert seen=={'effect':'punch'} and result['base']=='base123'
