/**
 * Slice 2 (outdoor placement vocabulary, keputusan Surya 2026-09-06):
 * `canRackMount` is the whole gate that stops a pole/wall/strand/ceiling
 * device from "succeeding" into a 19" rack slot, and `canPlaceDevice` must
 * actually apply it — not just accept it as decoration.
 */
import { describe, expect, it } from 'vitest';
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
