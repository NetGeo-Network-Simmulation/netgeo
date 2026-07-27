/**
 * OnboardingModal — first-run wizard that introduces NetGeo's core workflow.
 * Shown once after the very first login; state is persisted to localStorage.
 * Steps: Overview → Device Palette → Topology Canvas → Properties → Simulate.
 */
import { useState } from 'react';
import {
  ChevronLeft,
  ChevronRight,
  Network,
  PanelRightOpen,
  Play,
  SlidersHorizontal,
  X,
} from 'lucide-react';
import { cn } from '@/lib/cn';
import { zc } from '@/theme/z';

/** localStorage flag — ModalLayer reads it to trigger the first-run wizard once. */
export const ONBOARDING_KEY = 'netgeo.onboarding.done';

interface Step {
  icon: typeof Network;
  /** Hex accent for the icon box. Omit to use the theme accent token instead
   *  (coral/orange) — the fixed hexes below are per-step decoration, not
   *  brand color, so they stay as-is; this step is the one users see first
   *  and read as "the app's color," so it must track the theme (D1). */
  color?: string;
  title: string;
  description: string;
  hint?: string;
}

const STEPS: Step[] = [
  {
    icon: Network,
    title: 'Welcome to NetGeo',
    description:
      'NetGeo is a browser-based network simulation platform. Design, simulate, and generate configs for complex network topologies — all without physical hardware.',
    hint: 'Let\'s take a quick tour of the interface.',
  },
  {
    icon: PanelRightOpen,
    color: '#5856D6',
    title: 'Device Palette',
    description:
      'The Device Palette (left panel) lists all available device templates grouped by category: routers, switches, firewalls, hosts, and more.',
    hint: 'Drag any device card onto the Topology canvas to place it.',
  },
  {
    icon: Network,
    color: '#34C759',
    title: 'Topology Canvas',
    description:
      'The Topology canvas is your network workspace. Place devices, draw links between them by dragging from one port handle to another, and pan/zoom freely.',
    hint: 'Right-click a node to delete it or open its console.',
  },
  {
    icon: SlidersHorizontal,
    color: '#FF9F0A',
    title: 'Properties Panel',
    description:
      'Click any device to select it. The Properties panel shows its name, Network OS (NOS), mode (sim vs. emulation), and interface list.',
    hint: 'You can also generate vendor configs directly from the Properties panel.',
  },
  {
    icon: Play,
    color: '#34C759',
    title: 'Simulate & Generate',
    description:
      'Use the top toolbar to start, pause, or step through the simulation. Once your topology is ready, generate device configurations for any supported NOS from the Properties panel.',
    hint: 'Configs support IOS, IOS-XR, Junos, NX-OS, EOS, VRP, and more.',
  },
];

interface OnboardingModalProps {
  onClose: () => void;
}

export function OnboardingModal({ onClose }: OnboardingModalProps) {
  const [step, setStep] = useState(0);
  const current = STEPS[step]!;
  const Icon = current.icon;
  const isLast = step === STEPS.length - 1;
  const isFirst = step === 0;

  return (
    /* Backdrop */
    <div
      className={cn(
        'fixed inset-0 flex items-center justify-center bg-black/60 backdrop-blur-sm animate-fade-in',
        zc.modal,
      )}
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      {/* Card */}
      <div className="glass-strong relative w-full max-w-md overflow-hidden rounded-2xl border border-fg/15 shadow-glass-lg animate-scale-in">
        {/* Close */}
        <button
          onClick={onClose}
          aria-label="Close onboarding"
          className="absolute right-4 top-4 grid h-7 w-7 place-items-center rounded-md text-fg/40 hover:bg-fg/10 hover:text-fg"
        >
          <X className="h-4 w-4" />
        </button>

        {/* Step icon + header */}
        <div className="px-8 pb-4 pt-8 text-center">
          <div
            className={cn(
              'mx-auto mb-4 grid h-14 w-14 place-items-center rounded-2xl shadow-lg',
              !current.color && 'bg-accent/15 text-accent shadow-[0_8px_24px_rgb(var(--ng-accent-rgb)_/_0.25)]',
            )}
            style={
              current.color
                ? { background: `${current.color}22`, color: current.color, boxShadow: `0 8px 24px ${current.color}40` }
                : undefined
            }
          >
            <Icon className="h-7 w-7" />
          </div>
          <h2 className="text-lg font-semibold text-fg">{current.title}</h2>
        </div>

        {/* Body */}
        <div className="px-8 pb-2">
          <p className="text-center text-sm leading-relaxed text-fg/70">
            {current.description}
          </p>
          {current.hint && (
            <p className="mt-3 rounded-md border border-fg/10 bg-fg/5 px-3 py-2 text-center text-xs text-fg/50">
              {current.hint}
            </p>
          )}
        </div>

        {/* Step dots */}
        <div className="flex justify-center gap-1.5 py-4">
          {STEPS.map((_, i) => (
            <button
              key={i}
              onClick={() => setStep(i)}
              aria-label={`Go to step ${i + 1}`}
              className={cn(
                'h-1.5 rounded-full transition-all duration-std',
                i === step ? 'w-6 bg-accent' : 'w-1.5 bg-fg/20 hover:bg-fg/40',
              )}
            />
          ))}
        </div>

        {/* Navigation */}
        <div className="flex items-center justify-between border-t border-fg/10 px-6 py-4">
          <button
            onClick={() => setStep((s) => s - 1)}
            disabled={isFirst}
            className={cn(
              'flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm transition-colors',
              isFirst
                ? 'cursor-not-allowed text-fg/20'
                : 'text-fg/60 hover:bg-fg/10 hover:text-fg',
            )}
          >
            <ChevronLeft className="h-4 w-4" /> Back
          </button>

          {isLast ? (
            <button
              onClick={onClose}
              className="flex items-center gap-1.5 rounded-md bg-accent px-4 py-1.5 text-sm font-medium text-accent-fg transition-colors hover:bg-accent-soft"
            >
              Get started
            </button>
          ) : (
            <button
              onClick={() => setStep((s) => s + 1)}
              className="flex items-center gap-1.5 rounded-md bg-fg/10 px-3 py-1.5 text-sm text-fg transition-colors hover:bg-fg/15"
            >
              Next <ChevronRight className="h-4 w-4" />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
