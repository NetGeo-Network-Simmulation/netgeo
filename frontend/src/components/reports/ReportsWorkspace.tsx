/**
 * ReportsWorkspace — the Reports Center (v1.2.032, clay design; re-laid-out
 * slice/ui-edge-fit per Surya's QA: "bagian reports center bisa di layout
 * ulang"). Three columns — download actions (left) · document preview
 * (center) · report type cards (right) — so the "paper" page reads as the
 * page's actual focal point instead of competing with a 4-card grid for
 * attention. It's the standing home for engineering documentation.
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
  Clock,
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
  desc: string;
  icon: LucideIcon;
  /** false → no project-level endpoint; card is disabled with `disabledHint`. */
  available: boolean;
  disabledHint?: string;
}

const REPORTS: ReportType[] = [
  {
    id: 'bom',
    title: 'Bill of Materials',
    desc: 'Hardware inventory and unit counts.',
    icon: Boxes,
    available: true,
  },
  {
    id: 'summary',
    title: 'Project Summary',
    desc: 'High-level topology and site overview.',
    icon: FileText,
    available: true,
  },
  {
    id: 'linkBudget',
    title: 'Link Budget',
    desc: 'Optical and RF attenuation analysis.',
    icon: LineChart,
    available: false,
    disabledHint: 'Per-path budgets live in the Fiber / FTTH workspace — no project-wide report yet.',
  },
  {
    id: 'rfCoverage',
    title: 'RF Coverage Study',
    desc: 'Heatmaps and signal propagation results.',
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
    <div className="absolute inset-0 flex bg-surface" role="region" aria-label="Reports Center">
      {/* Left: download actions. This column's own background/border-r bleed
          to x=0 (AppShell); its content gets pl-[116px] — 16px past the
          floating rail's x=100 right edge — so the buttons never render
          under the rail (same convention the card grid used before this
          layout, see theme/shell.ts). Narrow and top-aligned on purpose: the
          less width this column and the cards column on the right both
          claim, the more is left for the document in the middle. */}
      <div className="flex w-[300px] shrink-0 flex-col border-r border-fg/10 bg-panel">
        <div className="flex flex-col gap-2 pt-4 pr-4 pb-4 pl-[116px]">
          <button
            onClick={() => window.print()}
            disabled={!canDownload}
            className="flex items-center gap-2 rounded border border-fg/10 px-3 py-2 text-sm font-medium text-fg/85 transition-colors hover:bg-fg/5 disabled:opacity-40"
          >
            <Printer className="h-[18px] w-[18px] shrink-0" aria-hidden />
            Download PDF
          </button>
          <button
            onClick={downloadHtml}
            disabled={!canDownload}
            className="flex items-center gap-2 rounded border border-fg/10 px-3 py-2 text-sm font-medium text-fg/85 transition-colors hover:bg-fg/5 disabled:opacity-40"
          >
            <Code2 className="h-[18px] w-[18px] shrink-0" aria-hidden />
            Download HTML
          </button>
          <span className="mt-2 rounded border border-fg/10 bg-recess/30 px-3 py-1.5 text-center font-mono text-[12px] text-fg/60">
            Template: Standard
          </span>
        </div>
      </div>

      {/* Center: the document itself — the page's actual focal point. */}
      <main className="ng-scroll min-w-0 flex-1 overflow-y-auto bg-recess/20 p-8">
        {selected === 'summary' ? (
          <SummaryPreview loading={reportQ.isLoading} error={reportQ.error} html={reportQ.data} />
        ) : (
          <BomPreview loading={bomQ.isLoading} error={bomQ.error} rows={bomQ.data ?? []} title={activeMeta.title} />
        )}
      </main>

      {/* Right: report type cards, one per row — a narrower rail than the
          old 2-column grid so the document keeps most of the width. No page
          title either — the Reports tab in TopBar's sub-nav already carries
          this page's identity (root gets aria-label="Reports Center" above
          instead). `p-4` matches the left column's inset so both rails start
          their content at the same 16px offset from the corner instead of
          the old py-6/pr-6 (24px) that made the left column read as
          centered rather than pinned. flex-1 + min-h-0 here sizes this as a
          SCROLL VIEWPORT (needed so 4 cards never overflow a short window),
          not a stretch target — cards pack at the default flex-start (top)
          with no h-full/justify-between on them or on this wrapper, so any
          leftover height stays empty below the cards, never divided into
          them. */}
      <div className="flex w-[300px] shrink-0 flex-col border-l border-fg/10 bg-panel">
        <div className="ng-scroll flex min-h-0 flex-1 flex-col justify-start gap-3 overflow-y-auto p-4">
          {REPORTS.map((r) => (
            <ReportCard key={r.id} report={r} active={r.id === selected} onSelect={() => r.available && setSelected(r.id)} />
          ))}
        </div>
      </div>
    </div>
  );
}

function ReportCard({ report, active, onSelect }: { report: ReportType; active: boolean; onSelect: () => void }) {
  const { icon: Icon } = report;
  const disabled = !report.available;
  return (
    <button
      onClick={onSelect}
      disabled={disabled}
      aria-pressed={active}
      title={report.disabledHint}
      className={cn(
        'group flex flex-col rounded-xl border p-4 text-left transition-colors',
        active
          ? 'border-accent bg-accent/5'
          : disabled
            ? 'cursor-not-allowed border-fg/10 opacity-50'
            : 'border-fg/10 hover:border-fg/20 hover:bg-fg/5',
      )}
    >
      <div className="mb-3 flex items-start justify-between">
        <span
          className={cn(
            'grid h-10 w-10 place-items-center rounded-lg border border-fg/10 bg-recess/40',
            active ? 'text-accent' : 'text-fg/55',
          )}
        >
          <Icon className="h-5 w-5" aria-hidden />
        </span>
        {active && <span className="mt-1 h-2 w-2 rounded-full bg-accent" aria-hidden />}
      </div>
      <h3 className="mb-1 text-[15px] font-medium text-fg">{report.title}</h3>
      <p className="mb-3 text-xs leading-snug text-fg/50">{report.desc}</p>
      <div className="mt-auto flex items-center gap-1 font-mono text-[11px] text-fg/40">
        <Clock className="h-3.5 w-3.5" aria-hidden />
        {disabled ? 'Not available' : 'Ready to generate'}
      </div>
    </button>
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
