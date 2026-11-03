import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import { fileURLToPath } from 'url';

const uiDir = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(uiDir, './src'),
    },
  },
  server: {
    port: 5173,
    // MOCK mode imports the canonical fixtures from the repository's monitor/fixtures folder.
    fs: {
      allow: [path.resolve(uiDir, '..'), uiDir],
    },
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8765',
        changeOrigin: true,
      },
    },
  },
});
