import { expect } from "@playwright/test";

export async function openPanel(page, path = "/tests/ui/harness/") {
  await page.clock.setFixedTime(new Date("2026-08-01T12:00:00+02:00"));
  await page.goto(path);
  const panel = page.locator("household-tasks-panel");
  await expect(panel.getByRole("heading", { name: "Haushaltsaufgaben" })).toBeVisible();
  await expect(panel.getByRole("heading", { name: "Frostschutz beim eigenen Auto prüfen", exact: true })).toBeVisible();
  return panel;
}

export function captureConsoleErrors(page) {
  const errors = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("pageerror", (error) => errors.push(error.message));
  return errors;
}
