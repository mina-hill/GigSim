import { defineConfig, devices } from "@playwright/test";

const target = process.env.GIGBID_LIVE_URL;
const username = process.env.GIGBID_REVIEWER_USER || "reviewer";
const password = process.env.GIGBID_REVIEWER_PASSWORD;

if (!target || !password) {
  throw new Error("Set GIGBID_LIVE_URL and GIGBID_REVIEWER_PASSWORD for the hosted smoke test.");
}

function smokeOrigin(value: string): string {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new Error("GIGBID_LIVE_URL must be an HTTPS or explicit HTTP loopback origin.");
  }
  const loopbackHttp = /^http:\/\/(?:127\.0\.0\.1|localhost)(?::\d+)?\/?$/i.test(value);
  if ((!loopbackHttp && url.protocol !== "https:") || url.username || url.password ||
      url.pathname !== "/" || url.search || url.hash) {
    throw new Error("Provide an HTTPS or explicit HTTP loopback origin without credentials or a path.");
  }
  return url.origin;
}

const origin = smokeOrigin(target);

export default defineConfig({
  testDir: "./e2e-hosted",
  workers: 1,
  retries: 0,
  timeout: 75_000,
  expect: { timeout: 15_000 },
  reporter: "list",
  use: {
    baseURL: origin,
    httpCredentials: { username, password, origin },
    trace: "off",
    screenshot: "off",
    video: "off",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
