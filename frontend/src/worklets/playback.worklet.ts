/**
 * Agent speech playback worklet.
 *
 * This is the single most important file for the barge-in budget, and the reason the
 * plan refuses to use an <audio> element or MediaSource. Both of those buffer ahead
 * inside the browser's audio pipeline, where we cannot reach — so neither can be
 * silenced within the 100 ms p95 the constitution requires.
 *
 * A ring buffer we own can be zeroed on the very next render quantum: 128 samples,
 * which is 8 ms at 16 kHz. That is the difference between an agent that stops when
 * interrupted and one that talks over its user.
 *
 * It also reports how many samples were ACTUALLY played. That number, cross-referenced
 * with TTS character alignment, is what makes `heard_prefix_len` exact — memory records
 * what the shopper heard, never what the model generated.
 */

// 30 s at 16 kHz. The synthesizer returns each clause whole and the server forwards it at
// once, so a reply arrives far faster than realtime. At the old ~4 s the overrun handler
// dropped the oldest unplayed audio — the start of every reply longer than four seconds.
// Measured in the production worklet: 8 s pushed, 4.06 s played. A flush is O(1)
// (readIdx = writeIdx) at any size, so capacity costs only memory: 1.9 MB of Float32.
const CAPACITY = 16000 * 30;
const PROGRESS_EVERY = 1600; // report roughly every 100 ms

type InboundMessage =
  | { type: "push"; pcm: ArrayBuffer; turnId: string }
  | { type: "flush" };

class PlaybackProcessor extends AudioWorkletProcessor {
  private ring = new Float32Array(CAPACITY);
  private readIdx = 0;
  private writeIdx = 0;
  private played = 0;
  private sinceReport = 0;
  private turnId = "";

  constructor() {
    super();
    this.port.onmessage = (event: MessageEvent<InboundMessage>) => {
      const msg = event.data;
      if (msg.type === "flush") {
        this.flush();
        return;
      }
      if (msg.turnId !== this.turnId) {
        // Progress is reported per turn. Counting from the last flush, across turns,
        // credited every earlier reply's audio to the one playing now — and the server
        // maps this count onto how much of *that* reply the shopper heard.
        this.played = 0;
        this.sinceReport = 0;
        this.turnId = msg.turnId;
      }
      this.push(new Int16Array(msg.pcm));
    };
  }

  /** Drop everything not yet played. The shopper hears silence on the next quantum. */
  private flush(): void {
    this.readIdx = this.writeIdx;
    this.port.postMessage({
      type: "flushed",
      turnId: this.turnId,
      framesPlayed: this.played,
    });
    this.played = 0;
    this.sinceReport = 0;
  }

  private push(pcm: Int16Array): void {
    for (let i = 0; i < pcm.length; i++) {
      const next = (this.writeIdx + 1) % CAPACITY;
      if (next === this.readIdx) {
        // Overrun: the network delivered faster than realtime playback. Dropping the
        // oldest unplayed audio keeps us in sync rather than accumulating delay —
        // latency is the thing we cannot buy back.
        this.readIdx = (this.readIdx + 1) % CAPACITY;
      }
      this.ring[this.writeIdx] = (pcm[i] ?? 0) / 0x8000;
      this.writeIdx = next;
    }
  }

  process(_inputs: Float32Array[][], outputs: Float32Array[][]): boolean {
    const out = outputs[0]?.[0];
    if (!out) return true;

    for (let i = 0; i < out.length; i++) {
      if (this.readIdx === this.writeIdx) {
        out[i] = 0; // underrun — silence, never a stale sample
        continue;
      }
      out[i] = this.ring[this.readIdx] ?? 0;
      this.readIdx = (this.readIdx + 1) % CAPACITY;
      this.played++;
      this.sinceReport++;
    }

    if (this.sinceReport >= PROGRESS_EVERY) {
      this.sinceReport = 0;
      this.port.postMessage({
        type: "progress",
        turnId: this.turnId,
        framesPlayed: this.played,
      });
    }
    return true;
  }
}

registerProcessor("playback-processor", PlaybackProcessor);
