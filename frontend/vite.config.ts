import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import {defineConfig} from 'vite';

// Dev proxy target — the Flask agent (dashboard app) running locally.
const AGENT = process.env.VITE_AGENT_URL || 'http://127.0.0.1:8787';

export default defineConfig(() => {
  return {
    // The production build is served by the Flask agent under /app, so assets
    // must be referenced with a /app/ base. Runtime fetch() calls use absolute
    // root paths (/api, /v6, /ws) and are unaffected by this.
    base: '/app/',
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, '.'),
      },
    },
    server: {
      // HMR is disabled in AI Studio via DISABLE_HMR env var.
      hmr: process.env.DISABLE_HMR !== 'true',
      watch: process.env.DISABLE_HMR === 'true' ? null : {},
      // Dev-only: `npm run dev` proxies the API surface to the local Flask
      // agent so the SPA talks to real data (no CORS). In production the SPA is
      // served by Flask itself, so these proxy rules never apply.
      proxy: {
        '/api': {target: AGENT, changeOrigin: true},
        '/v6': {target: AGENT, changeOrigin: true},
        '/chat': {target: AGENT, changeOrigin: true},
        '/ws': {target: AGENT, ws: true, changeOrigin: true},
        '/healthz': {target: AGENT, changeOrigin: true},
      },
    },
  };
});
