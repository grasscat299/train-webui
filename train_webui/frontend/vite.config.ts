import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// backend (FastAPI, 担当B) はローカルの 127.0.0.1:8000 で動く前提（design.md/CONTRACT.md）。
// 開発時は /api と /media を backend へ proxy する。
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/media': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
  },
});
