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

test("guided settings visual contract", async ({ page }) => {
  const panel = await openPanel(page);
  await panel.getByRole("link", { name: "Einstellungen", exact: true }).click();
  await expect(panel.locator(".content")).toHaveScreenshot("guided-settings.png", { animations: "disabled" });
});

test("history visual contract", async ({ page }) => {
  const panel = await openPanel(page);
  await panel.getByRole("link", { name: "Verlauf" }).click();
  await expect(panel.locator(".content")).toHaveScreenshot("history.png", { animations: "disabled" });
});

test("rule intelligence visual contract", async ({ page }) => {
  const panel = await openPanel(page);
  await page.evaluate(() => window.__setHouseholdTasksFixture({
    tasks: {
      laundry: { enabled: true, name: "Waschmaschine leeren", assignee: "dominik", assignment: { type: "fixed" }, schedule: { type: "manual" }, shadow: { enabled: true, review_at: "2026-08-01T10:00:00Z" } },
      fold: { enabled: true, name: "Wäsche falten", assignee: "alina", assignment: { type: "fixed" }, schedule: { type: "manual" }, depends_on: ["laundry"] },
    },
    observation_suggestions: [{ id: "laundry:washer:on:off", task_id: "laundry", task_name: "Waschmaschine leeren", entity_id: "binary_sensor.washer", from: "on", to: "off", samples: 4, typical_delay_seconds: 300, proposed_schedule: { type: "state_trigger", triggers: [{ entity_id: "binary_sensor.washer", from: "on", to: "off" }] } }],
    shadow_evaluations: [{ task_id: "laundry", due: "2026-08-02T12:00:00Z", assignee: "dominik" }, { task_id: "laundry", due: "2026-08-03T12:00:00Z", assignee: "dominik" }],
    rule_insights: {
      effects: {
        laundry: { task_id: "laundry", name: "Waschmaschine leeren", generated: 14, completed: 11, overdue: 1, completion_rate: 78.6, median_delay_minutes: 42, virtual_triggers: 2 },
        fold: { task_id: "fold", name: "WÃ¤sche falten", generated: 8, completed: 4, overdue: 2, completion_rate: 50, median_delay_minutes: 180, virtual_triggers: 0 },
      },
      noise_findings: [{ task_id: "fold", severity: "warning", message: "Nur 50 % von 8 erzeugten Aufgaben wurden erledigt." }],
      improvement_suggestions: [{ task_id: "fold", title: "FÃ¤lligkeit an Gewohnheit anpassen", message: "Erledigungen liegen typischerweise gegen 20:00 Uhr.", patch: { "schedule.time": "20:00:00" } }],
    },
    rule_graph: {
      nodes: [
        { id: "entity:washer", kind: "entity", label: "binary_sensor.washer" },
        { id: "rule:laundry", kind: "rule", label: "Waschmaschine leeren" },
        { id: "task:laundry", kind: "task", label: "Waschmaschine leeren" },
        { id: "task:fold", kind: "task", label: "Wäsche falten" },
        { id: "person:dominik", kind: "person", label: "Dominik" },
      ],
      edges: [
        { source: "entity:washer", target: "rule:laundry", kind: "triggers", label: "steuert" },
        { source: "rule:laundry", target: "task:laundry", kind: "creates", label: "erzeugt" },
        { source: "task:laundry", target: "task:fold", kind: "blocks", label: "blockiert" },
        { source: "task:laundry", target: "person:dominik", kind: "assigns", label: "weist zu" },
      ],
      issues: [],
    },
  }));
  await panel.getByRole("link", { name: "Regeln", exact: true }).click();
  await expect(panel.locator(".content")).toHaveScreenshot("rule-intelligence.png", { animations: "disabled" });
});
