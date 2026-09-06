/**
 * Slice port-fxs: PTYPE_MAP must give the RJ-11 voice port (`fxs`) its own
 * rack3d PortType, never collapsing it into `rj45` — physically a different,
 * smaller connector (keputusan Surya 2026-09-06).
 */
import { describe, expect, it } from 'vitest';
import { PTYPE_MAP } from './plantAdapter';

describe('PTYPE_MAP (catalog port type -> rack3d PortType)', () => {
  it('maps fxs to its own PortType, distinct from rj45', () => {
    expect(PTYPE_MAP.fxs).toBe('fxs');
    expect(PTYPE_MAP.fxs).not.toBe(PTYPE_MAP.rj45);
    expect(PTYPE_MAP.fxs).not.toBe(PTYPE_MAP['console-rj45']);
  });
});
