/**
 * AudioWorklet globals.
 *
 * The DOM lib does not declare the audio-thread scope, so these are declared here
 * rather than reached for with `any` — the worklets are the most timing-sensitive
 * code in the project and deserve real types.
 */

declare class AudioWorkletProcessor {
  readonly port: MessagePort;
  constructor();
  process(
    inputs: Float32Array[][],
    outputs: Float32Array[][],
    parameters: Record<string, Float32Array>,
  ): boolean;
}

declare function registerProcessor(
  name: string,
  processorCtor: new (options?: unknown) => AudioWorkletProcessor,
): void;

declare const sampleRate: number;
declare const currentTime: number;
