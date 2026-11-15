import { expect, test } from "@playwright/test";

test("selects a persona and navigates the workspace at mobile width", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 815 });
  await page.goto("/");
  await page.getByRole("combobox", { name: "Active persona" }).selectOption("data_analyst");
  await page.getByRole("searchbox", { name: "Search jobs" }).fill("Power BI");
  await page.getByRole("button", { name: /Power BI sales dashboard/i }).click();
  await expect(page.getByRole("heading", { name: /Power BI sales dashboard/i })).toBeVisible();
  await expect(page.getByRole("button", { name: "Analyze job" })).toBeVisible();
  const fitsViewport = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
  expect(fitsViewport).toBe(true);
  await page.getByRole("button", { name: "Inbox", exact: true }).click();
  await expect(page.getByRole("searchbox", { name: "Search jobs" })).toBeVisible();
  await page.getByRole("navigation", { name: "Workspace" }).getByRole("button", { name: "Messages" }).click();
  await page.getByRole("link", { name: "View proposal pipeline" }).click();
  await expect(page.getByRole("heading", { name: "Pipeline", exact: true })).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Active persona" })).toHaveValue("data_analyst");
});

test("resumes an approval after refresh and submits the proposal once", async ({ page }) => {
  await page.goto("/?persona=fullstack&job=job_web_stripe");
  await page.getByRole("button", { name: "Analyze job" }).click();
  await expect(page.getByRole("heading", { name: "Review proposal" })).toBeVisible();
  await expect(page.getByLabel("Submission quote")).toContainText("12");
  await page.reload();
  await expect(page.getByRole("heading", { name: "Review proposal" })).toBeVisible();
  await page.getByRole("button", { name: "Approve and submit" }).click();
  await expect(page.getByText("Proposal submitted", { exact: true })).toBeVisible();
  await page.getByRole("navigation", { name: "Workspace" }).getByRole("button", { name: "Pipeline" }).click();
  const pipeline = page.getByRole("region", { name: "Proposal pipeline" });
  await expect(pipeline.getByText("12 Connects spent")).toBeVisible();
  await expect(pipeline.getByRole("button", { name: "Open job" })).toHaveCount(1);
});

test("keeps an approval pending after an empty rejection and records a reason", async ({ page }) => {
  await page.goto("/?persona=ai_ml&job=job_ai_rag");
  await page.getByRole("button", { name: "Analyze job" }).click();
  await page.getByRole("button", { name: "Reject", exact: true }).click();
  await page.getByRole("button", { name: "Reject proposal", exact: true }).click();
  await expect(page.getByRole("alert").getByText(/enter a reason/i)).toBeVisible();
  await page.getByRole("textbox", { name: "Rejection reason" }).fill("No capacity for this project.");
  await page.getByRole("button", { name: "Reject proposal", exact: true }).click();
  await expect(page.getByText("Proposal rejected", { exact: true })).toBeVisible();
  await expect(page.getByText("No capacity for this project.", { exact: true })).toBeVisible();
});

test("edits and rechecks a draft before presenting it for approval again", async ({ page }) => {
  await page.goto("/?persona=data_analyst&job=job_data_pbi");
  await page.getByRole("button", { name: "Analyze job" }).click();
  await page.getByRole("button", { name: "Edit draft" }).click();
  const editor = page.getByRole("textbox", { name: "Revised cover letter" });
  await editor.fill(`${await editor.inputValue()}\n\nThanks for your time.`);
  await page.getByRole("button", { name: "Recheck draft" }).click();
  await expect(page.getByText("Draft 2 · Waiting for your decision", { exact: true })).toBeVisible();
  await expect(page.getByText(/Thanks for your time\./i)).toBeVisible();
  await expect(page.getByRole("button", { name: "Approve and submit" })).toBeVisible();
  await expect(page.getByText("Proposal submitted", { exact: true })).toHaveCount(0);
});

test("reports an injected marketplace outage and recovers after refresh", async ({ page, request }) => {
  const simulator = process.env.GIGBID_TEST_GIGSIM_URL ?? "http://127.0.0.1:8081";
  const adminToken = process.env.GIGSIM_ADMIN_TOKEN;
  if (!adminToken) throw new Error("Set GIGSIM_ADMIN_TOKEN for the isolated Playwright simulator.");
  const headers = { "X-Admin-Token": adminToken };
  const response = await request.put(`${simulator}/admin/faults`, { headers, data: { down: true } });
  expect(response.ok()).toBe(true);
  try {
    await page.goto("/");
    await expect(page.getByRole("alert").getByText("Marketplace unavailable")).toBeVisible();
  } finally {
    const reset = await request.put(`${simulator}/admin/faults`, { headers, data: { down: false } });
    expect(reset.ok()).toBe(true);
  }
  await page.getByRole("button", { name: "Refresh" }).click();
  await expect(page.getByText("Marketplace unavailable")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Add Stripe subscriptions/i })).toBeVisible();
});
