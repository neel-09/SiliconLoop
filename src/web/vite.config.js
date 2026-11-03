// The site is built into dist/ and served by FastAPI at /. The simulator modules in ../sim are shared with the
// backend's headless test, so the twin in the browser runs exactly the code that verified the firmware.
import { defineConfig } from 'vite';
import { fileURLToPath } from 'node:url';

const here = (p) => fileURLToPath(new URL(p, import.meta.url));

export default defineConfig({
  base: '/',
  resolve: {
    alias: {
      '@sim': here('../sim'),
      avr8js: here('./node_modules/avr8js'),   // ../sim has its own node_modules only on the backend
    },
  },
  build: { outDir: 'dist', emptyOutDir: true, chunkSizeWarningLimit: 2000, target: 'es2022' },
});
