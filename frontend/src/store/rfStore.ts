/**
 * RF store — state for the RF Planning workspace: PtP (link budget), PtMP
 * (sector planner, NG-RF-04), and auto product-select (NG-RF-05). Endpoints/AP
 * sites are references into the map store's placed devices; all three modes
 * are stateless POSTs against the backend registry — this store only
 * orchestrates requests and holds responses. `mode` picks which section
 * `RfAnalysisPanel` renders; each mode keeps its own request/result slice so
 * switching modes never clobbers another mode's in-progress work.
 *
 * Study persistence (`/api/rf/studies`, save-on-demand): PtMP and
 * product-select can snapshot their last request+result and reopen it later
 * verbatim (no recompute) — see `saveStudy`/`openStudy`.
 */
import { create } from 'zustand';
import {
  rfApi,
  type PropagationModelInfo,
  type PtpResult,
  type PtmpCpe,
  type PtmpRequest,
  type PtmpResult,
  type RadioCandidate,
  type RadioCatalogEntry,
  type ProductSelectRequest,
  type ProductSelectResult,
  type RfStudy,
  type RfStudyKind,
} from '@/api/client';
import { useMapStore } from '@/store/mapStore';
import { useUiStore } from '@/store/uiStore';

/** Fixed dish gain applied to both ends of the link budget (dBi). The map
 *  device model carries no antenna-gain field, so PtP uses one sensible default
 *  (a typical 5 GHz dish) for TX and RX. Surfaced in the panel as "2× N dBi". */
export const RF_ANT_GAIN_DBI = 23;

/** RX sensitivity floor used for fade margin (matches PtpRequest default). */
const RF_RX_SENSITIVITY_DBM = -85;

export type RfTab = 'summary' | 'budget' | 'terrain' | 'fresnel';
export type RfMode = 'ptp' | 'ptmp' | 'product_select';

/** One CPE row in the PtMP builder. Always resolved to lat/lon client-side
 *  (from a placed device or typed manually) — the backend also accepts
 *  distance/bearing, but that variant isn't exposed in this UI (ponytail: one
 *  input shape is enough; add the other if a workflow needs it). */
export interface PtmpCpeInput {
  id: string;
  name: string;
  lat: number;
  lon: number;
  heightM: number;
}

const uid = () => Math.random().toString(36).slice(2, 10);

interface RfState {
  mode: RfMode;
  setMode: (mode: RfMode) => void;

  aId: string | null;
  bId: string | null;
  freqGhz: number;
  bwMhz: number;
  modelId: string;
  models: PropagationModelInfo[];
  result: PtpResult | null;
  loading: boolean;
  error: string | null;
  tab: RfTab;

  /** Assign a device (from a map click) to the next free endpoint slot. */
  pickEndpoint: (id: string) => void;
  setA: (id: string | null) => void;
  setB: (id: string | null) => void;
  swap: () => void;
  clear: () => void;
  setFreq: (ghz: number) => void;
  setBw: (mhz: number) => void;
  setModel: (id: string) => void;
  setTab: (tab: RfTab) => void;
  loadModels: () => Promise<void>;
  calculate: () => Promise<void>;

  /** Static radio catalog (see RadioCatalogEntry) — prefill only, optional. */
  radios: RadioCatalogEntry[];
  loadRadios: () => Promise<void>;

  /* ---- PtMP sector planner (NG-RF-04) ---- */
  ptmpApId: string | null;
  ptmpAzimuthDeg: number;
  ptmpBeamwidthDeg: number;
  ptmpDowntiltDeg: number;
  ptmpFreqMhz: number;
  ptmpBandwidthMhz: number;
  ptmpTxPowerDbm: number;
  ptmpTxGainDbi: number;
  ptmpRxSensitivityDbm: number;
  ptmpModelId: string;
  ptmpCpes: PtmpCpeInput[];
  ptmpResult: PtmpResult | null;
  ptmpLastRequest: PtmpRequest | null;
  ptmpLoading: boolean;
  ptmpError: string | null;
  setPtmpApId: (id: string | null) => void;
  setPtmpAzimuth: (v: number) => void;
  setPtmpBeamwidth: (v: number) => void;
  setPtmpDowntilt: (v: number) => void;
  setPtmpFreqMhz: (v: number) => void;
  setPtmpBandwidthMhz: (v: number) => void;
  setPtmpTxPower: (v: number) => void;
  setPtmpTxGain: (v: number) => void;
  setPtmpRxSens: (v: number) => void;
  setPtmpModel: (id: string) => void;
  addCpeFromDevice: (deviceId: string) => void;
  addManualCpe: (lat: number, lon: number) => void;
  removeCpe: (id: string) => void;
  calculatePtmp: () => Promise<void>;

