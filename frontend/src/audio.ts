/**
 * Audio engine: microphone capture and agent playback.
 *
 * One AudioContext at 16 kHz drives both directions. That matches AssemblyAI's input
 * format and the TTS output format exactly, so no resampling happens anywhere in the
 * pipeline — resampling is latency, and every millisecond here is one the shopper hears.
 *
 * The render quantum at 16 kHz is 128 samples = 8 ms, which is the true floor on how
 * fast `flush()` can silence the agent.
 *
 * Playback and capture start SEPARATELY and in that order. They used to be one call that
 * asked for the microphone first, which coupled hearing the agent to being allowed to
 * speak to it: a denied microphone threw before the AudioContext existed, so every byte
 * the backend sent was dropped on the floor and the shopper sat in silence with a
 * transcript that claimed the agent was talking. Text input is a required fallback
 * (FR-060), and a fallback that cannot hear the reply is not one.
 */

const SAMPLE_RATE = 16_000;

/** Why capture is or is not running. `blocked` is a decision the shopper made and can
 * change; `unavailable` is a machine without a usable input device. */
export type CaptureStatus = "capturing" | "blocked" | "unavailable";

export interface AudioEngineEvents {
  /** A 1600-byte PCM16 frame ready for the socket. */
  onFrame(frame: ArrayBuffer): void;
  /** Playback progress, used to compute what the shopper actually heard. */
  onPlaybackProgress(turnId: string, framesPlayed: number): void;
  /** A flush completed; carries how much had been played when it landed. */
  onFlushed(turnId: string, framesPlayed: number): void;
  /** Smoothed input level, 0..1, for the visualiser. */
  onLevel(level: number): void;
}

export class AudioEngine {
  private ctx: AudioContext | null = null;
  private capture: AudioWorkletNode | null = null;
  private playback: AudioWorkletNode | null = null;
  private analyser: AnalyserNode | null = null;
  private stream: MediaStream | null = null;
  private levelTimer: number | null = null;
  private levelBuf = new Uint8Array(0);

  constructor(private readonly events: AudioEngineEvents) {}

  /** The session has an audio context — the agent can be heard. */
  get running(): boolean {
    return this.ctx !== null;
  }

  /** The microphone is open — the agent can be spoken to. */
  get capturing(): boolean {
    return this.capture !== null;
  }

  /**
   * Bring up output only. No permission prompt, nothing to deny, nothing to wait for.
   *
   * Called before the sockets connect so the playback node exists before the first byte
   * of the greeting can arrive. `enqueue()` on a null node is a silent drop, and the
   * greeting is the one turn guaranteed to race the setup.
   */
  async startPlayback(): Promise<void> {
    if (this.ctx) return;

    this.ctx = new AudioContext({ sampleRate: SAMPLE_RATE, latencyHint: "interactive" });
    // Served from public/, transpiled by scripts/build-worklets.mjs. Deliberately NOT
    // `new URL("./worklets/x.worklet.ts", import.meta.url)`: Vite fingerprints and copies
    // that verbatim, so a production build shipped raw TypeScript to addModule() and the
    // deployed app had no audio at all — while dev, which transpiles on the fly, was fine.
    await this.ctx.audioWorklet.addModule("/worklets/capture.js");
    await this.ctx.audioWorklet.addModule("/worklets/playback.js");

    this.playback = new AudioWorkletNode(this.ctx, "playback-processor", {
      numberOfInputs: 0,
      numberOfOutputs: 1,
      outputChannelCount: [1],
    });
    this.playback.port.onmessage = (
      e: MessageEvent<{ type: string; turnId: string; framesPlayed: number }>,
    ) => {
      const { type, turnId, framesPlayed } = e.data;
      if (type === "progress") this.events.onPlaybackProgress(turnId, framesPlayed);
      else if (type === "flushed") this.events.onFlushed(turnId, framesPlayed);
    };
    this.playback.connect(this.ctx.destination);

    // A context created outside a gesture — or while the tab was backgrounded — starts
    // suspended, and a suspended context renders nothing at all.
    if (this.ctx.state === "suspended") await this.ctx.resume();
  }

  /**
   * Bring up the microphone. Never throws for a permission answer: a denied microphone
   * degrades the session to typing, it does not end it.
   */
  async startCapture(): Promise<CaptureStatus> {
    if (!this.ctx) throw new Error("startPlayback() must run first");
    if (this.capture) return "capturing";

    // Ask for the format we actually want. Browser echo cancellation and noise
    // suppression matter more than usual here: the agent's own voice comes out of
    // the same speakers the mic is listening to, and without AEC the agent
    // interrupts itself.
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          sampleRate: SAMPLE_RATE,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
    } catch (err) {
      const name = err instanceof DOMException ? err.name : "";
      return name === "NotAllowedError" || name === "SecurityError" ? "blocked" : "unavailable";
    }

    const source = this.ctx.createMediaStreamSource(this.stream);
    this.capture = new AudioWorkletNode(this.ctx, "capture-processor");
    this.capture.port.onmessage = (e: MessageEvent<ArrayBuffer>) => {
      this.events.onFrame(e.data);
    };

    this.analyser = this.ctx.createAnalyser();
    this.analyser.fftSize = 256;
    this.analyser.smoothingTimeConstant = 0.75;
    this.levelBuf = new Uint8Array(this.analyser.frequencyBinCount);

    source.connect(this.analyser);
    source.connect(this.capture);
    // The capture node produces no output; connecting it to the destination would
    // feed the mic back to the speakers.

    this.startLevelMeter();
    return "capturing";
  }

  /** Queue synthesized audio for playback. */
  enqueue(pcm: ArrayBuffer, turnId: string): void {
    this.playback?.port.postMessage({ type: "push", pcm, turnId }, [pcm]);
  }

  /**
   * Silence the agent immediately.
   *
   * This is half of barge-in. The other half is the server-side `clear_buffer`, and the
   * two are fired independently — telling the provider to stop producing audio does
   * nothing about what is already buffered here.
   */
  flush(): void {
    this.playback?.port.postMessage({ type: "flush" });
  }

  private startLevelMeter(): void {
    const tick = () => {
      if (!this.analyser) return;
      this.analyser.getByteFrequencyData(this.levelBuf);
      let sum = 0;
      for (let i = 0; i < this.levelBuf.length; i++) sum += this.levelBuf[i] ?? 0;
      const avg = sum / Math.max(1, this.levelBuf.length) / 255;
      // Perceptual curve — raw RMS looks flat and unresponsive on screen.
      this.events.onLevel(Math.min(1, Math.pow(avg, 0.6) * 1.6));
      this.levelTimer = requestAnimationFrame(tick);
    };
    this.levelTimer = requestAnimationFrame(tick);
  }

  async stop(): Promise<void> {
    if (this.levelTimer !== null) cancelAnimationFrame(this.levelTimer);
    this.levelTimer = null;
    this.stream?.getTracks().forEach((t) => t.stop());
    this.capture?.disconnect();
    this.playback?.disconnect();
    await this.ctx?.close();
    this.ctx = null;
    this.capture = null;
    this.playback = null;
    this.analyser = null;
    this.stream = null;
  }
}
