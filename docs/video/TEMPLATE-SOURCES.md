# Free code-shot sources, 19 November 2026

Twenty-two source repositories were cloned and inspected, alongside official documentation and license policies. The first usable selection is three locally bundled, time-driven adaptations. This is a curated start, not a stock-video library. No paid models, API calls, hotlinked fonts, remote media, or CDN runtime. Root license badges were checked against source files and asset provenance.

| Rank | Template | Source and license | Why selected | Boundary |
|---|---|---|---|---|
| 1 | Liquid Chrome | [Paper Shaders](https://github.com/paper-design/shaders), Apache-2.0 | Strong chrome material, clean dark editorial composition, 28 KB tree-shaken library; library has a native deterministic setFrame API | Procedural shader shape, not a physically simulated 3D object or imported logo |
| 2 | Liquid Aurora | [CrazyGL / ybouane](https://github.com/CrazyGL-com/hero-liquid-aurora-gradient), Apache-2.0 | Rich full-frame blue/violet domain-warped flow, no model assets or runtime library | Adapted shader, not the whole interactive React hero |
| 3 | Particle Type | [morph-hero / Othmaro Fallas Rojas](https://github.com/othmarodev/morph-hero), MIT | Sphere into sampled text with perspective and a star field, local Three.js | Text and uniforms edited in code; no general model-to-particle importer |

Each source revision and retained license is under `skye/studio/code-assets/`. Adaptations change timing, composition, palette and host. Upstream notices are retained. Paper explicitly permits commercial end products and videos; redistributed code must preserve LICENSE and NOTICE: https://shaders.paper.design/liquid-metal and the repository README.

## Other sources checked

- [Vercel Nights](https://github.com/vercel-labs/nextjs-nights-shader): MIT inspected, attractive fluid/particle work. Deferred because this source is WebGPU-only, without a WebGL fallback. Not proved on the software renderer or M1 8 GB.
- [Three.js gallery](https://github.com/reynoldsktsr/threejs-gallery): MIT inspected. Useful particles/shader demonstrations, less visually distinctive than this selection.
- [Codrops shader animation](https://github.com/biazo/codrops-animate-shaders-with-gsap): MIT inspected. Promising image ripple/reveal/blur transitions. Would replace source imagery with owned assets before bundling.
- [Codrops ASCII OGL](https://github.com/codrops/codrops-ascii-ogl): MIT inspected, secondary procedural-noise dependency needs its own license review before reuse.
- [pmndrs examples](https://github.com/pmndrs/examples/): promising future product/device/lighting scenes, but example assets need individual review. Not adopted in this slice.
- [Poly Haven](https://polyhaven.com/license): actual HDRIs/models/textures are CC0. The website itself and its presentation images are not all CC0. Useful future environment/model source; no asset imported yet.
- [Dioramas](https://github.com/blendi-remade/dioramas): MIT code and explicit CC BY 4.0 generated-asset terms inspected. About 310 MB of models in this clone, too heavy for this first offline bundle. Not adopted; generated assets are not independent provenance verification.
- [LUME](https://github.com/younesshaki/LUME): MIT wrapper inspected, source relies on externally hosted product media; no portable, asset-cleared template adopted.
- [OGL](https://github.com/oframe/ogl): alternative small WebGL library, not needed by selected templates.

## Exclusions and license traps

- [React Bits](https://github.com/davidhdev/react-bits/blob/main/LICENSE.md) is MIT plus Commons Clause, not plain MIT. Current terms restrict component/template redistribution. Do not silently bundle its backgrounds as an Apache-compatible template pack.
- [radiant-particles](https://github.com/DV192/radiant-particles) and [ecommerce-product-showcase](https://github.com/KaranChandekar/ecommerce-product-showcase): no root reuse license found in inspected live clones. Do not copy on the strength of being freely viewable.
- [SILLAGE](https://github.com/shaozheng0503/SILLAGE), [Sneakers](https://github.com/mubin25s/Sneakers), [3D Shoes](https://github.com/Hamiz/3D-Shoes-Ecommerce-Landing-Page), [Luce](https://github.com/FrancescoPierfederici/luce-3d-landing), [Kinetiq](https://github.com/codebybilal18/kinetiq), and [V-Scent](https://github.com/G4EVA-dev/v-scent): no root reuse license found in inspected clones. Visual leads, not licensed dependencies.
- [Dreamy particles](https://github.com/DGFX/codrops-dreamy-particles): no root license found and external model credits need individual clearance.
- [Morph particles](https://github.com/MisterPrada/morph-particles) and [Ghibli shader](https://github.com/craftzdog/ghibli-style-shader): no root license found in inspected clones.
- [liquid-gradient](https://github.com/sotoia/liquid-gradient): root MIT, but its README says the shader came from an unpacked Framer bundle. Hold for provenance, not cleared by a wrapper license.
- [aurora](https://github.com/huangzida/aurora): root MIT, but shader functions match separately attributed nimitz code carrying noncommercial terms in another published copy. Excluded pending provenance clarity, not an accusation or a completed legal ruling. Original shader lead: https://www.shadertoy.com/view/XtGGRt

Codrops publishes an MIT default for downloadable demos unless otherwise stated, but that does not automatically clear third-party images/models/dependencies: https://tympanus.net/codrops/licensing/.

## Proof and limits

Native software-GL test: six seconds, 360x640, 30 fps, two workers, three two-second templates, 8.6 seconds elapsed on this small Linux host. Frames inspected from the real MP4. Each template was sought to .3, 1.4, .3 seconds: first/third screenshot SHA-256 matched, middle differed, no page problems. This is not a complex-3D or M1 performance estimate.

Actual UI test: new portrait project, insert all three through the picker, edit Chrome copy, save, native Undo, reopen and compare full restored source, enter the real editor, start native export. Project uses its local copied assets. A 480x854 UI export exposed resolution-dependent particle dimming. Point size is now scaled with the source page width; the final export is checked again before delivery. Thumbnails of page shots currently render at the thumbnail generator's size, so their text layout is not a reliable portrait-composition preview. Use the editor and final export for composition checks.

Remaining: product/device template with asset-cleared model, user logo/image upload wiring, more template controls, responsive preview/thumbnail sizing, custom-model resource limits, and real owner-CLI generation tests. No claim these are done.
