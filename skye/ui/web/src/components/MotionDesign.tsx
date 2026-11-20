import {useEffect,useState} from 'react';
import {post} from './video/net';
type Report={decisions:{scene:number;role:string;layout:string}[];rules:{rules:{id:string;rule:string}[]};warnings:{rule:string;note:string}[];metrics:{phrases:number;tilted:number;upright:number;scenes:number;transitions:number}};
export function MotionDesign({project,onClose,onError}:{project:string;onClose:()=>void;onError:(s:string)=>void}){
 const [report,setReport]=useState<Report|null>(null);
 useEffect(()=>{void post<Report>('/api/studio/motion-design',{project}).then(setReport).catch(e=>onError(e.message));},[project]);
 return <div className="studio-board-dialog studio-motion-dialog" role="dialog" aria-label="Design"><strong>Design</strong>{report?<><p>{report.metrics.upright}/{report.metrics.phrases} upright · {report.metrics.transitions} transitions</p><strong>{report.warnings.length?`${report.warnings.length} checks`:'Checks passed'}</strong>{report.warnings.map((w,i)=><small key={i}>{w.note}</small>)}<details><summary>Choices</summary>{report.decisions.map(d=><small style={{display:'block'}} key={d.scene}>{d.scene}. {d.role} · {d.layout}</small>)}</details><details><summary>Guide</summary>{report.rules.rules.map(r=><p key={r.id} style={{fontSize:13}}>{r.rule}</p>)}</details><small>Rules guide craft. They do not grade beauty.</small></>:<p>Checking...</p>}<button onClick={onClose}>Close</button></div>;
}
