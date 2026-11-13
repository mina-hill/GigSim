import { expect, test } from "@playwright/test";

test("guest can check out with a test card", async ({ page }) => {
  await page.goto("/listing/demo-bike");
  await page.getByRole("button", { name: "Buy now" }).click();
  await page.getByLabel("Card number").fill("4242 4242 4242 4242");
  await page.getByRole("button", { name: "Pay" }).click();
  await expect(page.getByText("Order confirmed")).toBeVisible();
});
