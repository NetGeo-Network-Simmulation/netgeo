/**
 * Select — themed replacement for native <select>. The native popup is
 * OS-painted: its "highlighted option" colour comes from the platform accent
 * (navy in light Chromium/Firefox, system blue in dark), which cannot be
 * overridden from CSS in any browser — confirmed across Chromium + Firefox ×
 * light + dark (docs/qa/ui-theme-audit-v1.2.041.md tried the CSS-only fix
 * first; the row background centralised fine, but the selected/hovered row
 * highlight stayed OS-coloured). This renders both trigger and listbox
 * ourselves so every pixel is a theme token.
 *
 * Pattern reuse (ponytail: no new popover primitive) — mirrors the existing
 * ref+mousedown click-outside effect in shell/TopBar.tsx's user menu, and
 * zc.popover from theme/z.ts for stacking. Deliberately local `useState`
 * rather than uiStore.activeModal: that store is a single global slot for
 * app-chrome singletons (command palette, user menu, settings modal), and
 * several of these selects render *inside* those modals — sharing the slot
 * would close the parent modal the moment its own select opened.
 *
 * Keyboard model: WAI-ARIA 1.2 "select-only combobox" — DOM focus stays on
 * the trigger button the whole time (never moves into the listbox), the
 * highlighted row is tracked in state and exposed via aria-activedescendant.
 * ArrowUp/Down move it, Enter commits, Escape closes and keeps focus put.
 */
import { useEffect, useId, useRef, useState } from 'react';
import { Check, ChevronDown } from 'lucide-react';
import { cn } from '@/lib/cn';
import { zc } from '@/theme/z';

export interface SelectOption {
  value: string;
  label: string;
  disabled?: boolean;
}

export interface SelectProps {
  value: string;
  onChange: (value: string) => void;
  options: SelectOption[];
  placeholder?: string;
  disabled?: boolean;
  id?: string;
  /** Layout only (width/margin) — appended after the trigger's own bg/border/
   *  padding classes. Tailwind resolves same-property conflicts by internal
   *  utility order, not source order, so passing another `bg-*`/`border-*`/
   *  `py-*` here would silently win-or-lose per build. One consistent trigger
   *  look app-wide was the point (this replaces 6 competing bg recipes). */
  className?: string;
  'aria-label'?: string;
}

export function Select({
  value,
  onChange,
  options,
  placeholder,
  disabled,
  id,
  className,
  'aria-label': ariaLabel,
}: SelectProps) {
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const baseId = useId();

  const selected = options.find((o) => o.value === value);

  // Click-outside close — same ref+mousedown idiom as TopBar's user menu.
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  useEffect(() => {
    if (open) {
      const i = options.findIndex((o) => o.value === value);
      setActiveIndex(i >= 0 ? i : options.findIndex((o) => !o.disabled));
    }
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const commit = (i: number) => {
    const opt = options[i];
    if (!opt || opt.disabled) return;
    onChange(opt.value);
    setOpen(false);
    triggerRef.current?.focus();
  };

  const step = (from: number, dir: 1 | -1) => {
    if (options.length === 0) return from;
    let i = from;
    for (let n = 0; n < options.length; n++) {
      i = Math.min(options.length - 1, Math.max(0, i + dir));
      if (!options[i]?.disabled) return i;
      if (i === 0 || i === options.length - 1) break;
    }
    return from;
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (disabled) return;
    if (!open) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp' || e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        setOpen(true);
      }
      return;
    }
    switch (e.key) {
      case 'Escape':
        e.preventDefault();
        setOpen(false);
        triggerRef.current?.focus();
        break;
      case 'ArrowDown':
        e.preventDefault();
        setActiveIndex((i) => step(i, 1));
        break;
      case 'ArrowUp':
        e.preventDefault();
        setActiveIndex((i) => step(i, -1));
        break;
      case 'Enter':
      case ' ':
        e.preventDefault();
        commit(activeIndex);
        break;
      case 'Tab':
        setOpen(false);
        break;
    }
  };

  return (
    <div ref={rootRef} className="relative">
      <button
        ref={triggerRef}
        id={id}
        type="button"
        disabled={disabled}
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={ariaLabel}
        aria-activedescendant={open && activeIndex >= 0 ? `${baseId}-opt-${activeIndex}` : undefined}
        onClick={() => !disabled && setOpen((o) => !o)}
        onKeyDown={onKeyDown}
        className={cn(
          // No default width: shrinks to content like native <select>, same as
          // every call site's pre-existing layout. Callers opt into w-full /
          // max-w-* / w-NN via `className` — there is only ever one width
          // utility in the merged list, so there is no same-property Tailwind
          // ordering hazard to reason about.
          'inline-flex items-center justify-between gap-1.5 rounded-md border border-fg/10 bg-recess/20 px-2 py-1.5 text-left text-xs text-fg/85 transition-colors focus:border-accent disabled:cursor-not-allowed disabled:opacity-40',
          className,
        )}
      >
        <span className={cn('truncate', !selected && 'text-fg/40')}>{selected ? selected.label : (placeholder ?? '')}</span>
        <ChevronDown className={cn('h-3.5 w-3.5 shrink-0 text-fg/40 transition-transform', open && 'rotate-180')} />
      </button>

      {open && (
        <ul
          role="listbox"
          aria-label={ariaLabel}
          id={`${baseId}-listbox`}
          className={cn(
            'panel absolute left-0 top-[calc(100%+4px)] max-h-60 min-w-full overflow-auto rounded-md border border-fg/15 py-1 shadow-glass-lg animate-fade-in',
            zc.popover,
          )}
        >
          {options.length === 0 && <li className="px-2.5 py-1.5 text-xs text-fg/40">No options</li>}
          {options.map((o, i) => (
            <li
              key={o.value}
              id={`${baseId}-opt-${i}`}
              role="option"
              aria-selected={o.value === value}
              aria-disabled={o.disabled || undefined}
              onMouseEnter={() => !o.disabled && setActiveIndex(i)}
              onClick={() => commit(i)}
              className={cn(
                'flex cursor-pointer items-center justify-between gap-2 px-2.5 py-1.5 text-xs',
                o.disabled
                  ? 'cursor-not-allowed text-fg/30'
                  : i === activeIndex
                    ? 'bg-accent/20 text-fg'
                    : 'text-fg/80 hover:bg-fg/8',
              )}
            >
              <span className="truncate">{o.label}</span>
              {o.value === value && <Check className="h-3 w-3 shrink-0 text-accent" />}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
