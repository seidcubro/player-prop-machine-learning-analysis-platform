/**
 * Vite build configuration for the web app.
 */

import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ command, mode }) => {
  // Refuse to build for production without an API base.
  //
  // src/api.ts falls back to http://localhost:8000 when VITE_API_BASE is
  // missing, which is right for local work and silently fatal anywhere else: a
  // forgotten variable on Vercel produces a site that builds, deploys and looks
  // correct, while the browser blocks every request from an https page to
  // http://localhost and reports nothing a user could act on.
  //
  // Failing the build says which variable is missing, in the deploy log, before
  // anybody sees the broken version.
  if (command === 'build' && mode === 'production') {
    const env = loadEnv(mode, process.cwd(), 'VITE_')
    if (!env.VITE_API_BASE) {
      throw new Error(
        'VITE_API_BASE is not set. Set it to the deployed API, for example ' +
        'https://api.priorline.io/api/v1, in the Vercel project settings. It ' +
        'is read at build time, so changing it needs a redeploy.'
      )
    }
    // Same reasoning, different symptom. index.html builds the Open Graph and
    // canonical tags out of this, and a crawler cannot resolve a relative
    // image, so a missing value means every shared link renders as a bare URL
    // with no title card. Vite would leave the placeholder in the markup and
    // only warn, which nobody reads in a deploy log.
    if (!env.VITE_SITE_URL) {
      throw new Error(
        'VITE_SITE_URL is not set. Set it to the site origin with no trailing ' +
        'slash, for example https://priorline.io, in the Vercel project ' +
        'settings. It is read at build time, so changing it needs a redeploy.'
      )
    }
  }

  return {
    plugins: [react()],
    server: {
      proxy: {
        '/api': {
          target: 'http://localhost:8000',
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, ''),
        },
      },
    },
  }
})
