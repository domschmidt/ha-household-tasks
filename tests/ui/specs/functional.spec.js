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

test("task templates can be paused temporarily and resumed without touching open work", async ({ page }) => {
  const panel = await openPanel(page);
  await panel.getByRole("link", { name: "Aufgaben", exact: true }).click();
  await panel.getByRole("button", { name: "Temporär pausieren" }).first().click();
  const dialog = panel.getByRole("dialog", { name: /Frostschutz.*pausieren/ });
  await dialog.getByRole("button", { name: "1 Woche" }).click();
  await dialog.getByRole("button", { name: "Pausieren", exact: true }).click();

  const pauseCall = await page.evaluate(() => window.__householdTaskCalls.findLast(
    (call) => call.type === "household_tasks/save_task"
  ));
  expect(pauseCall.task_id).toBe("frostschutz");
  expect(pauseCall.task.paused_until).toBe("2026-08-08T10:00:00.000Z");
  expect(pauseCall.task.enabled).toBe(true);
  expect(pauseCall.task).not.toHaveProperty("occurrences");

  await page.evaluate(() => window.__setHouseholdTasksFixture({
    tasks: {
      frostschutz: {
        enabled: true,
        paused_until: "2026-08-02T12:00:00.000Z",
        name: "Frostschutz beim eigenen Auto prüfen",
        assignee: "dominik",
        assignment: { type: "fixed" },
        schedule: { type: "manual" },
      },
    },
  }));
  await panel.getByRole("button", { name: "Jetzt fortsetzen" }).click();
  const resumeCall = await page.evaluate(() => window.__householdTaskCalls.findLast(
    (call) => call.type === "household_tasks/save_task"
  ));
  expect(resumeCall.task).not.toHaveProperty("paused_until");
  await panel.getByRole("link", { name: "Heute", exact: true }).click();
  await expect(panel.getByRole("heading", { name: "Frostschutz beim eigenen Auto prüfen" })).toBeVisible();
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

test("rule intelligence exposes observations, shadow runs, and an accessible dependency graph", async ({ page }) => {
  const panel = await openPanel(page);
  await page.evaluate(() => window.__setHouseholdTasksFixture({
    tasks: {
      frostschutz: {
        enabled: true, name: "Frostschutz prüfen", assignee: "dominik",
        assignment: { type: "fixed" }, schedule: { type: "manual" },
        shadow: { enabled: true, review_at: "2026-08-01T10:00:00Z" },
      },
    },
    observation_suggestions: [{
      id: "frostschutz:binary_sensor.washer:on:off", task_id: "frostschutz",
      task_name: "Frostschutz prüfen", entity_id: "binary_sensor.washer",
      from: "on", to: "off", samples: 3, typical_delay_seconds: 300,
      proposed_schedule: { type: "state_trigger", triggers: [{ entity_id: "binary_sensor.washer", from: "on", to: "off", for: "00:00:00" }], due_after: "00:05:00", cooldown: "12:00:00", skip_if_open: true },
    }],
    shadow_evaluations: [{ task_id: "frostschutz", due: "2026-08-02T12:00:00Z", assignee: "dominik" }],
    rule_graph: {
      nodes: [
        { id: "entity:binary_sensor.washer", kind: "entity", label: "binary_sensor.washer" },
        { id: "rule:frostschutz", kind: "rule", label: "Frostschutz prüfen" },
        { id: "task:frostschutz", kind: "task", label: "Frostschutz prüfen" },
      ],
      edges: [
        { id: "trigger", source: "entity:binary_sensor.washer", target: "rule:frostschutz", kind: "triggers", label: "steuert" },
        { id: "create", source: "rule:frostschutz", target: "task:frostschutz", kind: "creates", label: "erzeugt" },
      ],
      issues: [],
    },
  }));
  await panel.getByRole("link", { name: "Regeln", exact: true }).click();
  await expect(panel.getByRole("heading", { name: "Regeln verstehen und sicher verbessern" })).toBeVisible();
  await expect(panel.getByText("3 BEOBACHTUNGEN")).toBeVisible();
  await expect(panel.getByText("1 virtuelle Auslösungen")).toBeVisible();
  await expect(panel.locator(".rule-graph .node")).toHaveCount(3);
  await panel.getByText("Graph als Text anzeigen").click();
  await expect(panel.getByText("binary_sensor.washer steuert Frostschutz prüfen")).toBeVisible();
});

test("history decision dossier can be re-evaluated without creating a task", async ({ page }) => {
  const panel = await openPanel(page);
  await panel.getByRole("link", { name: "Verlauf" }).click();
  await panel.getByRole("button", { name: "Akte öffnen" }).click();
  await expect(panel.getByRole("heading", { name: "Entscheidungsakte" })).toBeVisible();
  await expect(panel.getByText("Kalenderereignis erkannt")).toBeVisible();
  await panel.getByRole("button", { name: "Mit aktuellen Daten erneut auswerten" }).click();
  await expect(panel.getByText("Würde aktuell erzeugen")).toBeVisible();
  const calls = await page.evaluate(() => window.__householdTaskCalls);
  expect(calls.some((item) => item.type === "household_tasks/reevaluate_occurrence")).toBeTruthy();
  expect(calls.some((item) => item.type === "household_tasks/create")).toBeFalsy();
});

test("rule simulator evaluates time travel and generated counterexamples without mutations", async ({ page }) => {
  const panel = await openPanel(page);
  await page.evaluate(() => window.__setHouseholdTasksFixture({
    rule_insights: {
      effects: {
        frostschutz: { task_id: "frostschutz", name: "Frostschutz prüfen", generated: 12, completed: 9, overdue: 1, completion_rate: 75, median_delay_minutes: 45, virtual_triggers: 2 },
      },
      noise_findings: [{ task_id: "frostschutz", severity: "warning", message: "Mehrere Aufgaben wurden ignoriert." }],
      improvement_suggestions: [{ task_id: "frostschutz", title: "Fälligkeit anpassen", message: "Erledigungen liegen meist später.", patch: { "schedule.time": "19:00:00" } }],
    },
  }));
  await panel.getByRole("link", { name: "Regeln", exact: true }).click();
  await expect(panel.getByText("12 erzeugt · 9 erledigt · 1 überfällig")).toBeVisible();
  await expect(panel.getByText("Mehrere Aufgaben wurden ignoriert.")).toBeVisible();
  await panel.getByRole("button", { name: "Regel simulieren" }).click();
  await expect(panel.getByRole("heading", { name: "Zeitreise ohne Nebenwirkungen" })).toBeVisible();
  await panel.getByRole("button", { name: "Szenario auswerten" }).click();
  await expect(panel.getByText("Aufgabe würde erzeugt")).toBeVisible();
  await expect(panel.getByText("Urlaubsmodus aktiv")).toBeVisible();
  const calls = await page.evaluate(() => window.__householdTaskCalls);
  expect(calls.some((item) => item.type === "household_tasks/simulate_task")).toBeTruthy();
  expect(calls.some((item) => item.type === "household_tasks/create")).toBeFalsy();
});

test("extended gallery filters presets and submits separate entity mappings", async ({ page }) => {
  const panel = await openPanel(page);
  await page.evaluate(() => window.__setHouseholdTasksFixture({
    template_gallery: [{
      id: "rain_open_window", category: "Wetter", name: "Offenes Fenster bei Regen", description: "Kombiniert Fenster und Wetter.",
      required_entities: [
        { key: "window", name: "Fensterkontakt", domains: ["binary_sensor"], description: "Kontakt des Fensters" },
        { key: "weather", name: "Wetter", domains: ["weather"], description: "Wetterentität" },
      ],
      task: { name: "Fenster schließen", assignment: { type: "fair" }, schedule: { type: "state_trigger", triggers: [{ entity_id: "{{window}}", to: "on" }] }, weather: { conditions: [{ entity_id: "{{weather}}" }] }, market: { priority: "high", points: 2 } },
    }, {
      id: "washer", category: "Geräte", name: "Waschmaschine", description: "Wäsche ausräumen", task: { name: "Waschmaschine", assignment: { type: "open" }, schedule: { type: "manual" } },
    }],
  }));
  await panel.getByRole("link", { name: "Aufgaben", exact: true }).click();
  await panel.getByRole("button", { name: "Vorlagengalerie" }).click();
  await panel.locator("#gallery-search").fill("Fenster");
  const gallery = panel.locator(".gallery-modal");
  await expect(gallery.getByRole("button", { name: /Offenes Fenster bei Regen/ })).toBeVisible();
  await expect(gallery.getByRole("button", { name: /Waschmaschine/ })).toBeHidden();
  await gallery.getByRole("button", { name: /Offenes Fenster bei Regen/ }).click();
  await panel.getByLabel("Fensterkontakt", { exact: false }).fill("binary_sensor.kitchen_window");
  await panel.getByLabel("Wetter", { exact: false }).fill("weather.home");
  await panel.getByRole("button", { name: "Vorlage übernehmen" }).click();
  const calls = await page.evaluate(() => window.__householdTaskCalls);
  expect(calls).toContainEqual(expect.objectContaining({
    type: "household_tasks/install_gallery_template",
    template_id: "rain_open_window",
    mappings: { window: "binary_sensor.kitchen_window", weather: "weather.home" },
  }));
});

test("settings explain the recommended path and progressively disclose optional automation", async ({ page }) => {
  const panel = await openPanel(page);
  await panel.getByRole("link", { name: "Einstellungen", exact: true }).click();

  await expect(panel.getByRole("heading", { name: "Was möchtest du einrichten?" })).toBeVisible();
  await expect(panel.locator(".configuration-path li")).toHaveCount(3);
  await expect(panel.locator(".settings-group")).toHaveCount(4);
  await expect(panel.locator("#settings-operation")).toContainText("Was gilt gerade im Haushalt?");
  await expect(panel.locator("#settings-connections")).toContainText("Apps und Home Assistant verbinden");

  const optional = panel.locator("#settings-automation .settings-collection");
  await expect(optional).not.toHaveAttribute("open", "");
  await expect(panel.locator("#notification-digest-form")).not.toBeVisible();
  await optional.locator("summary").click();
  await expect(panel.locator("#notification-digest-form")).toBeVisible();
});

test("settings search opens matching groups and clears without changing configuration", async ({ page }) => {
  const panel = await openPanel(page);
  await panel.getByRole("link", { name: "Einstellungen", exact: true }).click();
  const search = panel.getByRole("searchbox", { name: "Einstellungen durchsuchen" });
  const optional = panel.locator("#settings-automation .settings-collection");

  await search.fill("NFC");
  await expect(optional).toHaveAttribute("open", "");
  await expect(panel.locator("#nfc-feedback-form")).toBeVisible();
  await expect(panel.locator("#settings-search-result")).toContainText("passende Bereiche");
  expect(await page.evaluate(() => window.__householdTaskCalls.some((call) => /save|reset|install/.test(call.type)))).toBe(false);

  await search.fill("");
  await expect(optional).not.toHaveAttribute("open", "");
  await expect(panel.locator(".settings-group[hidden]")).toHaveCount(0);
});

test("local discovery produces contextual household recommendations", async ({ page }) => {
  const panel = await openPanel(page);
  await page.evaluate(() => window.__setHouseholdTasksFixture({
    discovery_suggestions: [{
      id: "calendar:calendar.waste",
      kind: "calendar",
      entity_id: "calendar.waste",
      name: "Abfallkalender bereitstellen",
      reason: "Ein Abfallkalender wurde erkannt.",
      task: { enabled: true, name: "Abfallkalender bereitstellen", assignment: { type: "open" }, schedule: { type: "calendar", entity_id: "calendar.waste", offset: "-12:00:00" } },
    }],
  }));
  await panel.getByRole("link", { name: "Einstellungen", exact: true }).click();

  await expect(panel.getByRole("heading", { name: "Sinnvolle nächste Schritte" })).toBeVisible();
  await expect(panel.getByText("Ein Abfallkalender wurde erkannt.", { exact: true })).toBeVisible();
  await panel.getByRole("button", { name: "Vorschlag einrichten" }).click();
  await expect(panel.getByRole("dialog", { name: "Abfallkalender bereitstellen" })).toBeVisible();
});

test("task and person editors explain decisions before advanced configuration", async ({ page }) => {
  const panel = await openPanel(page);
  await panel.getByRole("link", { name: "Aufgaben", exact: true }).click();
  await panel.getByRole("button", { name: "Bearbeiten" }).first().click();
  const taskDialog = panel.getByRole("dialog", { name: "Aufgabe bearbeiten" });
  await expect(taskDialog.getByRole("heading", { name: "Was soll erledigt werden?" })).toBeVisible();
  await taskDialog.locator('[data-task-wizard-step="4"]').click();
  await taskDialog.locator("details.advanced-fields").evaluate((element) => { element.open = true; });
  await expect(taskDialog.locator(".option-group")).toHaveCount(10);
  await expect(taskDialog.getByRole("heading", { name: "Ablauf und Verknüpfungen" })).toBeVisible();
  await expect(taskDialog.getByRole("heading", { name: "Haushaltsmodus und Saison" })).toBeVisible();
  await taskDialog.getByRole("button", { name: "Abbrechen" }).click();

  await panel.getByRole("link", { name: "Personen", exact: true }).click();
  await panel.getByRole("button", { name: "+ Person", exact: true }).click();
  const personDialog = panel.getByRole("dialog", { name: "Neue Person" });
  await expect(personDialog.getByRole("heading", { name: "Benachrichtigung und Anwesenheit" })).toBeVisible();
  await expect(personDialog.getByText(/Am zuverlässigsten ist person[.][*]/)).toBeVisible();
  await expect(personDialog.getByText(/Meine Aufgaben/)).toBeVisible();
});

test("mobile views do not overflow horizontally", async ({ page, isMobile }) => {
  test.skip(!isMobile, "Mobile browser project only");
  const panel = await openPanel(page);
  for (const view of ["Heute", "Ranking", "Aufgaben", "Regeln", "Verlauf", "Einstellungen"]) {
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
        panelOverflow: (() => {
          const main = document.querySelector("household-tasks-panel")?.shadowRoot?.querySelector("main");
          return main ? main.scrollWidth - main.clientWidth : 0;
        })(),
        offenders,
      };
    });
    expect(layout.overflow, `${view} has horizontal overflow: ${JSON.stringify(layout.offenders)}`).toBeLessThanOrEqual(1);
    expect(layout.panelOverflow, `${view} overflows inside the panel`).toBeLessThanOrEqual(1);
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

test("CalDAV setup exposes every server option and keeps app passwords ephemeral", async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: async (value) => { window.__copiedCredential = value; } },
    });
  });
  const panel = await openPanel(page);
  await panel.getByRole("link", { name: "Einstellungen" }).click();
  await expect(panel.getByRole("heading", { name: "CalDAV für Apple Erinnerungen" })).toBeVisible();
  await expect(panel.getByText("https://ha.example.test/api/household_tasks/caldav/", { exact: true })).toBeVisible();
  await panel.getByText("Serveroptionen", { exact: true }).click();

  const settings = panel.locator("#caldav-settings-form");
  await settings.getByRole("checkbox", { name: /CalDAV-Server aktivieren/ }).check();
  await settings.getByLabel("Listenname").fill("Familienaufgaben");
  await settings.getByRole("button", { name: "CalDAV-Einstellungen speichern" }).click();
  const saveCall = await page.evaluate(() => window.__householdTaskCalls.findLast(
    (call) => call.type === "household_tasks/caldav_save_settings"
  ));
  expect(saveCall.settings.enabled).toBe(true);
  expect(saveCall.settings.calendar_name).toBe("Familienaufgaben");
  expect(saveCall.settings.delete_mode).toBe("cancel");

  await panel.getByText("App-Passwort anlegen", { exact: true }).click();
  const credential = panel.locator("#caldav-credential-form");
  await credential.getByLabel("Bezeichnung").fill("Mein iPhone");
  await credential.getByLabel("Person").selectOption("dominik");
  await credential.getByRole("button", { name: "App-Passwort erzeugen" }).click();
  const dialog = panel.getByRole("dialog", { name: "CalDAV-Zugang angelegt" });
  await expect(dialog.getByText("one-time-secret", { exact: true })).toBeVisible();
  for (const [label, expected] of [
    ["Server kopieren", "https://ha.example.test/api/household_tasks/caldav/"],
    ["Benutzername kopieren", "dominik-12345678"],
    ["App-Passwort kopieren", "one-time-secret"],
  ]) {
    await dialog.getByRole("button", { name: label }).click();
    await expect.poll(() => page.evaluate(() => window.__copiedCredential)).toBe(expected);
  }
  const cached = await page.evaluate(() => localStorage.getItem("household_tasks_offline_snapshot"));
  expect(cached).not.toContain("one-time-secret");
});

