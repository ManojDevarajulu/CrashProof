import { defineConfig } from "vitest/config";

// Relative base so the build works from any GitHub Pages sub-path.
export default defineConfig({
  base: "./",
  test: { include: ["src/**/*.test.{ts,tsx}"] },
});
