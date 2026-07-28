import {
  householdTasksLocale,
  householdTasksLocaleTag,
  householdTasksText,
  localizeHouseholdTasksTree,
} from "./household-tasks-translations.js?v=3.0.0";

const HT_WEEKDAYS = [
  ["mon", "Mo", "Mon"], ["tue", "Di", "Tue"], ["wed", "Mi", "Wed"], ["thu", "Do", "Thu"],
  ["fri", "Fr", "Fri"], ["sat", "Sa", "Sat"], ["sun", "So", "Sun"],
];

class HouseholdTasksPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._data = null;
    this._view = "today";
    this._busy = false;
  }

  set hass(value) {
    this._hass = value;
    if (this.isConnected && !this._data && !this._busy) this._load();
  }
  get hass() { return this._hass; }

  connectedCallback() {
    this._render();
    if (this._hass) this._load();
  }

  async _call(type, payload = {}) {
    if (!this._hass) throw new Error("Home Assistant ist noch nicht verbunden.");
    this._busy = true;
    let success = false;
    const refresh = this.shadowRoot.querySelector(".refresh");
    if (refresh) refresh.disabled = true;
    try {
      const result = await this._hass.callWS({ type: `household_tasks/${type}`, ...payload });
      if (result) {
        this._data = result;
        if (this._hass.user) this._data.is_admin = this._hass.user.is_admin;
      }
      success = true;
      return result;
    } catch (error) {
      this._toast(this._errorText(error), true);
      throw error;
    } finally {
      this._busy = false;
      if (success || !this._data) this._render();
      else if (refresh) refresh.disabled = false;
    }
  }

  async _load() {
    try { await this._call("get"); } catch (_) { /* message is already visible */ }
  }

  _errorText(error) {
    return error?.message || error?.error?.message || String(error || "Unbekannter Fehler");
  }

  _toast(message, error = false) {
    const old = this.shadowRoot.querySelector(".toast");
    if (old) old.remove();
    const toast = document.createElement("div");
    toast.className = `toast${error ? " error" : ""}`;
    toast.textContent = this._t(message);
    this.shadowRoot.append(toast);
    setTimeout(() => toast.remove(), 4200);
  }

  _e(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;").replaceAll('"', "&quot;");
  }

  _t(source) {
    return householdTasksText(source, this._hass);
  }

  _locale() {
    return householdTasksLocaleTag(this._hass);
  }

  _weekdays() {
    const labelIndex = householdTasksLocale(this._hass) === "de" ? 1 : 2;
    return HT_WEEKDAYS.map((item) => [item[0], item[labelIndex]]);
  }

  _localize(root = this.shadowRoot) {
    localizeHouseholdTasksTree(root, this._hass);
  }

  _render() {
    const data = this._data;
    this.shadowRoot.innerHTML = `
      <style>${this._styles()}</style>
      <main>
        <header>
          <div>
            <div class="eyebrow">ZUHAUSE</div>
            <h1>Haushaltsaufgaben</h1>
          </div>
          <button class="icon-button refresh" title="Aktualisieren" ${this._busy ? "disabled" : ""}>↻</button>
        </header>
        <nav>
          ${this._navButton("today", "Heute")}
          ${this._navButton("tasks", "Aufgaben")}
          ${this._navButton("people", "Personen")}
          ${this._navButton("analytics", "Auswertung")}
          ${this._navButton("history", "Verlauf")}
          ${this._navButton("settings", "Einstellungen")}
        </nav>
        <section class="content">
          ${!data ? this._loading() : this._renderView()}
        </section>
      </main>
      <div id="modal"></div>
    `;
    this._bind();
    this._localize();
  }

  _navButton(id, label) {
    return `<button data-view="${id}" class="${this._view === id ? "active" : ""}">${label}</button>`;
  }

  _loading() {
    return `<div class="empty"><div class="spinner"></div><h2>Aufgaben werden geladen</h2></div>`;
  }

  _renderView() {
    if (this._view === "tasks") return this._renderTasks();
    if (this._view === "people") return this._renderPeople();
    if (this._view === "analytics") return this._renderAnalytics();
    if (this._view === "history") return this._renderHistory();
    if (this._view === "settings") return this._renderSettings();
    return this._renderToday();
  }

  _openOccurrences() {
    return this._data.occurrences
      .filter((item) => !item.resolved)
      .sort((a, b) => new Date(a.due) - new Date(b.due));
  }

  _renderToday() {
    if (!Object.keys(this._data.people).length) {
      return `<div class="empty card"><div class="big-icon">⌂</div>
        <h2>Haushalt einrichten</h2>
        <p>Lege zuerst eure Personen und Benachrichtigungsziele an. Danach kannst du Aufgaben hinzufügen.</p>
        ${this._data.is_admin ? `<button class="primary" id="add-person">+ Erste Person</button>` : `<p>Ein Administrator muss die Ersteinrichtung abschließen.</p>`}
      </div>`;
    }
    const open = this._openOccurrences();
    const now = new Date();
    const today = now.toLocaleDateString(this._locale());
    const dueToday = open.filter((o) => new Date(o.due).toLocaleDateString(this._locale()) === today);
    const overdue = open.filter((o) => new Date(o.due) < now && !dueToday.includes(o));
    const upcoming = open.filter((o) => !dueToday.includes(o) && !overdue.includes(o)).slice(0, 8);
    const personCounts = {};
    open.forEach((o) => { personCounts[o.assignee] = (personCounts[o.assignee] || 0) + 1; });
    return `
      <div class="hero">
        <div><div class="eyebrow">${now.toLocaleDateString(this._locale(), { weekday: "long", day: "2-digit", month: "long" })}</div>
        <h2>${dueToday.length ? (householdTasksLocale(this._hass) === "de" ? `${dueToday.length} ${dueToday.length === 1 ? "Aufgabe" : "Aufgaben"} heute` : `${dueToday.length} ${dueToday.length === 1 ? "task" : "tasks"} today`) : this._t("Heute ist alles im Griff")}</h2></div>
        <div class="hero-side"><div class="score">${open.length}<small>offen</small></div>
        <button id="quick-task" class="hero-button">+ Schnellaufgabe</button></div>
      </div>
      ${this._peopleStrip(personCounts)}
      ${this._ranking()}
      ${this._occurrenceSection("Überfällig", overdue, "danger")}
      ${this._occurrenceSection("Heute", dueToday)}
      ${this._occurrenceSection("Demnächst", upcoming, "muted")}
      ${!open.length ? `<div class="empty card"><div class="big-icon">✓</div><h2>Alles erledigt</h2><p>Im Moment ist keine Haushaltsaufgabe offen.</p></div>` : ""}
    `;
  }

  _peopleStrip(counts) {
    return `<div class="people-strip">${Object.entries(this._data.people).map(([id, p]) => `
      <div class="person-chip"><span class="avatar">${this._e(p.name).slice(0, 1)}</span>
      <span>${this._e(p.name)}</span><b>${counts[id] || 0}</b></div>`).join("")}</div>`;
  }

  _ranking() {
    const scores = this._data.scores || {};
    const now = new Date();
    const monthly = {};
    this._data.occurrences.filter((item) => item.resolved && item.completed_by)
      .forEach((item) => {
        const completed = new Date(item.resolved_at);
        if (completed.getFullYear() === now.getFullYear() && completed.getMonth() === now.getMonth()) {
          monthly[item.completed_by] = (monthly[item.completed_by] || 0) + 1;
        }
      });
    const ranking = Object.entries(this._data.people)
      .map(([id, person]) => ({
        id, name: person.name, total: Number(scores[id] || 0), month: monthly[id] || 0,
      }))
      .sort((a, b) => b.total - a.total || b.month - a.month || a.name.localeCompare(b.name, this._locale()));
    if (!ranking.length) return "";
    return `<article class="ranking-card">
      <div class="ranking-head"><div><div class="eyebrow">FAMILIEN-RANKING</div><h3>Punktestand</h3></div><span>1 Aufgabe = 1 Punkt</span></div>
      <div class="ranking-list">${ranking.map((entry, index) => `
        <div class="ranking-row">
          <span class="rank ${index < 3 ? `top-${index + 1}` : ""}">${index + 1}</span>
          <span class="avatar">${this._e(entry.name).slice(0, 1)}</span>
          <strong>${this._e(entry.name)}</strong>
          <span class="month-points">${householdTasksLocale(this._hass) === "de" ? `${entry.month} diesen Monat` : `${entry.month} this month`}</span>
          <b>${entry.total} ${householdTasksLocale(this._hass) === "de" ? (entry.total === 1 ? "Punkt" : "Punkte") : (entry.total === 1 ? "point" : "points")}</b>
        </div>`).join("")}</div>
    </article>`;
  }

  _occurrenceSection(title, items, tone = "") {
    if (!items.length) return "";
    return `<div class="section-title ${tone}"><h3>${title}</h3><span>${items.length}</span></div>
      <div class="occurrences">${items.map((item) => this._occurrenceCard(item)).join("")}</div>`;
  }

  _occurrenceCard(item) {
    const person = this._data.people[item.assignee] || { name: "Offen" };
    const due = new Date(item.due);
    const overdue = due < new Date();
    const ownPerson = this._currentPersonId();
    const allowed = item.task?.assignment?.people || [];
    const canClaim = !item.assignee && ownPerson && (!allowed.length || allowed.includes(ownPerson));
    return `<article class="task-card ${overdue ? "overdue" : ""}">
      <div class="avatar">${this._e(person.name).slice(0, 1)}</div>
      <div class="task-main">
        <h3>${this._e(this._plainTitle(item.title))}</h3>
        <p>${this._e(person.name)} · ${this._formatDue(due)}${item.sent_steps?.length ? (householdTasksLocale(this._hass) === "de" ? ` · ${item.sent_steps.length}. Hinweis gesendet` : ` · reminder ${item.sent_steps.length} sent`) : ""}</p>
        ${item.assignment_reason ? `<details class="assignment-explanation"><summary>Warum wurde mir das zugewiesen?</summary><p>${this._e(this._assignmentReason(item.assignment_reason))}</p></details>` : ""}
      </div>
      ${item.assignee
        ? `<button class="complete" data-complete="${this._e(item.id)}">Erledigt</button>`
        : `<button class="complete" data-claim="${this._e(item.id)}" ${canClaim ? "" : "disabled"}>Übernehmen</button>`}
    </article>`;
  }

  _currentPersonId() {
    return Object.entries(this._data.people).find(([, person]) =>
      person.user_id && person.user_id === this._hass.user?.id
    )?.[0] || null;
  }

  _plainTitle(title) {
    return String(title || "").replace(/^\[[^\]]+\]\s*/, "");
  }

  _assignmentReason(reason) {
    const name = (id) => this._data.people[id]?.name || id || "–";
    const de = householdTasksLocale(this._hass) === "de";
    if (reason.type === "fixed") {
      return de ? `Fest an ${name(reason.selected)} zugewiesen.` : `Fixed assignment to ${name(reason.selected)}.`;
    }
    if (reason.type === "rotation") {
      return de
        ? `${name(reason.selected)} war Position ${(reason.position || 0) + 1} von ${(reason.candidates || []).length} in der Rotation.`
        : `${name(reason.selected)} was position ${(reason.position || 0) + 1} of ${(reason.candidates || []).length} in the rotation.`;
    }
    if (reason.type === "fair") {
      const count = reason.assignment_counts?.[reason.selected] || 0;
      const load = reason.open_load?.[reason.selected] || 0;
      return de
        ? `${name(reason.selected)} hatte mit ${count} bisherigen Zuweisungen und ${load} offenen Aufgaben die geringste Last.`
        : `${name(reason.selected)} had the lowest workload with ${count} previous assignments and ${load} open tasks.`;
    }
    if (reason.type === "presence") {
      if (reason.waiting) return de ? "Die Aufgabe wartet, bis eine geeignete Person zuhause ist." : "The task is waiting for an eligible person to be home.";
      return de
        ? `${name(reason.selected)} wurde ausgewählt, weil diese Person zuhause ist.`
        : `${name(reason.selected)} was selected because this person is home.`;
    }
    if (reason.type === "handover") {
      return de
        ? `Die Zuständigkeit wurde von ${name(reason.original || reason.previous)} an ${name(reason.selected)} übergeben${reason.reason ? `: ${reason.reason}` : "."}`
        : `Responsibility was handed over from ${name(reason.original || reason.previous)} to ${name(reason.selected)}${reason.reason ? `: ${reason.reason}` : "."}`;
    }
    if (reason.type === "escalated_open") return de ? "Die Eskalationskette hat die Aufgabe zur freien Übernahme geöffnet." : "The escalation chain opened the task for anyone to claim.";
    if (reason.type === "claimed") return de ? `${name(reason.selected)} hat die offene Aufgabe übernommen.` : `${name(reason.selected)} claimed the open task.`;
    if (reason.type === "delegated") return de ? `Von ${name(reason.previous)} an ${name(reason.selected)} weitergegeben${reason.preferred_home ? ", weil die Person zuhause war" : ""}.` : `Delegated from ${name(reason.previous)} to ${name(reason.selected)}${reason.preferred_home ? " because they were home" : ""}.`;
    return de ? "Die Aufgabe war zur freien Übernahme offen." : "The task was open for anyone to claim.";
  }

  _formatDue(date) {
    const today = new Date().toLocaleDateString(this._locale());
    const day = date.toLocaleDateString(this._locale());
    const time = date.toLocaleTimeString(this._locale(), { hour: "2-digit", minute: "2-digit" });
    if (day === today) return `${this._t("Heute").toLocaleLowerCase(this._locale())}, ${time}`;
    return `${date.toLocaleDateString(this._locale(), { day: "2-digit", month: "short" })}, ${time}`;
  }

  _renderTasks() {
    const tasks = Object.entries(this._data.tasks).sort((a, b) => a[1].name.localeCompare(b[1].name, this._locale()));
    return `
      <div class="toolbar"><div><h2>Aufgabenvorlagen</h2><p>${householdTasksLocale(this._hass) === "de" ? `${tasks.length} Regeln für euren Haushalt` : `${tasks.length} household rules`}</p></div>
      ${this._data.is_admin ? `<button class="primary" id="add-task">+ Aufgabe</button>` : ""}</div>
      ${!tasks.length ? `<div class="empty card"><h2>Noch keine Vorlagen</h2><p>Lege eine wiederkehrende Aufgabe an oder nutze eine Schnellaufgabe.</p></div>` : ""}
      <div class="cards">${tasks.map(([id, task]) => {
        const assignment = this._assignmentLabel(task);
        return `<article class="config-card ${task.enabled === false ? "disabled" : ""}">
          <div class="card-top"><span class="avatar">${this._e(assignment.icon)}</span>
          <div><h3>${this._e(task.name)}</h3><p>${this._e(assignment.label)} · ${this._e(this._scheduleLabel(task.schedule))}${task.nfc?.tag_id ? " · NFC" : ""}</p></div>
          <span class="status">${task.enabled === false ? "Pausiert" : "Aktiv"}</span></div>
          ${task.description ? `<p class="description">${this._e(task.description)}</p>` : ""}
          <div class="actions">
            <button data-create="${this._e(id)}" ${task.enabled === false ? "disabled title=\"Aufgabe ist pausiert\"" : ""}>Jetzt erzeugen</button>
            ${this._data.is_admin ? `<button data-edit-task="${this._e(id)}">Bearbeiten</button>
            <button class="danger-button" data-delete-task="${this._e(id)}">Löschen</button>` : ""}
          </div>
        </article>`;
      }).join("")}</div>`;
  }

  _assignmentLabel(task) {
    const type = task.assignment?.type || "fixed";
    if (type === "fixed") {
      const person = this._data.people[task.assignee];
      return { icon: person?.name?.slice(0, 1) || "F", label: person?.name || this._t("Fest") };
    }
    const names = (task.assignment?.people || []).map((id) => this._data.people[id]?.name).filter(Boolean);
    if (type === "rotation") return { icon: "R", label: `Rotation: ${names.join(", ")}` };
    if (type === "fair") return { icon: "=", label: `Fair: ${names.join(", ")}` };
    return { icon: "O", label: names.length ? `${this._t("Offen")}: ${names.join(", ")}` : this._t("Offen für alle") };
  }

  _scheduleLabel(s) {
    if (!s) return this._t("Kein Zeitplan");
    if (s.type === "manual") return this._t("Manuell");
    if (s.type === "weekly") return `${(s.weekdays || []).map((d) => Object.fromEntries(this._weekdays())[d]).join(", ")} · ${s.time}`;
    const de = householdTasksLocale(this._hass) === "de";
    if (s.type === "monthly") return de ? `Monatlich am ${s.day}. · ${s.time}` : `Monthly on day ${s.day} · ${s.time}`;
    if (s.type === "yearly") return de ? `Jährlich am ${s.day}.${s.month}. · ${s.time}` : `Yearly on ${s.month}/${s.day} · ${s.time}`;
    if (s.type === "interval_months") return de ? `Alle ${s.months} Monate · ${s.time}` : `Every ${s.months} months · ${s.time}`;
    if (s.type === "after_completion") return de ? `${s.interval} nach Erledigung` : `${s.interval} after completion`;
    if (s.type === "calendar") return `${de ? "Kalender" : "Calendar"} ${s.entity_id} · ${s.offset || "00:00:00"}`;
    if (s.type === "state_trigger") return de ? `Bei Zustandswechsel · +${s.due_after || "00:00:00"}` : `On state change · +${s.due_after || "00:00:00"}`;
    if (s.type === "daily_after_state") return de ? `Nach Gerätestatus · fällig ${s.time}` : `After device state · due ${s.time}`;
    return s.type;
  }

  _renderPeople() {
    return `
      <div class="toolbar"><div><h2>Personen</h2><p>Zuordnung von Push und Anwesenheit</p></div>
      ${this._data.is_admin ? `<button class="primary" id="add-person">+ Person</button>` : ""}</div>
      ${!Object.keys(this._data.people).length ? `<div class="empty card"><h2>Noch keine Personen</h2><p>Personendaten werden erst gespeichert, wenn du sie hier selbst anlegst.</p></div>` : ""}
      <div class="cards people-grid">${Object.entries(this._data.people).map(([id, p]) => {
        const state = p.presence ? this._hass.states[p.presence]?.state : null;
        const handover = this._data.handovers?.[id];
        const handoverTarget = handover ? this._data.people[handover.to] : null;
        const handoverLabel = householdTasksLocale(this._hass) === "de" ? "Übergabe an" : "Handover to";
        const untilLabel = householdTasksLocale(this._hass) === "de" ? "bis" : "until";
        return `<article class="config-card">
          <div class="card-top"><span class="avatar large">${this._e(p.name).slice(0, 1)}</span>
          <div><h3>${this._e(p.name)}</h3><p>${this._e(id)}</p></div>
          <span class="status ${state === "home" ? "home" : ""}">${state === "home" ? "Zuhause" : state === "not_home" ? "Unterwegs" : "Ohne Status"}</span></div>
          <dl><dt>Push</dt><dd>${this._e(p.notify)}</dd><dt>Anwesenheit</dt><dd>${this._e(p.presence || "nicht konfiguriert")}</dd></dl>
          ${handoverTarget ? `<p class="handover-note">${handoverLabel} <strong>${this._e(handoverTarget.name)}</strong>${handover.until ? ` ${untilLabel} ${new Date(handover.until).toLocaleString(this._locale())}` : ""}${handover.reason ? ` · ${this._e(handover.reason)}` : ""}</p>` : ""}
          ${this._data.is_admin ? `<div class="actions"><button data-edit-person="${this._e(id)}">Bearbeiten</button>
          <button data-handover="${this._e(id)}">${handover ? "Übergabe ändern" : "Übergeben"}</button>
          ${handover ? `<button data-clear-handover="${this._e(id)}">Übergabe beenden</button>` : ""}
          <button class="danger-button" data-delete-person="${this._e(id)}">Löschen</button></div>` : ""}
        </article>`;
      }).join("")}</div>`;
  }

  _renderAnalytics() {
    const analytics = this._data.analytics || {};
    const people = Object.entries(analytics.per_person || {});
    const tasks = Object.entries(analytics.per_task || {})
      .sort((a, b) => b[1].completed - a[1].completed)
      .slice(0, 10);
    const value = (number, suffix = "") => number == null ? this._t("Keine Daten") : `${number}${suffix}`;
    const retrospective = analytics.retrospective || [];
    return `<div class="toolbar"><div><div class="eyebrow">AUSWERTUNG</div><h2>Letzte 30 Tage</h2></div></div>
      <div class="metric-grid">
        <article class="metric"><span>Erledigte Aufgaben</span><b>${analytics.completed || 0}</b></article>
        <article class="metric"><span>Offen</span><b>${analytics.open || 0}</b></article>
        <article class="metric danger"><span>Überfällig</span><b>${analytics.overdue || 0}</b></article>
        <article class="metric"><span>Pünktlich erledigt</span><b>${value(analytics.on_time_rate, "%")}</b></article>
        <article class="metric"><span>Ø Verspätung</span><b>${value(analytics.average_delay_minutes, ` ${this._t("Minuten")}`)}</b></article>
      </div>
      <article class="analytics-card"><h3>Arbeitslast nach Person</h3>
        <div class="table-wrap"><table><thead><tr><th>Person</th><th>Erledigt (30 Tage)</th><th>Offen</th><th>Überfällig</th><th>Ø Verspätung (Min.)</th></tr></thead>
        <tbody>${people.map(([, row]) => `<tr><td><strong>${this._e(row.name)}</strong></td><td>${row.completed}</td><td>${row.open}</td><td>${row.overdue}</td><td>${value(row.average_delay_minutes)}</td></tr>`).join("")}</tbody></table></div>
      </article>
      <article class="analytics-card"><h3>Aufgaben mit den meisten Abschlüssen</h3>
        <div class="table-wrap"><table><thead><tr><th>Aufgabe</th><th>Abschlüsse</th><th>Davon verspätet</th></tr></thead>
        <tbody>${tasks.map(([, row]) => `<tr><td><strong>${this._e(row.name)}</strong></td><td>${row.completed}</td><td>${row.late}</td></tr>`).join("")}</tbody></table></div>
      </article>
      <article class="analytics-card"><h3>Haushalts-Retrospektive</h3>
        <p>Konkrete Hinweise aus Rückständen, Verspätungen und Verteilung der letzten 30 Tage.</p>
        ${retrospective.length
          ? `<div class="insight-list">${retrospective.map((item) => `<div class="insight ${this._e(item.severity)}">${this._e(this._retrospectiveText(item))}</div>`).join("")}</div>`
          : `<p class="positive">Keine auffälligen Engpässe erkannt.</p>`}
      </article>`;
  }

  _retrospectiveText(item) {
    const de = householdTasksLocale(this._hass) === "de";
    if (item.type === "recurring_late") return de ? `${item.name}: ${item.late_rate} % der Abschlüsse waren verspätet. Intervall oder Fälligkeit prüfen.` : `${item.name}: ${item.late_rate}% of completions were late. Review interval or due time.`;
    if (item.type === "task_backlog") return de ? `${item.name}: ${item.overdue} überfällige Vorkommen. Aufgabe aufteilen oder neu planen.` : `${item.name}: ${item.overdue} overdue occurrences. Split or reschedule the task.`;
    if (item.type === "workload_imbalance") return de ? `${item.most_name} hat ${item.most_open} offene Aufgaben, ${item.least_name} nur ${item.least_open}. Verteilung prüfen.` : `${item.most_name} has ${item.most_open} open tasks while ${item.least_name} has ${item.least_open}. Review distribution.`;
    if (item.type === "frequent_reassignment") return de ? `${item.count} Übergaben in 30 Tagen. Zuständigkeiten oder Anwesenheitsregeln prüfen.` : `${item.count} handovers in 30 days. Review ownership or presence rules.`;
    return de ? "Hinweis zur Haushaltsorganisation." : "Household organization insight.";
  }

  _renderHistory() {
    const resolved = this._data.occurrences.filter((o) => o.resolved).slice(0, 60);
    return `<div class="toolbar"><div><h2>Verlauf</h2><p>Erledigte Aufgaben der letzten 90 Tage</p></div></div>
      ${resolved.length ? `<div class="timeline">${resolved.map((item) => `
        <div class="history-row"><span class="check">✓</span><div><h3>${this._e(item.title)}</h3>
        <p>${this._t("Erledigt")} ${new Date(item.resolved_at).toLocaleString(this._locale(), { dateStyle: "medium", timeStyle: "short" })}
        ${item.completed_by && this._data.people[item.completed_by] ? ` · ${householdTasksLocale(this._hass) === "de" ? "von" : "by"} ${this._e(this._data.people[item.completed_by].name)}` : ""}</p></div></div>`).join("")}</div>`
      : `<div class="empty card"><h2>Noch kein Verlauf</h2><p>Erledigte Aufgaben erscheinen hier.</p></div>`}`;
  }

  _renderSettings() {
    const stages = this._data.defaults?.escalation || [];
    const printer = this._data.monitors?.printers || {};
    const detectedPrinters = this._data.detected_printers || [];
    const nfcFeedback = this._data.defaults?.nfc_feedback || { mode: "always", recipients: "scanner" };
    const weeklySummary = this._data.defaults?.weekly_summary || { enabled: false, weekday: "sun", time: "18:00:00" };
    const resources = this._data.monitors?.resources || {};
    const first = stages[0] || {};
    const second = stages[1] || {};
    const final = stages[2] || {};
    return `<div class="toolbar"><div><h2>Einstellungen</h2><p>Globale Regeln und Datenquelle</p></div></div>
      <article class="settings-card">
        <h3>Standard-Eskalation</h3>
        <p>Diese Regeln gelten für alle Aufgaben ohne eigene Eskalation.</p>
        ${this._data.is_admin ? `<form id="defaults-form" class="form-grid">
          <label>Erster Hinweis nach (Std.)<input name="first" type="number" min="0" step=".5" value="${this._hours(first.after)}"></label>
          <label class="checkbox"><input name="presence" type="checkbox" ${first.presence_required ? "checked" : ""}> Nur wenn Person zuhause ist</label>
          <label>Zweiter Hinweis nach (Std.)<input name="second" type="number" min="0" step=".5" value="${this._hours(second.after || "02:00:00")}"></label>
          <label>Zweite Stufe<select name="second_action">
            <option value="delegate" ${second.action === "delegate" ? "selected" : ""}>An nächste Person übergeben</option>
            <option value="notify" ${second.action !== "delegate" && second.action !== "open" ? "selected" : ""}>Erneut erinnern</option>
            <option value="open" ${second.action === "open" ? "selected" : ""}>Zur freien Übernahme öffnen</option>
          </select></label>
          <label>Hinweis an alle nach (Std.)<input name="final" type="number" min="0" step=".5" value="${this._hours(final.after || "24:00:00")}"></label>
          <div class="full"><button class="primary" type="submit">Regeln speichern</button></div>
        </form>` : this._escalationSummary(stages)}
      </article>
      <article class="settings-card">
        <h3>Ressourcen und Verbrauch</h3>
        <p>Sensorwerte können Aufgaben erzeugen und nach Erholung automatisch abschließen – etwa für Vorräte, Füllstände, Batterien oder Filter.</p>
        ${this._data.is_admin ? `<form id="resources-form" class="form-grid">
          <label class="full">Ressourcenregeln (JSON)<textarea name="resources" rows="12">${this._e(JSON.stringify(resources, null, 2))}</textarea>
          <span class="hint">Bedingungen: below, at_most, above, at_least, equals, not_equals. Platzhalter in Name und Beschreibung: {state}, {unit}.</span></label>
          <div class="full"><button class="primary" type="submit">Ressourcenregeln speichern</button></div>
        </form>` : `<div class="info-row"><span>Aktive Regeln</span><span>${Object.values(resources).filter((item) => item.enabled !== false).length}</span></div>`}
      </article>
      <article class="settings-card">
        <h3>Wochenabschluss</h3>
        <p>Sendet allen Personen eine kompakte Zusammenfassung der letzten sieben Tage.</p>
        ${this._data.is_admin ? `<form id="weekly-summary-form" class="form-grid">
          <label class="checkbox full"><input name="enabled" type="checkbox" ${weeklySummary.enabled ? "checked" : ""}> Wochenabschluss aktivieren</label>
          <label>Wochentag<select name="weekday">${this._weekdays().map(([value, label]) => `<option value="${value}" ${weeklySummary.weekday === value ? "selected" : ""}>${label}</option>`).join("")}</select></label>
          <label>Uhrzeit<input name="time" type="time" step="1" value="${this._e(weeklySummary.time || "18:00:00")}"></label>
          <div class="full"><button class="primary" type="submit">Wochenabschluss speichern</button></div>
        </form>` : ""}
      </article>
      <article class="settings-card">
        <h3>NFC-Feedback</h3>
        <p>Kurze Push-Bestätigung nach dem Verarbeiten eines NFC-Tags. Der Scanner wird über die Home-Assistant-Benutzer-ID oder optional die Geräte-ID erkannt.</p>
        ${this._data.is_admin ? `<form id="nfc-feedback-form" class="form-grid">
          <label>Feedback senden<select name="mode">
            <option value="always" ${nfcFeedback.mode === "always" ? "selected" : ""}>Immer</option>
            <option value="errors" ${nfcFeedback.mode === "errors" ? "selected" : ""}>Nur bei Problemen</option>
            <option value="off" ${nfcFeedback.mode === "off" ? "selected" : ""}>Aus</option>
          </select></label>
          <label>Empfänger<select name="recipients">
            <option value="scanner" ${nfcFeedback.recipients === "scanner" ? "selected" : ""}>Scanner</option>
            <option value="assignee" ${nfcFeedback.recipients === "assignee" ? "selected" : ""}>Zuständige Person</option>
            <option value="both" ${nfcFeedback.recipients === "both" ? "selected" : ""}>Beide</option>
          </select></label>
          <div class="full"><button class="primary" type="submit">NFC-Feedback speichern</button></div>
        </form>` : ""}
      </article>
      <article class="settings-card">
        <h3>Druckerprobleme</h3>
        <p>IPP-Drucker werden automatisch erkannt. Papier-, Stopp- und andere gemeldete Fehler erzeugen genau eine Aufgabe; nach der Behebung wird sie automatisch abgeschlossen.</p>
        <div class="info-row"><span>Erkannte Drucker</span><span>${detectedPrinters.length}</span></div>
        ${detectedPrinters.length ? `<div class="printer-list">${detectedPrinters.map((item) =>
          `<span>${this._e(item.name)}</span>`).join("")}</div>` : `<p class="hint">Noch kein IPP-Statussensor erkannt.</p>`}
        ${this._data.is_admin ? `<form id="printer-form" class="form-grid monitor-form">
          <label class="checkbox full"><input name="enabled" type="checkbox" ${printer.enabled ? "checked" : ""}> Druckerprobleme als Aufgaben melden</label>
          <label class="full">Zuständig<select name="assignee" ${Object.keys(this._data.people).length ? "" : "disabled"}>
            <option value="">Bitte auswählen</option>
            ${Object.entries(this._data.people).map(([id, person]) =>
              `<option value="${this._e(id)}" ${id === printer.assignee ? "selected" : ""}>${this._e(person.name)}</option>`
            ).join("")}
          </select></label>
          <div class="full"><button class="primary" type="submit">Druckerüberwachung speichern</button></div>
        </form>` : `<p>${printer.enabled ? "Überwachung aktiv" : "Überwachung nicht aktiviert"}</p>`}
      </article>
      <article class="settings-card">
        <h3>Konfigurationsquelle</h3>
        <p>Personen, Vorlagen und Eskalationen werden vollständig im Home-Assistant-Speicher verwaltet.</p>
        <div class="info-row"><span>To-do-Liste</span><code>${this._e(this._data.todo_entity)}</code></div>
        <div class="info-row"><span>Letzte Prüfung</span><span>${this._data.last_check ? new Date(this._data.last_check).toLocaleString(this._locale()) : "noch nicht erfolgt"}</span></div>
        ${this._data.is_admin ? `<div class="config-transfer"><h3>Konfiguration sichern</h3>
          <p>Export enthält Personen, Vorlagen, Standardregeln und Monitore. Laufende Aufgaben und Verlauf bleiben unberührt.</p>
          <div class="actions"><button id="export-config">Exportieren</button><button id="import-config">Importieren</button>
          <input id="import-file" class="hidden" type="file" accept="application/json,.json"></div></div>` : ""}
        ${this._data.is_admin ? `<button id="reset-config" class="danger-button">Auf Ausgangswerte zurücksetzen</button>` : ""}
      </article>`;
  }

  _escalationSummary(stages) {
    const de = householdTasksLocale(this._hass) === "de";
    return `<ol>${stages.map((s) => `<li>${de ? "Nach" : "After"} ${this._hours(s.after)} ${de ? `Std. an ${this._e(s.recipients)}` : `hours to ${this._e(s.recipients)}`}</li>`).join("")}</ol>`;
  }

  _hours(duration) {
    const [h = 0, m = 0, s = 0] = String(duration || "0:0:0").split(":").map(Number);
    return +(h + m / 60 + s / 3600).toFixed(2);
  }

  _duration(hours) {
    const seconds = Math.round(Number(hours || 0) * 3600);
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    return [h, m, s].map((v) => String(v).padStart(2, "0")).join(":");
  }

  _bind() {
    this.shadowRoot.querySelectorAll("[data-view]").forEach((button) => button.onclick = () => {
      this._view = button.dataset.view; this._render();
    });
    this.shadowRoot.querySelector(".refresh")?.addEventListener("click", () => this._load());
    this.shadowRoot.querySelector("#quick-task")?.addEventListener("click", () => this._showQuickTask());
    this.shadowRoot.querySelectorAll("[data-complete]").forEach((b) => b.onclick = async () => {
      if (await this._confirm("Aufgabe als erledigt markieren?")) {
        await this._call("complete", { occurrence_id: b.dataset.complete });
        this._toast("Aufgabe erledigt");
      }
    });
    this.shadowRoot.querySelectorAll("[data-claim]").forEach((b) => b.onclick = async () => {
      await this._call("claim", { occurrence_id: b.dataset.claim });
      this._toast("Aufgabe übernommen");
    });
    this.shadowRoot.querySelectorAll("[data-create]").forEach((b) => b.onclick = async () => {
      await this._call("create", { task_id: b.dataset.create });
      this._toast("Aufgabe wurde erzeugt");
    });
    this.shadowRoot.querySelector("#add-task")?.addEventListener("click", () => this._showTaskEditor());
    this.shadowRoot.querySelectorAll("[data-edit-task]").forEach((b) => b.onclick = () => this._showTaskEditor(b.dataset.editTask));
    this.shadowRoot.querySelectorAll("[data-delete-task]").forEach((b) => b.onclick = async () => {
      if (await this._confirm("Diese Aufgabenvorlage wirklich löschen?")) {
        await this._call("delete_task", { task_id: b.dataset.deleteTask });
        this._toast("Vorlage gelöscht");
      }
    });
    this.shadowRoot.querySelector("#add-person")?.addEventListener("click", () => this._showPersonEditor());
    this.shadowRoot.querySelectorAll("[data-edit-person]").forEach((b) => b.onclick = () => this._showPersonEditor(b.dataset.editPerson));
    this.shadowRoot.querySelectorAll("[data-delete-person]").forEach((b) => b.onclick = async () => {
      if (await this._confirm("Diese Person wirklich löschen?")) {
        await this._call("delete_person", { person_id: b.dataset.deletePerson });
        this._toast("Person gelöscht");
      }
    });
    this.shadowRoot.querySelectorAll("[data-handover]").forEach((b) => b.onclick = () => this._showHandoverEditor(b.dataset.handover));
    this.shadowRoot.querySelectorAll("[data-clear-handover]").forEach((b) => b.onclick = async () => {
      await this._call("clear_handover", { from_person: b.dataset.clearHandover });
      this._toast("Übergabe beendet");
    });
    this.shadowRoot.querySelector("#defaults-form")?.addEventListener("submit", (event) => this._saveDefaults(event));
    this.shadowRoot.querySelector("#nfc-feedback-form")?.addEventListener("submit", (event) => this._saveNfcFeedback(event));
    this.shadowRoot.querySelector("#weekly-summary-form")?.addEventListener("submit", (event) => this._saveWeeklySummary(event));
    this.shadowRoot.querySelector("#printer-form")?.addEventListener("submit", (event) => this._savePrinters(event));
    this.shadowRoot.querySelector("#resources-form")?.addEventListener("submit", (event) => this._saveResources(event));
    this.shadowRoot.querySelector("#export-config")?.addEventListener("click", () => this._exportConfig());
    this.shadowRoot.querySelector("#import-config")?.addEventListener("click", () => this.shadowRoot.querySelector("#import-file")?.click());
    this.shadowRoot.querySelector("#import-file")?.addEventListener("change", (event) => this._importConfig(event));
    this.shadowRoot.querySelector("#reset-config")?.addEventListener("click", async () => {
      if (await this._confirm("Alle angepassten Personen, Vorlagen und Standardregeln auf die initialen Werte zurücksetzen?")) {
        await this._call("reset_config");
        this._toast("Ausgangswerte wiederhergestellt");
      }
    });
  }

  async _exportConfig() {
    try {
      const exportDocument = await this._hass.callWS({ type: "household_tasks/export_config" });
      const blob = new Blob([JSON.stringify(exportDocument, null, 2)], { type: "application/json" });
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = `household-tasks-${new Date().toISOString().slice(0, 10)}.json`;
      link.click();
      URL.revokeObjectURL(link.href);
      this._toast(householdTasksLocale(this._hass) === "de" ? "Konfiguration exportiert" : "Configuration exported");
    } catch (error) {
      this._toast(this._errorText(error), true);
    }
  }

  async _importConfig(event) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    try {
      const importDocument = JSON.parse(await file.text());
      const question = householdTasksLocale(this._hass) === "de"
        ? "Personen, Vorlagen, Standardregeln und Monitore durch diesen Import ersetzen?"
        : "Replace people, templates, defaults and monitors with this import?";
      if (!await this._confirm(question)) return;
      await this._call("import_config", { document: importDocument });
      this._toast(householdTasksLocale(this._hass) === "de" ? "Konfiguration importiert" : "Configuration imported");
    } catch (error) {
      this._toast(this._errorText(error), true);
    }
  }

  async _confirm(message) {
    return window.confirm(this._t(message));
  }

  async _saveDefaults(event) {
    event.preventDefault();
    const f = new FormData(event.target);
    const defaults = { ...this._data.defaults, escalation: [
      { after: this._duration(f.get("first")), recipients: "assignee", presence_required: f.get("presence") === "on" },
      { after: this._duration(f.get("second")), recipients: "assignee", relative_to: "first_notification", action: f.get("second_action") },
      { after: this._duration(f.get("final")), recipients: "all" },
    ]};
    await this._call("save_defaults", { defaults });
    this._toast("Standardregeln gespeichert");
  }

  async _saveNfcFeedback(event) {
    event.preventDefault();
    const f = new FormData(event.target);
    const defaults = {
      ...this._data.defaults,
      nfc_feedback: {
        mode: f.get("mode"),
        recipients: f.get("recipients"),
      },
    };
    await this._call("save_defaults", { defaults });
    this._toast("NFC-Feedback gespeichert");
  }

  async _saveWeeklySummary(event) {
    event.preventDefault();
    const f = new FormData(event.target);
    const defaults = {
      ...this._data.defaults,
      weekly_summary: {
        enabled: f.get("enabled") === "on",
        weekday: f.get("weekday"),
        time: f.get("time") || "18:00:00",
      },
    };
    await this._call("save_defaults", { defaults });
    this._toast("Wochenabschluss gespeichert");
  }

  async _savePrinters(event) {
    event.preventDefault();
    const f = new FormData(event.target);
    const enabled = f.get("enabled") === "on";
    const assignee = f.get("assignee") || null;
    if (enabled && !assignee) {
      this._toast("Bitte zuerst eine zuständige Person auswählen.", true);
      return;
    }
    await this._call("save_monitors", {
      monitors: { ...this._data.monitors, printers: { enabled, assignee } },
    });
    this._toast("Druckerüberwachung gespeichert");
  }

  async _saveResources(event) {
    event.preventDefault();
    try {
      const resources = JSON.parse(new FormData(event.target).get("resources") || "{}");
      if (!resources || Array.isArray(resources) || typeof resources !== "object") throw new Error();
      await this._call("save_monitors", {
        monitors: { ...this._data.monitors, resources },
      });
      this._toast("Ressourcenregeln gespeichert");
    } catch (_) {
      this._toast("Die Ressourcenregeln sind kein gültiges JSON-Objekt.", true);
    }
  }

  _showTaskEditor(id = null) {
    const task = id ? structuredClone(this._data.tasks[id]) : {
      enabled: true, name: "", assignee: Object.keys(this._data.people)[0] || "",
      assignment: { type: "fixed", people: [] },
      description: "", schedule: { type: "weekly", weekdays: ["mon"], time: "18:00:00" },
    };
    const s = task.schedule || { type: "manual" };
    const esc = task.escalation;
    const nfc = task.nfc || {};
    const assignmentType = task.assignment?.type || "fixed";
    const assignmentPeople = task.assignment?.people || Object.keys(this._data.people);
    const presenceRequired = task.assignment?.presence_required === true;
    const modal = this.shadowRoot.querySelector("#modal");
    modal.innerHTML = `<div class="backdrop"><div class="modal-card">
      <div class="modal-head"><div><div class="eyebrow">AUFGABENVORLAGE</div><h2>${id ? "Aufgabe bearbeiten" : "Neue Aufgabe"}</h2></div><button class="icon-button close">×</button></div>
      <form id="task-form" class="form-grid">
        <label>ID<input name="id" required pattern="[a-z0-9_]+" ${id ? "readonly" : ""} value="${this._e(id || "")}" placeholder="z_b_bad_putzen"></label>
        <label>Name<input name="name" required value="${this._e(task.name)}" placeholder="Bad putzen"></label>
        <label>Zuweisung<select name="assignment_type">
          ${[["fixed","Fest"],["rotation","Rotation"],["fair","Fair"],["open","Offen"]].map(([value, label]) =>
            `<option value="${value}" ${value === assignmentType ? "selected" : ""}>${label}</option>`
          ).join("")}
        </select></label>
        <label class="checkbox"><input name="enabled" type="checkbox" ${task.enabled !== false ? "checked" : ""}> Aufgabe aktiv</label>
        <label class="full fixed-assignee">Zuständig<select name="assignee">${Object.entries(this._data.people).map(([pid, p]) => `<option value="${this._e(pid)}" ${pid === task.assignee ? "selected" : ""}>${this._e(p.name)}</option>`).join("")}</select></label>
        <div class="full assignment-candidates">
          <span class="field-label">Teilnehmende Personen</span>
          <div class="candidate-grid">${Object.entries(this._data.people).map(([pid, person]) =>
            `<label class="checkbox"><input type="checkbox" name="assignment_person" value="${this._e(pid)}" ${assignmentPeople.includes(pid) ? "checked" : ""}> ${this._e(person.name)}</label>`
          ).join("")}</div>
          <p class="hint assignment-hint"></p>
        </div>
        <label class="full checkbox"><input name="presence_required" type="checkbox" ${presenceRequired ? "checked" : ""}> Nur an anwesende Personen zuweisen</label>
        <label class="full">Beschreibung<textarea name="description" rows="2">${this._e(task.description || "")}</textarea></label>
        <label class="full">Folgeaufgaben nach Erledigung (JSON)<textarea name="follow_ups" rows="4">${this._e(JSON.stringify(task.follow_ups || [], null, 2))}</textarea>
          <span class="hint">Beispiel: [{"task_id":"waesche_abnehmen","delay":"02:00:00"}]</span></label>
        <label class="full">NFC-Tag-ID (optional)<input name="nfc_tag_id" value="${this._e(nfc.tag_id || "")}" placeholder="50A3C7C8-1FE7-4BE8-8DC9-06E07D41B63D"></label>
        <label class="full">Beim Scannen<select name="nfc_action">
          <option value="create_or_complete" ${nfc.action === "create_or_complete" || !nfc.action ? "selected" : ""}>Erzeugen oder erledigen</option>
          <option value="create" ${nfc.action === "create" ? "selected" : ""}>Nur erzeugen</option>
          <option value="complete" ${nfc.action === "complete" ? "selected" : ""}>Nur erledigen</option>
        </select><span class="hint">Die Tag-ID findest du nach einem Scan unter Einstellungen → Tags in Home Assistant.</span></label>
        <label>Zeitplan<select name="type">
          ${[["manual","Manuell"],["weekly","Wöchentlich"],["monthly","Monatlich"],["yearly","Jährlich"],["interval_months","Alle N Monate"],["after_completion","Nach letzter Erledigung"],["calendar","Kalender / ICS"],["state_trigger","Bei Zustandswechsel"],["daily_after_state","Einmal täglich nach Gerätestatus"]].map(([v,l]) => `<option value="${v}" ${v === s.type ? "selected" : ""}>${l}</option>`).join("")}
        </select></label>
        <div class="full schedule-fields">${this._scheduleFields(s)}</div>
        <label class="full checkbox"><input name="custom_escalation" type="checkbox" ${esc ? "checked" : ""}> Eigene Eskalationsregeln verwenden</label>
        <div class="full escalation-fields ${esc ? "" : "hidden"}">
          <div class="form-grid">
            <label>Erster Hinweis (Std.)<input name="esc_first" type="number" min="0" step=".5" value="${this._hours(esc?.[0]?.after)}"></label>
            <label class="checkbox"><input name="esc_presence" type="checkbox" ${esc?.[0]?.presence_required ? "checked" : ""}> Nur zuhause</label>
            <label>Zweiter Hinweis (Std.)<input name="esc_second" type="number" min="0" step=".5" value="${this._hours(esc?.[1]?.after || "02:00:00")}"></label>
            <label>Zweite Stufe<select name="esc_second_action">
              <option value="delegate" ${esc?.[1]?.action === "delegate" ? "selected" : ""}>An nächste Person übergeben</option>
              <option value="notify" ${esc?.[1]?.action !== "delegate" && esc?.[1]?.action !== "open" ? "selected" : ""}>Erneut erinnern</option>
              <option value="open" ${esc?.[1]?.action === "open" ? "selected" : ""}>Zur freien Übernahme öffnen</option>
            </select></label>
            <label>Alle informieren (Std.)<input name="esc_final" type="number" min="0" step=".5" value="${this._hours(esc?.[2]?.after || "24:00:00")}"></label>
          </div>
        </div>
        <div class="full modal-actions"><button type="button" class="cancel">Abbrechen</button><button class="primary" type="submit">Speichern</button></div>
      </form></div></div>`;
    this._localize(modal);
    const close = () => { modal.innerHTML = ""; };
    modal.querySelector(".close").onclick = close;
    modal.querySelector(".cancel").onclick = close;
    modal.querySelector("[name=type]").onchange = (event) => {
      const fields = modal.querySelector(".schedule-fields");
      fields.innerHTML = this._scheduleFields({ type: event.target.value, time: "18:00:00" });
      this._localize(fields);
    };
    const updateAssignmentFields = () => {
      const type = modal.querySelector("[name=assignment_type]").value;
      modal.querySelector(".fixed-assignee").classList.toggle("hidden", type !== "fixed");
      modal.querySelector(".assignment-candidates").classList.toggle("hidden", type === "fixed");
      const hints = {
        rotation: this._t("Die Aufgabe wandert bei jeder Erzeugung zur nächsten ausgewählten Person."),
        fair: this._t("Gewählt wird, wer bisher am wenigsten Zuweisungen und aktuell die geringste offene Last hat."),
        open: this._t("Alle ausgewählten Personen erhalten Übernehmen. Ohne Auswahl ist die Aufgabe für alle offen."),
      };
      modal.querySelector(".assignment-hint").textContent = hints[type] || "";
    };
    modal.querySelector("[name=assignment_type]").onchange = updateAssignmentFields;
    updateAssignmentFields();
    modal.querySelector("[name=custom_escalation]").onchange = (event) => {
      modal.querySelector(".escalation-fields").classList.toggle("hidden", !event.target.checked);
    };
    modal.querySelector("#task-form").onsubmit = async (event) => {
      event.preventDefault();
      try {
        const { taskId, value } = this._readTaskForm(event.target);
        await this._call("save_task", { task_id: taskId, task: value });
        close(); this._toast("Aufgabe gespeichert");
      } catch (error) {
        if (!error?.message?.includes("household_tasks")) this._toast(this._errorText(error), true);
      }
    };
  }

  _showQuickTask() {
    const people = Object.entries(this._data.people);
    const userName = String(this._hass.user?.name || "").trim().toLocaleLowerCase("de");
    const ownPerson = people.find(([, person]) =>
      person.user_id && person.user_id === this._hass.user?.id
    )?.[0] || people.find(([, person]) =>
      String(person.name || "").trim().toLocaleLowerCase("de") === userName
    )?.[0] || people[0]?.[0] || "";
    const due = new Date();
    due.setSeconds(0, 0);
    due.setMinutes(Math.ceil((due.getMinutes() + 1) / 15) * 15);
    const modal = this.shadowRoot.querySelector("#modal");
    modal.innerHTML = `<div class="backdrop"><div class="modal-card small">
      <div class="modal-head"><div><div class="eyebrow">EINMALIGE AUFGABE</div><h2>Schnellaufgabe</h2></div><button class="icon-button close">×</button></div>
      <form id="quick-task-form" class="form-grid">
        <label class="full">Was ist zu tun?<input name="name" required autofocus placeholder="Paket zur Post bringen"></label>
        <label>Für wen?<select name="assignee">${people.map(([id, person]) =>
          `<option value="${this._e(id)}" ${id === ownPerson ? "selected" : ""}>${this._e(person.name)}${id === ownPerson ? (householdTasksLocale(this._hass) === "de" ? " (Ich)" : " (me)") : ""}</option>`
        ).join("")}</select></label>
        <label>Fällig<input name="due" type="datetime-local" required value="${this._localDateTimeValue(due)}"></label>
        <label class="full">Notiz<textarea name="description" rows="2" placeholder="Optional"></textarea></label>
        <label class="full">Erinnerungen<select name="reminder_mode">
          <option value="default">Standardregeln verwenden</option>
          <option value="custom">Eigene Zeiten</option>
          <option value="none">Keine Push-Erinnerungen</option>
        </select></label>
        <div class="full quick-escalation hidden">
          <div class="form-grid">
            <label>Erster Hinweis (Std.)<input name="esc_first" type="number" min="0" step=".5" value="0"></label>
            <label class="checkbox"><input name="esc_presence" type="checkbox" checked> Nur zuhause</label>
            <label>Zweiter Hinweis (Std.)<input name="esc_second" type="number" min="0" step=".5" value="2"></label>
            <label>Alle informieren (Std.)<input name="esc_final" type="number" min="0" step=".5" value="24"></label>
          </div>
        </div>
        <div class="full modal-actions"><button type="button" class="cancel">Abbrechen</button><button class="primary" type="submit">Aufgabe hinzufügen</button></div>
      </form></div></div>`;
    this._localize(modal);
    const close = () => { modal.innerHTML = ""; };
    modal.querySelector(".close").onclick = close;
    modal.querySelector(".cancel").onclick = close;
    modal.querySelector("[name=reminder_mode]").onchange = (event) => {
      modal.querySelector(".quick-escalation").classList.toggle("hidden", event.target.value !== "custom");
    };
    modal.querySelector("#quick-task-form").onsubmit = async (event) => {
      event.preventDefault();
      const f = new FormData(event.target);
      const payload = {
        name: f.get("name").trim(),
        assignee: f.get("assignee"),
        due: new Date(f.get("due")).toISOString(),
      };
      if (f.get("description")?.trim()) payload.description = f.get("description").trim();
      if (f.get("reminder_mode") === "none") payload.escalation = [];
      if (f.get("reminder_mode") === "custom") payload.escalation = [
        { after: this._duration(f.get("esc_first")), recipients: "assignee", presence_required: f.get("esc_presence") === "on" },
        { after: this._duration(f.get("esc_second")), recipients: "assignee", relative_to: "first_notification" },
        { after: this._duration(f.get("esc_final")), recipients: "all" },
      ];
      await this._call("create_ad_hoc", payload);
      close();
      this._toast("Schnellaufgabe hinzugefügt");
    };
    requestAnimationFrame(() => modal.querySelector("[name=name]")?.focus());
  }

  _localDateTimeValue(date) {
    const pad = (value) => String(value).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
  }

  _scheduleFields(s) {
    const time = this._e(s.time || "18:00:00");
    if (s.type === "manual") return `<p class="hint">Diese Vorlage wird nur über „Jetzt erzeugen“ oder eine Automation angelegt.</p>`;
    if (s.type === "weekly") return `<div class="weekdays">${this._weekdays().map(([v,l]) => `<label><input type="checkbox" name="weekday" value="${v}" ${(s.weekdays || []).includes(v) ? "checked" : ""}><span>${l}</span></label>`).join("")}</div><label>Uhrzeit<input name="time" type="time" step="1" value="${time}"></label>`;
    if (s.type === "monthly") return `<div class="form-grid"><label>Tag (1–31 oder last)<input name="day" value="${this._e(s.day || 1)}"></label><label>Uhrzeit<input name="time" type="time" step="1" value="${time}"></label></div>`;
    if (s.type === "yearly") return `<div class="form-grid"><label>Monat<input name="month" type="number" min="1" max="12" value="${this._e(s.month || 1)}"></label><label>Tag<input name="day" value="${this._e(s.day || 1)}"></label><label>Uhrzeit<input name="time" type="time" step="1" value="${time}"></label></div>`;
    if (s.type === "interval_months") return `<div class="form-grid"><label>Intervall in Monaten<input name="months" type="number" min="1" value="${this._e(s.months || 6)}"></label><label>Startdatum<input name="start" type="date" value="${this._e(s.start || new Date().toISOString().slice(0,10))}"></label><label>Uhrzeit<input name="time" type="time" step="1" value="${time}"></label></div>`;
    if (s.type === "calendar") return `<div class="form-grid"><label>Kalender-Entität<input name="entity_id" required value="${this._e(s.entity_id || "calendar.muellabfuhr")}"></label><label>Suchmuster<input name="match" value="${this._e(s.match || "")}" placeholder="Restmüll"></label><label>Versatz HH:MM:SS<input name="offset" value="${this._e(s.offset || "-12:00:00")}"></label></div>`;
    if (s.type === "after_completion") return `<div class="form-grid">
      <label>Erstmals fällig<input name="start" type="datetime-local" required value="${this._localDateTimeValue(s.start ? new Date(s.start) : new Date())}"></label>
      <label>Intervall nach Erledigung (HH:MM:SS)<input name="interval" required value="${this._e(s.interval || "168:00:00")}"></label>
      </div>`;
    if (s.type === "state_trigger") return `<div class="form-grid">
      <label>Fällig nach (HH:MM:SS)<input name="due_after" value="${this._e(s.due_after || "00:00:00")}"></label>
      <label>Cooldown (HH:MM:SS)<input name="cooldown" value="${this._e(s.cooldown || "00:00:00")}"></label>
      <label class="checkbox full"><input name="skip_if_open" type="checkbox" ${s.skip_if_open !== false ? "checked" : ""}> Nicht erneut erzeugen, solange die Aufgabe offen ist</label>
      <label class="full">Auslöser (JSON)<textarea name="triggers" rows="7">${this._e(JSON.stringify(s.triggers || [{ entity_id: "binary_sensor.beispiel", from: "off", to: "on" }], null, 2))}</textarea></label></div>
      <p class="hint">Pro Auslöser: entity_id, optional from, verpflichtend to und optional for.</p>`;
    return `<div class="form-grid"><label>Fälligkeit<input name="time" type="time" step="1" value="${time}"></label>
      <label class="full">Auslöser (JSON)<textarea name="triggers" rows="7">${this._e(JSON.stringify(s.triggers || [{ entity_id: "binary_sensor.beispiel", from: "on", to: "off" }], null, 2))}</textarea></label></div>
      <p class="hint">Pro Auslöser: entity_id, optional from, verpflichtend to und optional for.</p>`;
  }

  _readTaskForm(form) {
    const f = new FormData(form);
    const type = f.get("type");
    const schedule = { type };
    if (["weekly", "monthly", "yearly", "interval_months", "daily_after_state"].includes(type)) schedule.time = f.get("time") || "18:00:00";
    if (type === "weekly") schedule.weekdays = f.getAll("weekday");
    if (type === "monthly") schedule.day = f.get("day") === "last" ? "last" : Number(f.get("day"));
    if (type === "yearly") { schedule.month = Number(f.get("month")); schedule.day = f.get("day") === "last" ? "last" : Number(f.get("day")); }
    if (type === "interval_months") { schedule.months = Number(f.get("months")); schedule.start = f.get("start"); }
    if (type === "calendar") { schedule.entity_id = f.get("entity_id"); schedule.match = f.get("match"); schedule.offset = f.get("offset") || "00:00:00"; }
    if (type === "after_completion") {
      schedule.start = new Date(f.get("start")).toISOString();
      schedule.interval = f.get("interval");
    }
    if (["daily_after_state", "state_trigger"].includes(type)) {
      try { schedule.triggers = JSON.parse(f.get("triggers")); }
      catch (_) { throw new Error("Die Geräte-Auslöser sind kein gültiges JSON."); }
    }
    if (type === "state_trigger") {
      schedule.due_after = f.get("due_after") || "00:00:00";
      schedule.cooldown = f.get("cooldown") || "00:00:00";
      schedule.skip_if_open = f.get("skip_if_open") === "on";
    }
    const assignmentType = f.get("assignment_type") || "fixed";
    const assignmentPeople = f.getAll("assignment_person");
    if (["rotation", "fair"].includes(assignmentType) && !assignmentPeople.length) {
      throw new Error("Für Rotation oder faire Verteilung muss mindestens eine Person ausgewählt sein.");
    }
    const value = {
      enabled: f.get("enabled") === "on", name: f.get("name").trim(),
      schedule, assignment: { type: assignmentType },
    };
    if (f.get("presence_required") === "on") value.assignment.presence_required = true;
    if (assignmentType === "fixed") value.assignee = f.get("assignee");
    else if (assignmentPeople.length) value.assignment.people = assignmentPeople;
    if (f.get("description")?.trim()) value.description = f.get("description").trim();
    try {
      const followUps = JSON.parse(f.get("follow_ups") || "[]");
      if (!Array.isArray(followUps)) throw new Error();
      if (followUps.length) value.follow_ups = followUps;
    } catch (_) {
      throw new Error("Die Folgeaufgaben sind kein gültiges JSON-Array.");
    }
    if (f.get("nfc_tag_id")?.trim()) value.nfc = {
      tag_id: f.get("nfc_tag_id").trim(),
      action: f.get("nfc_action") || "create_or_complete",
    };
    if (f.get("custom_escalation") === "on") value.escalation = [
      { after: this._duration(f.get("esc_first")), recipients: "assignee", presence_required: f.get("esc_presence") === "on" },
      { after: this._duration(f.get("esc_second")), recipients: "assignee", relative_to: "first_notification", action: f.get("esc_second_action") },
      { after: this._duration(f.get("esc_final")), recipients: "all" },
    ];
    return { taskId: f.get("id").trim(), value };
  }

  _showHandoverEditor(fromPerson) {
    const source = this._data.people[fromPerson];
    const existing = this._data.handovers?.[fromPerson] || {};
    const targets = Object.entries(this._data.people).filter(([id]) => id !== fromPerson);
    if (!targets.length) {
      this._toast("Für eine Übergabe wird eine zweite Person benötigt.", true);
      return;
    }
    const suggestedUntil = new Date();
    suggestedUntil.setDate(suggestedUntil.getDate() + 7);
    const modal = this.shadowRoot.querySelector("#modal");
    modal.innerHTML = `<div class="backdrop"><div class="modal-card small">
      <div class="modal-head"><div><div class="eyebrow">HAUSHALTSÜBERGABE</div><h2>${householdTasksLocale(this._hass) === "de" ? `${this._e(source.name)} vertreten` : `Delegate ${this._e(source.name)}`}</h2></div><button class="icon-button close">×</button></div>
      <form id="handover-form" class="form-grid">
        <label class="full">Vertretung<select name="to_person">${targets.map(([id, person]) =>
          `<option value="${this._e(id)}" ${existing.to === id ? "selected" : ""}>${this._e(person.name)}</option>`
        ).join("")}</select></label>
        <label class="full">Bis (optional)<input name="until" type="datetime-local" value="${this._localDateTimeValue(existing.until ? new Date(existing.until) : suggestedUntil)}"></label>
        <label class="full">Grund (optional)<input name="reason" value="${this._e(existing.reason || "")}" placeholder="Urlaub, Krankheit, Dienstreise"></label>
        <p class="hint full">Offene Aufgaben werden sofort übertragen. Neue Aufgaben folgen der Vertretung automatisch.</p>
        <div class="full modal-actions"><button type="button" class="cancel">Abbrechen</button><button class="primary" type="submit">Übergabe aktivieren</button></div>
      </form></div></div>`;
    this._localize(modal);
    const close = () => { modal.innerHTML = ""; };
    modal.querySelector(".close").onclick = close;
    modal.querySelector(".cancel").onclick = close;
    modal.querySelector("#handover-form").onsubmit = async (event) => {
      event.preventDefault();
      const f = new FormData(event.target);
      const payload = {
        from_person: fromPerson,
        to_person: f.get("to_person"),
      };
      if (f.get("until")) payload.until = new Date(f.get("until")).toISOString();
      if (f.get("reason")?.trim()) payload.reason = f.get("reason").trim();
      await this._call("set_handover", payload);
      close();
      this._toast("Übergabe aktiviert");
    };
  }

  _showPersonEditor(id = null) {
    const p = id ? this._data.people[id] : { name: "", notify: "", presence: "", user_id: "" };
    const modal = this.shadowRoot.querySelector("#modal");
    modal.innerHTML = `<div class="backdrop"><div class="modal-card small">
      <div class="modal-head"><div><div class="eyebrow">PERSON</div><h2>${id ? "Person bearbeiten" : "Neue Person"}</h2></div><button class="icon-button close">×</button></div>
      <form id="person-form" class="form-grid">
        <label>ID<input name="id" required pattern="[a-z0-9_]+" ${id ? "readonly" : ""} value="${this._e(id || "")}" placeholder="vorname"></label>
        <label>Name<input name="name" required value="${this._e(p.name)}"></label>
        <label class="full">Push-Aktion<input name="notify" required pattern="notify\\..+" value="${this._e(p.notify)}" placeholder="notify.mobile_app_iphone"></label>
        <label class="full">Anwesenheits-Entität<input name="presence" value="${this._e(p.presence || "")}" placeholder="person.vorname"></label>
        <label class="full">Home-Assistant-Benutzer-ID<input name="user_id" value="${this._e(p.user_id || "")}" placeholder="Für die Vorauswahl „Ich selbst“"></label>
        <label class="full">NFC-Geräte-ID (optional)<input name="nfc_device_id" value="${this._e(p.nfc_device_id || "")}"><span class="hint">Wird nur benötigt, wenn der Scan keiner Home-Assistant-Benutzer-ID zugeordnet werden kann.</span></label>
        <div class="full modal-actions"><button type="button" class="cancel">Abbrechen</button><button class="primary" type="submit">Speichern</button></div>
      </form></div></div>`;
    this._localize(modal);
    const close = () => { modal.innerHTML = ""; };
    modal.querySelector(".close").onclick = close; modal.querySelector(".cancel").onclick = close;
    modal.querySelector("#person-form").onsubmit = async (event) => {
      event.preventDefault();
      const f = new FormData(event.target);
      const person = { name: f.get("name").trim(), notify: f.get("notify").trim() };
      if (f.get("presence")?.trim()) person.presence = f.get("presence").trim();
      if (f.get("user_id")?.trim()) person.user_id = f.get("user_id").trim();
      if (f.get("nfc_device_id")?.trim()) person.nfc_device_id = f.get("nfc_device_id").trim();
      await this._call("save_person", { person_id: f.get("id").trim(), person });
      close(); this._toast("Person gespeichert");
    };
  }

  _styles() {
    return `
      :host{display:block;min-height:100%;background:var(--primary-background-color,#f5f6f8);color:var(--primary-text-color,#202124);font-family:var(--paper-font-body1_-_font-family,system-ui,sans-serif)}
      *{box-sizing:border-box}button,input,select,textarea{font:inherit;color:inherit}main{max-width:1180px;margin:auto;padding:24px}
      header{display:flex;align-items:center;justify-content:space-between;margin-bottom:18px}h1{font-size:30px;margin:2px 0}h2{margin:0;font-size:24px}h3{margin:0}p{color:var(--secondary-text-color,#6b7280)}
      .eyebrow{font-size:11px;letter-spacing:.13em;font-weight:800;color:var(--primary-color,#03a9f4);text-transform:uppercase}
      nav{display:flex;gap:4px;border-bottom:1px solid var(--divider-color,#ddd);overflow:auto;margin-bottom:24px}nav button{border:0;background:none;padding:12px 16px;color:var(--secondary-text-color);cursor:pointer;white-space:nowrap;border-bottom:3px solid transparent}nav button.active{color:var(--primary-color);border-color:var(--primary-color);font-weight:700}
      button{border:1px solid var(--divider-color,#d6d9de);border-radius:10px;padding:9px 14px;background:var(--card-background-color,#fff);cursor:pointer;font-weight:650}button:hover{filter:brightness(.97)}button:disabled{opacity:.55}.primary{background:var(--primary-color,#03a9f4);color:var(--text-primary-color,#fff);border-color:transparent}.icon-button{width:42px;height:42px;border-radius:50%;font-size:22px;padding:0}
      .hero{display:flex;justify-content:space-between;align-items:center;border-radius:20px;padding:24px 28px;background:linear-gradient(135deg,var(--primary-color,#03a9f4),#536dfe);color:#fff;margin-bottom:16px}.hero .eyebrow,.hero p{color:#e9f7ff}.hero h2{font-size:28px}.hero-side{display:flex;align-items:center;gap:18px}.hero-button{background:#fff;color:#3347ba;border:0}.score{font-size:36px;font-weight:800;text-align:center}.score small{display:block;font-size:12px;font-weight:500}
      .people-strip{display:flex;gap:9px;overflow:auto;padding:4px 0 18px}.person-chip{display:flex;align-items:center;gap:8px;background:var(--card-background-color,#fff);border:1px solid var(--divider-color,#ddd);border-radius:99px;padding:6px 10px 6px 6px;white-space:nowrap}.person-chip b{color:var(--secondary-text-color)}
      .ranking-card{background:var(--card-background-color,#fff);border:1px solid var(--divider-color,#ddd);border-radius:16px;padding:17px 18px;margin-bottom:18px}.ranking-head{display:flex;justify-content:space-between;align-items:end;margin-bottom:10px}.ranking-head>span{font-size:12px;color:var(--secondary-text-color)}.ranking-list{display:grid;gap:4px}.ranking-row{display:grid;grid-template-columns:30px 38px minmax(100px,1fr) auto auto;align-items:center;gap:10px;padding:7px 2px;border-top:1px solid var(--divider-color,#ddd)}.ranking-row:first-child{border-top:0}.rank{display:grid;place-items:center;width:26px;height:26px;border-radius:50%;font-size:12px;font-weight:800;background:var(--divider-color,#eee)}.rank.top-1{background:#ffe08a;color:#6d4c00}.rank.top-2{background:#e2e5e9;color:#46505a}.rank.top-3{background:#e8c3a3;color:#70401f}.month-points{font-size:12px;color:var(--secondary-text-color)}.ranking-row>b{min-width:72px;text-align:right;color:var(--primary-color)}
      .avatar{display:grid;place-items:center;flex:none;width:38px;height:38px;border-radius:50%;background:color-mix(in srgb,var(--primary-color,#03a9f4) 16%,var(--card-background-color,#fff));color:var(--primary-color,#03a9f4);font-weight:800}.avatar.large{width:52px;height:52px;font-size:20px}
      .section-title{display:flex;align-items:center;gap:8px;margin:20px 2px 9px}.section-title span{font-size:12px;background:var(--divider-color,#ddd);padding:2px 7px;border-radius:99px}.section-title.danger h3{color:var(--error-color,#db4437)}
      .occurrences{display:grid;gap:8px}.task-card{display:flex;align-items:center;gap:13px;background:var(--card-background-color,#fff);padding:13px 15px;border-radius:14px;border:1px solid var(--divider-color,#ddd);box-shadow:0 1px 2px #0000000a}.task-card.overdue{border-left:4px solid var(--error-color,#db4437)}.task-main{flex:1;min-width:0}.task-main h3{font-size:16px}.task-main p{font-size:13px;margin:4px 0 0}.complete{color:var(--primary-color);border-color:color-mix(in srgb,var(--primary-color) 35%,transparent)}
      .assignment-explanation{margin-top:6px;font-size:12px;color:var(--secondary-text-color)}.assignment-explanation summary{cursor:pointer}.assignment-explanation p{margin:5px 0 0}
      .toolbar{display:flex;align-items:end;justify-content:space-between;margin-bottom:18px}.toolbar p{margin:5px 0 0}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:12px}.config-card,.settings-card,.card{background:var(--card-background-color,#fff);border:1px solid var(--divider-color,#ddd);border-radius:16px;padding:18px}.config-card.disabled{opacity:.65}.card-top{display:flex;align-items:center;gap:12px}.card-top>div:nth-child(2){flex:1}.card-top p{font-size:13px;margin:3px 0}.status{font-size:11px;font-weight:750;padding:4px 8px;border-radius:99px;background:var(--divider-color,#eee)}.status.home{background:#daf5df;color:#17752a}.description{font-size:14px}.actions{display:flex;gap:7px;margin-top:15px;flex-wrap:wrap}.danger-button{color:var(--error-color,#db4437)}dl{font-size:13px}dt{color:var(--secondary-text-color);margin-top:9px}dd{margin:2px 0;overflow-wrap:anywhere}
      .timeline{background:var(--card-background-color,#fff);border-radius:16px;border:1px solid var(--divider-color,#ddd);padding:4px 18px}.history-row{display:flex;gap:13px;align-items:center;padding:14px 0;border-bottom:1px solid var(--divider-color,#ddd)}.history-row:last-child{border:0}.history-row p{margin:4px 0 0;font-size:13px}.check{display:grid;place-items:center;border-radius:50%;width:34px;height:34px;background:#daf5df;color:#17752a;font-weight:800}
      .settings-card{max-width:760px;margin-bottom:14px}.settings-card>p{margin-top:6px}.info-row{display:flex;justify-content:space-between;gap:12px;padding:12px 0;border-top:1px solid var(--divider-color,#ddd);font-size:14px}.settings-card>.danger-button{margin-top:14px}
      .config-transfer{border-top:1px solid var(--divider-color,#ddd);margin-top:12px;padding-top:16px}.config-transfer p{font-size:13px}.metric-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:16px}.metric,.analytics-card{background:var(--card-background-color,#fff);border:1px solid var(--divider-color,#ddd);border-radius:16px;padding:18px}.metric{display:flex;flex-direction:column;gap:8px}.metric span{font-size:13px;color:var(--secondary-text-color)}.metric b{font-size:26px}.metric.danger b{color:var(--error-color,#db4437)}.analytics-card{margin-bottom:14px}.analytics-card h3{margin-bottom:12px}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;padding:10px;border-top:1px solid var(--divider-color,#ddd);white-space:nowrap}th{font-size:12px;color:var(--secondary-text-color)}.insight-list{display:grid;gap:8px}.insight{padding:11px 13px;border-radius:10px;background:var(--secondary-background-color,#f3f4f6);border-left:4px solid var(--primary-color)}.insight.warning{border-color:#f59e0b}.insight.critical{border-color:var(--error-color,#db4437)}.positive{color:#17752a}.handover-note{padding:9px 11px;border-radius:9px;background:color-mix(in srgb,var(--primary-color) 10%,transparent);font-size:13px}
      .monitor-form{margin-top:16px}.printer-list{display:flex;gap:7px;flex-wrap:wrap;margin:2px 0 10px}.printer-list span{font-size:12px;padding:5px 9px;border-radius:99px;background:var(--divider-color,#eee)}
      .empty{text-align:center;padding:50px 20px}.big-icon{font-size:42px;color:#25a244}.spinner{width:32px;height:32px;border:3px solid var(--divider-color);border-top-color:var(--primary-color);border-radius:50%;animation:spin .8s linear infinite;margin:auto}@keyframes spin{to{transform:rotate(360deg)}}
      .backdrop{position:fixed;z-index:1000;inset:0;background:#0009;display:grid;place-items:center;padding:18px}.modal-card{width:min(760px,100%);max-height:92vh;overflow:auto;background:var(--card-background-color,#fff);border-radius:20px;padding:22px}.modal-card.small{width:min(580px,100%)}.modal-head{display:flex;justify-content:space-between;align-items:start;margin-bottom:20px}.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:15px}.form-grid label{display:flex;flex-direction:column;gap:6px;font-size:13px;font-weight:650}.form-grid .full{grid-column:1/-1}.form-grid .checkbox{flex-direction:row;align-items:center;align-self:end;min-height:42px}.form-grid input,.form-grid select,.form-grid textarea{width:100%;padding:10px 11px;border:1px solid var(--divider-color,#bbb);border-radius:9px;background:var(--primary-background-color,#fafafa)}.checkbox input{width:auto}.modal-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:5px}.hidden{display:none!important}.hint{font-size:13px;margin:2px 0}.weekdays{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px}.weekdays input{display:none}.weekdays span{display:grid;place-items:center;width:40px;height:36px;border:1px solid var(--divider-color);border-radius:9px;cursor:pointer}.weekdays input:checked+span{background:var(--primary-color);color:#fff;border-color:var(--primary-color)}
      .field-label{display:block;font-size:13px;font-weight:650;margin-bottom:7px}.candidate-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:5px 12px;padding:7px 10px;border:1px solid var(--divider-color,#bbb);border-radius:9px}.candidate-grid .checkbox{min-height:32px;font-weight:500}
      .toast{position:fixed;z-index:2000;left:50%;bottom:28px;transform:translateX(-50%);background:#263238;color:#fff;padding:12px 18px;border-radius:10px;box-shadow:0 4px 20px #0005}.toast.error{background:var(--error-color,#db4437)}
      @media(max-width:650px){main{padding:16px 12px}header{margin-bottom:8px}h1{font-size:24px}nav{margin-bottom:16px}.hero{padding:20px;align-items:flex-start}.hero h2{font-size:21px}.hero-side{flex-direction:column-reverse;align-items:flex-end;gap:10px}.hero-button{padding:8px 10px}.cards{grid-template-columns:1fr}.task-card{align-items:flex-start}.complete{padding:8px}.form-grid{grid-template-columns:1fr}.form-grid .full{grid-column:auto}.modal-card{padding:18px 15px}.toolbar{align-items:center}.people-grid{grid-template-columns:1fr}.ranking-row{grid-template-columns:27px 34px 1fr auto}.ranking-row .avatar{width:34px;height:34px}.month-points{display:none}.ranking-head>span{display:none}}
    `;
  }
}

if (!customElements.get("household-tasks-panel")) {
  customElements.define("household-tasks-panel", HouseholdTasksPanel);
}
