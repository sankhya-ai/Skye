// Skye host adapter. This frame never receives the parent's Skye session token.
import type { StudioHost } from './lib/host';
const parentOrigin = (() => { try { return new URL(document.referrer).origin; } catch { return ''; } })();
const send = (data: unknown) => { if (parentOrigin && parent !== window) parent.postMessage(data, parentOrigin); };
let commandListener: ((command: any) => void) | null = null;
(globalThis as typeof globalThis & {openfilmHost: StudioHost}).openfilmHost = {
  version: 1, name: 'Skye Studio',
  onCommand: listener => {commandListener=listener;return () => {commandListener=null;};},
  onSelection: selection => send({type: 'skye:selection', selection}),
  refer: ref => send({type: 'skye:refer', ref}),
  ask: request => send({type: 'skye:ask', request}),
};
window.addEventListener('message', e => {
  if(e.source===parent && e.origin===parentOrigin && e.data?.type==='skye:project') {
    const {action,request,project,files,name,shape}=e.data;
    void (async()=>{
      try {
        const {api}=await import('./api');
        let result:unknown;
        if(action==='new') {
          const p=await api.newProject(String(name||'Untitled').slice(0,100));
          const snap=await api.film(p.id);
          const stage=shape==='landscape'?{w:1920,h:1080}:shape==='square'?{w:1080,h:1080}:{w:1080,h:1920};
          await api.edit(p.id,snap.rev,[{op:'stage',...stage}]);
          const {openProject}=await import('./lib/navigate');openProject(p.id);result=p;
        } else if(action==='open' && typeof project==='string' && /^[\w-]+$/.test(project)) {
          const {openProject}=await import('./lib/navigate');openProject(project);result={};
        } else if(action==='import' && typeof project==='string' && /^[\w-]+$/.test(project) && Array.isArray(files)) {
          const landed=[];
          for(const file of files) {
            if(!(file instanceof File)) throw new Error('Choose files to import');
            const path='assets/'+file.name.replace(/[/\\]/g,'_');
            const response=await fetch('/api/projects/'+project+'/files?path='+encodeURIComponent(path),{method:'PUT',body:file});
            const value=await response.json();if(!response.ok||value.unreadable)throw new Error(value.error||value.unreadable||'Import failed');landed.push(value.path);
          }
          result={paths:landed};
        } else throw new Error('Unsupported project action');
        send({type:'skye:project-result',request,result});
      } catch(error){send({type:'skye:project-result',request,error:error instanceof Error?error.message:String(error)});}
    })();return;
  }
  if (e.source === parent && e.origin === parentOrigin && e.data?.type === 'skye:undo') {const request=e.data.request;window.dispatchEvent(new CustomEvent('skye-undo',{detail:{done:(error:string|null)=>send({type:'skye:applied',request,error})}}));return;}
  if (e.source === parent && e.origin === parentOrigin && e.data?.type === 'skye:select-shot' && typeof e.data.id === 'string') {
    commandListener?.({type:'select-clip',id:e.data.id,seek:true});return;
  }
  if(e.source===parent && e.origin===parentOrigin && e.data?.type==='skye:seek' && Number.isFinite(e.data.time)){commandListener?.({type:'seek',time:Math.max(0,e.data.time)});return;}
  if (e.source !== parent || e.origin !== parentOrigin || e.data?.type !== 'skye:apply') return;
  const {project, base, ops, request} = e.data;
  if (typeof project !== 'string' || typeof base !== 'string' || !Array.isArray(ops)) return;
  window.dispatchEvent(new CustomEvent('skye-apply', {detail: {project, base, ops,
    done: (error: string | null) => send({type: 'skye:applied', request, error})}}));
});
