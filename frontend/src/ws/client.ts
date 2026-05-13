/**
 * Per-campaign WebSocket client with automatic reconnect.
 *
 * Reconnects with exponential backoff + jitter. While disconnected, listeners
 * are preserved; on reconnect the server re-emits any state we need to rehydrate.
 */

export interface WSMessage {
  type: string;
  [key: string]: unknown;
}

export type WSListener = (message: WSMessage) => void;
export type WSStatusListener = (status: WSStatus) => void;

export type WSStatus = "idle" | "connecting" | "open" | "closed" | "reconnecting";

interface ClientOptions {
  url: string;
  initialBackoffMs?: number;
  maxBackoffMs?: number;
  // Override for tests; defaults to global WebSocket
  factory?: (url: string) => WebSocket;
}

export class CampaignSocket {
  private socket: WebSocket | null = null;
  private listeners = new Set<WSListener>();
  private statusListeners = new Set<WSStatusListener>();
  private status: WSStatus = "idle";
  private reconnectAttempts = 0;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private closedByUser = false;

  private readonly url: string;
  private readonly initialBackoff: number;
  private readonly maxBackoff: number;
  private readonly factory: (url: string) => WebSocket;

  constructor(opts: ClientOptions) {
    this.url = opts.url;
    this.initialBackoff = opts.initialBackoffMs ?? 500;
    this.maxBackoff = opts.maxBackoffMs ?? 30_000;
    this.factory = opts.factory ?? ((u) => new WebSocket(u));
  }

  connect(): void {
    if (this.socket || this.retryTimer) return;
    this.closedByUser = false;
    this.openSocket();
  }

  close(): void {
    this.closedByUser = true;
    if (this.retryTimer !== null) {
      clearTimeout(this.retryTimer);
      this.retryTimer = null;
    }
    if (this.socket) {
      this.socket.close();
      this.socket = null;
    }
    this.setStatus("closed");
  }

  send(message: WSMessage): boolean {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(message));
      return true;
    }
    return false;
  }

  onMessage(listener: WSListener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  onStatus(listener: WSStatusListener): () => void {
    this.statusListeners.add(listener);
    listener(this.status);
    return () => this.statusListeners.delete(listener);
  }

  getStatus(): WSStatus {
    return this.status;
  }

  private openSocket(): void {
    // close() can run between scheduleReconnect()'s setTimeout and the
    // callback firing; bail before opening a fresh connection in that case.
    if (this.closedByUser) return;
    this.setStatus(this.reconnectAttempts === 0 ? "connecting" : "reconnecting");
    let ws: WebSocket;
    try {
      ws = this.factory(this.url);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.socket = ws;

    ws.onopen = () => {
      this.reconnectAttempts = 0;
      this.setStatus("open");
    };

    ws.onmessage = (event: MessageEvent) => {
      const raw = typeof event.data === "string" ? event.data : null;
      if (!raw) return;
      let parsed: unknown;
      try {
        parsed = JSON.parse(raw);
      } catch {
        return;
      }
      if (
        !parsed ||
        typeof parsed !== "object" ||
        typeof (parsed as { type?: unknown }).type !== "string"
      ) {
        return;
      }
      const message = parsed as WSMessage;
      for (const listener of this.listeners) listener(message);
    };

    ws.onerror = () => {
      // No-op: onclose will follow and drive reconnect.
    };

    ws.onclose = () => {
      this.socket = null;
      if (this.closedByUser) return;
      this.scheduleReconnect();
    };
  }

  private scheduleReconnect(): void {
    if (this.closedByUser) return;
    const base = Math.min(this.initialBackoff * 2 ** this.reconnectAttempts, this.maxBackoff);
    const jitter = base * 0.25 * Math.random();
    const delay = base + jitter;
    this.reconnectAttempts += 1;
    this.setStatus("reconnecting");
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null;
      if (this.closedByUser) return;
      this.openSocket();
    }, delay);
  }

  private setStatus(next: WSStatus): void {
    if (this.status === next) return;
    this.status = next;
    for (const listener of this.statusListeners) listener(next);
  }
}

export function campaignStreamUrl(campaignId: string): string {
  const base =
    typeof window !== "undefined" && window.location
      ? `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}`
      : "";
  return `${base}/ws/campaigns/${encodeURIComponent(campaignId)}/stream`;
}
