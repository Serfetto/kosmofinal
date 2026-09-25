import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  optimizeDeps: {
    // Worker поставляется отдельным ESM-файлом и не должен попадать в prebundle.
    exclude: ['maplibre-gl', 'maplibre-gl/dist/maplibre-gl-worker.mjs'],
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/data': 'http://127.0.0.1:8000',
    },
  },
});
