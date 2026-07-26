/**
 * UpdatesButton — menu-bar control that checks GitHub for a newer NetGeo
 * release and lets an operator apply it (pull + rebuild + restart) from the app.
 *
 * Applying requires the signed-in admin session; when the backend additionally
 * has UPDATE_TOKEN configured (token_required in the check payload), the user
 * is prompted for that shared secret first. After "apply" we poll status and
 * surface progress until the backend restarts.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Download, Loader2, RefreshCw } from 'lucide-react';
import {
  updateApi,
  type ApiError,
  type UpdateCheck,
  type UpdateStatus,
} from '@/api/client';
import { useUiStore } from '@/store/uiStore';
import { cn } from '@/lib/cn';
import { zc } from '@/theme/z';

/**
 * Turn a normalized {@link ApiError} (or anything else thrown) into an honest,
 * user-facing message. The previous version blamed *every* failure on an
 * unreachable backend, which was misleading for timeouts, CORS rejections,
 * 404s (backend too old to expose /update), and 5xx server errors — all of
 * which mean the backend *was* reachable.
 */
function describeError(e: unknown): string {
  const err = (e ?? {}) as Partial<ApiError> & { code?: string };
  const statusCode = typeof err.status === 'number' ? err.status : 0;
  const msg = typeof err.message === 'string' ? err.message : '';

  // status 0 = no HTTP response: timeout, DNS/connection failure, or a
  // CORS-blocked response (the browser hides the body, axios sees a network error).
  if (statusCode === 0) {
    if (/timeout|timed out|ECONNABORTED/i.test(msg) || err.code === 'ECONNABORTED') {
      return 'Update check timed out — the backend may be busy. Try again.';
    }
    return 'Could not reach the update service (network or CORS). Is the backend running?';
  }
  if (statusCode === 404) {
    return 'Update endpoint not found — the backend may be out of date.';
  }
  if (statusCode === 429) {
    return 'GitHub rate-limited the update check. Try again in a few minutes.';
  }
  if (statusCode >= 500) {
    return 'The backend errored while checking for updates. Check its logs.';
  }
  return msg || 'Update check failed.';
}

/**
 * Release notes arrive as GitHub-flavoured markdown; the popover renders plain
 * text, so strip the markup that would otherwise show up literally (##, **,
 * backticks, [text](url) links) while keeping the wording intact.
 */
