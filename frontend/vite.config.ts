// SPDX-License-Identifier: GPL-3.0-or-later
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

/**
 * Vite configuration for the CellScope frontend.
 *
 * During development the dev server proxies all `/api` requests (HTTP and
 * WebSocket) to the FastAPI backend at `http://localhost:8000`, so the
 * frontend can use same-origin relative URLs (e.g. `/api/datasets`,
 * `/api/ws/jobs`) in both dev and production. In production the built
 * static assets are served directly by FastAPI `StaticFiles`.
 *
 * deck.gl convention (shared across the frontend): the umbrella `deck.gl`
 * package is installed at ^9. Components import the React wrapper and core
 * types from `deck.gl` / `@deck.gl/core` and layer classes (e.g.
 * `ScatterplotLayer`) from `@deck.gl/layers`; extensions come from
 * `@deck.gl/extensions`. Keep these import sources consistent everywhere.
 */
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        ws: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
});
