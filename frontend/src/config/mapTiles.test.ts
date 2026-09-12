import { describe, expect, it } from 'vitest';
import { MAP_TILES, OFFLINE_TILE_PREFIX, resolveBaseTile } from './mapTiles';

describe('resolveBaseTile', () => {
  it('falls back to the online provider when no local map is installed', () => {
    expect(resolveBaseTile('satellite', { available: false, region: null, attribution: null })).toEqual({
      url: MAP_TILES.satellite.url,
      subdomains: undefined,
      maxZoom: MAP_TILES.satellite.maxZoom,
      attribution: MAP_TILES.satellite.attribution,
      offline: false,
    });
  });

  it('falls back to online when the status query has not resolved yet', () => {
    expect(resolveBaseTile('street', undefined).offline).toBe(false);
    expect(resolveBaseTile('street', null).offline).toBe(false);
  });

  it('uses the local tile endpoint for either basemap key when a region is installed', () => {
    const status = { available: true, region: 'Palembang', attribution: '© OpenStreetMap contributors', max_zoom: 16 };
    for (const key of ['satellite', 'street'] as const) {
      const cfg = resolveBaseTile(key, status);
      expect(cfg.offline).toBe(true);
      expect(cfg.url.startsWith(OFFLINE_TILE_PREFIX)).toBe(true);
      expect(cfg.url).toBe(`${OFFLINE_TILE_PREFIX}{z}/{x}/{y}`);
      expect(cfg.attribution).toBe('© OpenStreetMap contributors');
      expect(cfg.maxZoom).toBe(16);
    }
  });

  it('never fabricates an attribution string when the installed file has none', () => {
    const cfg = resolveBaseTile('satellite', { available: true, region: null, attribution: null });
    expect(cfg.attribution).toBe('Offline map data');
  });

  it('falls back to the online maxZoom when the installed file reports none', () => {
    const cfg = resolveBaseTile('street', { available: true, region: 'X', attribution: 'X', max_zoom: null });
    expect(cfg.maxZoom).toBe(MAP_TILES.street.maxZoom);
  });
});
