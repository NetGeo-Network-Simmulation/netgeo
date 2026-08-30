/**
 * "Approximate shape" marker (docs/design/24-DEVICE-PHYSICAL-SPEC.md slice):
 * a device that resolves to deviceTypes.ts's `generic-*` fallback (no
 * curated real SKU matched) must show a visible cue, not just an a11y-only
 * <title>/aria-label. Rendered to static markup — no DOM interaction needed,
 * so no new test-rendering dependency (react-dom/server ships with react-dom,
 * already a direct dependency).
 */
import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import type { NodeModel } from '@/api/types';
import { DeviceFaceplate } from './DeviceFaceplate';

function node(id: string, nos: NodeModel['nos'], ifaceCount: number): NodeModel {
  return {
    id,
    project_id: 'p1',
    name: id,
    kind: 'switch',
    nos,
    mode: 'sim',
    x: 0,
    y: 0,
    interfaces: Array.from({ length: ifaceCount }, (_, i) => ({
      id: `${id}-if${i}`,
      node_id: id,
      name: `eth${i}`,
      type: 'sfp28',
      ip: [],
      mac: '',
      speed: 10000,
      mtu: 1500,
      peer_link_id: null,
      admin_enabled: true,
      poe_enabled: false,
    })),
    config_ref: null,
    status: 'running',
  };
}

describe('DeviceFaceplate approximate-shape marker', () => {
  it('shows the dashed marker for a generic-fallback device, not for a curated real SKU', () => {
    // 'vyos' matches no curated switch DeviceType.nos (deviceTypes.ts) and
    // 'switch' has no kind-based heuristic fallback — resolveDeviceType()
    // has to fall through to genericFor(), slug 'generic-switch'.
    const genericHtml = renderToStaticMarkup(
      <DeviceFaceplate node={node('sw-generic', 'vyos', 2)} span={1} face="front" />,
    );
    // 'routeros' matches MikroTik CRS317/CRS328 — a curated real SKU.
    const curatedHtml = renderToStaticMarkup(
      <DeviceFaceplate node={node('sw-real', 'routeros', 16)} span={1} face="front" />,
    );

    expect(genericHtml).toContain('data-testid="approximate-shape-marker"');
    expect(curatedHtml).not.toContain('data-testid="approximate-shape-marker"');
  });
});
