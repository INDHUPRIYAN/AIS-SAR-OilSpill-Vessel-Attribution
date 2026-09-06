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
}

export default defineConfig({
  plugins: [react()],
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
