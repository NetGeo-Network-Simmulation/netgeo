/**
 * Device console sections (P4 + P5, docs/design/stitch-html/clay/device-console).
 * Split out of PropertiesPanel.tsx to keep that file readable: physical port
 * strip + PoE budget, the logical links table, and the IP/Port-settings tabs.
 * All writes go through the caller's `patch(partial Node)` — same optimistic
 * PATCH helper PropertiesPanel already uses for every other field, no new
 * endpoint.
 */
import { useState } from 'react';
import { Zap } from 'lucide-react';
import type { ConsolePort } from '@/components/rack/DeviceFaceplate';
import type { Interface, LinkModel, NodeModel } from '@/api/types';
import type { DeviceType } from '@/api/client';
import { cn } from '@/lib/cn';

/* ─── Toggle switch (P5 admin/PoE controls) ──────────────────────────────── */

export function Toggle({
  on,
  onChange,
  label,
  disabled,
}: {
  on: boolean;
  onChange: () => void;
  label: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      disabled={disabled}
      onClick={onChange}
      className={cn(
        'relative h-4 w-7 shrink-0 rounded-full border transition-colors disabled:cursor-not-allowed disabled:opacity-30',
        on ? 'border-accent bg-accent' : 'border-fg/15 bg-fg/10',
      )}
    >
      <span
        className={cn(
          'absolute top-0.5 h-3 w-3 rounded-full bg-panel shadow-sm transition-transform',
          on ? 'translate-x-3.5' : 'translate-x-0.5',
        )}
      />
    </button>
  );
}

/* ─── Physical ports strip + legend (P4) ─────────────────────────────────── */

function portGlyphClasses(port: ConsolePort): string {
  const iface = port.iface;
  if (iface && !iface.admin_enabled) {
    return 'border-danger bg-danger/10 text-danger';
  }
  if (iface && iface.peer_link_id) {
    return iface.speed >= 1000
      ? 'border-success bg-success/10 text-success'
      : 'border-warning bg-warning/10 text-warning';
  }
  return 'border-fg/15 text-fg/30';
}

export function PortStrip({ ports }: { ports: ConsolePort[] }) {
  if (ports.length === 0) return null;

  return (
    <section className="flex flex-col gap-3">
      <div className="flex items-center justify-between border-b border-fg/8 pb-2">
        <h3 className="text-[11px] font-semibold uppercase tracking-wider text-fg/45">
          Physical Ports
        </h3>
        <span className="text-[11px] text-fg/40">{ports.length} Ports</span>
      </div>
      <div className="rounded-lg border border-fg/8 bg-fg/4 p-3">
        <div className="grid grid-cols-8 gap-1.5">
          {ports.map((port) => (
            <div
              key={port.ordinal}
              title={port.iface ? port.iface.name : `Port ${port.ordinal + 1} (unprovisioned)`}
              className={cn(
                'relative flex h-5 w-5 items-center justify-center rounded text-[10px] border',
                portGlyphClasses(port),
              )}
            >
              {port.ordinal + 1}
              {port.iface && !port.iface.admin_enabled && (
                <span className="absolute inset-0 flex items-center">
                  <span className="h-px w-full rotate-45 bg-danger" />
                </span>
              )}
              {port.poeCapable && port.iface?.poe_enabled && (
                <Zap
                  className="absolute -right-1 -top-1 h-2.5 w-2.5 rounded-full bg-panel text-accent"
                  fill="currentColor"
                />
              )}
            </div>
          ))}
        </div>
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-1.5 text-[10px] text-fg/45">
        <Legend swatch="border-success bg-success/10">1 Gbps</Legend>
        <Legend swatch="border-warning bg-warning/10">100/10 Mbps</Legend>
        <Legend swatch="border-fg/20">Disconnected</Legend>
        <Legend swatch="border-danger bg-danger/10">Disabled</Legend>
        <span className="flex items-center gap-1.5">
          <Zap className="h-2.5 w-2.5 text-accent" fill="currentColor" />
          PoE Active
        </span>
      </div>
    </section>
  );
}

