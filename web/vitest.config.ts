import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "path";

// React's act() is unavailable when a parent process launched Vitest with
// NODE_ENV=production. Tests must own their renderer mode rather than inherit
// a production dashboard shell.
process.env.NODE_ENV = "test";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