test("live refresh preserves unsaved settings instead of resetting the form", async ({ page }) => {
  const panel = await openPanel(page);
  await panel.getByRole("link", { name: "Einstellungen" }).click();
  await panel.getByText("Serveroptionen", { exact: true }).click();
  const settings = panel.locator("#caldav-settings-form");
  await settings.getByLabel("Listenname").fill("Noch nicht gespeicherte Familienliste");
  await settings.getByRole("checkbox", { name: /CalDAV-Server aktivieren/ }).check();

  await page.evaluate(() => {
    window.__householdTaskCalls.length = 0;
    window.__setHouseholdTasksServerState({
      caldav: {
        ...document.querySelector("household-tasks-panel")._data.caldav,
        settings: {
          ...document.querySelector("household-tasks-panel")._data.caldav.settings,
          calendar_name: "Externer Serverwert",
          enabled: false,
        },
      },
    });
    window.__emitHouseholdTasksUpdated();
  });

  await expect.poll(async () => page.evaluate(() => window.__householdTaskCalls
    .filter((call) => call.type === "household_tasks/get").length)).toBe(1);
  await expect(settings.getByLabel("Listenname")).toHaveValue("Noch nicht gespeicherte Familienliste");
  await expect(settings.getByRole("checkbox", { name: /CalDAV-Server aktivieren/ })).toBeChecked();
});

