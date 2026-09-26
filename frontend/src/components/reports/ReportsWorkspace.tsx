/**
 * ReportsWorkspace — the Reports Center (v1.2.032, clay design; re-laid-out
 * slice/ui-edge-fit per Surya's QA: "bagian reports center bisa di layout
 * ulang"). The document is the canvas; report and export actions live in one
 * compact toolbar, matching Topology/Maps instead of a permanent card rail.
 *
 * Reuses the existing REST surface — NO new backend:
 *  - `fiberApi.bom(projectId)`    → Bill of Materials rows (NG-FI-04),
 *  - `fiberApi.report(projectId)` → the full HTML project report (NG-CFG-02),
 *    rendered in a sandboxed iframe (no allow-scripts) so its paper styling is
 *    isolated and it can never execute script.
 *
 * Honest deviations from the clay mock (the backend has no data for them):
 *  - Only two of the four cards are backed by real endpoints: Bill of Materials
 *    and Project Summary. Link Budget and RF Coverage have no project-level
 *    report endpoint, so their cards are disabled with a hint pointing to the
 *    Fiber and RF workspaces where those analyses live.
 *  - The BOM has no pricing — the model exposes category/item/qty/unit/notes but
 *    no unit price, so the mock's Unit Price / Subtotal / Total columns are
 *    replaced by Category / Notes. No fabricated money.
 *  - "Download PDF" is the browser's native print-to-PDF (window.print()) — there
 *    is no server PDF renderer. "Template" is a static label (the report backend
 *    ships one layout).
 */
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Boxes,
  LineChart,
  RadioTower,
  FileText,
  Code2,
  Printer,
  Loader2,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { fiberApi } from '@/api/client';
import { useUiStore } from '@/store/uiStore';
import { WorkspaceEmptyState } from '@/components/shell/WorkspaceEmptyState';
import { cn } from '@/lib/cn';

type ReportId = 'bom' | 'summary' | 'linkBudget' | 'rfCoverage';

interface ReportType {
  id: ReportId;
  title: string;
  icon: LucideIcon;
  /** false → no project-level endpoint; card is disabled with `disabledHint`. */
  available: boolean;
  disabledHint?: string;
}

const REPORTS: ReportType[] = [
  {
    id: 'bom',
    title: 'Bill of Materials',
    icon: Boxes,
    available: true,
  },
  {
    id: 'summary',
    title: 'Project Summary',
    icon: FileText,
    available: true,
  },
  {
    id: 'linkBudget',
    title: 'Link Budget',
    icon: LineChart,
    available: false,
    disabledHint: 'Per-path budgets live in the Fiber / FTTH workspace — no project-wide report yet.',
  },
  {
    id: 'rfCoverage',
    title: 'RF Coverage Study',
    icon: RadioTower,
    available: false,
    disabledHint: 'Coverage heatmaps live in the RF Planning workspace — no exportable report yet.',
  },
];