  /* ---- Auto product-select (NG-RF-05) ---- */
  psDistanceM: number;
  psFreqMhz: number;
  psTargetMbps: number;
  psModelId: string;
  psTxHeightM: number;
  psRxHeightM: number;
  psMiscLossDb: number;
  psCandidates: RadioCandidate[];
  psResult: ProductSelectResult | null;
  psLastRequest: ProductSelectRequest | null;
  psLoading: boolean;
  psError: string | null;
  setPsDistance: (v: number) => void;
  setPsFreq: (v: number) => void;
  setPsTarget: (v: number) => void;
  setPsTxHeight: (v: number) => void;
  setPsRxHeight: (v: number) => void;
  setPsMiscLoss: (v: number) => void;
  setPsModel: (id: string) => void;
  addCandidate: () => void;
  updateCandidate: (index: number, patch: Partial<RadioCandidate>) => void;
  removeCandidate: (index: number) => void;
  fillFromPtp: () => void;
  calculateProductSelect: () => Promise<void>;

  /* ---- Study persistence (/api/rf/studies) ---- */
  studies: RfStudy[];
  studyBusy: boolean;
  studyError: string | null;
  loadStudies: () => Promise<void>;
  saveStudy: (
    kind: RfStudyKind,
    name: string | undefined,
    request: Record<string, unknown>,
    result: Record<string, unknown>,
  ) => Promise<void>;
  openStudy: (id: string) => Promise<void>;
  deleteStudy: (id: string) => Promise<void>;
}

const RF_KINDS = new Set(['ap', 'tower']);

