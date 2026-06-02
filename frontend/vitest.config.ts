/// <reference types="vitest" />
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// Vitest config split from vite.config.ts so the production build doesn't
// depend on vitest being installed. The smoke test only needs jsdom + the
// React plugin; richer setups can layer on top.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/__tests__/setup.ts'],
    include: ['src/**/*.{test,spec}.{ts,tsx}', 'eslint-rules/**/*.{test,spec}.{ts,tsx}'],
  },
});
