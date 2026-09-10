/**
 * Transpile the AudioWorklet modules to plain JS in public/worklets/.
 *
 * Vite does not transform a file referenced as `new URL('./x.worklet.ts', import.meta.url)`
 * — it fingerprints it and copies it verbatim. In dev that is invisible, because the dev
 * server transpiles every .ts it serves. In a production build it shipped raw TypeScript,
 * which `audioWorklet.addModule()` rejects twice over: the wrong MIME type from the static
 * server, and `private ring = ...` is not JavaScript.
 *
 * The result was a deployed build with no audio in either direction — no playback, and no
 * capture either, so nothing was ever transcribed. It cannot happen silently again: these
 * are emitted as .js, served from public/ at a fixed path, and identical in dev and prod.
 */

import { build } from "esbuild";
import { mkdirSync } from "node:fs";

mkdirSync("public/worklets", { recursive: true });

await build({
  entryPoints: {
    capture: "src/worklets/capture.worklet.ts",
    playback: "src/worklets/playback.worklet.ts",
  },
  outdir: "public/worklets",
  bundle: true,
  format: "esm",
  // AudioWorkletGlobalScope is a modern V8 realm; nothing needs downlevelling, and the
  // ring buffer is the last place to want transpiler helpers.
  target: "es2022",
  logLevel: "info",
});
