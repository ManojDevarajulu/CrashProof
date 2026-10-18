import { expect, test } from "@playwright/test";

// Seed 1 is always exported; naive fails I1 on it whatever the seed count.
test("from a failing count to the duplicate refund and back to hardened", async ({ page }) => {
  await page.goto("./");
  await expect(page.getByText(/Violating seeds out of \d+: hardened 0 · keyed \d+ · naive \d+/)).toBeVisible();

  const naiveRow = page.getByRole("row").filter({ has: page.getByRole("rowheader", { name: "naive", exact: true }) });
  await naiveRow.getByRole("button").first().click();
  await expect(page.getByRole("heading", { name: /naive · I1 · \d+ seeds/ })).toBeVisible();

  await page.locator(".panel").getByRole("link", { name: "1", exact: true }).click();
  await expect(page).toHaveURL(/#\/seed\/1\/naive$/);
  await expect(page.getByRole("status")).toHaveText("✓ matches the file");
  await expect(page.getByText("World recorded 2 refunds and 1 label.")).toBeVisible();
  await expect(page.locator("tr.hit")).toHaveCount(2);

  await page.getByRole("link", { name: "hardened ✓" }).click();
  await expect(page).toHaveURL(/#\/seed\/1\/hardened$/);
  await expect(page.getByRole("status")).toHaveText("✓ matches the file");
  await expect(page.getByText("No invariant violated.")).toBeVisible();
  await expect(page.getByText("UNKNOWN (#31) → reconcile: applied → CONFIRMED (#35)")).toBeVisible();
});

test("an unknown seed explains how to reproduce it locally", async ({ page }) => {
  await page.goto("./#/seed/999999/naive");
  await expect(page.getByRole("alert")).toContainText("make replay SEED=999999 RUNTIME=naive");
});
