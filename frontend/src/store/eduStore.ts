/**
 * Education store — orchestration for the Education Lab workspace (NG-EDU-01/02/03).
 *
 * Thin, like rfStore: it holds the activity list, the author draft, and the last
 * grade/submit responses — no client-side grading (that is entirely server-side in
 * `services/grading.py`). `instantiate` imports an activity's starting network as a
 * fresh project and points the app at it; the existing `['topology', projectId]`
 * query in App.tsx then repopulates the canvas for free (no extra plumbing).
 *
 * ponytail: no PATCH — the backend has no `PATCH /activities/{id}`, so "editing" is
 * delete + recreate. selectForAuthor(id) clones an existing activity into the draft
 * as a starting point; Save always POSTs a new one.
 */
import { create } from 'zustand';
import { educationApi, projectsApi } from '@/api/client';
import type {
  Activity,
  ActivityCreate,
  GradeCheck,
  GradeReport,
  GradeResult,
} from '@/api/types';
import { useUiStore } from '@/store/uiStore';
import { useAuthStore } from '@/store/authStore';

export type EduMode = 'browse' | 'author' | 'student';

function blankDraft(): ActivityCreate {
  return { name: '', instructions: '', checks: [], time_limit_s: null, initial: {}, answer: {} };
}

interface EduState {
  activities: Activity[];
  selectedId: string | null;
  mode: EduMode;
  draft: ActivityCreate;
  /** JSON snapshot of `draft` as of the last `selectForAuthor` call — the
   *  baseline `isDraftDirty` diffs against to warn before an unsaved draft
   *  is discarded (toBrowse). */
  draftBaseline: string;
  liveReport: GradeReport | null;
  lastResult: GradeResult | null;
  startedAt: number | null;
  submitted: boolean;
  loading: boolean;
  saving: boolean;
  error: string | null;

  loadActivities: () => Promise<void>;
  toBrowse: () => void;
  /** True while in author mode with edits not reflected in `draftBaseline`. */
  isDraftDirty: () => boolean;
  selectForAuthor: (id: string | null) => void;
  selectForStudent: (id: string) => void;
  updateDraft: (patch: Partial<ActivityCreate>) => void;
  addCheck: (c: GradeCheck) => void;
  updateCheck: (i: number, patch: Partial<GradeCheck>) => void;
  removeCheck: (i: number) => void;
  captureNetwork: (target: 'initial' | 'answer') => Promise<void>;
  saveActivity: () => Promise<Activity | null>;
  removeActivity: (id: string) => Promise<void>;
  instantiate: (id: string) => Promise<void>;
  checkWork: () => Promise<void>;
  submit: (student: string, elapsedS: number) => Promise<void>;
  exportActivity: (id: string) => Promise<void>;
  importActivity: (envelope: Record<string, unknown>) => Promise<void>;
}

function errMsg(err: unknown, fallback: string): string {
  const status = (err as { status?: number })?.status;
  if (status === 0) return 'Cannot reach the server. Check your connection.';
  return (err as { message?: string })?.message ?? fallback;
}