function Legend({ swatch, children }: { swatch: string; children: React.ReactNode }) {
  return (
    <span className="flex items-center gap-1.5">
      <span className={cn('h-2 w-2 rounded-sm border', swatch)} />
      {children}
    </span>
  );
}

/* ─── PoE power availability (P4) ─────────────────────────────────────────── */

// ponytail: PortSpec only marks a port "PoE-capable" as a bool, not a tier
// (poe/poe+/poe++), so a real per-port draw can't be looked up — this flat
// estimate stands in for "a device is actively drawing PoE" until PortSpec
// carries a tier. Documented, not hidden: the UI labels it "estimated".
const POE_DRAW_ESTIMATE_W = 7;

export function PoeBudget({
  node,
  deviceType,
}: {
  node: NodeModel;
  deviceType: DeviceType | undefined;
}) {
  const budget = deviceType?.poe_budget_w;
  if (!budget) {
    return (
      <section className="flex flex-col gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-fg/45">
          PoE Power Availability
        </span>
        <p className="rounded-md border border-dashed border-fg/10 px-3 py-2 text-xs text-fg/35">
          {deviceType
            ? 'This product model has no published PoE budget.'
            : 'Select a product model above to see PoE budget.'}
        </p>
      </section>
    );
  }

  const activePoe = node.interfaces.filter((i) => i.poe_enabled && i.peer_link_id).length;
  const drawW = activePoe * POE_DRAW_ESTIMATE_W;
  const pct = Math.min(100, (drawW / budget) * 100);

  return (
    <section className="flex flex-col gap-2">
      <div className="flex items-end justify-between">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-fg/45">
          PoE Power Availability
        </span>
        <span className="font-mono text-xs text-fg/80">
          ~{drawW} / {budget} W
        </span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-fg/10">
        <div className="h-full rounded-full bg-accent" style={{ width: `${pct}%` }} />
      </div>
      <p className="text-[10px] text-fg/35">Estimated from {activePoe} active PoE port(s).</p>
    </section>
  );
}

/* ─── Logical links table (P4) ────────────────────────────────────────────── */

