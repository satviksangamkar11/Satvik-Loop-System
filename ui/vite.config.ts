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
  // Bind 127.0.0.1 explicitly: the e2e configs poll http://127.0.0.1:5173, and a default bind is localhost (::1 on some Windows machines) (I71).
  server: {
    host: '127.0.0.1',
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
