// Skye host adapter. This frame never receives the parent's Skye session token.
import type { StudioHost } from './lib/host';
const parentOrigin = (() => { try { return new URL(document.referrer).origin; } catch { return ''; } })();
const send = (data: unknown) => { if (parentOrigin && parent !== window) parent.postMessage(data, parentOrigin); };
(globalThis as typeof globalThis & {openfilmHost: StudioHost}).openfilmHost = {
  version: 1, name: 'Skye Studio',
  onSelection: selection => send({type: 'skye:selection', selection}),
  refer: ref => send({type: 'skye:refer', ref}),
  ask: request => send({type: 'skye:ask', request}),
};
window.addEventListener('message', e => {
  if (e.source !== parent || e.origin !== parentOrigin || e.data?.type !== 'skye:apply') return;
  const {project, base, ops, request} = e.data;
  if (typeof project !== 'string' || typeof base !== 'string' || !Array.isArray(ops)) return;
  window.dispatchEvent(new CustomEvent('skye-apply', {detail: {project, base, ops,
    done: (error: string | null) => send({type: 'skye:applied', request, error})}}));
});
