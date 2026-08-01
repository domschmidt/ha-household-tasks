import { expect, test } from "@playwright/test";
import { captureConsoleErrors, openPanel } from "./helpers.js";

test("tabs use deep links and history exposes evidence", async ({ page }) => {
  const panel = await openPanel(page);
  await panel.getByRole("link", { name: "Verlauf" }).click();
  await expect(page).toHaveURL(/view=history/);
  await expect(panel.getByText("📎 1")).toBeVisible();
  await panel.getByRole("button", { name: "Akte öffnen" }).click();
  await expect(panel.getByRole("dialog", { name: "Gelbe Tonne rausstellen" })).toBeVisible();
  await expect(panel.getByRole("button", { name: /tonne[.]jpg/ })).toBeVisible();
});

test("quick-task validation never targets an invisible required control", async ({ page }) => {
  const errors = captureConsoleErrors(page);
  const panel = await openPanel(page);
  await panel.getByRole("button", { name: "+ Schnellaufgabe" }).click();
  const dialog = panel.getByRole("dialog", { name: "Schnellaufgabe" });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "Aufgabe hinzufügen" }).click();
  await expect(dialog.locator("[aria-invalid=true]")).toHaveCount(1);
  expect(errors).toEqual([]);
});

test("NFC creator is aligned and invokes Home Assistant", async ({ page }) => {
  const panel = await openPanel(page);
  await panel.getByRole("link", { name: "Aufgaben", exact: true }).click();
  await panel.getByRole("button", { name: "Bearbeiten" }).first().click();
  const dialog = panel.getByRole("dialog", { name: "Aufgabe bearbeiten" });
  await dialog.locator("details.advanced-fields").evaluate((element) => { element.open = true; });
  const input = dialog.locator("[name=nfc_tag_id]");
  const add = dialog.locator("[data-toggle-tag-creator]");
  const [inputBox, addBox] = await Promise.all([input.boundingBox(), add.boundingBox()]);
  expect(inputBox).not.toBeNull();
  expect(addBox).not.toBeNull();
  expect(Math.abs((inputBox.y + inputBox.height / 2) - (addBox.y + addBox.height / 2))).toBeLessThanOrEqual(4);
  await add.click();
  await dialog.locator("[name=new_tag_name]").fill("Neuer Test-Tag");
  await dialog.getByRole("button", { name: "Tag anlegen", exact: true }).click();
  await expect(input).toHaveValue("new-tag");
  const calls = await page.evaluate(() => window.__householdTaskCalls);
  expect(calls).toContainEqual({ type: "tag/create", name: "Neuer Test-Tag" });
});

test("mobile views do not overflow horizontally", async ({ page, isMobile }) => {
  test.skip(!isMobile, "Mobile browser project only");
  const panel = await openPanel(page);
  for (const view of ["Heute", "Aufgaben", "Verlauf"]) {
    await panel.getByRole("link", { name: view, exact: true }).click();
    const layout = await page.evaluate(() => {
      const viewportWidth = document.documentElement.clientWidth;
      const offenders = [...document.querySelectorAll("*")]
        .map((element) => {
          const bounds = element.getBoundingClientRect();
          return {
            element: `${element.tagName.toLowerCase()}.${[...element.classList].join(".")}`,
            left: Math.round(bounds.left),
            right: Math.round(bounds.right),
          };
        })
        .filter(({ left, right }) => left < -1 || right > viewportWidth + 1)
        .slice(0, 5);
      return {
        overflow: document.documentElement.scrollWidth - viewportWidth,
        offenders,
      };
    });
    expect(layout.overflow, `${view} has horizontal overflow: ${JSON.stringify(layout.offenders)}`).toBeLessThanOrEqual(1);
  }
});

test("small attachments use the chunked upload API", async ({ page }) => {
  const panel = await openPanel(page);
  const card = panel.locator("[data-card-occurrence=open-frost]");
  await card.locator("details.more-actions").evaluate((element) => { element.open = true; });
  await card.getByRole("button", { name: /Foto oder Beleg/ }).click();
  await panel.locator("[data-attachment-file]").setInputFiles({
    name: "proof.png",
    mimeType: "image/png",
    buffer: Buffer.from("small-image"),
  });
  await expect.poll(async () => page.evaluate(() => window.__householdTaskCalls.some((call) => call.type === "household_tasks/add_attachment_chunk"))).toBe(true);
});
