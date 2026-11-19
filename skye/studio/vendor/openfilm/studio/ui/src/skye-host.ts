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
  if (e.source === parent && e.origin === parentOrigin && e.data?.type === 'skye:undo') {window.dispatchEvent(new CustomEvent('skye-undo'));return;}
  if (e.source === parent && e.origin === parentOrigin && e.data?.type === 'skye:select-shot' && typeof e.data.id === 'string') {
    commandListener?.({type:'select-clip',id:e.data.id,seek:true});return;
  }
  if (e.source !== parent || e.origin !== parentOrigin || e.data?.type !== 'skye:apply') return;
  const {project, base, ops, request} = e.data;
  if (typeof project !== 'string' || typeof base !== 'string' || !Array.isArray(ops)) return;
  window.dispatchEvent(new CustomEvent('skye-apply', {detail: {project, base, ops,
    done: (error: string | null) => send({type: 'skye:applied', request, error})}}));
});
