import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The API origin the dev/preview server proxies to. Defaults to the documented
// port; override with API_TARGET when 8000 is already taken by another project
// on the same machine (a hardcoded target silently proxies to whatever answers
// on 8000, which looks like a broken app rather than a port clash).
const apiTarget = process.env.API_TARGET || 'http://127.0.0.1:8000'
const proxy = {
  '/api': { target: apiTarget, changeOrigin: true },
  '/health': { target: apiTarget, changeOrigin: true },
  // The hindcast engines stream over a WebSocket; same origin, so the session cookie rides along.
  '/ws': { target: apiTarget, ws: true, changeOrigin: true },
}

export default defineConfig({
  plugins: [react()],
  // Vitest arrives here (not with the 3D work) because the session layer is
  // the first frontend logic worth testing on its own: it decides whether a
  // user sees the app or the sign-in form.
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./tests-unit/setup.js'],
    include: ['tests-unit/**/*.test.{js,jsx}'],
  },
  preview: {
    port: Number(process.env.PREVIEW_PORT) || 5174,
    proxy,
  },
  server: {
    port: Number(process.env.PORT) || 5173,
    // The API is same-origin through this proxy, so the browser never needs
    // CORS and the admin token never has to cross an origin boundary.
    proxy,
  },
})
