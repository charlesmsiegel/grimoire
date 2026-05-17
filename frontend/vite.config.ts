import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Avoids pulling in @types/node just to read a few env vars at config load.
declare const process: { env: Record<string, string | undefined> };

// Backend host/port are configurable so scripts/run.sh can pick free ports.
// Defaults match the prerequisites in README.md.
const backendHost = process.env.GRIMOIRE_BACKEND_HOST ?? "127.0.0.1";
const backendPort = process.env.GRIMOIRE_BACKEND_PORT ?? "8173";
const backendOrigin = `http://${backendHost}:${backendPort}`;
const backendWs = `ws://${backendHost}:${backendPort}`;
const frontendPort = Number(process.env.GRIMOIRE_FRONTEND_PORT ?? "5173");

export default defineConfig({
  plugins: [react()],
  server: {
    port: frontendPort,
    proxy: {
      "/api": backendOrigin,
      "/ws": { target: backendWs, ws: true },
    },
  },
});
