import { defineConfig, devices } from "@playwright/test";

// Runs against the production build; `npm run build` must run first.
export default defineConfig({
  testDir: "e2e",
  use: { baseURL: "http://localhost:4173/" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "npx vite preview --port 4173 --strictPort",
    url: "http://localhost:4173/",
    reuseExistingServer: false,
  },
});
