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
    return (url.protocol === 'http:' || url.protocol === 'https:') && url.hostname && !url.username && !url.password
      ? url.origin : null;
  } catch {
    return null;
  }
}

export function readsRemoteBackend(mode: DistributionMode): boolean {
  return mode === 'native-remote' || mode === 'full-online';
}

export function modeForEngine(mode: DistributionMode, remote: boolean): DistributionMode {
  if (remote) return mode === 'full-online' ? 'full-online' : 'native-remote';
  return mode === 'headless' ? 'headless' : 'native-offline';
}

export async function checkRemoteEngine(origin: string): Promise<string> {
  const normalized = normalizeRemoteOrigin(origin);
  if (!normalized) throw new Error('Enter a valid http:// or https:// server origin.');
  const response = await fetch(`${normalized}/api/health`, { signal: AbortSignal.timeout(5000) });
  if (!response.ok) throw new Error(`Server health check failed (${response.status}).`);
  const health = await response.json() as { status?: string; app?: string; version?: string };
  if (health.status !== 'ok' || health.app !== 'NetGeo') {
    throw new Error('This server did not identify itself as a healthy NetGeo backend.');
  }
  return health.version ?? 'unknown version';
}

export function readRuntimeProfile(): RuntimeProfile {
  try {
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? 'null') as Partial<RuntimeProfile> | null;
    const mode = stored?.mode && MODES.has(stored.mode) ? stored.mode : DEFAULT_PROFILE.mode;
    const remoteOrigin = normalizeRemoteOrigin(stored?.remoteOrigin ?? '') ?? '';
    return { mode: readsRemoteBackend(mode) && !remoteOrigin ? 'native-offline' : mode, remoteOrigin };
  } catch {
    return DEFAULT_PROFILE;
  }
}

export function saveRuntimeProfile(profile: RuntimeProfile): void {
  const remoteOrigin = normalizeRemoteOrigin(profile.remoteOrigin) ?? '';
  if (readsRemoteBackend(profile.mode) && !remoteOrigin) throw new Error('A valid remote server origin is required.');
  localStorage.setItem(
    STORAGE_KEY,
    JSON.stringify({ ...profile, remoteOrigin }),
  );
}

export function applyRuntimeProfile(profile: RuntimeProfile, reload: () => void): void {
  saveRuntimeProfile(profile);
  reload();
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
