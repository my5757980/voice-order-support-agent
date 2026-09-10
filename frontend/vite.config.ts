import { defineConfig } from "vite";

export default defineConfig({
  server: {
    port: 5173,
    proxy: {
      // Both sockets and the session endpoint live on the backend. The browser never
      // opens a vendor socket — it only ever talks to us.
      "/ws": { target: "ws://localhost:8000", ws: true },
      "/api": { target: "http://localhost:8000" },
    },
  },
  build: {
    outDir: "dist",
    // The worklets must stay as separate files: AudioWorklet.addModule() loads them
    // by URL, so they cannot be inlined into the main bundle.
    assetsInlineLimit: 0,
  },
});
