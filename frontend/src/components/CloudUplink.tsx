/**
 * CloudUplink — properties section for a **cloud** node: binds the simulation to
 * a real host ethernet adapter / the internet. Lists the NICs the backend
 * detected (`GET /api/system/interfaces`), shows live internet reachability, and
 * persists the choice on the node at `intent.uplink` ({ adapter, mode }).
 */
import { useCallback, useEffect, useState } from 'react';
import { Globe, RefreshCw, Wifi, WifiOff } from 'lucide-react';
import { systemApi } from '@/api/client';
import type { HostInterface, InternetStatus, NodeModel, Uplink, UplinkMode } from '@/api/types';
import { Select } from '@/components/ui/Select';

export function CloudUplink({
  node,
  patch,
}: {
  node: NodeModel;
  patch: (p: Partial<NodeModel>) => void;
}) {
  const [ifaces, setIfaces] = useState<HostInterface[] | null>(null);
  const [net, setNet] = useState<InternetStatus | null>(null);
  const [loading, setLoading] = useState(false);

  const uplink = node.intent?.uplink as Uplink | undefined;

  const setUplink = useCallback(
    (u: Uplink) => patch({ intent: { ...(node.intent ?? {}), uplink: u } }),
    [node.intent, patch],
  );

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [list, status] = await Promise.all([systemApi.interfaces(), systemApi.internet()]);
      setIfaces(list);
      setNet(status);
      // Auto-pick a sensible uplink the first time (primary → real/up → first).
      if (!uplink && list.length) {
        const pick =
          list.find((i) => i.is_primary) ??
          list.find((i) => !i.is_virtual && i.is_up) ??
          list[0];
        if (pick) setUplink({ adapter: pick.name, mode: 'nat' });
      }
    } catch {
      setIfaces([]);
    } finally {
      setLoading(false);
    }
  }, [uplink, setUplink]);

  useEffect(() => {
    void load();
    // Reload when switching to a different cloud node.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [node.id]);

  const selected = uplink?.adapter;

  return (
    <section className="space-y-2 rounded-md border border-accent/30 bg-accent/5 p-2.5">
      <div className="flex items-center justify-between">
        <h4 className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-fg/60">
          <Globe className="h-3.5 w-3.5" /> Real-world uplink
        </h4>
        <button
          onClick={() => void load()}
          title="Re-scan adapters"
          className="grid h-6 w-6 place-items-center rounded hover:bg-fg/10"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </div>

      {/* Internet status */}
      <div
        className={`flex items-center gap-1.5 text-xs ${
          net?.online ? 'text-success' : 'text-warning'
        }`}
      >
        {net?.online ? <Wifi className="h-3.5 w-3.5" /> : <WifiOff className="h-3.5 w-3.5" />}
        {net == null
          ? 'Checking internet…'
          : net.online
            ? `Internet reachable · ${net.latency_ms ?? '?'} ms${net.source_ip ? ` · src ${net.source_ip}` : ''}`
            : 'No internet from host'}
      </div>

      {/* Adapter selector */}
      <label className="block space-y-1">
        <span className="text-[10px] font-medium uppercase tracking-wide text-fg/45">
          Host adapter
        </span>
        {ifaces == null ? (
          <p className="text-xs text-fg/40">Detecting adapters…</p>
        ) : ifaces.length === 0 ? (
          <p className="text-xs text-warning">No adapters detected.</p>
        ) : (
          <Select
            aria-label="Host adapter"
            value={selected ?? ''}
            onChange={(v) => setUplink({ adapter: v, mode: uplink?.mode ?? 'nat' })}
            placeholder="Select adapter…"
            options={ifaces.map((i) => ({
              value: i.name,
              label:
                i.name +
                (i.is_primary ? ' ★' : '') +
                (i.is_virtual ? ' (virtual)' : '') +
                (i.ipv4[0] ? ` · ${i.ipv4[0]}` : '') +
                (i.is_up ? '' : ' · down'),
            }))}
            className="w-full"
          />
        )}
      </label>

      {/* NAT vs bridge */}
      <label className="block space-y-1">
        <span className="text-[10px] font-medium uppercase tracking-wide text-fg/45">Mode</span>
        <div className="flex rounded-md border border-fg/10 bg-recess/20 p-0.5">
          {(['nat', 'bridge'] as UplinkMode[]).map((m) => (
            <button
              key={m}
              onClick={() => selected && setUplink({ adapter: selected, mode: m })}
              disabled={!selected}
              className={`flex-1 rounded px-2 py-1 text-xs uppercase ${
                uplink?.mode === m ? 'bg-accent text-accent-fg' : 'text-fg/60'
              } disabled:opacity-40`}
            >
              {m}
            </button>
          ))}
        </div>
      </label>

      <p className="text-[10px] leading-snug text-fg/40">
        <b>NAT</b>: sim reaches the internet via the host (outbound). <b>Bridge</b>: place sim
        nodes directly on the adapter&apos;s L2 segment. Applied by the emulation backend when the
        node runs in <code>emul</code> mode.
      </p>
    </section>
  );
}
