import { describe, expect, it } from 'vitest';
import { mediaForIfaceTypes } from './cableMedia';

describe('mediaForIfaceTypes', () => {
  it('eth<->eth is copper', () => {
    expect(mediaForIfaceTypes('eth', 'eth')).toBe('cat6');
  });

  it('sfp<->sfp is fiber, not copper', () => {
    expect(mediaForIfaceTypes('sfp', 'sfp')).toBe('mmf_om3');
  });

  it('sfp28<->sfp28 is fiber', () => {
    expect(mediaForIfaceTypes('sfp28', 'sfp28')).toBe('mmf_om3');
  });

  it('qsfp<->qsfp gets the higher-grade fiber', () => {
    expect(mediaForIfaceTypes('qsfp', 'qsfp')).toBe('mmf_om4');
  });

  it('gpon<->gpon is a PON drop cable', () => {
    expect(mediaForIfaceTypes('gpon', 'gpon')).toBe('gpon_drop');
  });

  it('any link touching wifi gets no cable at all', () => {
    expect(mediaForIfaceTypes('wifi', 'eth')).toBeNull();
    expect(mediaForIfaceTypes('eth', 'wifi')).toBeNull();
    expect(mediaForIfaceTypes('wifi', 'wifi')).toBeNull();
  });

  it('mismatched ends: the more constrained optical connector wins over copper', () => {
    expect(mediaForIfaceTypes('eth', 'sfp')).toBe('mmf_om3');
    expect(mediaForIfaceTypes('sfp', 'eth')).toBe('mmf_om3');
    expect(mediaForIfaceTypes('eth', 'gpon')).toBe('gpon_drop');
  });

  it('mismatched optical ends: the higher-capacity cage wins', () => {
    expect(mediaForIfaceTypes('qsfp', 'sfp')).toBe('mmf_om4');
    expect(mediaForIfaceTypes('gpon', 'qsfp')).toBe('gpon_drop');
  });
});
