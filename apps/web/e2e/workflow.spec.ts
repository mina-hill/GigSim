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

test("reviews, edits and sends a reply once, then rejects a new client turn", async ({ page, request }) => {
  const simulator = process.env.GIGBID_TEST_GIGSIM_URL ?? "http://127.0.0.1:8081";
  const adminToken = process.env.GIGSIM_ADMIN_TOKEN;
  if (!adminToken) throw new Error("Set GIGSIM_ADMIN_TOKEN for the isolated Playwright simulator.");
  const accountTokens = process.env.GIGSIM_API_TOKENS ?? "dev-token-ai:persona_ai_ml";
  const token = accountTokens.split(",").map((pair) => pair.trim().split(":"))
    .find(([, account]) => account === "persona_ai_ml")?.[0];
  if (!token) throw new Error("Configure the synthetic AI persona for the Playwright simulator.");
  const advance = async (n: number) => {
    const response = await request.post(`${simulator}/admin/tick`, {
      headers: { "X-Admin-Token": adminToken }, data: { n },
    });
    expect(response.ok()).toBe(true);
  };

  // Complete one synthetic proposal setup directly to get a deterministic interviewing client.
  await advance(2);
  const auth = { Authorization: `Bearer ${token}` };
  const setup = await request.post(`${simulator}/proposals`, {
    headers: { ...auth, "Idempotency-Key": "playwright:conversation:forecast:draft" },
    data: {
      job_id: "job_later_ml",
      bid_amount: 2500,
      bid_type: "fixed",
      screening_answers: [],
      cover_letter: [
        "For weekly demand forecasting across 300 SKUs, I built the Python machine learning workflow in a comparable synthetic case study.",
        "I built the Python forecasting pipeline to align weekly demand and SKU history before training.",
        "I shipped a machine learning case study that compared seasonal baselines against supervised models using rolling validation windows.",
        "My Python case study documented validation for weekly demand, errors by SKU, and a reproducible forecast handover.",
      ].join(" "),
    },
  });
  expect(setup.ok()).toBe(true);
  const proposal = await setup.json() as { proposal_id: string };
  const submitted = await request.post(`${simulator}/proposals/${proposal.proposal_id}/submit`, {
    headers: { ...auth, "Idempotency-Key": "playwright:conversation:forecast:submit" },
  });
  expect(submitted.ok()).toBe(true);
  await advance(4);

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/?persona=ai_ml&view=messages");
  const threads = page.getByRole("region", { name: "Conversations", exact: true });
  const thread = page.getByRole("region", { name: "Messages in conversation", exact: true });
  await threads.getByRole("button", { name: /Time-series demand forecasting model/i }).click();
  await thread.getByText("Offer an interview time").click();
  await thread.getByLabel("Start (local time)").fill("2026-11-17T12:00");
  await thread.getByLabel("End (local time)").fill("2026-11-17T12:30");
  await thread.getByRole("button", { name: "Draft reply" }).click();

  const review = thread.getByRole("region", { name: "Review reply" });
  await expect(review).toContainText("Interview request · Reply draft 1");
  await expect(review).toContainText("UTC");
  await expect(thread.locator(".bubble--own")).toHaveCount(0);
  await review.getByRole("button", { name: "Edit reply" }).click();
  const editor = review.getByRole("textbox", { name: "Revised reply" });
  await editor.fill(`${await editor.inputValue()}\n\nThanks for your time.`);
  await review.getByRole("button", { name: "Recheck reply" }).click();
  await expect(review).toContainText("Reply draft 2");
  await expect(thread.locator(".bubble--own")).toHaveCount(0);
  await page.reload();
  await expect(review).toContainText("Thanks for your time.");
  await review.getByRole("button", { name: "Approve and send" }).click();
  await expect(thread.getByText("Reply sent", { exact: true })).toBeVisible();
  await expect(thread.locator(".bubble--own")).toHaveCount(1);
  await expect(thread.locator(".bubble--own")).toContainText("Thanks for your time.");

  await advance(4);
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await thread.getByRole("button", { name: "Draft reply", exact: true }).click();
  await review.getByRole("button", { name: "Reject", exact: true }).click();
  await review.getByRole("button", { name: "Reject reply", exact: true }).click();
  await expect(thread.getByRole("alert").getByText(/enter a reason/i)).toBeVisible();
  await review.getByRole("textbox", { name: "Rejection reason" }).fill("The client already confirmed a time.");
  await review.getByRole("button", { name: "Reject reply", exact: true }).click();
  await expect(thread.getByText("Reply rejected", { exact: true })).toBeVisible();
  await expect(thread.getByText("The client already confirmed a time.", { exact: true })).toBeVisible();
  await expect(thread.locator(".bubble--own")).toHaveCount(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
