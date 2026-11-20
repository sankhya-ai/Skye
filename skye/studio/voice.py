"""Local voice generation into imported project assets, without timeline writes."""
from pathlib import Path
import uuid
from ..media.tts import ENGINE,VOICES,generate,validate
from .storyboard import _file

def voices():
    return {'engine':ENGINE,'voices':VOICES,'note':'First use downloads model files. Local CPU generation.'}

def create(state,studio,project,body,progress):
    _file(state,project)
    args={'text':body.get('text'),'voice':body.get('voice','Danny'),'language':body.get('language','en'),'speed':body.get('speed',1),'engine':body.get('engine',ENGINE)}
    validate(**args)
    snap=studio.request(f'/api/projects/{project}');root=Path(snap['project']['path']).resolve()
    rel='assets/voice-'+uuid.uuid4().hex[:12]+'.wav';out=root/rel
    progress({'stage':'Local voice'})
    report=generate(output=out,**args)
    return {'source':rel,'report':report,'folder':snap.get('folder',''),'answer':'Voice saved. Segment timing is ready; word alignment is separate.'}
