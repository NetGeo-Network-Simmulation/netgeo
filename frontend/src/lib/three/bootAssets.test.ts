/**
 * NG-PH3D 3a — proves the headless Blender pipeline is reproducible and
 * dimensionally correct: reads the committed .glb bytes straight off disk
 * (no network, no Blender needed to run this) and checks the parsed
 * bounding box against the verified numbers in
 * docs/design/24-DEVICE-PHYSICAL-SPEC.md §2.a/§2.c.
 */
import { describe, expect, it } from 'vitest';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { loadBootAssets, getBootGeometry } from './bootAssets';

async function nodeFetch(url: string): Promise<ArrayBuffer> {
  const file = path.resolve(__dirname, '../../../public', url.replace(/^\//, ''));
  const buf = await readFile(file);
  return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
}

describe('bootAssets (NG-PH3D 3a pipeline reproducibility)', () => {
  it('loads both committed .glb files and their bounding boxes match the verified spec numbers', async () => {
    await loadBootAssets(nodeFetch);

    const rj45 = getBootGeometry('rj45')!;
    expect(rj45).toBeDefined();
    rj45.computeBoundingBox();
    const rBox = rj45.boundingBox!;
    const rSize = new THREE.Vector3();
    rBox.getSize(rSize);
    // body 11.68 x 8.75 x 16.25mm + boot 12.5mm along Y -> total Y ~28.75mm
    expect(rSize.x).toBeCloseTo(0.01168, 3);
    expect(rSize.y).toBeCloseTo(0.02875, 2);
    expect(rBox.max.y).toBeCloseTo(0, 3); // tip at the local origin

    const lc = getBootGeometry('lc')!;
    expect(lc).toBeDefined();
    lc.computeBoundingBox();
    const lBox = lc.boundingBox!;
    const lSize = new THREE.Vector3();
    lBox.getSize(lSize);
    // body 5.58 x 10.43 x 42mm + boot 8mm along Y -> total Y = 50mm
    expect(lSize.x).toBeCloseTo(0.00558, 3);
    expect(lSize.y).toBeCloseTo(0.05, 3);
    expect(lBox.max.y).toBeCloseTo(0, 3);
  });

  // 3a.2 also builds the SFP and QSFP cage shells (dimension-verified,
  // SFF-8432/SFF-8663) — wired into the live scene as of 3b (see
  // rack3d.cageInstancing.test.ts for the instancing behaviour). This test
  // only proves the raw pipeline output's own dimensions.
  it('cage shells parse and match their verified footprint', async () => {
    const loader = new GLTFLoader();
    const sfpBuf = await nodeFetch('/3d/cage-sfp.glb');
    const sfpGltf = await loader.parseAsync(sfpBuf, '');
    const sfpBox = new THREE.Box3().setFromObject(sfpGltf.scene);
    const sfpSize = new THREE.Vector3();
    sfpBox.getSize(sfpSize);
    // Blender authors length along local Z; the exporter's Y-up conversion
    // (same as the boot assets above) rotates that into glTF/three.js Y, and
    // Blender's Y (cross-section) lands on glTF Z.
    expect(sfpSize.x).toBeCloseTo(0.015, 2); // 14.00mm opening + 2x0.5mm wall
    expect(sfpSize.y).toBeCloseTo(0.0475, 2); // Dim T, cage depth
    expect(sfpSize.z).toBeCloseTo(0.00995, 2); // 8.95mm opening + 2x0.5mm wall

    const qsfpBuf = await nodeFetch('/3d/cage-qsfp.glb');
    const qsfpGltf = await loader.parseAsync(qsfpBuf, '');
    const qsfpBox = new THREE.Box3().setFromObject(qsfpGltf.scene);
    const qsfpSize = new THREE.Vector3();
    qsfpBox.getSize(qsfpSize);
    expect(qsfpSize.x).toBeCloseTo(0.02315, 2); // 22.15mm footprint width + wall
    expect(qsfpSize.y).toBeCloseTo(0.037, 2); // Datum L/K-to-PCB
    expect(qsfpSize.z).toBeCloseTo(0.01602, 2); // 15.02mm component-free height + wall
  });
});
