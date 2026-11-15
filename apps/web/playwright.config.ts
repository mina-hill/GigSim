import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  workers: 1,
  timeout: 35_000,
  expect: { timeout: 10_000 },
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: process.env.GIGBID_WEB_URL ?? "http://127.0.0.1:3000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