export const useEduStore = create<EduState>((set, get) => ({
  activities: [],
  selectedId: null,
  mode: 'browse',
  draft: blankDraft(),
  draftBaseline: JSON.stringify(blankDraft()),
  liveReport: null,
  lastResult: null,
  startedAt: null,
  submitted: false,
  loading: false,
  saving: false,
  error: null,

  loadActivities: async () => {
    set({ loading: true, error: null });
    try {
      set({ activities: await educationApi.list(), loading: false });
    } catch (err) {
      set({ loading: false, error: errMsg(err, 'Could not load activities.') });
    }
  },

  toBrowse: () => set({ mode: 'browse', selectedId: null, liveReport: null, lastResult: null }),

  isDraftDirty: () => {
    const s = get();
    return s.mode === 'author' && JSON.stringify(s.draft) !== s.draftBaseline;
  },

  selectForAuthor: (id) => {
    if (!id) {
      const draft = blankDraft();
      set({ mode: 'author', selectedId: null, draft, draftBaseline: JSON.stringify(draft), error: null });
      return;
    }
    const a = get().activities.find((x) => x.id === id);
    // Clone into the draft as a starting point (no PATCH; Save posts a new one).
    const draft: ActivityCreate = a
      ? {
          name: a.name,
          instructions: a.instructions,
          checks: a.checks.map((c) => ({ ...c })),
          time_limit_s: a.time_limit_s,
          initial: a.initial,
          answer: a.answer,
        }
      : blankDraft();
    set({ mode: 'author', selectedId: id, draft, draftBaseline: JSON.stringify(draft), error: null });
  },

  selectForStudent: (id) =>
    set({ mode: 'student', selectedId: id, liveReport: null, lastResult: null, error: null }),

  updateDraft: (patch) => set((s) => ({ draft: { ...s.draft, ...patch } })),
  addCheck: (c) => set((s) => ({ draft: { ...s.draft, checks: [...(s.draft.checks ?? []), c] } })),
  updateCheck: (i, patch) =>
    set((s) => ({
      draft: {
        ...s.draft,
        checks: (s.draft.checks ?? []).map((c, idx) => (idx === i ? { ...c, ...patch } : c)),
      },
    })),
  removeCheck: (i) =>
    set((s) => ({ draft: { ...s.draft, checks: (s.draft.checks ?? []).filter((_, idx) => idx !== i) } })),

  captureNetwork: async (target) => {
    const projectId = useUiStore.getState().projectId;
    if (!projectId) {
      set({ error: 'Open a project first — its current network is what gets captured.' });
      return;
    }
    set({ saving: true, error: null });
    try {
      const envelope = await projectsApi.archive(projectId);
      set((s) => ({ draft: { ...s.draft, [target]: envelope }, saving: false }));
    } catch (err) {
      set({ saving: false, error: errMsg(err, 'Could not capture the current network.') });
    }
  },

  saveActivity: async () => {
    const draft = get().draft;
    if (!draft.name.trim()) {
      set({ error: 'Give the activity a name before saving.' });
      return null;
    }
    set({ saving: true, error: null });
    try {
      const activity = await educationApi.create(draft);
      await get().loadActivities();
      set({ saving: false, mode: 'browse', selectedId: null, draft: blankDraft() });
      return activity;
    } catch (err) {
      set({ saving: false, error: errMsg(err, 'Could not save the activity.') });
      return null;
    }
  },

  removeActivity: async (id) => {
    try {
      await educationApi.remove(id);
      set((s) => ({
        activities: s.activities.filter((a) => a.id !== id),
        selectedId: s.selectedId === id ? null : s.selectedId,
      }));
    } catch (err) {
      set({ error: errMsg(err, 'Could not delete the activity.') });
    }
  },

  instantiate: async (id) => {
    set({ saving: true, error: null });
    try {
      const project = await educationApi.instantiate(id);
      useUiStore.getState().setProject(project.id);
      set({
        saving: false,
        mode: 'student',
        selectedId: id,
        startedAt: Date.now(),
        submitted: false,
        liveReport: null,
        lastResult: null,
      });
    } catch (err) {
      set({ saving: false, error: errMsg(err, 'Could not start the activity.') });
    }
  },

  checkWork: async () => {
    const { selectedId } = get();
    const projectId = useUiStore.getState().projectId;
    if (!selectedId || !projectId) return;
    set({ saving: true, error: null });
    try {
      const liveReport = await educationApi.grade(selectedId, projectId);
      set({ liveReport, saving: false });
    } catch (err) {
      set({ saving: false, error: errMsg(err, 'Grading failed. Try again.') });
    }
  },

  submit: async (student, elapsedS) => {
    const { selectedId, submitted } = get();
    const projectId = useUiStore.getState().projectId;
    if (!selectedId || !projectId || submitted) return;
    set({ saving: true, error: null });
    try {
      const lastResult = await educationApi.submit(selectedId, {
        project_id: projectId,
        student: student || useAuthStore.getState().username || 'student',
        elapsed_s: elapsedS,
      });
      // A submit is also an authoritative grade — surface it as the live report too.
      set({
        lastResult,
        submitted: true,
        saving: false,
        liveReport: {
          items: lastResult.items,
          score_pct: lastResult.score_pct,
          earned_weight: lastResult.earned_weight,
          total_weight: lastResult.total_weight,
        },
      });
    } catch (err) {
      set({ saving: false, error: errMsg(err, 'Submit failed. Try again.') });
    }
  },

  exportActivity: async (id) => {
    try {
      const envelope = await educationApi.export(id);
      const name = get().activities.find((a) => a.id === id)?.name ?? id;
      const blob = new Blob([JSON.stringify(envelope, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${name.replace(/[^\w.-]+/g, '_')}.netgeo-lab`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      set({ error: errMsg(err, 'Could not export the activity.') });
    }
  },

  importActivity: async (envelope) => {
    set({ saving: true, error: null });
    try {
      await educationApi.import(envelope);
      await get().loadActivities();
      set({ saving: false });
    } catch (err) {
      set({ saving: false, error: errMsg(err, 'That file is not a valid .netgeo-lab activity.') });
    }
  },
}));
