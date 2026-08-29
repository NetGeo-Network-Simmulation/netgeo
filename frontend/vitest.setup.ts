// NG-PH3D P4: rack3d.ts draws its rail/faceplate/label textures onto a
// `<canvas>` 2D context. happy-dom (like jsdom) has no real canvas backend,
// so `getContext('2d')` normally returns null and every fillRect/fillText
// call in rack3d.ts would throw. The scene GEOMETRY under test never reads
// pixels back — only the texture pass draws them — so a no-op stub context
// is enough to let buildScene() run under a test runner without a real
// browser or a canvas-rendering dependency.
const noop = () => {};
const stubCtx = {
  fillRect: noop,
  strokeRect: noop,
  fillText: noop,
  beginPath: noop,
  arc: noop,
  fill: noop,
  stroke: noop,
  roundRect: noop,
  measureText: () => ({ width: 0 }),
} as unknown as CanvasRenderingContext2D;

if (typeof HTMLCanvasElement !== 'undefined') {
  HTMLCanvasElement.prototype.getContext = (() => stubCtx) as typeof HTMLCanvasElement.prototype.getContext;
}
