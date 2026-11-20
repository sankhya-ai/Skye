"""Local audio timing. ffmpeg + stdlib onsets; optional offline Vosk words."""
import array,json,math,os,re,statistics,subprocess,sys
from pathlib import Path
from ..media.model import MediaError

def asset(root,source):
    if not isinstance(source,str) or not source.startswith('assets/') or '..' in Path(source).parts:raise MediaError('Choose imported audio or video')
    p=(root/source).resolve()
    if not p.is_relative_to(root.resolve()) or not p.is_file() or p.suffix.lower() not in {'.wav','.mp3','.m4a','.ogg','.flac','.aac','.mp4','.mov','.webm'}:raise MediaError('Choose imported audio or video')
    return p

def pcm(path):
    probe=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','json',str(path)],capture_output=True,text=True,timeout=20)
    try:duration=float(json.loads(probe.stdout)['format']['duration'])
    except (ValueError,KeyError):raise MediaError('Audio duration unavailable')
    if not math.isfinite(duration) or duration<=0 or duration>180:raise MediaError('Timing analysis supports up to 180 seconds. Trim the source first.')
    r=subprocess.run(['ffmpeg','-v','error','-i',str(path),'-t','180','-vn','-ac','1','-ar','16000','-f','s16le','-'],capture_output=True,timeout=60)
    if r.returncode or not r.stdout:raise MediaError('Could not decode audio')
    a=array.array('h');a.frombytes(r.stdout)
    if sys.byteorder!='little':a.byteswap()
    return a

def beats(samples):
    hop=160;energies=[math.sqrt(sum(v*v for v in samples[i:i+hop])/hop) for i in range(0,len(samples)-hop,hop)]
    if not energies or max(energies)<20:return {'bpm':None,'confidence':0,'onsets':[],'grid':[],'note':'No clear pulse. Use manual BPM.'}
    flux=[max(0,energies[i]-energies[i-1]) for i in range(1,len(energies))];floor=statistics.median(flux);mad=statistics.median(abs(x-floor) for x in flux);threshold=max(floor+3*mad,max(flux)*.1)
    peaks=[]
    for i in range(1,len(flux)-1):
        t=(i+1)*.01
        if flux[i]>threshold and flux[i]>=flux[i-1] and flux[i]>flux[i+1]:
            if peaks and t-peaks[-1][0]<.18:
                if flux[i]>peaks[-1][1]:peaks[-1]=(t,flux[i])
            else:peaks.append((t,flux[i]))
    if len(peaks)<5:return {'bpm':None,'confidence':0,'onsets':[round(t,3) for t,_ in peaks],'grid':[],'note':'Too few onsets. Use manual BPM.'}
    def score(bpm,phase):
        period=60/bpm;hits=sum(w for t,w in peaks if abs((t-phase+period/2)%period-period/2)<.055);expected=max(1,len(samples)/16000/period);count=sum(1 for t,_ in peaks if abs((t-phase+period/2)%period-period/2)<.055)
        return hits/sum(w for _,w in peaks)*min(1,count/expected)
    candidates=[(score(b,p),b,p) for b in range(60,181) for p,_ in peaks[:16]];conf,bpm,phase=max(candidates);period=60/bpm;phase=phase%period
    if conf<.45:return {'bpm':None,'confidence':round(conf,3),'onsets':[round(t,3) for t,_ in peaks],'grid':[],'note':'Pulse is uncertain. Use manual BPM.'}
    return {'bpm':bpm,'confidence':round(conf,3),'onsets':[round(t,3) for t,_ in peaks],'grid':[round(phase+i*period,3) for i in range(math.ceil((len(samples)/16000-phase)/period))],'note':'Estimated pulse, not a downbeat or genre detector. Check half/double tempo.'}

