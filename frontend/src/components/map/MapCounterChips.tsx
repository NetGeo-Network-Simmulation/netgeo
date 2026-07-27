/**
 * MapCounterChips — UISP-style roll-up counters, top-right (QA P3 / slice A2).
 * User: "list jumlah device yang wire di site, jumlah device wireless, jumlah
 * fiber dan lain-lain" — mirrors UISP's `624 · 175 · 37 · 412` chip row.
 *
 * Zero backend: every number is a client-side count over data the map already
 * renders from. `topologyStore` links carry an explicit `type` (copper/fiber/
 * wireless/virtual — api/types.ts), so link-type counts are the one honest,
 * non-invented breakdown available. `mapStore` devices are the legacy
 * RF-simulated layer (always point-to-point wireless, no `type` field of
 * their own) — their links fold into the Wireless count.
 *
 * Each chip carries a text label, not a bare number — UISP's own counters are
 * unlabelled and the user separately flagged that as a mystery (QA notes).
 */
import { useMapStore } from '@/store/mapStore';
import { useTopologyStore } from '@/store/topologyStore';
import { cn } from '@/lib/cn';
import { zc } from '@/theme/z';

export function MapCounterChips() {
  const legacyDeviceCount = useMapStore((s) => s.devices.size);
  const legacyLinkCount = useMapStore((s) => s.links.size);
  const topoNodes = useTopologyStore((s) => s.nodeList());
  const topoLinks = useTopologyStore((s) => s.linkList());

  const geoNodeCount = topoNodes.filter((n) => n.lat != null && n.lon != null).length;
  const deviceCount = legacyDeviceCount + geoNodeCount;

  let wireless = legacyLinkCount; // legacy map links are always simulated RF/wireless
  let fiber = 0;
  let wired = 0;
  for (const link of topoLinks) {
    if (link.type === 'wireless') wireless++;
    else if (link.type === 'fiber') fiber++;
    else if (link.type === 'copper') wired++;
  }

  const chips = [
    { label: 'Devices', value: deviceCount },
    { label: 'Wireless', value: wireless },
    { label: 'Fiber', value: fiber },
    { label: 'Wired', value: wired },
  ];

  // Nothing placed on the map yet — an all-zero row is noise, not signal.
  if (chips.every((c) => c.value === 0)) return null;

  return (
    <div className={cn('pointer-events-auto absolute right-4 top-3 flex gap-1.5', zc.workspace)}>
      {chips.map(({ label, value }) => (
        <div
          key={label}
          title={`${label}: ${value}`}
          className="glass-strong flex items-center gap-1 rounded-lg border border-fg/15 px-2 py-1 shadow-glass"
        >
          <span className="text-xs font-semibold text-fg">{value}</span>
          <span className="text-[9px] uppercase tracking-wide text-fg/55">{label}</span>
        </div>
      ))}
    </div>
  );
}