test("what-if laboratory explains a multi-day run without creating tasks", async ({ page }) => {
  const panel = await openPanel(page);
  await panel.getByRole("link", { name: "Regeln" }).click();
  await panel.getByRole("button", { name: "Was-wäre-wenn-Labor" }).click();
  const dialog = panel.getByRole("dialog");
  await expect(dialog.getByRole("heading", { name: "Den ganzen Haushalt vorausberechnen" })).toBeVisible();
  await dialog.getByLabel("Dauer").selectOption("7");
  await dialog.getByRole("button", { name: "Zeitraum simulieren" }).click();
  await expect(dialog.getByText("2 mögliche Aufgaben aus 3 Auswertungen")).toBeVisible();
  await expect(dialog.getByText("Würde wartend angelegt", { exact: false })).toBeVisible();
  const call = await page.evaluate(() => window.__householdTaskCalls.findLast(
    (item) => item.type === "household_tasks/simulate_period"
  ));
  expect(call.scenario.days).toBe(7);
  expect(call.scenario).not.toHaveProperty("side_effects", true);
});

test("task wizard exposes waiting energy quiet-hours and privacy guidance", async ({ page }) => {
  const panel = await openPanel(page);
  await panel.getByRole("link", { name: "Aufgaben", exact: true }).click();
  await panel.getByRole("button", { name: "Bearbeiten" }).first().click();
  const dialog = panel.getByRole("dialog");
  await dialog.getByRole("button", { name: /Optionen/ }).click();
  await dialog.getByText("Optionale Verfeinerungen", { exact: true }).click();
  await expect(dialog.getByRole("heading", { name: "Wartet auf Zustand" })).toBeVisible();
  await expect(dialog.getByRole("heading", { name: "Energie- und Tarifoptimierung" })).toBeVisible();
  await expect(dialog.getByRole("heading", { name: "Stille Stunden und Eskalationsbudget" })).toBeVisible();
  await expect(dialog.getByRole("heading", { name: "Privat und sensibel" })).toBeVisible();
});
