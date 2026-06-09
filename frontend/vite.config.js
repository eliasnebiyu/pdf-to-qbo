import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],

  optimizeDeps: {
    // Include pdfjs-dist so esbuild wraps its CJS exports into a proper ESM
    // module that the browser can load without errors.
    include: ['pdfjs-dist'],
  },

  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },

  build: {
    chunkSizeWarningLimit: 1500,
  },
})
