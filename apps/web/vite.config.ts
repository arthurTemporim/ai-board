import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

// In Docker the API is reachable as http://api:8000; locally set API_URL.
const apiUrl = process.env.API_URL ?? 'http://localhost:8000';

export default defineConfig({
  plugins: [react()],
  cacheDir: '/tmp/.vite', // node_modules may not be writable when running as the host user
  server: {
    host: true,
    port: 5173,
    proxy: { '/api': { target: apiUrl, changeOrigin: true } },
    watch: { usePolling: true }, // file events do not cross the Docker bind mount on WSL
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.ts',
    css: false,
  },
});
