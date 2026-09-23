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
 * The chevron buttons themselves are `bg-panel` (fully opaque), not
 * `glass-strong` (Surya QA 2026-09-20): at rest — not mid-scroll — the fade
 * zone sits wherever content happens to be clipped, which can still read
 * as text through a translucent button (a ghost of whatever's underneath);
 * an opaque button fully occludes it regardless of the mask's exact edge.
 */
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { cn } from '@/lib/cn';

const FADE = 28; // px — the mask fade distance.
// Chevron's reserved edge padding: FADE plus a little extra so the button
// doesn't sit flush against the last real item's own border (visually tight
// at 980px — Surya QA 2026-09-20 was about the overlay covering content,
// but zero clearance from the item next to it read as cramped too).
const EDGE_PAD = FADE + 8;

export function HScrollToolbar({
  children,
  className,
  onMouseDown,
}: {
  children: ReactNode;
  className?: string;
  /** Passed straight to the root wrapper — TopBar's native-chrome drag
   * region uses this to mark the toolbar (and everything inside it) as a
   * no-drag zone via stopPropagation, same convention NativeTitleBar's own
   * window buttons use. No-op for every other caller. */
  onMouseDown?: (e: React.MouseEvent) => void;
}) {
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
    // flex-1 + min-w-0 live here, not in the caller's className (bug fixed
    // 2026-09-20): every call site (TopBar/ConfigWorkspace/
    // Rack3DElevationPanel) passes `className="flex-1 ..."`, but that prop
    // only ever reached the INNER scrollable div below, never this root. The
    // root — the thing actually laid out by the caller's flex row — stayed
    // shrink-to-content, so the toolbar claimed less width than the row
    // actually had free, forcing needless scrolling while real space sat
    // unused past it (Surya QA: right cluster clipped, "yet empty space
    // remains further right").
    <div className="relative flex min-w-0 flex-1 items-center" onMouseDown={onMouseDown}>
      {canLeft && (
        <button
          onClick={() => scrollBy(-160)}
          aria-label="Scroll toolbar left"
          className="absolute left-0.5 z-20 grid h-6 w-6 shrink-0 place-items-center rounded-full border border-fg/15 bg-panel text-fg/70 shadow-glass hover:text-fg"
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
        style={{
          WebkitMaskImage: mask,
          maskImage: mask,
          // Reserve the chevron's own footprint so it only ever overlays
          // the fade zone (already faded to transparent above), never real
          // content still at full opacity.
          paddingLeft: canLeft ? EDGE_PAD : undefined,
          paddingRight: canRight ? EDGE_PAD : undefined,
        }}
        className={cn('ng-scroll-hidden flex min-w-0 items-center overflow-x-auto', className)}
      >
        {children}
      </div>
      {canRight && (
        <button
          onClick={() => scrollBy(160)}
          aria-label="Scroll toolbar right"
          className="absolute right-0.5 z-20 grid h-6 w-6 shrink-0 place-items-center rounded-full border border-fg/15 bg-panel text-fg/70 shadow-glass hover:text-fg"
        >
          <ChevronRight className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
  );
}
