/**
 * D6 regression: the search-results dropdown must close on an outside
 * click, not just on picking a result or pressing Escape (QA
 * netgeo-qa-user-2026-07-26.md — the "selector doesn't auto-close" pattern).
 */
import { describe, expect, it, vi, afterEach } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MapSearch } from './MapSearch';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock('@/services/geocodeService', () => ({
  geocode: vi.fn(async () => [{ label: 'Balaraja, Tangerang', lat: -6.17, lng: 106.45 }]),
}));

let container: HTMLDivElement;
let root: Root;

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function mount() {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => root.render(<MapSearch />));
}

describe('MapSearch results dropdown', () => {
  it('closes on outside click', async () => {
    mount();

    const input = container.querySelector('input')!;
    // React tracks the input's previous value to dedupe native events, so a
    // plain `input.value = …` assignment is invisible to it — go through the
    // native setter, same trick React Testing Library uses under the hood.
    const nativeSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
    await act(async () => {
      nativeSetter.call(input, 'Balaraja');
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
      // let the mocked geocode() promise resolve
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(container.textContent).toContain('Balaraja, Tangerang');

    // Click somewhere outside the component (e.g. the map canvas behind it).
    await act(async () => {
      document.body.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
    });

    expect(container.textContent).not.toContain('Balaraja, Tangerang');
  });
});
