import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { openPanel } from "./helpers.js";

test("main panel has no serious automated accessibility violations", async ({ page }) => {
  await openPanel(page);
  const results = await new AxeBuilder({ page })
    .include("household-tasks-panel")
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  const blocking = results.violations.filter((violation) => ["serious", "critical"].includes(violation.impact));
  expect(blocking).toEqual([]);
});

test("task dialog traps focus and closes with Escape", async ({ page }) => {
  const panel = await openPanel(page);
  await panel.getByRole("link", { name: "Aufgaben", exact: true }).click();
  const edit = panel.getByRole("button", { name: "Bearbeiten" }).first();
  await edit.focus();
  await edit.press("Enter");
  const dialog = panel.getByRole("dialog", { name: "Aufgabe bearbeiten" });
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(edit).toBeFocused();
});
