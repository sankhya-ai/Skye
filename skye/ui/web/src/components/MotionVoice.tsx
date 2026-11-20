import {useEffect,useRef,useState} from 'react';
import {api} from '../api';
import {post} from './video/net';
export type VoiceResult={source:string;folder:string;report:{duration:number;segments:{text:string;start:number;end:number}[];note:string}};
export function MotionVoice({project,script,onReady,onError}:{project:string;script:string;onReady:(r:VoiceResult)=>void;onError:(s:string)=>void}){
 const [voices,setVoices]=useState<Record<string,string[]>>({}),[lang,setLang]=useState('en'),[voice,setVoice]=useState('Danny'),[busy,setBusy]=useState(false),[result,setResult]=useState<VoiceResult|null>(null);
 const live=useRef(true),timer=useRef<ReturnType<typeof setTimeout>>();
 useEffect(()=>{live.current=true;void post<{voices:Record<string,string[]>}>('/api/studio/voice-list',{}).then(r=>{if(live.current)setVoices(r.voices);}).catch(e=>onError(e.message));return()=>{live.current=false;clearTimeout(timer.current);};},[project]);
 const generate=async()=>{setBusy(true);setResult(null);try{
   const job=await post<{id:string}>('/api/studio/voice-generate',{project,text:script,voice,language:lang,engine:'kitten-mini-int4'});const end=Date.now()+300000;
   const poll=async()=>{if(!live.current)return;try{const j=await api<{status:string;error?:string;result?:VoiceResult}>('/api/media/jobs/'+job.id);if(j.status==='done'&&j.result){setResult(j.result);onReady(j.result);setBusy(false);}else if(j.status==='failed')throw Error(j.error||'Voice failed');else if(Date.now()>end)throw Error('Voice is still running. Reopen after it finishes.');else timer.current=setTimeout(()=>void poll(),800);}catch(e){setBusy(false);onError(e instanceof Error?e.message:String(e));}};void poll();
 }catch(e){setBusy(false);onError(e instanceof Error?e.message:String(e));}};
 return <details><summary>Voice</summary><div style={{display:'grid',gap:8,paddingTop:8}}><label>Language<select aria-label="Voice language" value={lang} disabled={busy} onChange={e=>{setLang(e.target.value);setVoice(voices[e.target.value]?.[0]||'');}}><option value="en">English</option><option value="hi">Hindi</option></select></label><label>Voice<select aria-label="Voice name" value={voice} disabled={busy} onChange={e=>setVoice(e.target.value)}>{(voices[lang]??[]).map(v=><option key={v}>{v}</option>)}</select></label><small>Mini int4 · CPU · First use downloads files</small><button disabled={busy||!voice||!script.trim()} onClick={()=>void generate()}>{busy?'Generating...':'Generate'}</button>{result&&<><audio controls style={{width:'100%'}} src={result.folder+result.source.split('/').map(encodeURIComponent).join('/')}/><small>{result.report.duration.toFixed(1)}s · Phrase sync ready</small></>}</div></details>;
}
