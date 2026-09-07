/**
 * Slice 2 (outdoor placement vocabulary, keputusan Surya 2026-09-06):
 * `canRackMount` is the whole gate that stops a pole/wall/strand/ceiling
 * device from "succeeding" into a 19" rack slot, and `canPlaceDevice` must
 * actually apply it — not just accept it as decoration.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { canPlaceDevice, canRackMount } from './plantAdapter';
import type { RackBay } from './rack3d';

function emptyBay(key: string): RackBay {
  return { key, enclosure: 'apc', devices: [] };
}

describe('canRackMount', () => {
  it('rejects the known non-rack form_factor values', () => {
    expect(canRackMount('pole-mount')).toBe(false);
    expect(canRackMount('wall-mount')).toBe(false);
    expect(canRackMount('strand-mount')).toBe(false);
    expect(canRackMount('ceiling-mount')).toBe(false);
  });

  it('rejects any outdoor-* marker', () => {
    expect(canRackMount('outdoor-rru')).toBe(false);
    expect(canRackMount('outdoor-cpe')).toBe(false);
  });

  it('accepts real rackmount values already in use across the packs', () => {
    expect(canRackMount('1U-fixed')).toBe(true);
    expect(canRackMount('2U-rackmount')).toBe(true);
    expect(canRackMount('modular-chassis')).toBe(true);
    expect(canRackMount('baseband-unit')).toBe(true); // ru:2/3 BBUs — real rack occupants
  });

  it('accepts undefined — legacy data predating form_factor must not regress', () => {
    expect(canRackMount(undefined)).toBe(true);
  });

  it('accepts an unrecognized value — never blocks a device this list has not seen', () => {
    expect(canRackMount('some-future-value')).toBe(true);
  });
});

describe('canPlaceDevice form_factor gate', () => {
  const bays = [emptyBay('a')];

  it('rejects a pole-mount device even into an otherwise-free slot', () => {
    expect(canPlaceDevice(bays, 'a', 1, 1, 42, undefined, 'pole-mount')).toBe(false);
  });

  it('still accepts a rackmount device in the same slot', () => {
    expect(canPlaceDevice(bays, 'a', 1, 1, 42, undefined, '1U-fixed')).toBe(true);
  });

  it('still accepts a device with no form_factor at all (legacy data)', () => {
    expect(canPlaceDevice(bays, 'a', 1, 1, 42)).toBe(true);
  });
});

/**
 * Slice 8 (cell-site/RAN outdoor batch): proves the real pack data, not just
 * the pure-function cases above, gates correctly — 6 outdoor RRU/AAU/O-RU
 * SKUs rejected, the one rackmount DAS headend (SOLiD nBIU) accepted.
 */
describe('cell-site pack physical.form_factor (real data)', () => {
  const packPath = path.join(
    __dirname,
    '../../../../network/devices/packs/cell-site/devices/cell-site.json',
  );
  const pack = JSON.parse(readFileSync(packPath, 'utf-8')) as {
    devices: { id: string; physical?: { form_factor?: string } }[];
  };
  const byId = new Map(pack.devices.map((d) => [d.id, d.physical?.form_factor]));

  it.each([
    'benetel-ran650',
    'airspan-airstrand-2200',
    'samsung-mmu-mt6402-48a',
    'mavenir-b12-4t4r-mr44ea',
    'comba-cws-4240-71',
    'parallel-wireless-crossfire-x2ru',
  ])('rejects outdoor SKU %s from the rack', (id) => {
    expect(canRackMount(byId.get(id))).toBe(false);
  });

  it('accepts the SOLiD nBIU (rackmount 19"/3U DAS headend)', () => {
    expect(canRackMount(byId.get('solid-alliance-nbiu'))).toBe(true);
  });
});
