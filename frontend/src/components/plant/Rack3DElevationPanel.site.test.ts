import { describe, expect, it } from 'vitest';
import { rackSiteForView } from './Rack3DElevationPanel';

describe('new rack site follows the visible scene', () => {
  it('keeps a populated Site A scene selected when no destination was overridden', () => {
    const visibleSite = 'site-a';
    const submittedSite = rackSiteForView(visibleSite, null);
    const nodes = Array.from({ length: 80 }, (_, i) => ({ id: `n${i}`, siteId: visibleSite }));
    const cables = Array.from({ length: 15 }, (_, i) => ({ id: `c${i}`, siteId: visibleSite }));

    expect(submittedSite).toBe(visibleSite);
    expect(nodes.filter((n) => n.siteId === submittedSite)).toHaveLength(80);
    expect(cables.filter((c) => c.siteId === submittedSite)).toHaveLength(15);
  });

  it('preserves an explicit no-site or alternate-site choice', () => {
    expect(rackSiteForView('site-a', '')).toBe('');
    expect(rackSiteForView('site-a', 'site-b')).toBe('site-b');
  });
});
