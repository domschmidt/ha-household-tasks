import { expect, test } from "@playwright/test";
import { openPanel } from "./helpers.js";

test("today worklist visual contract", async ({ page }) => {
  const panel = await openPanel(page);
  await expect(panel).toHaveScreenshot("today-dashboard.png", { animations: "disabled" });
});

test("household ranking visual contract", async ({ page }) => {
  const panel = await openPanel(page);
  await panel.getByRole("link", { name: "Ranking", exact: true }).click();
  await expect(panel).toHaveScreenshot("household-dashboard.png", { animations: "disabled" });
});

test("task editor visual contract", async ({ page }) => {
  const panel = await openPanel(page);
  await panel.getByRole("link", { name: "Aufgaben", exact: true }).click();
  await panel.getByRole("button", { name: "Bearbeiten" }).first().click();
  await expect(panel.getByRole("dialog", { name: "Aufgabe bearbeiten" })).toHaveScreenshot("task-editor.png", { animations: "disabled" });
});

test("new task wizard visual contract", async ({ page }) => {
  const panel = await openPanel(page);
  await panel.getByRole("link", { name: "Aufgaben", exact: true }).click();
  await panel.getByRole("button", { name: "+ Aufgabe", exact: true }).click();
  await expect(panel.getByRole("dialog", { name: "Neue Aufgabe" })).toHaveScreenshot("task-wizard.png", { animations: "disabled" });
});

test("history visual contract", async ({ page }) => {
  const panel = await openPanel(page);
  await panel.getByRole("link", { name: "Verlauf" }).click();
  await expect(panel.locator(".content")).toHaveScreenshot("history.png", { animations: "disabled" });
});