export function ReportsWorkspace() {
  const projectId = useUiStore((s) => s.projectId);
  const [selected, setSelected] = useState<ReportId>('bom');

  const bomQ = useQuery({
    queryKey: ['bom', projectId],
    queryFn: () => fiberApi.bom(projectId!),
    enabled: !!projectId && selected === 'bom',
    staleTime: 30_000,
  });
  const reportQ = useQuery({
    queryKey: ['report-html', projectId],
    queryFn: () => fiberApi.report(projectId!),
    enabled: !!projectId && selected === 'summary',
    staleTime: 30_000,
  });

  if (!projectId) {
    return (
      <div className="absolute inset-0">
        <WorkspaceEmptyState
          icon={FileText}
          title="No project open"
          hint="Open a project from the Projects portal to generate its engineering reports."
        />
      </div>
    );
  }

  const downloadHtml = async () => {
    const html = selected === 'summary' ? reportQ.data : bomToHtml(bomQ.data ?? []);
    if (!html) return;
    const blob = new Blob([html], { type: 'text/html' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `netgeo-${selected}-${projectId.slice(0, 8)}.html`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const activeMeta = REPORTS.find((r) => r.id === selected)!;
  const canDownload = selected === 'summary' ? !!reportQ.data : (bomQ.data?.length ?? 0) > 0;

  return (
    <div
      className="absolute inset-0 flex flex-col gap-3 bg-surface p-3 pl-[116px]"
      role="region"
      aria-label="Reports Center"
    >
      <header className="glass-strong flex min-h-14 shrink-0 items-center gap-3 overflow-x-auto rounded-xl border border-fg/15 px-3 shadow-glass">
        <div className="flex shrink-0 items-center gap-1 rounded-full border border-fg/10 bg-surface p-1" role="tablist" aria-label="Report type">
          {REPORTS.map(({ id, title, icon: Icon, available, disabledHint }) => (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={id === selected}
              disabled={!available}
              title={disabledHint ?? title}
              onClick={() => available && setSelected(id)}
              className={cn(
                'flex shrink-0 items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-medium transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-35',
                id === selected
                  ? 'bg-accent text-accent-fg'
                  : 'text-fg/65 hover:bg-fg/8 hover:text-fg',
              )}
            >
              <Icon className="h-4 w-4" aria-hidden />
              {title}
            </button>
          ))}
        </div>

        <div className="ml-auto flex shrink-0 items-center gap-2">
          <span className="rounded-full border border-fg/10 bg-surface px-3 py-1.5 font-mono text-[11px] text-fg/55">
            Standard template
          </span>
          <button
            type="button"
            onClick={() => window.print()}
            disabled={!canDownload}
            className="flex items-center gap-1.5 rounded-full border border-fg/10 px-3 py-1.5 text-xs font-medium text-fg/80 transition-colors hover:bg-fg/5 disabled:opacity-40"
          >
            <Printer className="h-4 w-4" aria-hidden />
            PDF
          </button>
          <button
            type="button"
            onClick={downloadHtml}
            disabled={!canDownload}
            className="flex items-center gap-1.5 rounded-full bg-accent px-3 py-1.5 text-xs font-medium text-accent-fg transition-colors hover:bg-accent-soft disabled:opacity-40"
          >
            <Code2 className="h-4 w-4" aria-hidden />
            HTML
          </button>
        </div>
      </header>

      <main className="ng-scroll min-h-0 min-w-0 flex-1 overflow-y-auto rounded-xl border border-fg/15 bg-recess/20 p-8 shadow-glass">
        {selected === 'summary' ? (
          <SummaryPreview loading={reportQ.isLoading} error={reportQ.error} html={reportQ.data} />
        ) : (
          <BomPreview loading={bomQ.isLoading} error={bomQ.error} rows={bomQ.data ?? []} title={activeMeta.title} />
        )}
      </main>
    </div>
  );
}

/** Loading / error / empty gate for the preview panes. */
function PreviewGate({
  loading,
  error,
  empty,
  emptyMsg,
  children,
}: {
  loading: boolean;
  error: unknown;
  empty: boolean;
  emptyMsg: string;
  children: React.ReactNode;
}) {
  if (loading)
    return (
      <div className="grid h-full place-items-center text-fg/40">
        <Loader2 className="h-6 w-6 animate-spin text-accent" aria-hidden />
      </div>
    );
  if (error)
    return (
      <div className="grid h-full place-items-center p-6 text-center text-sm text-danger">
        {(error as Error)?.message ?? 'Failed to load report.'}
      </div>
    );
  if (empty)
    return (
      <div className="grid h-full place-items-center p-6 text-center text-fg/40">
        <div className="max-w-sm space-y-2">
          <FileText className="mx-auto h-8 w-8" aria-hidden />
          <p className="text-xs leading-relaxed">{emptyMsg}</p>
        </div>
      </div>
    );
  return <>{children}</>;
}

function SummaryPreview({ loading, error, html }: { loading: boolean; error: unknown; html?: string }) {
  return (
    <PreviewGate
      loading={loading}
      error={error}
      empty={!html}
      emptyMsg="No report content — add devices and fiber paths to this project, then reopen."
    >
      {/* Sandboxed WITHOUT allow-scripts — style-isolated, cannot execute JS.
          bg-paper (not a theme token): this preview renders a document meant
          to be printed on paper, so it stays white even in dark mode. */}
      <iframe
        title="Project report preview"
        sandbox=""
        srcDoc={html}
        className="mx-auto block h-[1100px] w-full max-w-[720px] rounded-sm border border-fg/10 bg-paper shadow-glass-lg"
      />
    </PreviewGate>
  );
}

interface BomRow {
  category: string;
  item: string;
  qty: number;
  unit: string;
  notes: string;
}

function BomPreview({
  loading,
  error,
  rows,
  title,
}: {
  loading: boolean;
  error: unknown;
  rows: BomRow[];
  title: string;
}) {
  return (
    <PreviewGate
      loading={loading}
      error={error}
      empty={rows.length === 0}
      emptyMsg="This project has no hardware to itemize yet — add devices and fiber paths."
    >
      {/* Paper page — ivory sheet, dark ink, matches the clay mock's document. */}
      <div className="mx-auto min-h-[900px] w-full max-w-[720px] rounded-sm bg-[#FAF9F5] p-10 text-[#1F1E1D] shadow-glass-lg">
        <header className="mb-8 border-b-2 border-[#1F1E1D] pb-4">
          <h2 className="font-display text-2xl font-bold">NetGeo — {title}</h2>
          <div className="mt-3 font-mono text-[11px] text-[#494740]">
            Generated {new Date().toISOString().slice(0, 10)} · {rows.reduce((s, r) => s + r.qty, 0)} items
          </div>
        </header>
        <table className="w-full border-collapse text-left">
          <thead>
            <tr className="border-b border-[#a38c85]/50 font-mono text-[11px] text-[#494740]">
              <th className="py-2 pr-4 font-medium">Item</th>
              <th className="py-2 px-4 font-medium">Category</th>
              <th className="py-2 px-4 text-right font-medium">Qty</th>
              <th className="py-2 px-4 font-medium">Unit</th>
              <th className="py-2 pl-4 font-medium">Notes</th>
            </tr>
          </thead>
          <tbody className="font-mono text-[12px]">
            {rows.map((r, i) => (
              <tr key={i} className="border-b border-[#a38c85]/25">
                <td className="py-2.5 pr-4">{r.item}</td>
                <td className="py-2.5 px-4 text-[#494740]">{r.category}</td>
                <td className="py-2.5 px-4 text-right tabular-nums">{r.qty}</td>
                <td className="py-2.5 px-4 text-[#494740]">{r.unit}</td>
                <td className="py-2.5 pl-4 text-[#494740]">{r.notes || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </PreviewGate>
  );
}

/** Minimal standalone HTML for the BOM "Download HTML" action (no backend BOM
 *  report endpoint exists — the summary report has its own server HTML). */
function bomToHtml(rows: BomRow[]): string {
  const esc = (s: string) => s.replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[c] ?? c);
  const body = rows
    .map(
      (r) =>
        `<tr><td>${esc(r.item)}</td><td>${esc(r.category)}</td><td style="text-align:right">${r.qty}</td><td>${esc(
          r.unit,
        )}</td><td>${esc(r.notes)}</td></tr>`,
    )
    .join('');
  return `<!doctype html><meta charset="utf-8"><title>NetGeo Bill of Materials</title><style>body{font:14px system-ui;margin:40px;color:#1F1E1D}table{border-collapse:collapse;width:100%}th,td{border-bottom:1px solid #ccc;padding:6px 10px;text-align:left}th{font-size:12px;color:#494740}</style><h1>NetGeo — Bill of Materials</h1><table><thead><tr><th>Item</th><th>Category</th><th>Qty</th><th>Unit</th><th>Notes</th></tr></thead><tbody>${body}</tbody></table>`;
}
