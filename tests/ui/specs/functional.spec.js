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

test("today owns daily planning while ranking shows household insights", async ({ page }) => {
  const panel = await openPanel(page);

  await expect(panel.locator(".hero")).toBeVisible();
  await expect(panel.getByRole("button", { name: "+ Schnellaufgabe" })).toBeVisible();
  await expect(panel.locator(".task-card")).toHaveCount(1);
  await expect(panel.locator(".context-home")).toBeVisible();
  await expect(panel.getByRole("button", { name: "Heute planen" })).toBeVisible();
  await expect(panel.locator(".people-strip")).toHaveCount(0);
  await expect(panel.locator(".ranking-card")).toHaveCount(0);

  await panel.getByRole("link", { name: "Ranking", exact: true }).click();

  await expect(page).toHaveURL(/view=ranking/);
  await expect(panel.locator(".hero")).toHaveCount(0);
  await expect(panel.getByRole("button", { name: "+ Schnellaufgabe" })).toHaveCount(0);
  await expect(panel.locator(".context-home")).toHaveCount(0);
  await expect(panel.locator(".stack-strip")).toBeVisible();
  await expect(panel.locator(".people-strip")).toBeVisible();
  await expect(panel.locator(".ranking-card")).toBeVisible();
  await expect(panel.locator(".task-card")).toHaveCount(0);
});

test("legacy dashboard links remain compatible and open ranking", async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-08-01T12:00:00+02:00"));
  await page.goto("/tests/ui/harness/?view=dashboard");
  const panel = page.locator("household-tasks-panel");

  await expect(panel.locator('a[data-view="ranking"]')).toHaveClass(/active/);
  await expect(panel.locator(".ranking-card")).toBeVisible();
  await expect(panel.locator(".hero")).toHaveCount(0);
});

test("live updates and iOS resume refresh stale panel data", async ({ page }) => {
  const panel = await openPanel(page);
  await expect.poll(async () => page.evaluate(() => window.__householdTaskSubscriptions)).toEqual([
    "household_tasks_updated",
  ]);

  await page.evaluate(() => {
    window.__householdTaskCalls.length = 0;
    window.__setHouseholdTasksServerState({ occurrences: [] });
    window.__emitHouseholdTasksUpdated();
  });

  await expect(panel.locator(".task-card")).toHaveCount(0);
  await expect.poll(async () => page.evaluate(() => window.__householdTaskCalls
    .filter((call) => call.type === "household_tasks/get").length)).toBe(1);

  await page.evaluate(() => {
    window.__resetHouseholdTasksServerState();
    document.querySelector("household-tasks-panel")._lastRefreshAt = 0;
    window.dispatchEvent(new Event("pageshow"));
  });

  await expect.poll(async () => page.evaluate(() => window.__householdTaskCalls
    .filter((call) => call.type === "household_tasks/get").length)).toBe(2);
  await expect(panel.locator(".task-card")).toHaveCount(1);
});

test("live refresh does not discard an open editor", async ({ page }) => {
  const panel = await openPanel(page);
  await panel.getByRole("button", { name: "+ Schnellaufgabe" }).click();
  const dialog = panel.getByRole("dialog", { name: "Schnellaufgabe" });
  await expect(dialog).toBeVisible();

  await page.evaluate(() => {
    window.__householdTaskCalls.length = 0;
    window.__emitHouseholdTasksUpdated();
  });
  await page.waitForTimeout(300);

  await expect(dialog).toBeVisible();
  expect(await page.evaluate(() => window.__householdTaskCalls
    .filter((call) => call.type === "household_tasks/get").length)).toBe(0);

  await dialog.getByRole("button", { name: "Abbrechen" }).click();
  await expect.poll(async () => page.evaluate(() => window.__householdTaskCalls
    .filter((call) => call.type === "household_tasks/get").length)).toBe(1);
});

test("task action menus open toward available viewport space", async ({ page }) => {
  const panel = await openPanel(page);
  await page.evaluate(() => {
    const element = document.querySelector("household-tasks-panel");
    const base = structuredClone(element._data.occurrences.find((item) => item.id === "open-frost"));
    element._data.occurrences = Array.from({ length: 12 }, (_, index) => ({
      ...structuredClone(base),
      id: `position-${index}`,
      title: `[Dominik] Position ${index + 1}`,
      due: `2026-07-31T${String(8 + index).padStart(2, "0")}:00:00+02:00`,
    }));
    element._render();
  });

  const menus = panel.locator("details.more-actions");
  const menuCount = await menus.count();
  expect(menuCount).toBe(12);
  const firstMenu = menus.nth(0);
  const lastMenu = menus.nth(menuCount - 1);

  await firstMenu.evaluate((element) => element.closest(".task-card").scrollIntoView({ block: "start" }));
  await firstMenu.locator("summary").click();
  await expect(firstMenu).not.toHaveClass(/opens-up/);
  await firstMenu.locator("summary").click();

  await lastMenu.evaluate((element) => element.closest(".task-card").scrollIntoView({ block: "end" }));
  await lastMenu.locator("summary").click();
  await expect(lastMenu).toHaveClass(/opens-up/);
  const bounds = await lastMenu.locator(":scope > div").boundingBox();
  const viewportHeight = await page.evaluate(() => window.innerHeight);
  expect(bounds).not.toBeNull();
  expect(bounds.y).toBeGreaterThanOrEqual(0);
  expect(bounds.y + bounds.height).toBeLessThanOrEqual(viewportHeight + 1);
});

test("NFC creator is aligned and invokes Home Assistant", async ({ page }) => {
  const panel = await openPanel(page);
  await panel.getByRole("link", { name: "Aufgaben", exact: true }).click();
  await panel.getByRole("button", { name: "Bearbeiten" }).first().click();
  const dialog = panel.getByRole("dialog", { name: "Aufgabe bearbeiten" });
  await dialog.locator('[data-task-wizard-step="4"]').click();
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
  for (const view of ["Heute", "Ranking", "Aufgaben", "Verlauf"]) {
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
