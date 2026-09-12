/**
 * ConfirmDialog — themed replacement for window.confirm/alert/prompt
 * (QA-visual #2, 2026-09-12). One shared component for all three: a plain
 * message, an optional password field (update token), and danger styling for
 * destructive actions. Built on the existing ModalScrim chrome (backdrop,
 * click-outside-to-close, the X button doubles as Cancel).
 *
 * This dialog is opened from local component state, not uiStore.activeModal,
 * so the global Escape handler in useShortcuts doesn't know about it — Escape/
 * Enter/Tab are handled locally, captured on window so they run before that
 * handler's bubble-phase listener and don't also fire its side effects
 * (closing/clearing whatever else is active).
 */
import { useEffect, useRef, useState } from 'react';
import { ModalScrim } from './ModalScrim';
import { cn } from '@/lib/cn';

export interface ConfirmDialogProps {
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  /** Destructive action (delete): danger-token confirm button, Cancel is the
   *  default focus so an accidental Enter/click lands on the safe choice. */
  danger?: boolean;
  /** Renders a password input; its value is passed to onConfirm. Never logged. */
  passwordLabel?: string;
  onConfirm: (value?: string) => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  title,
  message,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  danger = false,
  passwordLabel,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);
  const [pw, setPw] = useState('');

  // Focus the dialog on open (Cancel for a destructive prompt — the safe
  // default; the password field otherwise, for typing convenience) and
  // restore focus to whatever triggered it on close.
  useEffect(() => {
    const trigger = document.activeElement as HTMLElement | null;
    (danger ? cancelRef.current : (passwordRef.current ?? cancelRef.current))?.focus();
    return () => trigger?.focus?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        onCancel();
        return;
      }
      if (e.key === 'Enter') {
        e.preventDefault();
        e.stopPropagation();
        onConfirm(passwordLabel ? pw : undefined);
        return;
      }
      if (e.key === 'Tab' && rootRef.current) {
        const items = Array.from(rootRef.current.querySelectorAll<HTMLElement>('button, input'));
        if (items.length === 0) return;
        const first = items[0]!;
        const last = items[items.length - 1]!;
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    window.addEventListener('keydown', onKey, { capture: true });
    return () => window.removeEventListener('keydown', onKey, { capture: true });
  }, [onCancel, onConfirm, passwordLabel, pw]);

  return (
    <ModalScrim label={title} onClose={onCancel} className="max-w-sm">
      <div ref={rootRef} className="p-5">
        <h2 className="pr-6 text-sm font-semibold text-fg">{title}</h2>
        <p className="mt-1.5 text-sm text-fg/70">{message}</p>
        {passwordLabel && (
          <label className="mt-3 block text-xs text-fg/60">
            {passwordLabel}
            <input
              ref={passwordRef}
              type="password"
              autoComplete="off"
              value={pw}
              onChange={(e) => setPw(e.target.value)}
              className="mt-1 w-full rounded-md border border-fg/15 bg-fg/5 px-2.5 py-1.5 text-sm text-fg outline-none focus:border-accent/50"
            />
          </label>
        )}
        <div className="mt-5 flex justify-end gap-2">
          <button
            ref={cancelRef}
            onClick={onCancel}
            className="rounded-md px-3 py-1.5 text-sm font-medium text-fg/70 hover:bg-fg/10"
          >
            {cancelLabel}
          </button>
          <button
            onClick={() => onConfirm(passwordLabel ? pw : undefined)}
            className={cn(
              'rounded-md px-3 py-1.5 text-sm font-medium transition-colors',
              danger
                ? 'border border-danger/30 bg-danger/10 text-danger hover:bg-danger/20'
                : 'bg-accent text-accent-fg hover:bg-accent-soft',
            )}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </ModalScrim>
  );
}
