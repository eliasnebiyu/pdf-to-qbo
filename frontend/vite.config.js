import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],

  // pdfjs-dist 3.x has CJS internals that Vite 5's pre-bundler cannot fully
  // process. Excluding it prevents the "Cannot use import statement" build
  // error and lets the library load from node_modules at runtime instead.
  optimizeDeps: {
    exclude: ['pdfjs-dist'],
  },

  server: {
    port: 5173,
    proxy: {
      // All /api/* calls are forwarded to the FastAPI backend (uvicorn default: 8000)
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },

  build: {
    // Raise the warning threshold – the PDF.js worker is legitimately large
    chunkSizeWarningLimit: 1500,
  },
})
