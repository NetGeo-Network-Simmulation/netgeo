import { beforeEach, describe, expect, it } from 'vitest';
import {
  normalizeRemoteOrigin,
  readRuntimeProfile,
  runtimeApiBase,
  runtimeWebSocketBase,
  saveRuntimeProfile,
} from './runtimeProfile';

beforeEach(() => localStorage.clear());

describe('runtime profile', () => {
  it('keeps local API and socket defaults for the offline profile', () => {
    expect(runtimeApiBase(undefined)).toBe('/api');
    expect(runtimeWebSocketBase(undefined)).toBeUndefined();
  });

  it('uses a normalized remote origin for REST and WebSocket connections', () => {
    saveRuntimeProfile({ mode: 'native-remote', remoteOrigin: 'https://netgeo.example.test/path' });

    expect(readRuntimeProfile()).toEqual({ mode: 'native-remote', remoteOrigin: 'https://netgeo.example.test' });
    expect(runtimeApiBase('/api')).toBe('https://netgeo.example.test/api');
    expect(runtimeWebSocketBase(undefined)).toBe('wss://netgeo.example.test');
  });

  it('rejects non-HTTP remote origins', () => {
    expect(normalizeRemoteOrigin('javascript:alert(1)')).toBeNull();
    saveRuntimeProfile({ mode: 'full-online', remoteOrigin: 'ftp://netgeo.example.test' });
    expect(runtimeApiBase('/api')).toBe('/api');
  });
});
