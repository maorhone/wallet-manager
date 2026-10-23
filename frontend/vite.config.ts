/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

const API_PREFIXES =
  '^/(me|organizations|departments|groups|grants|projects|initiatives|service-keys|production|policies|role-bindings|sync|outbox|status|reports|requests|audit|auth|healthz|onboarding|pending-placements)(/|$|\\?)';

export default defineConfig(() => {
  const target = process.env.API_ORIGIN ?? 'http://127.0.0.1:4101';
  return {
    plugins: [react()],
    server: { host: '0.0.0.0', port: 4100, proxy: { [API_PREFIXES]: { target } } },
    test: { environment: 'node', include: ['src/**/*.test.{ts,tsx}'] },
  };
});
