/**
 * Slice 3 (outdoor placement): a node with `site_id` but no `rack_id` has
 * nowhere else in the UI to show or edit its `mount` — this is the panel
 * that does. Covers the two editable controls (mount type, height AGL) and
 * the "nothing to show" case.
 */
import { describe, expect, it, vi, afterEach } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { UnrackedDevicesPanel } from './UnrackedDevicesPanel';
import type { UnrackedNode } from '@/api/types';

const updateMock = vi.fn(async (_id: string, _patch: unknown) => ({}));
vi.mock('@/api/client', () => ({
  nodesApi: { update: (id: string, patch: unknown) => updateMock(id, patch) },
}));

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  updateMock.mockClear();
});

function mount(nodes: UnrackedNode[]) {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  const qc = new QueryClient();
  act(() => {
    root.render(
      <QueryClientProvider client={qc}>
        <UnrackedDevicesPanel projectId="p1" nodes={nodes} />
      </QueryClientProvider>,
    );
  });
}

describe('UnrackedDevicesPanel', () => {
  it('renders nothing when there are no unracked nodes for the site', () => {
    mount([]);
    expect(container.querySelector('[data-testid="unracked-devices-panel"]')).toBeNull();
  });

  it('lists an unracked node and lets its mount type be set', async () => {
    mount([{ id: 'n1', name: 'rru1', mount: null }]);
    const select = container.querySelector('select') as HTMLSelectElement;
    expect(select.value).toBe('');
    // height input starts disabled — nothing to set a height on yet.
    const heightInput = container.querySelector('input[type="number"]') as HTMLInputElement;
    expect(heightInput.disabled).toBe(true);

    const nativeSetter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!;
    await act(async () => {
      nativeSetter.call(select, 'pole');
      select.dispatchEvent(new Event('change', { bubbles: true }));
      await Promise.resolve();
    });

    expect(updateMock).toHaveBeenCalledWith('n1', { mount: { type: 'pole', height_agl_m: null } });
  });

  it('edits height_agl_m on an already-mounted node', async () => {
    mount([{ id: 'n2', name: 'rru2', mount: { type: 'wall', height_agl_m: 5 } }]);
    const heightInput = container.querySelector('input[type="number"]') as HTMLInputElement;
    expect(heightInput.disabled).toBe(false);
    expect(heightInput.value).toBe('5');

    const nativeSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
    await act(async () => {
      nativeSetter.call(heightInput, '9.5');
      heightInput.dispatchEvent(new Event('change', { bubbles: true }));
      await Promise.resolve();
    });

    expect(updateMock).toHaveBeenCalledWith('n2', { mount: { type: 'wall', height_agl_m: 9.5 } });
  });
});
