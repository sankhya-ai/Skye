# Code shots

A code shot is an ordinary editable HTML page clip, tagged `data-skye-shot="code"`
so the storyboard treats it as a shot. Insert chooses Code shot and a template:
Liquid Chrome (Paper WebGL), Liquid Aurora (raw WebGL), Particle Type (Three.js),
3D Orbit or Kinetic type. Source ranking, license checks and exclusions are in
[TEMPLATE-SOURCES.md](TEMPLATE-SOURCES.md). This is a curated starting set, not
a library of product spins or arbitrary 3D asset importers.

The existing native page clock is the scene API:

```js
window.film = {
  duration: 2,
  ready: Promise.resolve(), // optional, wait for local assets
  frame(t) { /* draw the entire scene from time in seconds */ }
};
```

No animation loop, wall-time integration or incrementing state. The same t must
produce the same scene even after seeking backwards. Use deterministic particle
positions. WebGL uses `preserveDrawingBuffer: true`. Sound remains normal clips
in the timeline, not playback inside a page. Templates load entirely local files.
The film origin is separate from the editor origin; it receives no editor API key.
This separation is not a promise that arbitrary third-party HTML is safe.

Select a code card and choose Edit code. Saving writes a new page version and
changes the native clip source through normal revision-checked operations.
Timeline Undo restores the previous page. Old pages can remain unused. Code is
limited to 55 KB per page in this editor; use a local code editor for larger pages.
The contract check is structural, not a proof that arbitrary user code is valid,
deterministic or visually good. Inspect preview and export after editing.

Three.js 0.187.0 is shipped locally under MIT with its LICENSE. Inserting the
3D template copies the versioned module/core files into project assets. A different
existing file at those fixed paths is never overwritten. This avoids CDN/network
requirements and preserves portable project rendering. The library is about 2 MB
uncompressed, not a model/API call. No paid tools are needed.

Verification on the small Linux test host: the native renderer's Chromium used
ANGLE / SwiftShader (software Vulkan), not a hardware GPU. The initial 360x640,
2-second / 30 fps scene rendered in 2.8 seconds with two workers. It is a small
scene benchmark, not a speed guarantee for complex 3D or the user's M1. Native
page screenshots at t=.3, t=1.4, t=.3 had matching first/third SHA-256 hashes and
no page errors. The 3D appearance changed between the two different times.
A separate actual UI flow created a portrait project, inserted the 3D template,
edited its text, used native Undo to restore the original page, and exported it.
Both exported times were visually inspected.

Remaining: more templates, custom asset/model import UX, code diagnostics,
auto-fit across aspect ratios, scrubber inside the code editor, stronger resource
budgets and actual owner-CLI code generation tests. The optional video agent can
propose template insertion or code edits for an explicit code-shot request;
no real Claude/Codex run has yet been verified on this host.

Storyboard edges also expose three composed samples of the **saved film** at the next shot's boundary and ±0.12 seconds. The native film renderer draws all layers, including code scenes and effects. These are still frames, not a playback preview or an unapplied transition preview. The picker starts at the edge's current effect; apply and reopen to inspect the changed edge. A flash can correctly appear as a white sample. Do not infer full transition quality from three samples.
