import{useEffect,useState}from'react';import{post}from'./video/net';
type Row={id:string;text:string;at:number;locked:boolean};
export function MotionWords({project,onClose,onApply,onError}:{project:string;onClose:()=>void;onApply:(r:Record<string,unknown>)=>void;onError:(s:string)=>void}){
 const[base,setBase]=useState(''),[rows,setRows]=useState<Row[]>([]),[edits,setEdits]=useState<Record<string,string>>({}),[busy,setBusy]=useState(false);
 useEffect(()=>{let live=true;void post<{base:string;rows:Row[]}>('/api/studio/motion-text',{project}).then(r=>{if(live){setBase(r.base);setRows(r.rows);}}).catch(e=>onError(e.message));return()=>{live=false;};},[project]);
 const save=async()=>{setBusy(true);try{onApply(await post<Record<string,unknown>>('/api/studio/motion-text-change',{project,base,edits}));onClose();}catch(e){onError(e instanceof Error?e.message:String(e));}finally{setBusy(false);}};
 return <div className="studio-board-dialog studio-motion-dialog" role="dialog" aria-label="Words"><strong>Words</strong><div style={{maxHeight:'60vh',overflowY:'auto'}}>{rows.map((r,i)=><label key={r.id}>{r.at.toFixed(2)}s<input aria-label={`Phrase ${i+1}`} maxLength={70} disabled={r.locked||busy} value={edits[r.id]??r.text} onChange={e=>setEdits({...edits,[r.id]:e.target.value})}/></label>)}</div><button disabled={!base||!Object.keys(edits).length||busy} onClick={()=>void save()}>Save words</button><button disabled={busy} onClick={onClose}>Close words</button></div>;
}
