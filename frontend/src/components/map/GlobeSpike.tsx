/**
 * Stage 0 spike (docs/design/21-GLOBE-MAP-MIGRATION.md) — throwaway proof that
 * MapLibre GL JS renders an existing key-less raster provider (Esri satellite,
 * same config as `config/mapTiles.ts`) under globe projection, unmodified.
 * Not wired into the app; superseded by the Stage 1 basemap swap in MapView.tsx.
 */
import { useEffect, useRef } from 'react';
import { MapLibreMap } from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import { MAP_TILES } from '@/config/mapTiles';

export function GlobeSpike() {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const cfg = MAP_TILES.satellite;
    const map = new MapLibreMap({
      container: ref.current,
      style: {
        version: 8,
        sources: {
          esri: {
            type: 'raster',
            tiles: [cfg.url],
            tileSize: 256,
            maxzoom: cfg.maxZoom ?? 19,
            attribution: cfg.attribution,
          },
        },
        layers: [{ id: 'esri', type: 'raster', source: 'esri' }],
      },
      center: [106.8, -6.2],
      zoom: 2,
    });
    map.once('load', () => map.setProjection({ type: 'globe' }));
    return () => map.remove();
  }, []);

  return <div ref={ref} style={{ position: 'fixed', inset: 0 }} />;
}
