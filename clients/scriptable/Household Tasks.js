// Household Tasks for Scriptable
// Version 1.0.0 · Requires Household Tasks 3.0.0+

const CLIENT_VERSION = "1.0.0";
const API_VERSION = 1;
const CONFIG_KEY = "household_tasks.scriptable.config.v1";
const TOKEN_KEY = "household_tasks.scriptable.token.v1";
const CACHE_FILE = "household-tasks-scriptable-cache-v1.json";

const de = Device.language().toLowerCase().startsWith("de");
const t = (german, english) => (de ? german : english);
const query = args.queryParameters || {};

function readConfig() {
  if (!Keychain.contains(CONFIG_KEY) || !Keychain.contains(TOKEN_KEY)) return null;
  try {
    return {
      ...JSON.parse(Keychain.get(CONFIG_KEY)),
      token: Keychain.get(TOKEN_KEY),
    };
  } catch (_) {
    return null;
  }
}

async function configure(existing = {}) {
  const alert = new Alert();
  alert.title = t("Household Tasks einrichten", "Set up Household Tasks");
  alert.message = t(
    "Die URL muss von deinem iPhone erreichbar sein. Der Token wird ausschließlich im iOS-Schlüsselbund gespeichert.",
    "The URL must be reachable from your iPhone. The token is stored only in the iOS Keychain.",
  );
  alert.addTextField("https://home.example.com", existing.baseUrl || "");
  alert.addSecureTextField(t("Langlebiger Zugriffstoken", "Long-lived access token"), "");
  alert.addTextField(t("Person-ID (für Admins)", "Person ID (for admins)"), existing.personId || "");
  alert.addAction(t("Sicher speichern", "Save securely"));
  alert.addCancelAction(t("Abbrechen", "Cancel"));
  if (await alert.presentAlert() === -1) return null;

  const baseUrl = alert.textFieldValue(0).trim().replace(/\/+$/, "");
  const token = alert.textFieldValue(1).trim();
  const personId = alert.textFieldValue(2).trim();
  if (!/^https?:\/\//i.test(baseUrl) || (!token && !existing.token)) {
    await showMessage(
      t("Ungültige Konfiguration", "Invalid configuration"),
      t("Bitte URL und Zugriffstoken vollständig angeben.", "Please provide the URL and access token."),
    );
    return configure(existing);
  }

  let allowInsecure = false;
  if (baseUrl.toLowerCase().startsWith("http://")) {
    const warning = new Alert();
    warning.title = t("Unverschlüsselte Verbindung", "Unencrypted connection");
    warning.message = t(
      "HTTP kann den Zugriffstoken im Netzwerk offenlegen. Nur in einem vertrauenswürdigen lokalen Netz fortfahren.",
      "HTTP can expose the access token on the network. Continue only on a trusted local network.",
    );
    warning.addDestructiveAction(t("HTTP trotzdem verwenden", "Use HTTP anyway"));
    warning.addCancelAction(t("Abbrechen", "Cancel"));
    if (await warning.presentAlert() === -1) return null;
    allowInsecure = true;
  }

  const stored = { baseUrl, personId, allowInsecure, clientVersion: CLIENT_VERSION };
  Keychain.set(CONFIG_KEY, JSON.stringify(stored));
  if (token) Keychain.set(TOKEN_KEY, token);
  return { ...stored, token: token || existing.token };
}

function apiUrl(cfg, suffix = "") {
  const person = cfg.personId ? `?person_id=${encodeURIComponent(cfg.personId)}` : "";
  return `${cfg.baseUrl}/api/household_tasks/v${API_VERSION}/tasks${suffix}${person}`;
}

