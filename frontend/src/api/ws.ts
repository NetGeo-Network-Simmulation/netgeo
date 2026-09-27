/**
 * Resilient WebSocket client for NetGeo realtime channels:
 *   /ws/topology              — graph snapshots, node/link status, sim ticks
 *   /ws/console/{node_id}     — interactive device console stream
 *
 * Features: auto-reconnect with exponential backoff + jitter, JSON framing,
 * typed listeners, and a heartbeat so dead connections are dropped quickly.
 * Kept dependency-free (no socket.io) to stay within the lightweight budget.
 */

import { getToken, notifyUnauthorized } from './token';
import { runtimeWebSocketBase } from '@/config/runtimeProfile';

type Listener<T> = (event: T) => void;

export interface RealtimeOptions {
  /** Max backoff between reconnect attempts, ms. */
  maxBackoff?: number;
  /** Heartbeat interval, ms. 0 disables. */
  heartbeat?: number;
}

export type ConnState = 'connecting' | 'open' | 'closed' | 'reconnecting';

/** Resolve ws(s):// origin from current page when base is relative. */
function wsUrl(path: string): string {
  const base = runtimeWebSocketBase();
  if (base) return base.replace(/\/$/, '') + path;
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${location.host}${path}`;
}

/**
 * Append the bearer token as a query param (AUTH_CONTRACT §4) — browsers can't
 * set the Authorization header on the WS upgrade. Read at connect time so each
 * reconnect picks up a refreshed token. No-op when unauthenticated.
 */
function withToken(url: string): string {
  const t = getToken();
  if (!t) return url;
  const sep = url.includes('?') ? '&' : '?';
  return `${url}${sep}token=${encodeURIComponent(t)}`;
}

export class RealtimeChannel<TEvent> {
  private ws: WebSocket | null = null;
  private listeners = new Set<Listener<TEvent>>();
  private stateListeners = new Set<Listener<ConnState>>();
  private attempt = 0;
  private closedByUser = false;
  private hbTimer: ReturnType<typeof setInterval> | null = null;
  private readonly maxBackoff: number;
  private readonly heartbeat: number;

  constructor(
    private readonly path: string,
    opts: RealtimeOptions = {},
  ) {
    this.maxBackoff = opts.maxBackoff ?? 15_000;
    this.heartbeat = opts.heartbeat ?? 25_000;
  }

  connect(): this {
    this.closedByUser = false;
    this.open();
    return this;
  }

  private open(): void {
    this.emitState(this.attempt === 0 ? 'connecting' : 'reconnecting');
    const sock = new WebSocket(withToken(wsUrl(this.path)));
    this.ws = sock;

    sock.onopen = () => {
      this.attempt = 0;
      this.emitState('open');
      this.startHeartbeat();
    };

    sock.onmessage = (ev) => {
      // Ignore heartbeat pongs; forward everything else as a typed event.
      if (ev.data === 'pong') return;
      try {
        const parsed = JSON.parse(ev.data) as TEvent;
        this.listeners.forEach((l) => l(parsed));
      } catch {
        // Non-JSON frame (e.g. raw console bytes) — wrap as a string payload.
        this.listeners.forEach((l) => l({ type: 'output', data: ev.data } as unknown as TEvent));
      }
    };

    sock.onclose = (ev) => {
      this.stopHeartbeat();
      // 4401 = server rejected the token (AUTH_CONTRACT §4). Don't reconnect-
      // loop against a dead session — tear it down and bounce to login.
      if (ev.code === 4401) {
        this.closedByUser = true;
        this.emitState('closed');
        notifyUnauthorized();
        return;
      }
      if (this.closedByUser) {
        this.emitState('closed');
        return;
      }
      this.scheduleReconnect();
    };

    sock.onerror = () => sock.close();
  }

  private scheduleReconnect(): void {
    this.attempt += 1;
    const backoff = Math.min(this.maxBackoff, 500 * 2 ** this.attempt);
    const jitter = Math.random() * 400;
    this.emitState('reconnecting');
    setTimeout(() => {
      if (!this.closedByUser) this.open();
    }, backoff + jitter);
  }

  private startHeartbeat(): void {
    if (!this.heartbeat) return;
    this.stopHeartbeat();
    this.hbTimer = setInterval(() => {
      if (this.ws?.readyState === WebSocket.OPEN) this.ws.send('ping');
    }, this.heartbeat);
  }

  private stopHeartbeat(): void {
    if (this.hbTimer) clearInterval(this.hbTimer);
    this.hbTimer = null;
  }

  /** Send a JSON command (e.g. console keystrokes, sim controls). */
  send(payload: unknown): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(typeof payload === 'string' ? payload : JSON.stringify(payload));
    }
  }

  onMessage(listener: Listener<TEvent>): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  onState(listener: Listener<ConnState>): () => void {
    this.stateListeners.add(listener);
    return () => this.stateListeners.delete(listener);
  }

  private emitState(s: ConnState): void {
    this.stateListeners.forEach((l) => l(s));
  }

  close(): void {
    this.closedByUser = true;
    this.stopHeartbeat();
    this.ws?.close();
  }
}

/** Factory: topology channel (one per open project).
 *  The backend scopes the stream — and sends the initial snapshot — only when
 *  `?project=<id>` is present (see backend/app/api/ws.py). Without it the socket
 *  receives every project's events and no snapshot, so always pass the id. */
export function topologyChannel(projectId?: string | null) {
  const qs = projectId ? `?project=${encodeURIComponent(projectId)}` : '';
  return new RealtimeChannel<import('./types').TopologyEvent>(`/ws/topology${qs}`);
}

/** Factory: per-node console channel. */
export function consoleChannel(nodeId: string) {
  return new RealtimeChannel<import('./types').ConsoleEvent>(`/ws/console/${nodeId}`);
}

/** Factory: realtime-collaboration channel (presence + CRDT op-log) per project.
 *  Scoped by `?project=<id>` like the topology channel. Backed by the backend
 *  `/ws/collab` presence broadcaster (backend/app/api/ws.py). */
export function collabChannel(projectId?: string | null) {
  const qs = projectId ? `?project=${encodeURIComponent(projectId)}` : '';
  return new RealtimeChannel<import('./types').PresenceEvent>(`/ws/collab${qs}`);
}