export function LinksTable({
  node,
  links,
  nodesById,
}: {
  node: NodeModel;
  links: Map<string, LinkModel>;
  nodesById: Map<string, NodeModel>;
}) {
  const rows = node.interfaces
    .filter((i) => i.peer_link_id)
    .map((i) => {
      const link = links.get(i.peer_link_id!);
      const remoteIfaceId = link ? (link.a_iface === i.id ? link.b_iface : link.a_iface) : null;
      const remoteNode = remoteIfaceId
        ? [...nodesById.values()].find((n) => n.interfaces.some((ri) => ri.id === remoteIfaceId))
        : undefined;
      return { key: i.id, localPort: i.name, remoteLabel: remoteNode?.name ?? 'Unknown' };
    });

  return (
    <section className="flex flex-col gap-2">
      <h3 className="border-b border-fg/8 pb-2 text-[11px] font-semibold uppercase tracking-wider text-fg/45">
        Logical Links
      </h3>
      {rows.length === 0 ? (
        <p className="rounded-md border border-dashed border-fg/10 px-3 py-2 text-center text-xs text-fg/35">
          No connected links.
        </p>
      ) : (
        <div className="overflow-hidden rounded-lg border border-fg/8">
          <table className="w-full border-collapse text-left text-xs">
            <thead>
              <tr className="bg-fg/5">
                <th className="px-2.5 py-1.5 font-medium text-fg/45">Remote Link</th>
                <th className="px-2.5 py-1.5 text-right font-medium text-fg/45">Local Port</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-fg/8 font-mono">
              {rows.map((row) => (
                <tr key={row.key}>
                  <td className="px-2.5 py-1.5 text-fg/80">{row.remoteLabel}</td>
                  <td className="px-2.5 py-1.5 text-right text-fg/50">{row.localPort}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

/* ─── Config tabs: IP Addressing / Port Settings (P5) ────────────────────── */

export function ConfigTabs({
  node,
  ports,
  patchInterface,
}: {
  node: NodeModel;
  ports: ConsolePort[];
  patchInterface: (ifaceId: string, p: Partial<Interface>) => void;
}) {
  const [tab, setTab] = useState<'ip' | 'ports'>('ip');
  const poeCapableByIface = new Map(ports.filter((p) => p.iface).map((p) => [p.iface!.id, p.poeCapable]));

  return (
    <section className="flex flex-col gap-3 pb-2">
      <div className="flex border-b border-fg/10 text-xs">
        {(['ip', 'ports'] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={cn(
              'px-3 py-1.5 font-medium transition-colors',
              tab === t
                ? 'border-b-2 border-accent text-accent'
                : 'text-fg/45 hover:text-fg/70',
            )}
          >
            {t === 'ip' ? 'IP Addressing' : 'Port Settings'}
          </button>
        ))}
      </div>

      {node.interfaces.length === 0 ? (
        <p className="rounded-md border border-dashed border-fg/10 px-3 py-2 text-center text-xs text-fg/35">
          No interfaces yet. Connect a link to provision one.
        </p>
      ) : tab === 'ip' ? (
        <div className="overflow-hidden rounded-lg border border-fg/8">
          <table className="w-full border-collapse text-left text-xs">
            <thead>
              <tr className="bg-fg/5">
                <th className="px-2.5 py-1.5 font-medium text-fg/45">Interface</th>
                <th className="px-2.5 py-1.5 font-medium text-fg/45">IP Address</th>
                <th className="px-2.5 py-1.5 text-center font-medium text-fg/45">Admin</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-fg/8">
              {node.interfaces.map((iface) => (
                <IpRow key={iface.id} iface={iface} patchInterface={patchInterface} />
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="overflow-hidden rounded-lg border border-fg/8">
          <table className="w-full border-collapse text-left text-xs">
            <thead>
              <tr className="bg-fg/5">
                <th className="px-2.5 py-1.5 font-medium text-fg/45">Interface</th>
                <th className="px-2.5 py-1.5 text-center font-medium text-fg/45">Admin</th>
                <th className="px-2.5 py-1.5 text-center font-medium text-fg/45">PoE</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-fg/8">
              {node.interfaces.map((iface) => (
                <tr key={iface.id}>
                  <td className="px-2.5 py-1.5 font-mono text-fg/80">{iface.name}</td>
                  <td className="px-2.5 py-1.5">
                    <div className="flex justify-center">
                      <Toggle
                        on={iface.admin_enabled}
                        onChange={() => patchInterface(iface.id, { admin_enabled: !iface.admin_enabled })}
                        label={`Admin state for ${iface.name}`}
                      />
                    </div>
                  </td>
                  <td className="px-2.5 py-1.5">
                    <div className="flex justify-center">
                      <Toggle
                        on={iface.poe_enabled}
                        onChange={() => patchInterface(iface.id, { poe_enabled: !iface.poe_enabled })}
                        label={`PoE for ${iface.name}`}
                        disabled={!poeCapableByIface.get(iface.id)}
                      />
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function IpRow({
  iface,
  patchInterface,
}: {
  iface: Interface;
  patchInterface: (ifaceId: string, p: Partial<Interface>) => void;
}) {
  const [text, setText] = useState(iface.ip.join(', '));

  return (
    <tr>
      <td className="px-2.5 py-1.5 font-mono text-fg/70">{iface.name}</td>
      <td className="px-2.5 py-1.5">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          onBlur={() => {
            const next = text
              .split(',')
              .map((s) => s.trim())
              .filter(Boolean);
            if (next.join(', ') !== iface.ip.join(', ')) patchInterface(iface.id, { ip: next });
          }}
          placeholder="unassigned"
          className="w-full rounded border border-transparent bg-transparent px-1 py-0.5 font-mono text-fg/90 outline-none transition-colors focus:border-accent focus:bg-recess/20"
        />
      </td>
      <td className="px-2.5 py-1.5">
        <div className="flex justify-center">
          <Toggle
            on={iface.admin_enabled}
            onChange={() => patchInterface(iface.id, { admin_enabled: !iface.admin_enabled })}
            label={`Admin state for ${iface.name}`}
          />
        </div>
      </td>
    </tr>
  );
}
