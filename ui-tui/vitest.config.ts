import { fileURLToPath } from 'node:url'

import { defineConfig } from 'vitest/config'

const hermesInkSource = fileURLToPath(new URL('./packages/hermes-ink/src/entry-exports.ts', import.meta.url))

export default defineConfig({
  resolve: {
    // Tests must exercise the in-tree Ink source, not its ignored local dist/
    // bundle. The latter is generated and can be stale after an Ink API change.
    alias: { '@hermes/ink': hermesInkSource }
  },
  test: {
    // This host's interactive shell exports NODE_ENV=production. Force Vitest
    // workers into their intended scheduler mode instead of inheriting it.
    env: { NODE_ENV: 'test' },
    exclude: ['dist/**', 'node_modules/**']
  }
})
