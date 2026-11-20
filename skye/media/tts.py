"""Optional local voice engines. No remote inference, cloning or paid fallback."""
from __future__ import annotations
import argparse,json,math,os,subprocess,sys,time,uuid,wave
from pathlib import Path
from .model import MediaError

ENGINE='kitten-mini-int4'
MODEL='KittenML/kitten-tts-2-mini'
SDK_REV='5802f6b956844919ecf210f4e55f4750a6ad7c6f'
VOICES={'en':['Eleanor','Martha','Serena','Iris','Grace','Edith','Diana','Otis','Vincent','Max','Danny'],'hi':['Priya','Ananya','Arjun','Vikram']}

def validate(text,voice,language,speed,engine):
    if engine!=ENGINE:raise MediaError('Choose Kitten Mini')
    if language not in VOICES or voice not in VOICES[language]:raise MediaError('Choose a voice for this language')
    if not isinstance(text,str) or not text.strip() or len(text)>1600:raise MediaError('Use 1..1600 characters')
    if type(speed) not in (int,float) or not math.isfinite(speed) or not .7<=speed<=1.3:raise MediaError('Speed must be 0.7..1.3')
    chunks=[s.strip() for line in text.splitlines() for s in line.split('|') if s.strip()]
    if not 1<=len(chunks)<=60 or any(len(s)>240 for s in chunks):raise MediaError('Use up to 60 short phrase groups, 240 characters each')
    return chunks

def _worker(request):
    # Lazy optional imports keep the normal Skye install light. Model/G2P files
    # download on explicit generation only, never when opening the voice picker.
    try:
        from kittenml import KittenTTS
        import numpy as np
        import soundfile as sf
    except ImportError as exc:raise MediaError('Install Skye voice extra. See docs/video/VOICE.md') from exc
    chunks=validate(**{k:request[k] for k in ('text','voice','language','speed','engine')})
    model=KittenTTS(os.environ.get('SKYE_KITTEN_MODEL',MODEL),weights='int4',backend='onnx',device='cpu')
    if getattr(model,'weights',None)!='int4':raise MediaError('Kitten SDK ignored int4. Install the pinned voice extra; no int8 fallback')
    # Bound CPU oversubscription on small machines. This is supported by the
    # pinned Mini backend, not a claim that the language front end needs no torch.
    model.threads=2
    segments=[];audio=[];cursor=0;sr=int(model.sample_rate)
    for i,text in enumerate(chunks):
        wav=np.asarray(model.generate(text,voice=request['voice'],language=request['language'],speed=request['speed']),dtype=np.float32).reshape(-1)
        if not len(wav) or not np.isfinite(wav).all():raise MediaError('Voice produced invalid audio')
        duration=len(wav)/sr
        if duration>30 or (cursor+len(wav))/sr>180:raise MediaError('Voice exceeds 180 seconds. Use shorter text')
        segments.append({'text':text,'start':round(cursor/sr,6),'end':round((cursor+len(wav))/sr,6)})
        audio.append(wav);cursor+=len(wav)
        if i<len(chunks)-1:
            gap=np.zeros(round(.15*sr),dtype=np.float32);audio.append(gap);cursor+=len(gap)
    out=Path(request['output']);sf.write(str(out),np.concatenate(audio),sr,subtype='PCM_16')
    return {'engine':ENGINE,'model':MODEL,'weights':'int4','sampleRate':sr,'duration':round(cursor/sr,6),'voice':request['voice'],'language':request['language'],'script':request['text'],'segments':segments,'timingKind':'synthesis-segments','note':'Exact generated segment bounds, not word alignment or phonetic onsets. Review the sound.','downloadNote':'47.5 MB graph plus voice packs, G2P and Python dependencies. First use downloads files.'}

def generate(text,output,voice='Danny',language='en',speed=1.0,engine=ENGINE):
    validate(text,voice,language,speed,engine)
    output=Path(output).resolve()
    if output.exists():raise MediaError('Voice output already exists')
    output.parent.mkdir(parents=True,exist_ok=True)
    request={'text':text,'voice':voice,'language':language,'speed':speed,'engine':engine,'output':str(output)}
    started=time.monotonic()
    try:
        result=subprocess.run([sys.executable,'-m','skye.media.tts','--worker'],input=json.dumps(request),capture_output=True,text=True,timeout=240)
        if result.returncode:
            raise MediaError('Local voice failed: '+result.stderr[-1800:])
        # Frontend setup can print informational lines. Only the final line is ours.
        report=json.loads(result.stdout.strip().splitlines()[-1]);report['elapsed']=round(time.monotonic()-started,3)
        with wave.open(str(output)) as w:
            if w.getnchannels()!=1 or w.getnframes()<=0 or w.getframerate()!=report['sampleRate']:raise MediaError('Invalid voice WAV')
        output.with_suffix('.voice.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        return report
    except BaseException:
        output.unlink(missing_ok=True);output.with_suffix('.voice.json').unlink(missing_ok=True);raise

def main(argv=None):
    if argv is None and sys.argv[1:]==['--worker']:
        try:print(json.dumps(_worker(json.load(sys.stdin)),ensure_ascii=False));return 0
        except Exception as exc:print(str(exc),file=sys.stderr);return 1
    p=argparse.ArgumentParser(description='Generate local Mini int4 speech. First use downloads model/front-end files.')
    p.add_argument('--text-file',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--engine',choices=[ENGINE],default=ENGINE);p.add_argument('--language',choices=VOICES,default='en');p.add_argument('--voice',default='Danny');p.add_argument('--speed',type=float,default=1)
    a=p.parse_args(argv);print(json.dumps(generate(a.text_file.read_text(),a.output,a.voice,a.language,a.speed,a.engine),ensure_ascii=False));return 0

if __name__=='__main__':raise SystemExit(main())