def speech(samples):
    try:from vosk import Model,KaldiRecognizer,SetLogLevel
    except ImportError:raise MediaError('Speech needs local Vosk. Install skye[speech] and set SKYE_VOSK_MODEL. See the timing guide.')
    model=os.environ.get('SKYE_VOSK_MODEL','')
    if not model or not Path(model).is_dir():raise MediaError('Set SKYE_VOSK_MODEL to an unpacked local language model. No cloud fallback.')
    SetLogLevel(-1);recognizer=KaldiRecognizer(Model(model),16000);recognizer.SetWords(True);words=[]
    # Vosk consumes little-endian PCM.
    if sys.byteorder!='little':samples=array.array('h',samples);samples.byteswap()
    data=samples.tobytes()
    for i in range(0,len(data),8000):
        if recognizer.AcceptWaveform(data[i:i+8000]):words.extend(json.loads(recognizer.Result()).get('result',[]))
    words.extend(json.loads(recognizer.FinalResult()).get('result',[]))
    return [{'word':w['word'],'start':round(w['start'],3),'end':round(w['end'],3),'confidence':round(w.get('conf',0),3)} for w in words]

def analyze(root,source,mode):
    if mode not in ('beat','speech'):raise MediaError('Choose beat or speech timing')
    path=asset(Path(root),source)
    samples=pcm(path);result={'mode':mode,'source':source,'duration':round(len(samples)/16000,3),'limit':180}
    if mode=='beat':result.update(beats(samples))
    else:
        words=speech(samples);result.update({'words':words,'transcript':' '.join(w['word'] for w in words),'note':'Machine transcript. Review every word and timestamp. Not forced alignment or stress detection.'})
    return result

def reviewed(root,source,words):
    samples=pcm(asset(Path(root),source));duration=len(samples)/16000
    if not isinstance(words,list) or not 1<=len(words)<=1200:raise MediaError('Review 1..1200 timed words')
    previous=0;out=[]
    for w in words:
        if not isinstance(w,dict) or not isinstance(w.get('word'),str) or not re.fullmatch(r"[\w']{1,40}",w['word']):raise MediaError('Use one word per timing row')
        start,end=w.get('start'),w.get('end')
        if any(type(t) not in (int,float) or not math.isfinite(t) for t in (start,end)) or not previous<=start<end<=duration+.001:raise MediaError('Word timings must be ordered and within the source')
        previous=end;out.append({'word':w['word'].lower(),'start':start,'end':end,'confidence':1,'reviewed':True})
    return {'mode':'speech','source':source,'duration':duration,'words':out,'transcript':' '.join(w['word'] for w in out),'note':'User-reviewed/imported word timings, not unmodified ASR output.'}

def _tokens(s):return re.findall(r"[^\W_]+(?:'[^\W_]+)?",s.lower())

def align(rows,report):
    """Exact monotonic token matches only. Never fabricate missing timestamps."""
    words=report['words'];tokens=[w['word'].lower() for w in words];cursor=0;matches=[]
    for row in rows:
        group=[]
        phrases=row['words'].split('|') if isinstance(row['words'],str) else row['words']
        for phrase in phrases:
            wanted=_tokens(phrase)
            if not wanted:raise MediaError('Speech phrases need words, not only punctuation')
            found=next((i for i in range(cursor,len(tokens)-len(wanted)+1) if tokens[i:i+len(wanted)]==wanted),None)
            if found is None:raise MediaError('Speech phrase not found: '+phrase+'. Review the transcript or use manual timing.')
            matched=words[found:found+len(wanted)]
            if min(w['confidence'] for w in matched)<.65:raise MediaError('Low-confidence speech phrase: '+phrase+'. Review timing before composing.')
            group.append(matched[0]['start']);cursor=found+len(wanted)
        matches.append(group)
    for i,row in enumerate(rows):
        start=0 if i==0 else max(0,matches[i][0]-.12)
        end=matches[i+1][0]-.12 if i+1<len(rows) else min(report['duration'],words[cursor-1]['end']+.65)
        if end-start>12 or end-start<.8:raise MediaError('Speech scene must be 0.8..12 seconds. Split or combine phrases.')
        row['duration']=end-start;row['_delays']=[t-start for t in matches[i]]
    return rows


def main(argv=None):
    import argparse
    p=argparse.ArgumentParser(description='Analyze imported audio locally. No cloud calls.')
    p.add_argument('project',type=Path);p.add_argument('--source',required=True);p.add_argument('--mode',choices=['beat','speech'],required=True)
    a=p.parse_args(argv);print(json.dumps(analyze(a.project,a.source,a.mode)));return 0

if __name__=='__main__':raise SystemExit(main())
