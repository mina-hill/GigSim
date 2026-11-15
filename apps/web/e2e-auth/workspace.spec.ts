import { expect, test } from "@playwright/test";

test("owns one local persona and restores the account on reload", async ({ browser, page }) => {
  const passphrase = "synthetic local account password";
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Sign in to GigBid" })).toBeVisible();
  await page.getByRole("button", { name: "Create a local account" }).click();
  await page.getByRole("textbox", { name: "Username" }).fill("browser.fullstack");
  await page.getByLabel("Password", { exact: true }).fill(passphrase);
  await page.getByLabel("GigSim persona").selectOption("fullstack");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("heading", { name: "Job inbox" })).toBeVisible();
  await expect(page.getByLabel("Active persona")).toHaveValue("fullstack");
  await expect(page.getByLabel("Active persona").locator("option")).toHaveCount(1);

  await page.reload();
  await expect(page.getByLabel("Active persona")).toHaveValue("fullstack");
  const second = await browser.newPage({ baseURL: new URL(page.url()).origin });
  try {
    expect((await second.request.get("/api/v1/personas")).status()).toBe(401);
    await second.goto("/?persona=fullstack");
    await second.getByRole("button", { name: "Create a local account" }).click();
    await expect(second.getByLabel("GigSim persona").locator('option[value="fullstack"]')).toHaveCount(0);
    await second.getByRole("textbox", { name: "Username" }).fill("browser.ai");
    await second.getByLabel("Password", { exact: true }).fill(passphrase);
    await second.getByLabel("GigSim persona").selectOption("ai_ml");
    await second.getByRole("button", { name: "Create account" }).click();
    await expect(second.getByLabel("Active persona")).toHaveValue("ai_ml");
    expect((await second.request.get("/api/v1/jobs?persona=fullstack")).status()).toBe(403);
  } finally {
    await second.close();
  }

  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("heading", { name: "Sign in to GigBid" })).toBeVisible();
  expect((await page.request.get("/api/v1/personas")).status()).toBe(401);
  await page.getByRole("textbox", { name: "Username" }).fill("BROWSER.FULLSTACK");
  await page.getByLabel("Password", { exact: true }).fill(passphrase);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByLabel("Active persona")).toHaveValue("fullstack");
});
