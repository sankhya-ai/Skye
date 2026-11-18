// Original Skye implementation of the page-per-shot frame(t) convention.
// One file, two hosts: the headless renderer (final pixels) and the editor's sandboxed live preview.
// It is inserted before shot code, so the clock is already frozen when that code runs.
(() => {
  const NativeDate = Date;
  window.__skyeTime = 0;
  window.Date = class extends NativeDate {
    constructor(...args) { super(...(args.length ? args : [window.__skyeTime * 1000])); }
    static now() { return window.__skyeTime * 1000; }
  };
  performance.now = () => window.__skyeTime * 1000;
  Math.random = () => 0.5;
  window.requestAnimationFrame = () => 0;
  window.setInterval = () => 0;
  window.setTimeout = () => 0;
  let canvas = null, captionNode = null;
  const api = {
    // composite: the footage canvas #source exists below the shot's graphics.
    setup({width, height, designWidth, designHeight, composite}) {
      document.body.style.cssText = `width:${designWidth}px;height:${designHeight}px;transform-origin:0 0;transform:scale(${width / designWidth})`;
      if (composite) {
        canvas = document.createElement('canvas');
        canvas.id = 'source'; canvas.width = width; canvas.height = height;
        canvas.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;z-index:-1';
        document.body.prepend(canvas);
      }
      captionNode = document.createElement('div');
      captionNode.id = 'skye-caption';
      captionNode.style.cssText = 'position:absolute;left:8%;right:8%;bottom:8%;text-align:center;color:white;font:600 28px system-ui;white-space:pre-wrap;text-shadow:0 2px 5px black;background:#0008;border-radius:8px;padding:8px;z-index:2147483647';
      document.body.append(captionNode);
      if (typeof window.frame !== 'function') throw new Error('Define window.frame(t) in visual.html');
    },
    drawRgba(base64, width, height) {
      const bytes = Uint8ClampedArray.from(atob(base64), c => c.charCodeAt(0));
      canvas.getContext('2d').putImageData(new ImageData(bytes, width, height), 0, 0);
    },
    drawImage(image) {
      const ctx = canvas.getContext('2d');
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
    },
    async frame(t, caption) {
      window.__skyeTime = t;
      await window.frame(t);
      captionNode.textContent = caption;
      captionNode.style.display = caption ? '' : 'none';
    },
  };
  window.__skye = api;
  // Live preview host: messages in, drawn frames and errors out. No data besides frames and text crosses.
  api.listen = (post) => {
    window.addEventListener('error', e => post({type: 'error', message: String(e.message || e)}));
    window.addEventListener('message', async e => {
      const m = e.data || {};
      try {
        if (m.type === 'setup') api.setup(m.options);
        if (m.type === 'frame') {
          if (m.image && canvas) api.drawImage(m.image);
          await api.frame(m.t, m.caption || '');
          post({type: 'drawn', id: m.id});
        }
      } catch (error) { post({type: 'error', message: String(error && error.message || error)}); }
    });
    post({type: 'ready'});
  };
})();
