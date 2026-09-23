export type DistributionMode =
  | 'native-offline'
  | 'native-google'
  | 'native-remote'
  | 'headless'
  | 'full-online';

export interface RuntimeProfile {
  mode: DistributionMode;
  remoteOrigin: string;
}

const STORAGE_KEY = 'netgeo.runtime-profile';
const DEFAULT_PROFILE: RuntimeProfile = { mode: 'native-offline', remoteOrigin: '' };

const MODES = new Set<DistributionMode>([
  'native-offline',
  'native-google',
  'native-remote',
  'headless',
  'full-online',
]);

export function normalizeRemoteOrigin(value: string): string | null {
  try {
    const url = new URL(value.trim());
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.origin : null;
  } catch {
    return null;
  }
}

export function readsRemoteBackend(mode: DistributionMode): boolean {
  return mode === 'native-remote' || mode === 'full-online';
}

export function readRuntimeProfile(): RuntimeProfile {
  try {
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? 'null') as Partial<RuntimeProfile> | null;
    const mode = stored?.mode && MODES.has(stored.mode) ? stored.mode : DEFAULT_PROFILE.mode;
    return { mode, remoteOrigin: normalizeRemoteOrigin(stored?.remoteOrigin ?? '') ?? '' };
  } catch {
    return DEFAULT_PROFILE;
  }
}

export function saveRuntimeProfile(profile: RuntimeProfile): void {
  localStorage.setItem(
    STORAGE_KEY,
    JSON.stringify({ ...profile, remoteOrigin: normalizeRemoteOrigin(profile.remoteOrigin) ?? '' }),
  );
}

export function runtimeApiBase(defaultBase: string | undefined): string {
  const profile = readRuntimeProfile();
  return readsRemoteBackend(profile.mode) && profile.remoteOrigin
    ? `${profile.remoteOrigin}/api`
    : (defaultBase ?? '/api');
}

export function runtimeWebSocketBase(defaultBase: string | undefined): string | undefined {
  const profile = readRuntimeProfile();
  if (readsRemoteBackend(profile.mode) && profile.remoteOrigin) {
    return profile.remoteOrigin.replace(/^http/, 'ws');
  }
  return defaultBase;
}
