/**
 * EduWorkspace — the Education Lab full-bleed view (NG-EDU-01/02/03).
 *
 * One workspace, three modes (mirrors TwinWorkspace's canvas-plus-floating-chrome
 * shape): `browse` lists activities, `author` composes one, `student` takes one.
 * The live TopologyCanvas sits underneath every mode — the author builds the
 * initial/answer network on it, the student solves the activity on it — so the
 * same graph plumbing (topologyStore + the App.tsx topology query) is reused, not
 * duplicated. A segmented Author|Student switch appears only once a saved activity
 * is selected: that shared selection is what makes this one workspace, not two.
 */
import { useEffect } from 'react';
import { ArrowLeft, PenLine, GraduationCap } from 'lucide-react';
import { TopologyCanvas } from '@/components/canvas/TopologyCanvas';
import { useEduStore } from '@/store/eduStore';
import { cn } from '@/lib/cn';
import { ActivityListPanel } from './ActivityListPanel';
import { ActivityAuthorPanel } from './ActivityAuthorPanel';
import { ActivityStudentPanel } from './ActivityStudentPanel';

export function EduWorkspace() {
  const mode = useEduStore((s) => s.mode);
  const loadActivities = useEduStore((s) => s.loadActivities);

  useEffect(() => {
    void loadActivities();
  }, [loadActivities]);

  return (
    <>
      <div className="absolute inset-0">
        <TopologyCanvas topLeftExtra={mode !== 'browse' ? <EduModeBar /> : undefined} />
      </div>

      {mode === 'browse' ? (
        <ActivityListPanel />
      ) : mode === 'author' ? (
        <ActivityAuthorPanel />
      ) : (
        <ActivityStudentPanel />
      )}
    </>
  );
}

/** Toolbar row: back-to-list, plus an Author|Student segmented switch for the
 *  selected activity (hidden while composing a brand-new, unsaved draft).
 *  Rendered as `TopologyCanvas`'s `topLeftExtra` (see EduWorkspace), so it
 *  stacks above the canvas's own overlay-chips/toolbar row instead of a
 *  second absolutely-positioned layer landing in the same corner. */
function EduModeBar() {
  const mode = useEduStore((s) => s.mode);
  const selectedId = useEduStore((s) => s.selectedId);
  const toBrowse = useEduStore((s) => s.toBrowse);
  const selectForAuthor = useEduStore((s) => s.selectForAuthor);
  const selectForStudent = useEduStore((s) => s.selectForStudent);

  return (
    <div className="flex items-center gap-2">
      <button
        onClick={toBrowse}
        aria-label="Back to activities"
        className="glass-strong inline-flex items-center gap-1.5 rounded-lg border border-fg/15 px-2.5 py-1.5 text-xs font-medium text-fg/80 shadow-glass hover:text-fg"
      >
        <ArrowLeft className="h-3.5 w-3.5" />
        <span className="hidden sm:inline">Activities</span>
      </button>

      {selectedId && (
        <div
          role="group"
          aria-label="Education mode"
          className="glass-strong flex items-center rounded-lg border border-fg/15 p-0.5 shadow-glass"
        >
          <SegButton active={mode === 'author'} onClick={() => selectForAuthor(selectedId)} icon={PenLine} label="Author" />
          <SegButton active={mode === 'student'} onClick={() => selectForStudent(selectedId)} icon={GraduationCap} label="Student" />
        </div>
      )}
    </div>
  );
}

function SegButton({
  active,
  onClick,
  icon: Icon,
  label,
}: {
  active: boolean;
  onClick: () => void;
  icon: typeof PenLine;
  label: string;
}) {
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      aria-label={label}
      className={cn(
        'flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs transition-colors',
        active ? 'bg-accent text-accent-fg' : 'text-fg/55 hover:text-fg/85',
      )}
    >
      <Icon className="h-3.5 w-3.5" />
      <span className="hidden sm:inline">{label}</span>
    </button>
  );
}
