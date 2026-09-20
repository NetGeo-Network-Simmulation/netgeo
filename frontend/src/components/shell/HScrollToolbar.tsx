/**
 * HScrollToolbar — a row of fixed-size items that must never wrap or clip.
 * When the row is wider than its box, it becomes horizontally scrollable
 * (wheel included) instead of clipping, with edge chevrons + a fade mask
 * that only appear once there's actually more content offscreen — the
 * pattern a WPS/Office ribbon uses at narrow widths (Surya 2026-09-18,
 * reference: WPS Docs' Home ribbon staying one row and sliding instead of
 * wrapping/clipping when the window narrows).
 *
 * ponytail: mask-image (not a solid-color gradient div) fades the *content*
 * to transparent at the edges — correct under TopBar's `glass-strong`
 * blur/translucency without needing to know or match its background color.
 */
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { cn } from '@/lib/cn';

const FADE = 28; // px

export function HScrollToolbar({ children, className }: { children: ReactNode; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [canLeft, setCanLeft] = useState(false);
  const [canRight, setCanRight] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const update = () => {
      setCanLeft(el.scrollLeft > 1);
      setCanRight(el.scrollLeft + el.clientWidth < el.scrollWidth - 1);
    };
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    el.addEventListener('scroll', update, { passive: true });
    return () => {
      ro.disconnect();
      el.removeEventListener('scroll', update);
    };
  }, []);

  const scrollBy = (dx: number) => ref.current?.scrollBy({ left: dx, behavior: 'smooth' });

  const mask = `linear-gradient(to right, ${canLeft ? 'transparent, black ' + FADE + 'px' : 'black'}, black calc(100% - ${canRight ? FADE : 0}px), ${canRight ? 'transparent' : 'black'})`;

  return (
    <div className="relative flex min-w-0 items-center">
      {canLeft && (
        <button
          onClick={() => scrollBy(-160)}
          aria-label="Scroll toolbar left"
          className="glass-strong absolute left-0.5 z-20 grid h-6 w-6 shrink-0 place-items-center rounded-full border border-fg/15 text-fg/70 shadow-glass hover:text-fg"
        >
          <ChevronLeft className="h-3.5 w-3.5" />
        </button>
      )}
      <div
        ref={ref}
        onWheel={(e) => {
          if (!ref.current || ref.current.scrollWidth <= ref.current.clientWidth) return;
          if (Math.abs(e.deltaY) <= Math.abs(e.deltaX)) return; // already horizontal — let it be native
          e.preventDefault();
          ref.current.scrollLeft += e.deltaY;
        }}
        style={{ WebkitMaskImage: mask, maskImage: mask }}
        className={cn('ng-scroll-hidden flex min-w-0 items-center overflow-x-auto', className)}
      >
        {children}
      </div>
      {canRight && (
        <button
          onClick={() => scrollBy(160)}
          aria-label="Scroll toolbar right"
          className="glass-strong absolute right-0.5 z-20 grid h-6 w-6 shrink-0 place-items-center rounded-full border border-fg/15 text-fg/70 shadow-glass hover:text-fg"
        >
          <ChevronRight className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
  );
}