async function requestJson(cfg, method = "GET", suffix = "", body = null) {
  const request = new Request(apiUrl(cfg, suffix));
  request.method = method;
  request.timeoutInterval = 15;
  request.allowInsecureRequest = Boolean(cfg.allowInsecure);
  request.headers = {
    Authorization: `Bearer ${cfg.token}`,
    Accept: "application/json",
    "Content-Type": "application/json",
    "X-Household-Tasks-Client": `scriptable/${CLIENT_VERSION}`,
  };
  if (body) request.body = JSON.stringify({ ...body, person_id: cfg.personId || undefined });
  const raw = await request.loadString();
  let data;
  try {
    data = JSON.parse(raw);
  } catch (_) {
    throw new Error(t("Home Assistant lieferte keine JSON-Antwort.", "Home Assistant did not return JSON."));
  }
  const status = request.response?.statusCode || 0;
  if (status < 200 || status >= 300) {
    throw new Error(data?.error?.message || `${t("HTTP-Fehler", "HTTP error")} ${status}`);
  }
  if (data.api_version !== API_VERSION) {
    throw new Error(t("Nicht unterstützte API-Version.", "Unsupported API version."));
  }
  return data;
}

function cachePath() {
  const fm = FileManager.local();
  return [fm, fm.joinPath(fm.libraryDirectory(), CACHE_FILE)];
}

function saveCache(data) {
  const [fm, path] = cachePath();
  fm.writeString(path, JSON.stringify(data));
}

function loadCache() {
  const [fm, path] = cachePath();
  if (!fm.fileExists(path)) return null;
  try {
    return JSON.parse(fm.readString(path));
  } catch (_) {
    return null;
  }
}

async function loadFeed(cfg, allowCache = true) {
  try {
    const data = await requestJson(cfg);
    saveCache(data);
    return { data, offline: false, error: null };
  } catch (error) {
    const cached = allowCache ? loadCache() : null;
    if (cached) return { data: cached, offline: true, error: error.message };
    throw error;
  }
}

async function perform(cfg, task, action, payload = {}) {
  return requestJson(
    cfg,
    "POST",
    `/${encodeURIComponent(task.id)}/${encodeURIComponent(action)}`,
    { expected_revision: task.revision, ...payload },
  );
}

