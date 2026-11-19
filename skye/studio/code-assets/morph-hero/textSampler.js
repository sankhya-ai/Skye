// Copyright (c) 2026 Othmaro Fallas Rojas. MIT, see LICENSE.
function sampleTextToPoints(text, options) {
  const {
    worldWidth,
    step = 3,
    fontSize = 120,
    fontFamily = "system-ui, -apple-system, sans-serif",
    fontWeight = 700,
    alphaThreshold = 128
  } = options;
  if (typeof document === "undefined") {
    throw new Error(
      "sampleTextToPoints requires a browser environment with document.createElement"
    );
  }
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Failed to get 2D canvas context");
  ctx.font = `${fontWeight} ${fontSize}px ${fontFamily}`;
  const metrics = ctx.measureText(text);
  const pad = 40;
  canvas.width = Math.ceil(metrics.width) + pad;
  canvas.height = Math.ceil(fontSize * 1.5);
  ctx.font = `${fontWeight} ${fontSize}px ${fontFamily}`;
  ctx.fillStyle = "#fff";
  ctx.textBaseline = "middle";
  ctx.fillText(text, pad / 2, canvas.height / 2);
  const scale = worldWidth / canvas.width;
  const worldHeight = canvas.height * scale;
  const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
  const pixels = imageData.data;
  const targets = [];
  const halfW = canvas.width / 2;
  const halfH = canvas.height / 2;
  for (let y = 0; y < canvas.height; y += step) {
    for (let x = 0; x < canvas.width; x += step) {
      const idx = (y * canvas.width + x) * 4;
      if (pixels[idx + 3] > alphaThreshold) {
        targets.push((x - halfW) * scale, -(y - halfH) * scale, 0);
      }
    }
  }
  return {
    positions: new Float32Array(targets),
    count: targets.length / 3,
    worldWidth,
    worldHeight
  };
}
export {
  sampleTextToPoints
};
