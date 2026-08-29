import { defineConfig } from 'vitest/config';
import path from 'node:path';

// NG-PH3D P4: minimal test runner, added only for this slice's invariant
// (rack3d.ts cable routing must never cross a solid). No React/component
// tests live here yet — see docs/qa/plant-3d-qa-2026-08-29.md for why this
// repo had zero frontend test tooling before.
export default defineConfig({
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  test: {
    environment: 'happy-dom',
    setupFiles: ['./vitest.setup.ts'],
  },
});
