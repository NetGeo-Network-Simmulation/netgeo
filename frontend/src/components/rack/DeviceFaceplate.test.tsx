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

  it('renders a real pack faceplate (no marker) for a node with device_type_id + pack ports (N4)', () => {
    const packNode: NodeModel = { ...node('sw-pack', 'forgeos', 4), device_type_id: 'switches:cisco-c9300-48p-access' };
    const deviceTypesById = new Map([
      [
        'switches:cisco-c9300-48p-access',
        {
          id: 'switches:cisco-c9300-48p-access',
          name: 'Cisco Catalyst 9300 48p',
          category: 'wired',
          description: '',
          builtin: true,
          vendor: 'Cisco',
          ports: [
            { pattern: 'GigabitEthernet1/0/{n}', count: 48, type: 'eth', speed_mbps: 1000, poe: true },
            { pattern: 'TenGigabitEthernet1/1/{n}', count: 4, type: 'sfp28', speed_mbps: 10000 },
          ],
          physical: { ru: 1, form_factor: '1U-fixed' },
        },
      ],
    ]);

    const packHtml = renderToStaticMarkup(
      <DeviceFaceplate node={packNode} span={1} face="front" deviceTypesById={deviceTypesById} />,
    );
    // Real pack data resolved -> no "approximate shape" marker, real model name shown.
    expect(packHtml).not.toContain('data-testid="approximate-shape-marker"');
    expect(packHtml).toContain('Cisco Catalyst 9300 48p');

    // Same node, but the caller has no /device-types lookup available (e.g.
    // still loading) -> falls back to the nos/kind heuristic, a different shape.
    const fallbackHtml = renderToStaticMarkup(
      <DeviceFaceplate node={packNode} span={1} face="front" />,
    );
    expect(fallbackHtml).not.toBe(packHtml);
  });
});

describe('Juniper QFX5120-48Y chassis LEDs (docs/design/24-DEVICE-PHYSICAL-SPEC.md §10)', () => {
  it('renders the 4 chassis LEDs (ALM/SYS/MST/ID) on the rear face, not the front', () => {
    const n = node('sw-qfx5120', 'junos', 8);
    const frontHtml = renderToStaticMarkup(<DeviceFaceplate node={n} span={1} face="front" />);
    const rearHtml = renderToStaticMarkup(<DeviceFaceplate node={n} span={1} face="back" />);

    for (const label of ['ALM', 'SYS', 'MST', 'ID']) {
      expect(frontHtml).not.toContain(`>${label}<`);
      expect(rearHtml).toContain(`>${label}<`);
    }
  });
});