function runUrl(parameters = {}) {
  const base = URLScheme.forRunningScript();
  const separator = base.includes("?") ? "&" : "?";
  const encoded = Object.entries(parameters)
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(value)}`)
    .join("&");
  return `${base}${separator}${encoded}`;
}

function palette() {
  return {
    background: Color.dynamic(new Color("F4F7FB"), new Color("111418")),
    card: Color.dynamic(new Color("FFFFFF"), new Color("1C2128")),
    primary: new Color("03A9F4"),
    text: Color.dynamic(new Color("17202A"), new Color("F2F5F7")),
    secondary: Color.dynamic(new Color("66727D"), new Color("AAB4BE")),
    warning: new Color("F59E0B"),
    danger: new Color("E5484D"),
  };
}

function addSymbol(stack, name, color, size = 15) {
  const image = stack.addImage(SFSymbol.named(name).image);
  image.imageSize = new Size(size, size);
  image.tintColor = color;
  return image;
}

function dueText(task) {
  if (!task.due) return t("ohne Termin", "no due date");
  const date = new Date(task.due);
  const formatter = new DateFormatter();
  formatter.locale = de ? "de_DE" : "en_US";
  formatter.dateFormat = task.due_today ? "HH:mm" : "EEE, dd.MM. HH:mm";
  return task.overdue
    ? `${t("überfällig", "overdue")} · ${formatter.string(date)}`
    : formatter.string(date);
}

function addTaskRow(container, task, colors) {
  const row = container.addStack();
  row.layoutHorizontally();
  row.centerAlignContent();
  row.url = runUrl({ action: "task", id: task.id });
  const iconColor = task.overdue ? colors.danger : task.status === "blocked" ? colors.warning : colors.primary;
  addSymbol(row, task.status === "blocked" ? "lock.fill" : "circle", iconColor, 14);
  row.addSpacer(8);
  const copy = row.addStack();
  copy.layoutVertically();
  const title = copy.addText(task.title);
  title.font = Font.semiboldSystemFont(13);
  title.textColor = colors.text;
  title.lineLimit = 1;
  const details = [dueText(task)];
  if (task.checklist.total) details.push(`${task.checklist.completed}/${task.checklist.total}`);
  if (task.points) details.push(`${task.points} P`);
  const meta = copy.addText(details.join(" · "));
  meta.font = Font.systemFont(10);
  meta.textColor = task.overdue ? colors.danger : colors.secondary;
  meta.lineLimit = 1;
  row.addSpacer();
  addSymbol(row, "chevron.right", colors.secondary, 10);
}

function setupWidget(message) {
  const colors = palette();
  const widget = new ListWidget();
  widget.backgroundColor = colors.background;
  widget.setPadding(16, 16, 16, 16);
  addSymbol(widget, "checklist", colors.primary, 24);
  widget.addSpacer(8);
  const title = widget.addText(t("Einrichtung nötig", "Setup required"));
  title.font = Font.boldSystemFont(15);
  title.textColor = colors.text;
  const text = widget.addText(message);
  text.font = Font.systemFont(11);
  text.textColor = colors.secondary;
  text.minimumScaleFactor = 0.8;
  widget.url = runUrl({ configure: "1" });
  return widget;
}

function buildWidget(feed, offline = false) {
  const colors = palette();
  const widget = new ListWidget();
  widget.backgroundColor = colors.background;
  widget.setPadding(14, 14, 12, 14);
  widget.url = runUrl({ action: "list" });

  const header = widget.addStack();
  header.centerAlignContent();
  addSymbol(header, "checklist", colors.primary, 17);
  header.addSpacer(7);
  const heading = header.addText(feed.person?.name || "Household Tasks");
  heading.font = Font.boldSystemFont(15);
  heading.textColor = colors.text;
  header.addSpacer();
  const count = header.addText(String(feed.summary?.open || 0));
  count.font = Font.boldSystemFont(13);
  count.textColor = colors.primary;
  if (offline) {
    header.addSpacer(6);
    addSymbol(header, "wifi.slash", colors.warning, 12);
  }
  widget.addSpacer(10);

  const limits = { small: 1, medium: 3, large: 7, extraLarge: 10 };
  const limit = limits[config.widgetFamily] || 3;
  const tasks = (feed.tasks || []).slice(0, limit);
  if (!tasks.length) {
    widget.addSpacer();
    const done = widget.addText(t("Alles erledigt ✓", "All done ✓"));
    done.font = Font.semiboldSystemFont(15);
    done.textColor = colors.text;
    done.centerAlignText();
    widget.addSpacer();
  } else {
    tasks.forEach((task, index) => {
      if (index) widget.addSpacer(8);
      addTaskRow(widget, task, colors);
    });
  }
  widget.addSpacer();
  const footer = widget.addText(
    offline
      ? t("Offline · letzter bekannter Stand", "Offline · last known state")
      : `${feed.summary?.due_today || 0} ${t("heute", "today")} · ${feed.summary?.overdue || 0} ${t("überfällig", "overdue")}`,
  );
  footer.font = Font.systemFont(9);
  footer.textColor = offline ? colors.warning : colors.secondary;
  widget.refreshAfterDate = new Date(Date.now() + 15 * 60 * 1000);
  return widget;
}

async function showMessage(title, message) {
  const alert = new Alert();
  alert.title = title;
  alert.message = message;
  alert.addAction("OK");
  await alert.presentAlert();
}

async function checklistMenu(cfg, task) {
  const items = task.checklist?.items || [];
  const alert = new Alert();
  alert.title = task.title;
  alert.message = t("Checklistenpunkt umschalten", "Toggle checklist item");
  items.forEach((item) => alert.addAction(`${item.completed ? "✓" : "○"} ${item.title}`));
  alert.addCancelAction(t("Abbrechen", "Cancel"));
  const choice = await alert.presentSheet();
  if (choice < 0 || !items[choice]) return null;
  const item = items[choice];
  return perform(cfg, task, "checklist", { item_id: item.id, completed: !item.completed });
}

async function taskMenu(cfg, task) {
  const choices = [];
  if (task.actions?.claim) choices.push([t("Übernehmen", "Claim"), "claim", {}]);
  if (task.actions?.complete) choices.push([t("Erledigt", "Complete"), "complete", {}]);
  if (task.actions?.checklist && task.checklist?.total) choices.push([t("Checkliste", "Checklist"), "checklist", {}]);
  if (task.actions?.snooze) {
    choices.push([t("Heute Abend", "This evening"), "snooze", { choice: "evening" }]);
    choices.push([t("Morgen", "Tomorrow"), "snooze", { choice: "tomorrow" }]);
    choices.push([t("In Arbeit", "In progress"), "status", { status: "in_progress" }]);
  }
  if (task.actions?.help) choices.push([t("Hilfe anfordern", "Request help"), "help", {}]);

  const alert = new Alert();
  alert.title = task.title;
  alert.message = `${dueText(task)}${task.description ? `\n${task.description}` : ""}`;
  choices.forEach(([label]) => alert.addAction(label));
  alert.addCancelAction(t("Abbrechen", "Cancel"));
  const selected = await alert.presentSheet();
  if (selected < 0 || !choices[selected]) return;
  const [, action, payload] = choices[selected];
  try {
    const result = action === "checklist"
      ? await checklistMenu(cfg, task)
      : await perform(cfg, task, action, payload);
    if (result) {
      saveCache(result);
      await showMessage(t("Aktualisiert", "Updated"), t("Die Aufgabe wurde aktualisiert.", "The task was updated."));
    }
  } catch (error) {
    await showMessage(t("Aktion fehlgeschlagen", "Action failed"), error.message);
  }
}

async function appList(cfg, feed, offline) {
  const tasks = feed.tasks || [];
  const alert = new Alert();
  alert.title = `${feed.person?.name || "Household Tasks"} · ${tasks.length}`;
  alert.message = offline
    ? t("Offline: Aktionen sind bis zur nächsten Verbindung nicht verfügbar.", "Offline: actions are unavailable until reconnected.")
    : t("Aufgabe auswählen", "Select a task");
  tasks.slice(0, 18).forEach((task) => alert.addAction(`${task.overdue ? "!" : "○"} ${task.title}`));
  alert.addAction(t("Einstellungen", "Settings"));
  alert.addCancelAction(t("Schließen", "Close"));
  const selected = await alert.presentSheet();
  if (selected < 0) return;
  if (selected === Math.min(tasks.length, 18)) {
    await configure(cfg);
    return;
  }
  if (!offline && tasks[selected]) await taskMenu(cfg, tasks[selected]);
}

async function main() {
  let cfg = readConfig();
  if (query.configure === "1" || (!cfg && !config.runsInWidget)) cfg = await configure(cfg || {});

  if (!cfg) {
    const widget = setupWidget(t("Widget antippen und Zugangsdaten hinterlegen.", "Tap the widget and enter credentials."));
    if (config.runsInWidget) Script.setWidget(widget);
    else await widget.presentMedium();
    Script.complete();
    return;
  }

  try {
    const loaded = await loadFeed(cfg);
    if (query.action === "task" && query.id && !loaded.offline) {
      const task = loaded.data.tasks.find((item) => item.id === query.id);
      if (task) await taskMenu(cfg, task);
      else await showMessage(t("Nicht mehr offen", "No longer open"), t("Die Aufgabe wurde bereits verändert.", "The task has already changed."));
    } else if (!config.runsInWidget) {
      await appList(cfg, loaded.data, loaded.offline);
    }
    if (config.runsInWidget) Script.setWidget(buildWidget(loaded.data, loaded.offline));
  } catch (error) {
    if (config.runsInWidget) {
      Script.setWidget(setupWidget(error.message));
    } else {
      await showMessage(t("Verbindung fehlgeschlagen", "Connection failed"), error.message);
    }
  }

  Script.complete();
}

await main();
