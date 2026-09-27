import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  applyRuntimeProfile,
  checkRemoteEngine,
  modeForEngine,
  normalizeLocalOrigin,
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
    saveRuntimeProfile({ mode: 'native-remote', remoteOrigin: 'https://netgeo.example.test:8443/path' });

    expect(readRuntimeProfile()).toEqual({ mode: 'native-remote', remoteOrigin: 'https://netgeo.example.test:8443', localOrigin: '' });
    expect(runtimeApiBase()).toBe('https://netgeo.example.test:8443/api');
    expect(runtimeWebSocketBase()).toBe('wss://netgeo.example.test:8443');
    expect(modeForEngine('native-offline', true)).toBe('native-remote');
    expect(modeForEngine('full-online', true)).toBe('full-online');
    expect(runtimeApiBase()).toBe('https://netgeo.example.test:8443/api');
  });

  it('uses an optional loopback endpoint only for headless clients', () => {
    saveRuntimeProfile({ mode: 'headless', remoteOrigin: '', localOrigin: 'http://[::1]:8001/api' });
    expect(readRuntimeProfile().localOrigin).toBe('http://[::1]:8001');
    expect(runtimeApiBase()).toBe('http://[::1]:8001/api');
    expect(runtimeWebSocketBase()).toBe('ws://[::1]:8001');

    saveRuntimeProfile({ mode: 'native-offline', remoteOrigin: 'https://remote.test:8443', localOrigin: 'http://localhost:8000' });
    expect(runtimeApiBase()).toBe('/api');
    expect(runtimeWebSocketBase()).toBeUndefined();
    saveRuntimeProfile({ mode: 'headless', remoteOrigin: '', localOrigin: '' });
    expect(runtimeApiBase()).toBe('/api');
    expect(runtimeWebSocketBase()).toBeUndefined();
  });

  it('rejects non-HTTP remote origins', () => {
    expect(normalizeRemoteOrigin('javascript:alert(1)')).toBeNull();
    expect(normalizeRemoteOrigin('https://user:secret@netgeo.example.test')).toBeNull();
    expect(normalizeRemoteOrigin('http://netgeo.example.test:0')).toBeNull();
    expect(normalizeRemoteOrigin('http://netgeo.example.test:65536')).toBeNull();
    expect(normalizeLocalOrigin('http://localhost:1234')).toBe('http://localhost:1234');
    expect(normalizeLocalOrigin('http://127.0.0.1:65535')).toBe('http://127.0.0.1:65535');
    expect(normalizeLocalOrigin('http://[::1]:8000')).toBe('http://[::1]:8000');
    expect(normalizeLocalOrigin('http://example.test:8000')).toBeNull();
    expect(normalizeLocalOrigin('http://127.0.0.2:8000')).toBeNull();
    expect(normalizeLocalOrigin('http://user:secret@localhost:8000')).toBeNull();
    expect(() => saveRuntimeProfile({ mode: 'headless', remoteOrigin: '', localOrigin: 'http://example.test:8000' })).toThrow('loopback');
    expect(() => saveRuntimeProfile({ mode: 'full-online', remoteOrigin: 'ftp://netgeo.example.test' })).toThrow();
    expect(runtimeApiBase()).toBe('/api');
    localStorage.setItem('netgeo.runtime-profile', JSON.stringify({ mode: 'native-remote', remoteOrigin: 'ftp://netgeo.example.test' }));
    expect(readRuntimeProfile().mode).toBe('native-offline');
    localStorage.setItem('netgeo.runtime-profile', JSON.stringify({ mode: 'headless', localOrigin: 'http://example.test:8000' }));
    expect(runtimeApiBase()).toBe('/api');
  });

  it('checks a remote backend before applying and does not persist failures', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      json: async () => ({ status: 'ok', app: 'NetGeo', version: '1.2.126' }),
    } as Response);
    expect(await checkRemoteEngine('https://netgeo.example.test/path')).toBe('1.2.126');
    expect(fetchMock).toHaveBeenCalledWith('https://netgeo.example.test/api/health', expect.any(Object));
    expect(localStorage.length).toBe(0);
    expect(await checkRemoteEngine('http://localhost:8000')).toBe('1.2.126');
    expect(fetchMock).toHaveBeenLastCalledWith('http://localhost:8000/api/health', expect.any(Object));
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ status: 'ok', app: 'Other' }) } as Response);
    await expect(checkRemoteEngine('https://netgeo.example.test')).rejects.toThrow('healthy NetGeo');
    fetchMock.mockResolvedValue({ ok: false, status: 401 } as Response);
    await expect(checkRemoteEngine('https://netgeo.example.test')).rejects.toThrow('authentication');
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'));
    await expect(checkRemoteEngine('https://netgeo.example.test')).rejects.toThrow('CORS');
    fetchMock.mockRestore();
  });

  it('health-checks configured endpoints before persistence and reload', async () => {
    const reload = vi.fn();
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      json: async () => ({ status: 'ok', app: 'NetGeo' }),
    } as Response);
    await applyRuntimeProfile({ mode: 'native-remote', remoteOrigin: 'https://netgeo.example.test/path' }, reload);
    expect(readRuntimeProfile().remoteOrigin).toBe('https://netgeo.example.test');
    expect(reload).toHaveBeenCalledOnce();
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'));
    await expect(applyRuntimeProfile({ mode: 'headless', remoteOrigin: '', localOrigin: 'http://localhost:8000' }, reload)).rejects.toThrow('CORS');
    expect(readRuntimeProfile().mode).toBe('native-remote');
    await expect(applyRuntimeProfile({ mode: 'full-online', remoteOrigin: 'ftp://bad.test' }, reload)).rejects.toThrow();
    expect(reload).toHaveBeenCalledOnce();
    fetchMock.mockRestore();
  });
});
