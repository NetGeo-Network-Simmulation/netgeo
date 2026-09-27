export type DistributionMode =
  | 'native-offline'
  | 'native-google'
  | 'native-remote'
  | 'headless'
  | 'full-online';

export interface RuntimeProfile {
  mode: DistributionMode;
  remoteOrigin: string;
  localOrigin?: string;
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
    return (url.protocol === 'http:' || url.protocol === 'https:') && url.hostname && url.port !== '0' && !url.username && !url.password
      ? url.origin : null;
  } catch {
    return null;
  }
}

export function normalizeLocalOrigin(value: string): string | null {
  const origin = normalizeRemoteOrigin(value);
  if (!origin) return null;
  const { hostname } = new URL(origin);
  return hostname === 'localhost' || hostname === '127.0.0.1' || hostname === '[::1]' ? origin : null;
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
  let response: Response;
  try {
    response = await fetch(`${normalized}/api/health`, { signal: AbortSignal.timeout(5000) });
  } catch (cause) {
    if (cause instanceof Error && cause.name === 'TimeoutError') {
      throw new Error('Server did not respond within 5 seconds. Check its address and network connection.');
    }
    throw new Error('Could not reach the server. Check its address, HTTPS compatibility, and whether it allows this app origin through CORS.');
  }
  if (response.status === 401 || response.status === 403) {
    throw new Error('Server denied the health check. Check its authentication or proxy configuration.');
  }
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
    const localOrigin = normalizeLocalOrigin(stored?.localOrigin ?? '') ?? '';
    return { mode: readsRemoteBackend(mode) && !remoteOrigin ? 'native-offline' : mode, remoteOrigin, localOrigin };
  } catch {
    return DEFAULT_PROFILE;
  }
}

export function saveRuntimeProfile(profile: RuntimeProfile): void {
  const remoteOrigin = normalizeRemoteOrigin(profile.remoteOrigin) ?? '';
  const localOrigin = profile.localOrigin?.trim() ? normalizeLocalOrigin(profile.localOrigin) : '';
  if (readsRemoteBackend(profile.mode) && !remoteOrigin) throw new Error('A valid remote server origin is required.');
  if (localOrigin === null) throw new Error('Local engine must use a loopback HTTP(S) address and a port from 1 to 65535.');
  localStorage.setItem(
    STORAGE_KEY,
    JSON.stringify({ ...profile, remoteOrigin, localOrigin }),
  );
}

export async function applyRuntimeProfile(profile: RuntimeProfile, reload: () => void): Promise<void> {
  const endpoint = readsRemoteBackend(profile.mode)
    ? normalizeRemoteOrigin(profile.remoteOrigin)
    : profile.mode === 'headless' && profile.localOrigin?.trim()
      ? normalizeLocalOrigin(profile.localOrigin)
      : null;
  if (endpoint) await checkRemoteEngine(endpoint);
  saveRuntimeProfile(profile);
  reload();
}

export function runtimeApiBase(): string {
  const origin = runtimeEngineOrigin();
  return origin ? `${origin}/api` : '/api';
}

export function runtimeWebSocketBase(): string | undefined {
  const origin = runtimeEngineOrigin();
  return origin ? origin.replace(/^http/, 'ws') : undefined;
}

export function runtimeEngineOrigin(): string | undefined {
  const profile = readRuntimeProfile();
  if (readsRemoteBackend(profile.mode)) return profile.remoteOrigin || undefined;
  return profile.mode === 'headless' ? profile.localOrigin || undefined : undefined;
}