export const useRfStore = create<RfState>((set, get) => ({
  mode: 'ptp',
  setMode: (mode) => set({ mode }),

  aId: null,
  bId: null,
  freqGhz: 5.8,
  bwMhz: 40,
  modelId: 'fspl',
  models: [],
  result: null,
  loading: false,
  error: null,
  tab: 'summary',

  pickEndpoint: (id) => {
    const dev = useMapStore.getState().devices.get(id);
    if (!dev || !RF_KINDS.has(dev.kind)) return; // only AP/Tower are RF endpoints
    const { aId, bId } = get();
    if (!aId) set({ aId: id, result: null, error: null });
    else if (aId === id) return;
    else if (!bId) set({ bId: id, result: null, error: null });
    else set({ aId: id, bId: null, result: null, error: null }); // full → restart
  },

  setA: (aId) => set({ aId, result: null, error: null }),
  setB: (bId) => set({ bId, result: null, error: null }),
  swap: () => set((s) => ({ aId: s.bId, bId: s.aId, result: null, error: null })),
  clear: () => set({ aId: null, bId: null, result: null, error: null }),
  setFreq: (freqGhz) => set({ freqGhz, result: null }),
  setBw: (bwMhz) => set({ bwMhz }), // capacity-only — recomputed live, no refetch
  setModel: (modelId) => set({ modelId, result: null }),
  setTab: (tab) => set({ tab }),

  loadModels: async () => {
    if (get().models.length) return;
    try {
      const models = await rfApi.models();
      set({ models });
      if (models[0] && !models.some((m) => m.id === get().modelId)) {
        set({ modelId: models[0].id });
      }
    } catch {
      /* keep the 'fspl' default if the registry can't be reached */
    }
  },

  radios: [],
  loadRadios: async () => {
    if (get().radios.length) return;
    try {
      const radios = await rfApi.radios();
      set({ radios });
    } catch {
      /* catalog prefill is optional — manual candidate entry still works */
    }
  },

  calculate: async () => {
    const { aId, bId, freqGhz, modelId } = get();
    const devices = useMapStore.getState().devices;
    const a = aId ? devices.get(aId) : null;
    const b = bId ? devices.get(bId) : null;
    if (!a || !b) return;
    set({ loading: true, error: null });
    try {
      const result = await rfApi.ptp({
        a_lat: a.lat, a_lon: a.lng,
        b_lat: b.lat, b_lon: b.lng,
        freq_mhz: freqGhz * 1000,
        tx_power_dbm: a.txPower,
        tx_gain_dbi: RF_ANT_GAIN_DBI,
        rx_gain_dbi: RF_ANT_GAIN_DBI,
        rx_sensitivity_dbm: RF_RX_SENSITIVITY_DBM,
        tx_height_m: Math.max(a.antennaHeight, 0.1), // schema requires > 0
        rx_height_m: Math.max(b.antennaHeight, 0.1),
        model_id: modelId,
      });
      set({ result, loading: false });
    } catch (err) {
      const status = (err as { status?: number })?.status;
      const msg =
        status === 503
          ? 'Elevation provider unavailable — try again shortly.'
          : (err as { message?: string })?.message ?? 'Link calculation failed.';
      set({ loading: false, error: msg, result: null });
    }
  },

  /* ---- PtMP sector planner (NG-RF-04) ---- */
  ptmpApId: null,
  ptmpAzimuthDeg: 0,
  ptmpBeamwidthDeg: 60,
  ptmpDowntiltDeg: 0,
  ptmpFreqMhz: 5800,
  ptmpBandwidthMhz: 20,
  ptmpTxPowerDbm: 23,
  ptmpTxGainDbi: 16,
  ptmpRxSensitivityDbm: -85,
  ptmpModelId: 'fspl',
  ptmpCpes: [],
  ptmpResult: null,
  ptmpLastRequest: null,
  ptmpLoading: false,
  ptmpError: null,

  setPtmpApId: (ptmpApId) => set({ ptmpApId, ptmpResult: null, ptmpError: null }),
  setPtmpAzimuth: (ptmpAzimuthDeg) => set({ ptmpAzimuthDeg, ptmpResult: null }),
  setPtmpBeamwidth: (ptmpBeamwidthDeg) => set({ ptmpBeamwidthDeg, ptmpResult: null }),
  setPtmpDowntilt: (ptmpDowntiltDeg) => set({ ptmpDowntiltDeg }),
  setPtmpFreqMhz: (ptmpFreqMhz) => set({ ptmpFreqMhz, ptmpResult: null }),
  setPtmpBandwidthMhz: (ptmpBandwidthMhz) => set({ ptmpBandwidthMhz, ptmpResult: null }),
  setPtmpTxPower: (ptmpTxPowerDbm) => set({ ptmpTxPowerDbm, ptmpResult: null }),
  setPtmpTxGain: (ptmpTxGainDbi) => set({ ptmpTxGainDbi, ptmpResult: null }),
  setPtmpRxSens: (ptmpRxSensitivityDbm) => set({ ptmpRxSensitivityDbm, ptmpResult: null }),
  setPtmpModel: (ptmpModelId) => set({ ptmpModelId, ptmpResult: null }),

  addCpeFromDevice: (deviceId) => {
    const dev = useMapStore.getState().devices.get(deviceId);
    if (!dev) return;
    const cpe: PtmpCpeInput = { id: dev.id, name: dev.name, lat: dev.lat, lon: dev.lng, heightM: Math.max(dev.antennaHeight, 0.1) };
    set((s) => (s.ptmpCpes.some((c) => c.id === cpe.id) ? s : { ptmpCpes: [...s.ptmpCpes, cpe], ptmpResult: null }));
  },
  addManualCpe: (lat, lon) => {
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;
    const cpe: PtmpCpeInput = { id: `manual-${uid()}`, name: `Manual (${lat.toFixed(3)}, ${lon.toFixed(3)})`, lat, lon, heightM: 5 };
    set((s) => ({ ptmpCpes: [...s.ptmpCpes, cpe], ptmpResult: null }));
  },
  removeCpe: (id) => set((s) => ({ ptmpCpes: s.ptmpCpes.filter((c) => c.id !== id), ptmpResult: null })),

  calculatePtmp: async () => {
    const s = get();
    const ap = s.ptmpApId ? useMapStore.getState().devices.get(s.ptmpApId) : null;
    if (!ap || s.ptmpCpes.length === 0) return;
    const body: PtmpRequest = {
      lat: ap.lat,
      lon: ap.lng,
      height_m: Math.max(ap.antennaHeight, 0.1),
      azimuth_deg: s.ptmpAzimuthDeg,
      beamwidth_deg: s.ptmpBeamwidthDeg,
      downtilt_deg: s.ptmpDowntiltDeg,
      freq_mhz: s.ptmpFreqMhz,
      bandwidth_mhz: s.ptmpBandwidthMhz,
      tx_power_dbm: s.ptmpTxPowerDbm,
      tx_gain_dbi: s.ptmpTxGainDbi,
      rx_sensitivity_dbm: s.ptmpRxSensitivityDbm,
      model_id: s.ptmpModelId,
      cpes: s.ptmpCpes.map((c): PtmpCpe => ({ id: c.id, lat: c.lat, lon: c.lon, height_m: c.heightM })),
    };
    set({ ptmpLoading: true, ptmpError: null });
    try {
      const result = await rfApi.ptmp(body);
      set({ ptmpResult: result, ptmpLastRequest: body, ptmpLoading: false });
    } catch (err) {
      set({ ptmpLoading: false, ptmpError: (err as { message?: string })?.message ?? 'Sector calculation failed.', ptmpResult: null });
    }
  },

  /* ---- Auto product-select (NG-RF-05) ---- */
  psDistanceM: 1000,
  psFreqMhz: 5800,
  psTargetMbps: 100,
  psModelId: 'fspl',
  psTxHeightM: 30,
  psRxHeightM: 5,
  psMiscLossDb: 0,
  psCandidates: [
    { name: 'Radio A', tx_power_dbm: 23, antenna_gain_dbi: 16, rx_sensitivity_dbm: -85, cost: 1000, bandwidth_mhz: 20 },
  ],
  psResult: null,
  psLastRequest: null,
  psLoading: false,
  psError: null,

  setPsDistance: (psDistanceM) => set({ psDistanceM, psResult: null }),
  setPsFreq: (psFreqMhz) => set({ psFreqMhz, psResult: null }),
  setPsTarget: (psTargetMbps) => set({ psTargetMbps, psResult: null }),
  setPsTxHeight: (psTxHeightM) => set({ psTxHeightM, psResult: null }),
  setPsRxHeight: (psRxHeightM) => set({ psRxHeightM, psResult: null }),
  setPsMiscLoss: (psMiscLossDb) => set({ psMiscLossDb, psResult: null }),
  setPsModel: (psModelId) => set({ psModelId, psResult: null }),

  addCandidate: () =>
    set((s) => ({
      psCandidates: [
        ...s.psCandidates,
        { name: `Radio ${String.fromCharCode(65 + s.psCandidates.length)}`, tx_power_dbm: 23, antenna_gain_dbi: 16, rx_sensitivity_dbm: -85, cost: 1000, bandwidth_mhz: 20 },
      ],
      psResult: null,
    })),
  updateCandidate: (index, patch) =>
    set((s) => ({
      psCandidates: s.psCandidates.map((c, i) => (i === index ? { ...c, ...patch } : c)),
      psResult: null,
    })),
  removeCandidate: (index) =>
    set((s) => ({ psCandidates: s.psCandidates.filter((_, i) => i !== index), psResult: null })),

  fillFromPtp: () => {
    const { result, freqGhz } = get();
    if (!result) return;
    set({ psDistanceM: result.distance_m, psFreqMhz: freqGhz * 1000, psResult: null });
  },

  calculateProductSelect: async () => {
    const s = get();
    if (s.psCandidates.length === 0) return;
    const body: ProductSelectRequest = {
      distance_m: s.psDistanceM,
      freq_mhz: s.psFreqMhz,
      target_throughput_mbps: s.psTargetMbps,
      model_id: s.psModelId,
      tx_height_m: s.psTxHeightM,
      rx_height_m: s.psRxHeightM,
      misc_loss_db: s.psMiscLossDb,
      candidates: s.psCandidates,
    };
    set({ psLoading: true, psError: null });
    try {
      const result = await rfApi.productSelect(body);
      set({ psResult: result, psLastRequest: body, psLoading: false });
    } catch (err) {
      set({ psLoading: false, psError: (err as { message?: string })?.message ?? 'Product selection failed.', psResult: null });
    }
  },

  /* ---- Study persistence (/api/rf/studies) ---- */
  studies: [],
  studyBusy: false,
  studyError: null,

  loadStudies: async () => {
    const projectId = useUiStore.getState().projectId;
    if (!projectId) return;
    try {
      const studies = await rfApi.studies.list(projectId);
      set({ studies });
    } catch (err) {
      set({ studyError: (err as { message?: string })?.message ?? 'Failed to load saved studies.' });
    }
  },

  saveStudy: async (kind, name, request, result) => {
    const projectId = useUiStore.getState().projectId;
    if (!projectId) return;
    set({ studyBusy: true, studyError: null });
    try {
      const study = await rfApi.studies.create({ project_id: projectId, kind, name: name ?? '', request, result });
      set((s) => ({ studies: [study, ...s.studies], studyBusy: false }));
    } catch (err) {
      set({ studyBusy: false, studyError: (err as { message?: string })?.message ?? 'Failed to save study.' });
    }
  },

  openStudy: async (id) => {
    set({ studyBusy: true, studyError: null });
    try {
      const study = await rfApi.studies.get(id);
      if (study.kind === 'ptmp') {
        const req = study.request as unknown as PtmpRequest;
        const res = study.result as unknown as PtmpResult;
        set({
          mode: 'ptmp',
          ptmpAzimuthDeg: req.azimuth_deg,
          ptmpBeamwidthDeg: req.beamwidth_deg,
          ptmpDowntiltDeg: req.downtilt_deg ?? 0,
          ptmpFreqMhz: req.freq_mhz,
          ptmpBandwidthMhz: req.bandwidth_mhz ?? 20,
          ptmpTxPowerDbm: req.tx_power_dbm ?? 23,
          ptmpTxGainDbi: req.tx_gain_dbi ?? 16,
          ptmpRxSensitivityDbm: req.rx_sensitivity_dbm ?? -85,
          ptmpModelId: req.model_id ?? 'fspl',
          ptmpCpes: req.cpes.map((c) => ({
            id: c.id,
            name: c.id,
            lat: c.lat ?? 0,
            lon: c.lon ?? 0,
            heightM: c.height_m ?? 5,
          })),
          ptmpResult: res,
          ptmpLastRequest: req,
          studyBusy: false,
        });
      } else if (study.kind === 'product_select') {
        const req = study.request as unknown as ProductSelectRequest;
        const res = study.result as unknown as ProductSelectResult;
        set({
          mode: 'product_select',
          psDistanceM: req.distance_m,
          psFreqMhz: req.freq_mhz,
          psTargetMbps: req.target_throughput_mbps,
          psModelId: req.model_id ?? 'fspl',
          psTxHeightM: req.tx_height_m ?? 30,
          psRxHeightM: req.rx_height_m ?? 5,
          psMiscLossDb: req.misc_loss_db ?? 0,
          psCandidates: req.candidates,
          psResult: res,
          psLastRequest: req,
          studyBusy: false,
        });
      } else {
        set({ studyBusy: false }); // coverage/ptp studies aren't reopened from this panel
      }
    } catch (err) {
      set({ studyBusy: false, studyError: (err as { message?: string })?.message ?? 'Failed to open study.' });
    }
  },

  deleteStudy: async (id) => {
    set({ studyBusy: true, studyError: null });
    try {
      await rfApi.studies.remove(id);
      set((s) => ({ studies: s.studies.filter((st) => st.id !== id), studyBusy: false }));
    } catch (err) {
      set({ studyBusy: false, studyError: (err as { message?: string })?.message ?? 'Failed to delete study.' });
    }
  },
}));
