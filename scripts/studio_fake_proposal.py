"""Deterministic fake for browser equivalence testing; never a model call."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from skye.studio.agent import propose
from skye.studio import Studio
from skye.adapters.base import Result

state, origin, key, project = sys.argv[1:]
# Reuse the live server without creating another one or disclosing a key to the worker.
studio = object.__new__(Studio)
studio.origin = origin
studio.session = {'key': key}
film = studio.request(f'/api/projects/{project}')['value']
selected = [c['id'] for t in film['tracks'] for c in t['clips']]
ops = json.loads(Path(state, 'manual-ops.json').read_text())

def fake(spec):
    assert 'Bash' not in spec['tools']
    Path(spec['repo'], 'ops.json').write_text(json.dumps(ops))
    return Result(ok=True, output='Shortened the linked opening and rippled following clips.', returncode=0)

print(json.dumps(propose(Path(state), studio, project, selected, 'Shorten opening by the measured manual amount', runner=fake)))
