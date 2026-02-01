import os from 'node:os';
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    css: true,
    // The E2E specs use @playwright/test, whose test.describe() throws under
    // any other runner.
    exclude: ['**/node_modules/**', '**/dist/**', 'tests/e2e/**'],
    // Vitest's default pool is cores minus one. On a 16-core machine this default runs the
    // suite next to the backend testcontainers and can make the OOM killer end other
    // processes. A fixed 4 breaks the other way: on a two-core CI runner, tests that take
    // 1 s alone time out at 5 s. So the pool is Vitest's default, capped at 4.
    // CLAVIS_TEST_WORKERS overrides it per machine, and the repository's `make test` passes 4.
    maxWorkers: process.env['CLAVIS_TEST_WORKERS']
      ? Number(process.env['CLAVIS_TEST_WORKERS'])
      : Math.max(1, Math.min(4, os.availableParallelism() - 1)),
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
});
