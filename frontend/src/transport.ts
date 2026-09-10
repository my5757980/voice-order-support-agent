/**
 * Two sockets to our backend. The browser never opens a vendor socket and never holds
 * a vendor API key — it authenticates to us with a short-lived session token.
 *
 * Splitting audio from control is not ceremony: it keeps `audio.flush` off a channel
 * that is saturated with 20 audio frames per second. A flush queued behind audio is a
 * flush that arrives late, and late is the whole failure mode.
 */

import type { ClientMessage, ServerMessage } from "./protocol";

export interface TransportEvents {
  onMessage(msg: ServerMessage): void;
  onAudio(pcm: ArrayBuffer): void;
  onOpen(): void;
  onClose(reason: string): void;
}

export class Transport {
  private audioWs: WebSocket | null = null;
  private controlWs: WebSocket | null = null;
  private closing = false;

  constructor(private readonly events: TransportEvents) {}

  get connected(): boolean {
    return this.audioWs?.readyState === WebSocket.OPEN;
  }

  async connect(): Promise<void> {
    this.closing = false;

    // Mint a single-use, session-scoped token. This is the only place credentials are
    // involved, and the vendor keys stay entirely server-side.
    const res = await fetch("/api/session", { method: "POST" });
    if (!res.ok) throw new Error(`session request failed: ${res.status}`);
    const { token } = (await res.json()) as { token: string };

    const base = location.origin.replace(/^http/, "ws");
    this.controlWs = new WebSocket(`${base}/ws/control?session=${encodeURIComponent(token)}`);
    this.audioWs = new WebSocket(`${base}/ws/audio?session=${encodeURIComponent(token)}`);
    this.audioWs.binaryType = "arraybuffer";

    this.controlWs.onmessage = (e: MessageEvent<string>) => {
      try {
        this.events.onMessage(JSON.parse(e.data) as ServerMessage);
      } catch {
        // A malformed control frame is a backend bug, not something to crash the
        // session over — the shopper is mid-conversation.
      }
    };
    this.audioWs.onmessage = (e: MessageEvent<ArrayBuffer>) => {
      this.events.onAudio(e.data);
    };

    const onClose = (reason: string) => () => {
      if (!this.closing) this.events.onClose(reason);
    };
    this.controlWs.onclose = onClose("control socket closed");
    this.audioWs.onclose = onClose("audio socket closed");

    await Promise.all([waitOpen(this.controlWs), waitOpen(this.audioWs)]);
    this.events.onOpen();
  }

  /** Send one 1600-byte PCM16 frame. Dropped silently if the socket is not ready —
   * buffering here would only convert a network problem into a latency problem. */
  sendFrame(frame: ArrayBuffer): void {
    if (this.audioWs?.readyState === WebSocket.OPEN) this.audioWs.send(frame);
  }

  send(msg: ClientMessage): void {
    if (this.controlWs?.readyState === WebSocket.OPEN) {
      this.controlWs.send(JSON.stringify(msg));
    }
  }

  close(): void {
    this.closing = true;
    this.send({ type: "session.end" });
    this.audioWs?.close();
    this.controlWs?.close();
    this.audioWs = null;
    this.controlWs = null;
  }
}

function waitOpen(ws: WebSocket): Promise<void> {
  return new Promise((resolve, reject) => {
    if (ws.readyState === WebSocket.OPEN) return resolve();
    ws.onopen = () => resolve();
    ws.onerror = () => reject(new Error("socket failed to open"));
  });
}
