import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { openPanel } from "./helpers.js";

async function openWizard(page) {
  const panel = await openPanel(page);
  await panel.getByRole("link", { name: "Aufgaben", exact: true }).click();
  await panel.getByRole("button", { name: "+ Aufgabe", exact: true }).click();
  return { panel, dialog: panel.getByRole("dialog", { name: "Neue Aufgabe" }) };
}

test("new tasks use a validated five-step wizard with generated IDs and preview", async ({ page }) => {
  const { panel, dialog } = await openWizard(page);
  const steps = dialog.locator("[data-task-wizard-step]");
  await expect(steps).toHaveCount(5);
  await expect(steps.first()).toHaveAttribute("aria-current", "step");

  await dialog.getByRole("button", { name: "Weiter" }).click();
  const name = dialog.getByRole("textbox", { name: "Name" });
  await expect(name).toHaveAttribute("aria-invalid", "true");
  await expect(steps.first()).toHaveAttribute("aria-current", "step");

  await name.fill("Fenster & Rahmen putzen");
  await expect(dialog.locator('[name="id"]')).toHaveValue("fenster_rahmen_putzen");
  await dialog.getByRole("button", { name: "Weiter" }).click();
  await expect(dialog.getByRole("combobox", { name: "Zuweisung" })).toBeVisible();
  await dialog.getByRole("button", { name: "Weiter" }).click();
  await expect(dialog.getByRole("combobox", { name: "Zeitplan" })).toBeVisible();
  await dialog.getByRole("button", { name: "Weiter" }).click();
  await expect(dialog.getByText(/Expertenoptionen/)).toBeVisible();
  await dialog.getByRole("button", { name: "Weiter" }).click();

  await expect(dialog.getByRole("heading", { name: "So wird die Aufgabe angelegt" })).toBeVisible();
  await expect(dialog.locator("[data-review-name]")).toHaveText("Fenster & Rahmen putzen");
  await expect(dialog.locator("[data-review-preview]")).toContainText("Eine Aufgabe");
  await dialog.getByRole("button", { name: "Speichern" }).click();
  await expect(dialog).toBeHidden();

  const calls = await page.evaluate(() => window.__householdTaskCalls);
  expect(calls).toContainEqual(expect.objectContaining({
    type: "household_tasks/save_task",
    task_id: "fenster_rahmen_putzen",
  }));
  expect(calls.filter((call) => call.type === "household_tasks/preview_task").length).toBeGreaterThan(0);
});

test("wizard navigation supports going back without losing values", async ({ page }) => {
  const { dialog } = await openWizard(page);
  await dialog.getByRole("textbox", { name: "Name" }).fill("Keller prüfen");
  await dialog.getByRole("button", { name: "Weiter" }).click();
  await dialog.getByRole("button", { name: "Zurück" }).click();
  await expect(dialog.getByRole("textbox", { name: "Name" })).toHaveValue("Keller prüfen");
  await expect(dialog.locator('[data-task-wizard-step="2"]')).toBeEnabled();
});

