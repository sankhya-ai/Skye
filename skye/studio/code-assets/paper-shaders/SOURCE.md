# Paper Shaders

Source: https://github.com/paper-design/shaders

Revision: e7003a2d4e6e932e30b8fa11ec1239e6ea48e16a
Package version: 0.0.82
License: Apache-2.0. Upstream LICENSE and NOTICE retained unchanged.

This is an esbuild ESM bundle of ShaderMount and liquidMetalFragmentShader, tree-shaken and minified without shader algorithm changes. No React or npm runtime dependency. Entry exports src/shader-mount.ts and src/shaders/liquid-metal.ts. Built with esbuild --bundle --minify --format=esm. Skye chrome.html supplies its own layout, palette and uniforms; speed stays zero and frame(t) calls setFrame(t*1000). No upstream images or logos are included.
