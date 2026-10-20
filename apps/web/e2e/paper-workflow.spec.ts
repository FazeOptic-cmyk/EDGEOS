import { expect, test } from "@playwright/test";

test("paper bundle remains unrealized until fills and settlement are recorded", async ({ page }) => {
  await page.goto("/portfolio");
  await expect(page.getByText("Portfolio unavailable", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "New paper position" }).click();
  const create = page.getByRole("dialog");
  await expect(create.getByLabel("Verified opportunity ID")).toHaveValue("demo-opportunity-001");
  await create.getByLabel("Total paper capital").fill("210");
  await create.getByLabel("Research note").fill(`Browser verification ${test.info().project.name}`);
  await create.getByRole("button", { name: "Calculate and create bundle" }).click();
  await expect(create).toHaveCount(0);
  await expect(page.getByText("Paper position recorded.", { exact: false })).toBeVisible();

  const position = page.locator("tbody tr").last();
  await expect(position).toContainText("Not realized");
  await position.getByRole("button", { name: "Manage", exact: true }).click();
  const manage = page.getByRole("dialog");
  await expect(manage.getByRole("tab", { name: "Record fill" })).toHaveAttribute("aria-selected", "true");
  // This fixture's two decimal-odds legs are exactly $100 at 2.20
  // and $110 at 2.00. One leg filled is exposed, not realized profit.
  await manage.getByLabel("Quantity", { exact: true }).fill("100");
  await manage.getByLabel("Fill price", { exact: true }).fill("2.2");
  await manage.getByLabel("Fee", { exact: true }).fill("0");
  await manage.getByRole("button", { name: "Append simulated fill" }).click();
  await expect(manage).toHaveCount(0);
  await expect(position).toContainText("Partially filled");
  await expect(position).toContainText("Not realized");

  await position.getByRole("button", { name: "Manage", exact: true }).click();
  await manage.getByLabel("Position leg").selectOption({ index: 2 });
  await manage.getByLabel("Quantity", { exact: true }).fill("110");
  await manage.getByLabel("Fill price", { exact: true }).fill("2");
  await manage.getByRole("button", { name: "Append simulated fill" }).click();
  await expect(manage).toHaveCount(0);
  await expect(position).toContainText("Not realized");

  await position.getByRole("button", { name: "Manage", exact: true }).click();
  await manage.getByRole("tab", { name: "Settle", exact: true }).click();
  await manage.getByRole("button", { name: "Settle from recorded fills" }).click();
  await expect(manage).toHaveCount(0);
  await expect(position).toContainText("Settled");
  await expect(position).toContainText("$10.00");
  await page.reload();
  await expect(page.locator("tbody tr").last()).toContainText("$10.00");
});
