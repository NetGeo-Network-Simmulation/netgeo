import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  applyRuntimeProfile,
  checkRemoteEngine,
  modeForEngine,
  normalizeRemoteOrigin,
  readRuntimeProfile,
  runtimeApiBase,
  runtimeWebSocketBase,
  saveRuntimeProfile,
} from './runtimeProfile';

beforeEach(() => localStorage.clear());

describe('runtime profile', () => {
  it('keeps local API and socket defaults for the offline profile', () => {
    expect(runtimeApiBase()).toBe('/api');
    expect(runtimeWebSocketBase()).toBeUndefined();
    expect(modeForEngine('native-remote', false)).toBe('native-offline');
    expect(modeForEngine('headless', false)).toBe('headless');
  });

  it('uses a normalized remote origin for REST and WebSocket connections', () => {
    saveRuntimeProfile({ mode: 'native-remote', remoteOrigin: 'https://netgeo.example.test/path' });

    expect(readRuntimeProfile()).toEqual({ mode: 'native-remote', remoteOrigin: 'https://netgeo.example.test' });
    expect(runtimeApiBase()).toBe('https://netgeo.example.test/api');
    expect(runtimeWebSocketBase()).toBe('wss://netgeo.example.test');
    expect(modeForEngine('native-offline', true)).toBe('native-remote');
    expect(modeForEngine('full-online', true)).toBe('full-online');
    expect(runtimeApiBase()).toBe('https://netgeo.example.test/api');
  });

  it('rejects non-HTTP remote origins', () => {
    expect(normalizeRemoteOrigin('javascript:alert(1)')).toBeNull();
    expect(normalizeRemoteOrigin('https://user:secret@netgeo.example.test')).toBeNull();
    expect(() => saveRuntimeProfile({ mode: 'full-online', remoteOrigin: 'ftp://netgeo.example.test' })).toThrow();
    expect(runtimeApiBase()).toBe('/api');
    localStorage.setItem('netgeo.runtime-profile', JSON.stringify({ mode: 'native-remote', remoteOrigin: 'ftp://netgeo.example.test' }));
    expect(readRuntimeProfile().mode).toBe('native-offline');
  });

  it('checks a remote backend before applying and does not persist failures', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      json: async () => ({ status: 'ok', app: 'NetGeo', version: '1.2.126' }),
    } as Response);
    expect(await checkRemoteEngine('https://netgeo.example.test/path')).toBe('1.2.126');
    expect(fetchMock).toHaveBeenCalledWith('https://netgeo.example.test/api/health', expect.any(Object));
    expect(localStorage.length).toBe(0);
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ status: 'ok', app: 'Other' }) } as Response);
    await expect(checkRemoteEngine('https://netgeo.example.test')).rejects.toThrow('healthy NetGeo');
    fetchMock.mockResolvedValue({ ok: false, status: 401 } as Response);
    await expect(checkRemoteEngine('https://netgeo.example.test')).rejects.toThrow('authentication');
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'));
    await expect(checkRemoteEngine('https://netgeo.example.test')).rejects.toThrow('CORS');
    fetchMock.mockRestore();
  });

  it('persists the selected engine before reconnecting and never reloads invalid input', () => {
    const reload = vi.fn();
    applyRuntimeProfile({ mode: 'native-remote', remoteOrigin: 'https://netgeo.example.test/path' }, reload);
    expect(readRuntimeProfile().remoteOrigin).toBe('https://netgeo.example.test');
    expect(reload).toHaveBeenCalledOnce();
    expect(() => applyRuntimeProfile({ mode: 'full-online', remoteOrigin: 'ftp://bad.test' }, reload)).toThrow();
    expect(reload).toHaveBeenCalledOnce();
  });
});