test("existing tasks expose every editor section and keep their stable ID", async ({ page }) => {
  const panel = await openPanel(page);
  await panel.getByRole("link", { name: "Aufgaben", exact: true }).click();
  await panel.getByRole("button", { name: "Bearbeiten" }).first().click();
  const dialog = panel.getByRole("dialog", { name: "Aufgabe bearbeiten" });
  const steps = dialog.locator("[data-task-wizard-step]");
  await expect(steps).toHaveCount(5);
  for (const step of await steps.all()) await expect(step).toBeEnabled();
  await expect(dialog.locator('[name="id"]')).toHaveValue("frostschutz");
  await dialog.locator('[data-task-wizard-step="3"]').click();
  await expect(dialog.getByRole("combobox", { name: "Zeitplan" })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Speichern" })).toBeVisible();
});

test("task editor configures automatic credit with a default person and grace period", async ({ page }) => {
  const panel = await openPanel(page);
  await panel.getByRole("link", { name: "Aufgaben", exact: true }).click();
  await panel.getByRole("button", { name: "Bearbeiten" }).first().click();
  const dialog = panel.getByRole("dialog", { name: "Aufgabe bearbeiten" });
  await dialog.locator('[data-task-wizard-step="4"]').click();
  await dialog.locator("details.advanced-fields").evaluate((element) => { element.open = true; });

  const automatic = dialog.getByRole("checkbox", { name: "Ohne Bestätigung automatisch gutschreiben" });
  await automatic.check();
  await dialog.getByRole("combobox", { name: "Standardperson" }).selectOption("alina");
  await dialog.getByRole("textbox", { name: "Kulanzzeit nach Fälligkeit" }).fill("06:30:00");
  await dialog.getByRole("button", { name: "Speichern" }).click();

  const saveCall = await page.evaluate(() => window.__householdTaskCalls.findLast(
    (call) => call.type === "household_tasks/save_task"
  ));
  expect(saveCall.task.automatic_completion).toEqual({
    enabled: true,
    default_person: "alina",
    after: "06:30:00",
  });
});

test("dirty task editors require confirmation before discarding changes", async ({ page }) => {
  const { dialog } = await openWizard(page);
  await dialog.getByRole("textbox", { name: "Name" }).fill("Nicht verlieren");

  page.once("dialog", async (confirmation) => {
    expect(confirmation.message()).toContain("Ungespeicherte Änderungen");
    await confirmation.dismiss();
  });
  await dialog.getByRole("button", { name: "Abbrechen" }).click();
  await expect(dialog).toBeVisible();

  page.once("dialog", (confirmation) => confirmation.accept());
  await dialog.getByRole("button", { name: "Abbrechen" }).click();
  await expect(dialog).toBeHidden();
});

test("mobile wizard keeps navigation reachable and uses adequate touch targets", async ({ page, isMobile }) => {
  test.skip(!isMobile, "Mobile browser project only");
  const { dialog } = await openWizard(page);
  const actions = dialog.locator(".task-editor-actions");
  await expect(actions).toHaveCSS("position", "sticky");
  const stepSizes = await dialog.locator("[data-task-wizard-step]").evaluateAll((buttons) =>
    buttons.map((button) => ({ width: button.getBoundingClientRect().width, height: button.getBoundingClientRect().height })),
  );
  expect(stepSizes.every(({ width, height }) => width >= 44 && height >= 44)).toBe(true);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
});

test("wizard steps have no serious automated accessibility violations", async ({ page }) => {
  const { dialog } = await openWizard(page);
  await dialog.getByRole("textbox", { name: "Name" }).fill("Barrierefreie Aufgabe");
  for (let step = 1; step <= 5; step += 1) {
    const results = await new AxeBuilder({ page })
      .include("household-tasks-panel")
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    expect(
      results.violations.filter((violation) => ["serious", "critical"].includes(violation.impact)),
      `Accessibility violations in wizard step ${step}`,
    ).toEqual([]);
    if (step < 5) await dialog.getByRole("button", { name: "Weiter" }).click();
  }
});

test("wizard navigation is localized in English", async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-08-01T12:00:00+02:00"));
  await page.goto("/tests/ui/harness/?lang=en");
  const panel = page.locator("household-tasks-panel");
  await expect(panel.getByRole("heading", { name: "Household tasks" })).toBeVisible();
  await panel.getByRole("link", { name: "Tasks", exact: true }).click();
  await panel.getByRole("button", { name: "+ Task", exact: true }).click();
  const dialog = panel.getByRole("dialog", { name: "New task" });
  await expect(dialog.locator('[data-task-wizard-step="1"]')).toContainText("Basics");
  await expect(dialog.getByRole("button", { name: "Next" })).toBeVisible();
  await expect(dialog.locator('[data-task-wizard-step="5"]')).toContainText("Review");
  await expect(dialog.getByRole("checkbox", { name: "Require the full checklist before completion" })).toBeVisible();
});
