import { expect, test } from "@playwright/test";

// Seed 1 is always exported; naive fails I1 on it whatever the seed count.
test("from a failing count to the duplicate refund and back to hardened", async ({ page }) => {
  await page.goto("./");
  await expect(page.getByRole("img", { name: /^hardened: 0 of \d+ seeds failed$/ })).toBeVisible();

  await page.getByRole("button", { name: /^naive I1: \d+ seeds, list them$/ }).click();
  await expect(page.getByRole("heading", { name: /\d+ seeds where naive fails I1/ })).toBeVisible();

  await page.locator(".seeds").getByRole("link", { name: "1", exact: true }).click();
  await expect(page).toHaveURL(/#\/seed\/1\/naive$/);
  await expect(page.getByRole("status")).toHaveText("trace file matches its hash");
  await expect(page.getByRole("img", { name: /^naive: \d+ events, \d+ faults, 2 refunds, 1 labels$/ })).toBeVisible();
  await expect(page.locator(".log li.is-hit")).toHaveCount(2);

  await page.getByRole("button", { name: /hardened/ }).click();
  await expect(page).toHaveURL(/#\/seed\/1\/hardened$/);
  await expect(page.getByRole("status")).toHaveText("trace file matches its hash");
  await expect(page.getByText("All nine invariants hold for this run.")).toBeVisible();
  await expect(page.getByText("reconcile: applied → CONFIRMED #35")).toBeVisible();
});

test("the sidebar opens a trace", async ({ page }) => {
  await page.goto("./");
  await page.getByRole("navigation", { name: "Runs" }).getByRole("link", { name: /Seed 2\b/ }).click();
  await expect(page.getByRole("heading", { name: "Seed 2", exact: true })).toBeVisible();
});

test("an unknown seed explains how to reproduce it locally", async ({ page }) => {
  await page.goto("./#/seed/999999/naive");
  await expect(page.getByRole("alert")).toContainText("make replay SEED=999999 RUNTIME=naive");
});
