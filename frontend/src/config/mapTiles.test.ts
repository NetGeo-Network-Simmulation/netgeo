import { describe, expect, it } from 'vitest';
import { MAP_TILES, OFFLINE_TILE_PREFIX, resolveBaseTile, vectorBaseLayers } from './mapTiles';

describe('resolveBaseTile', () => {
  it('falls back to the online provider when no local map is installed', () => {
    expect(resolveBaseTile('satellite', { available: false, region: null, attribution: null })).toEqual({
      url: MAP_TILES.satellite.url,
      subdomains: undefined,
      maxZoom: MAP_TILES.satellite.maxZoom,
      attribution: MAP_TILES.satellite.attribution,
      offline: false,
      vector: false,
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

  it('is not vector for a raster (or formatless) installed file', () => {
    expect(resolveBaseTile('street', { available: true, region: 'X', attribution: 'X' }).vector).toBe(false);
    expect(
      resolveBaseTile('street', { available: true, region: 'X', attribution: 'X', format: 'png' }).vector,
    ).toBe(false);
  });

  it('is vector when the installed MBTiles reports format=pbf (OFFLINE-MAP-4)', () => {
    const cfg = resolveBaseTile('satellite', {
      available: true,
      region: 'Maluku',
      attribution: '© OpenMapTiles © OpenStreetMap contributors',
      format: 'pbf',
    });
    expect(cfg.vector).toBe(true);
    expect(cfg.url).toBe(`${OFFLINE_TILE_PREFIX}{z}/{x}/{y}`);
  });

  it('stays online (not vector) when nothing is installed, regardless of format noise', () => {
    expect(resolveBaseTile('street', { available: false, region: null, attribution: null, format: 'pbf' }).vector).toBe(
      false,
    );
  });
});

describe('vectorBaseLayers', () => {
  it('renders no symbol/text layers — offline has no glyph server to draw them with', () => {
    for (const theme of ['dark', 'light'] as const) {
      const layers = vectorBaseLayers(theme);
      expect(layers.length).toBeGreaterThan(0);
      for (const layer of layers) {
        expect(layer.type === 'fill' || layer.type === 'line').toBe(true);
        expect(layer.paint).not.toHaveProperty('text-field');
      }
    }
  });

  it('covers the OpenMapTiles layers this basemap is expected to draw', () => {
    const ids = vectorBaseLayers('dark').map((l) => l.sourceLayer);
    for (const expected of ['water', 'landcover', 'landuse', 'park', 'building', 'transportation', 'boundary']) {
      expect(ids).toContain(expected);
    }
  });

  it('picks different colors per theme', () => {
    const dark = vectorBaseLayers('dark').find((l) => l.id === 'water')!;
    const light = vectorBaseLayers('light').find((l) => l.id === 'water')!;
    expect(dark.paint['fill-color']).not.toBe(light.paint['fill-color']);
  });
});
