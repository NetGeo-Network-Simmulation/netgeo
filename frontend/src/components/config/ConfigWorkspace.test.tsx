import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ConfigWorkspace } from './ConfigWorkspace';
import { useUiStore } from '@/store/uiStore';
import { useTopologyStore } from '@/store/topologyStore';

const api = vi.hoisted(() => ({
  mode: 'pure-emul',
  nodes: [{ id: 'n1', name: 'r1', kind: 'router', nos: 'frr', mode: 'emul', config_ref: 'a1' }],
  artifacts: [{ id: 'a1', node_id: 'n1', vendor: 'frr', format: 'cli', content: 'hostname r1' }],
  apply: vi.fn(),
}));
vi.mock('@/api/client', () => ({
  projectsApi: {
    get: async () => ({ id: 'p1', mode: api.mode }),
    topology: async () => ({ nodes: api.nodes }),
  },
  configsApi: {
    forNode: async () => api.artifacts,
    diff: async () => ({ changed: false }),
    exportProject: async () => ({ configs: {} }),
    downloadProjectConfigs: vi.fn(),
    apply: (...args: unknown[]) => api.apply(...args),
  },
}));

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
let root: Root;
let container: HTMLDivElement;

async function mount() {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root.render(<QueryClientProvider client={client}><ConfigWorkspace /></QueryClientProvider>);
  });
  for (let i = 0; i < 10 && container.textContent?.includes('Loading'); i++) {
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 10)); });
  }
  return container.querySelector('button[aria-describedby="config-apply-status"]') as HTMLButtonElement;
}

beforeEach(() => {
  api.mode = 'pure-emul';
  api.nodes = [{ id: 'n1', name: 'r1', kind: 'router', nos: 'frr', mode: 'emul', config_ref: 'a1' }];
  api.artifacts = [{ id: 'a1', node_id: 'n1', vendor: 'frr', format: 'cli', content: 'hostname r1' }];
  api.apply.mockReset().mockResolvedValue({ project_id: 'p1', node_id: 'n1', artifact_id: 'a1' });
  useUiStore.setState({ projectId: 'p1', viewMode: 'config', drawerOpen: false });
});
afterEach(() => { act(() => root.unmount()); container.remove(); });

describe('Config Center FRR apply', () => {
  it.each(['dark', 'light'] as const)('applies once and opens the existing console in %s', async (theme) => {
    useUiStore.getState().setTheme(theme);
    const button = await mount();
    expect(container.querySelector('#config-apply-status')?.textContent).toBe('Ready');
    expect(button.disabled).toBe(false);
    await act(async () => { button.click(); button.click(); await Promise.resolve(); });
    expect(api.apply).toHaveBeenCalledTimes(1);
    expect(api.apply).toHaveBeenCalledWith('p1', 'n1', 'a1');
    expect(container.querySelector('#config-apply-status')?.textContent).toBe('Applied');
    await act(async () => { (Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.includes('Open console')) as HTMLButtonElement).click(); });
    expect(useUiStore.getState()).toMatchObject({ viewMode: 'topology', drawerTab: 'console', drawerOpen: true });
    expect(useTopologyStore.getState().selectedNodeId).toBe('n1');
  });

  it('disables incompatible project, node, and artifact', async () => {
    api.mode = 'pure-sim';
    let button = await mount();
    expect(button.disabled).toBe(true);
    expect(container.textContent).toContain('Requires a pure-emul project.');
    act(() => root.unmount()); container.remove();
    api.mode = 'pure-emul'; api.nodes[0]!.nos = 'ios';
    button = await mount();
    expect(button.disabled).toBe(true);
    expect(container.textContent).toContain('Select an FRRouting device.');
  });

  it('sanitizes failures', async () => {
    api.apply.mockRejectedValue(new Error('password secret'));
    const button = await mount();
    await act(async () => { button.click(); await Promise.resolve(); });
    expect(container.textContent).toContain('Check the FRR lab and backend logs');
    expect(container.textContent).not.toContain('password secret');
  });

  it('does not show success after switching device during apply', async () => {
    api.nodes.push({ id: 'n2', name: 'r2', kind: 'router', nos: 'frr', mode: 'emul', config_ref: 'a2' });
    api.artifacts.push({ id: 'a2', node_id: 'n2', vendor: 'frr', format: 'cli', content: 'hostname r2' });
    let complete!: (value: unknown) => void;
    api.apply.mockImplementation(() => new Promise((resolve) => { complete = resolve; }));
    const button = await mount();
    await act(async () => { button.click(); });
    expect(api.apply).toHaveBeenCalledTimes(1);
    const second = Array.from(container.querySelectorAll('[role="option"]')).find((el) => el.textContent?.includes('r2')) as HTMLButtonElement;
    await act(async () => { second.click(); });
    const pendingButton = container.querySelector('button[aria-describedby="config-apply-status"]') as HTMLButtonElement;
    expect(pendingButton.disabled).toBe(true);
    expect(container.querySelector('#config-apply-status')?.textContent).toContain('Applying to previous selection');
    await act(async () => { pendingButton.click(); });
    expect(api.apply).toHaveBeenCalledTimes(1);
    await act(async () => { complete({}); await Promise.resolve(); });
    expect(container.textContent).not.toContain('Applied');
    expect(container.textContent).not.toContain('Open console');
    expect((container.querySelector('button[aria-describedby="config-apply-status"]') as HTMLButtonElement).disabled).toBe(false);
  });

  it('disables a non-CLI artifact', async () => {
    api.artifacts[0]!.format = 'yaml';
    const button = await mount();
    expect(button.disabled).toBe(true);
    expect(container.textContent).toContain('Requires a stored FRR CLI config.');
  });
});
