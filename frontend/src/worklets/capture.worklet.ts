/**
 * Microphone capture worklet.
 *
 * Emits exactly 50 ms frames of PCM16 signed little-endian mono at 16 kHz —
 * 800 samples, 1600 bytes — which is precisely what AssemblyAI's v3 streaming
 * endpoint expects. Framing here rather than server-side means no resampling on
 * either end.
 *
 * Runs on the audio thread. It does nothing but convert and post: any work added
 * here shows up as jitter in frame cadence, which the ingest budget cannot absorb.
 */

const FRAME_SAMPLES = 800; // 50 ms @ 16 kHz

class CaptureProcessor extends AudioWorkletProcessor {
  private frame = new Int16Array(FRAME_SAMPLES);
  private filled = 0;

  process(inputs: Float32Array[][]): boolean {
    const channel = inputs[0]?.[0];
    if (!channel) return true; // mic muted or not yet flowing — keep the node alive

    for (let i = 0; i < channel.length; i++) {
      const sample = channel[i] ?? 0;
      // Clamp before scaling: values outside [-1, 1] wrap catastrophically in Int16.
      const clamped = sample < -1 ? -1 : sample > 1 ? 1 : sample;
      // Asymmetric scale — Int16 range is [-32768, 32767], not symmetric.
      this.frame[this.filled++] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;

      if (this.filled === FRAME_SAMPLES) {
        // Transfer rather than copy: this runs 20x/second on the audio thread.
        const out = this.frame.buffer;
        this.port.postMessage(out, [out]);
        this.frame = new Int16Array(FRAME_SAMPLES);
        this.filled = 0;
      }
    }
    return true;
  }
}

registerProcessor("capture-processor", CaptureProcessor);