function plainNotes(md: string): string {
  return md
    .replace(/^#{1,6}\s+/gm, '')             // headings
    .replace(/\*\*([^*]+)\*\*/g, '$1')        // bold
    .replace(/\*([^*]+)\*/g, '$1')            // italics
    .replace(/`([^`]+)`/g, '$1')              // inline code
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')  // links → text
    .trim();
}

export function UpdatesButton() {
  // Shared with the command palette and TopBar's user menu (uiStore.activeModal)
  // so opening one closes the others, and Escape-to-close comes for free from
  // the global handler in useShortcuts.
  const open = useUiStore((s) => s.activeModal === 'updates');
  const openModal = useUiStore((s) => s.openModal);
  const closeModal = useUiStore((s) => s.closeModal);
  const rootRef = useRef<HTMLDivElement>(null);
  // `info` holds the last *successful* check so the version stays visible even
  // if a later refresh fails. `error` is tracked separately from `info` so a
  // transient failure does not wipe out a known-good version, and so we never
  // mistake "haven't checked yet" for "up to date".
  const [info, setInfo] = useState<UpdateCheck | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval>>();

  const check = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const next = await updateApi.check();
      setInfo(next);
      // The backend was reached but may itself have failed to reach GitHub; it
      // reports that as a 200 with an `error` field. Surface it as an error so
      // we don't fall through to "up to date".
      setError(next.error ?? null);
    } catch (e) {
      setError(describeError(e));
    } finally {
      setBusy(false);
    }
  }, []);

  // Check on first open, and once a day in the background.
  useEffect(() => {
    void check();
    const t = setInterval(() => void check(), 24 * 60 * 60 * 1000);
    return () => clearInterval(t);
  }, [check]);

  useEffect(() => () => clearInterval(pollRef.current), []);

  // Close on click outside (same pattern as TopBar's user menu).
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) closeModal();
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open, closeModal]);

  const apply = useCallback(async () => {
    // The admin session authorises the update; only prompt for the extra
    // shared secret when the backend says one is configured.
    let token: string | undefined;
    if (info?.token_required) {
      token = window.prompt('Enter the update token (UPDATE_TOKEN) to apply:') ?? undefined;
      if (!token) return;
    }
    setBusy(true);
    try {
      setStatus(await updateApi.apply(token));
      // Poll status until the backend goes away (restart) or reports done/error.
      clearInterval(pollRef.current);
      pollRef.current = setInterval(async () => {
        try {
          const s = await updateApi.status();
          setStatus(s);
          if (s.state === 'done' || s.state === 'error' || s.state === 'up-to-date') {
            clearInterval(pollRef.current);
            if (s.state === 'done') setTimeout(() => window.location.reload(), 2000);
          }
        } catch {
          // Backend unreachable === it's restarting. Reload shortly.
          setStatus({ state: 'restarting', message: 'App is restarting…' });
        }
      }, 3000);
    } catch (e) {
      setStatus({ state: 'error', message: describeError(e) });
    } finally {
      setBusy(false);
    }
  }, [info?.token_required]);

  const available = info?.update_available ?? false;
  // We are mid-check with nothing to show yet — distinct from "up to date".
  const checking = busy && !info;
  // Only claim "up to date" once we have a successful, error-free check that
  // reports no newer version (covers latest === current gracefully).
  const upToDate = !!info && !error && !available;

  return (
    <div className="relative" ref={rootRef}>
      <button
        onClick={() => (open ? closeModal() : openModal('updates'))}
        aria-label="Updates"
        title={available ? `Update available: ${info?.latest}` : 'Check for updates'}
        className={cn(
          'relative grid h-7 w-7 place-items-center rounded-md hover:bg-fg/10',
          available && 'text-accent',
        )}
      >
        <Download className="h-4 w-4" />
        {available && (
          <span className="absolute right-1 top-1 h-1.5 w-1.5 rounded-full bg-accent" />
        )}
      </button>

      {open && (
        <div className={cn('absolute right-0 top-9 w-72 rounded-lg border border-fg/10 bg-panel p-3 text-[13px] text-fg/85 shadow-xl backdrop-blur', zc.popover)}>
          <div className="mb-2 flex items-center justify-between">
            <span className="font-semibold">Software update</span>
            <button
              onClick={() => void check()}
              disabled={busy}
              className="grid h-6 w-6 place-items-center rounded hover:bg-fg/10 disabled:opacity-50"
              title="Check again"
            >
              <RefreshCw className={cn('h-3.5 w-3.5', busy && 'animate-spin')} />
            </button>
          </div>

          <div className="space-y-1 text-fg/70">
            <div>
              Current:{' '}
              <span className="tabular-nums text-fg">
                {info?.current ?? (checking ? '…' : '—')}
              </span>
            </div>
            <div>
              Latest release:{' '}
              <span className="tabular-nums text-fg">
                {info
                  ? (info.latest ? `v${info.latest}` : 'Could not reach GitHub')
                  : (checking ? '…' : '—')}
              </span>
            </div>
          </div>

          {error && <p className="mt-2 text-xs text-warning">{error}</p>}

          {available ? (
            <>
              {info?.notes && (
                <p className="mt-2 max-h-24 overflow-auto whitespace-pre-wrap text-xs text-fg/60">
                  {plainNotes(info.notes)}
                </p>
              )}
              <button
                onClick={() => void apply()}
                disabled={busy || info?.can_apply === false}
                className="mt-3 flex w-full items-center justify-center gap-2 rounded-md bg-accent px-3 py-1.5 font-medium text-accent-fg disabled:opacity-50"
              >
                {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
                Update &amp; restart
              </button>
              {info?.token_required && (
                <p className="mt-1 text-[11px] text-fg/40">
                  You&apos;ll be asked for the update token (UPDATE_TOKEN).
                </p>
              )}
            </>
          ) : checking ? (
            <p className="mt-2 text-xs text-fg/50">Checking for updates…</p>
          ) : upToDate ? (
            info?.latest && info.current !== info.latest ? (
              // No update available yet current ≠ latest → this build is ahead of
              // the public release (dev build). Say so instead of "up to date",
              // which reads like a downgrade next to a lower "latest" number.
              <p className="mt-2 text-xs text-fg/50">
                This dev build (v{info.current}) is ahead of the latest public
                release (v{info.latest}).
              </p>
            ) : (
              <p className="mt-2 text-xs text-success">You&apos;re up to date.</p>
            )
          ) : null}

          {status && (
            <p className="mt-2 border-t border-fg/10 pt-2 text-xs text-fg/70">
              <span className="font-medium capitalize">{status.state}</span>
              {status.message ? ` — ${status.message}` : ''}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
