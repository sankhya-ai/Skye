// Skye's local editor process. Only loopback services are reachable via fetch.
// Provider/generation/update routes are additionally denied by Studio localOnly.
import {mkdirSync} from 'node:fs';
import path from 'node:path';
const home = path.resolve(process.argv[2]);
mkdirSync(home, {recursive: true, mode: 0o700});
process.env.OPENFILM_HOME = path.join(home, 'settings');
process.env.OPENFILM_SHARED_HOME = process.env.OPENFILM_HOME;
process.env.OPENFILM_LIBRARY = path.join(home, 'projects');
process.env.OPENFILM_SAVED = path.join(home, 'saved');
process.env.OPENFILM_EXPORTS = path.join(home, 'exports');
process.env.OPENFILM_NO_UPDATE_CHECK = '1';
const localFetch = globalThis.fetch;
globalThis.fetch = (input, init) => {
  const url = new URL(typeof input === 'string' || input instanceof URL ? input : input.url);
  if (!['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)) return Promise.reject(new Error('Remote services are disabled in Skye Studio'));
  return localFetch(input, init);
};
const {startStudio} = await import('./vendor/openfilm/studio/server/server.mjs');
const {closeBrowsers} = await import('./vendor/openfilm/src/host.mjs');
const {studioKey} = await import('./vendor/openfilm/studio/server/projects.mjs');
const studio = await startStudio({port: 0, key: studioKey(), localOnly: true, stays: true, advertise: false});
process.stdout.write(JSON.stringify({origin: studio.origin, url: `${studio.origin}/?key=${studio.key}`, key: studio.key}) + '\n');
let closing = false;
async function stop() { if (closing) return; closing = true; await studio.close(); await closeBrowsers(); process.exit(0); }
process.on('SIGTERM', stop); process.on('SIGINT', stop);
// The parent keeps stdin open. A crashed/closed Skye must not leave an editor behind.
process.stdin.resume(); process.stdin.on('end', stop);
