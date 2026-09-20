import { describe, expect, it } from 'vitest';
import { MAP_TILES, MAPS_API_PREFIX, OFFLINE_GLYPHS_URL, OFFLINE_TILE_PREFIX, resolveBaseTile, vectorBaseLayers } from './mapTiles';

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
  it('renders symbol/text layers for place, road and water names (OFFLINE-MAP-5)', () => {
    for (const theme of ['dark', 'light'] as const) {
      const layers = vectorBaseLayers(theme);
      const symbolLayers = layers.filter((l) => l.type === 'symbol');
      expect(symbolLayers.map((l) => l.sourceLayer).sort()).toEqual(
        ['place', 'transportation_name', 'water_name'].sort(),
      );
      for (const layer of symbolLayers) {
        expect(layer.layout).toHaveProperty('text-field');
        expect(layer.layout).toHaveProperty('text-font');
        expect(layer.paint).toHaveProperty('text-color');
        expect(layer.paint).toHaveProperty('text-halo-color');
      }
    }
  });

  it('restricts place labels to settlement classes only (no clutter from country/state/etc.)', () => {
    const place = vectorBaseLayers('dark').find((l) => l.sourceLayer === 'place')!;
    expect(place.filter).toEqual(['in', ['get', 'class'], ['literal', ['city', 'town', 'village']]]);
  });

  it('places road names along the line, not as points', () => {
    const road = vectorBaseLayers('dark').find((l) => l.sourceLayer === 'transportation_name')!;
    expect(road.layout?.['symbol-placement']).toBe('line');
  });

  it('covers the OpenMapTiles layers this basemap is expected to draw', () => {
    const ids = vectorBaseLayers('dark').map((l) => l.sourceLayer);
    for (const expected of [
      'water',
      'landcover',
      'landuse',
      'park',
      'building',
      'transportation',
      'boundary',
      'place',
      'transportation_name',
      'water_name',
    ]) {
      expect(ids).toContain(expected);
    }
  });

  it('picks different colors per theme', () => {
    const dark = vectorBaseLayers('dark').find((l) => l.id === 'water')!;
    const light = vectorBaseLayers('light').find((l) => l.id === 'water')!;
    expect(dark.paint['fill-color']).not.toBe(light.paint['fill-color']);

    const darkLabel = vectorBaseLayers('dark').find((l) => l.id === 'place-label')!;
    const lightLabel = vectorBaseLayers('light').find((l) => l.id === 'place-label')!;
    expect(darkLabel.paint['text-color']).not.toBe(lightLabel.paint['text-color']);
  });
});

describe('glyph URLs', () => {
  it('the glyph URL template sits under the same authed prefix as tiles', () => {
    expect(OFFLINE_GLYPHS_URL.startsWith(MAPS_API_PREFIX)).toBe(true);
    expect(OFFLINE_TILE_PREFIX.startsWith(MAPS_API_PREFIX)).toBe(true);
    expect(OFFLINE_GLYPHS_URL).toBe(`${MAPS_API_PREFIX}fonts/{fontstack}/{range}.pbf`);
  });
});
