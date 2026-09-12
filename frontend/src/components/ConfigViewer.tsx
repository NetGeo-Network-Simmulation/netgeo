/**
 * ConfigViewer — shows generated config artifacts for a node (MASTER_SPEC §4
 * ConfigArtifact). Tabs per vendor/format, copy-to-clipboard, regenerate.
 * Demonstrates the NetGeo OS "one intent → many target NOS" output: the
 * same node can render IOS/Junos/RouterOS side by side.
 *
 * Heavy syntax highlighting is intentionally deferred (kept as a <pre> with
 * mono font) to protect the bundle/RAM budget; a lazy highlighter can be
 * dropped in later behind a dynamic import.
 */
import { useEffect, useState } from 'react';
import { Check, Copy, FileCode2, RefreshCw } from 'lucide-react';
import { configsApi } from '@/api/client';
import { useTopologyStore } from '@/store/topologyStore';
import type { ConfigArtifact } from '@/api/types';
import { cn } from '@/lib/cn';

export function ConfigViewer() {
  const nodeId = useTopologyStore((s) => s.selectedNodeId);

  const [artifacts, setArtifacts] = useState<ConfigArtifact[]>([]);
  const [active, setActive] = useState(0);
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!nodeId) return;
    setActive(0);
    setLoading(true);
    setError(null);
    configsApi
      .forNode(nodeId)
      .then((a) => setArtifacts(a))
      .catch((e) => setError(e?.message ?? 'Failed to load configs'))
      .finally(() => setLoading(false));
  }, [nodeId]);

  if (!nodeId) {
    return (
      <Empty>Select a node, then open Config Viewer to see generated output.</Empty>
    );
  }

  const regenerate = () => {
    setLoading(true);
    configsApi
      .generate(nodeId)
      .then((a) => setArtifacts((prev) => [a, ...prev]))
      .catch((e) => setError(e?.message ?? 'Generation failed'))
      .finally(() => setLoading(false));
  };

  const current = artifacts[active];
  const copy = () => {
    if (!current) return;
    void navigator.clipboard.writeText(current.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 1200);
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-fg/10 px-2 py-1.5">
        <div className="ng-scroll flex flex-1 gap-1 overflow-x-auto">
          {artifacts.map((a, i) => (
            <button
              key={a.id}
              onClick={() => setActive(i)}
              className={cn(
                'shrink-0 rounded-md px-2 py-1 text-xs',
                i === active ? 'bg-accent text-accent-fg' : 'bg-fg/5 text-fg/60 hover:bg-fg/10',
              )}
            >
              {a.vendor} · {a.format}
            </button>
          ))}
        </div>
        <button
          onClick={copy}
          disabled={!current}
          aria-label="Copy config"
          className="grid h-7 w-7 place-items-center rounded text-fg/70 hover:bg-fg/10 disabled:opacity-40"
        >
          {copied ? <Check className="h-4 w-4 text-success" /> : <Copy className="h-4 w-4" />}
        </button>
        <button
          onClick={regenerate}
          disabled={loading}
          aria-label="Regenerate config"
          className="grid h-7 w-7 place-items-center rounded text-fg/70 hover:bg-fg/10 disabled:opacity-40"
        >
          <RefreshCw className={cn('h-4 w-4', loading && 'animate-spin')} />
        </button>
      </div>

      <div className="ng-scroll flex-1 overflow-auto bg-recess/40 p-3">
        {error ? (
          <p className="text-sm text-danger">{error}</p>
        ) : loading && artifacts.length === 0 ? (
          <p className="text-sm text-fg/40">Generating…</p>
        ) : !current ? (
          <Empty>No config artifacts yet. Click regenerate to build one.</Empty>
        ) : (
          <pre className="whitespace-pre-wrap font-mono text-[12.5px] leading-relaxed text-fg/85">
            {current.content}
          </pre>
        )}
      </div>
    </div>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid h-full place-items-center p-6 text-center text-fg/40">
      <div className="space-y-2">
        <FileCode2 className="mx-auto h-8 w-8" />
        <p className="text-xs">{children}</p>
      </div>
    </div>
  );
}
