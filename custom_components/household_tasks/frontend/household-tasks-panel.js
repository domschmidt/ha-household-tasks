const frontendVersion = new URL(import.meta.url).searchParams.get("v") || "development";
const {
  householdTasksLocale,
  householdTasksLocaleTag,
  householdTasksText,
  localizeHouseholdTasksTree,
} = await import(`./household-tasks-translations.js?v=${encodeURIComponent(frontendVersion)}`);

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
    this._references = { users: [], devices: [], tags: [] };
    this._referencesLoaded = false;
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
    try {
      await this._call("get");
      await this._loadReferences();
    } catch (_) { /* message is already visible */ }
  }

  async _loadReferences() {
    if (this._referencesLoaded || !this._hass?.user?.is_admin) return;
    this._referencesLoaded = true;
    const load = async (type) => {
      try {
        const result = await this._hass.callWS({ type });
        return Array.isArray(result) ? result : [];
      } catch (_) {
        return [];
      }
    };
    const [users, devices, tags] = await Promise.all([
      load("config/auth/list"),
      load("config/device_registry/list"),
      load("tag/list"),
    ]);
    this._references = { users, devices, tags };
    this._render();
  }

  _errorText(error) {
    return error?.message || error?.error?.message || String(error || "Unbekannter Fehler");
  }

  _toast(message, error = false) {
    const old = this.shadowRoot.querySelector(".toast");
    if (old) old.remove();
    const toast = document.createElement("div");
    toast.className = `toast${error ? " error" : ""}`;
    toast.setAttribute("role", error ? "alert" : "status");
    toast.setAttribute("aria-live", error ? "assertive" : "polite");
    toast.setAttribute("aria-atomic", "true");
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

  _entitySuggestions(domains = []) {
    const allowed = new Set(domains);
    return Object.entries(this._hass?.states || {})
      .filter(([entityId]) => !allowed.size || allowed.has(entityId.split(".")[0]))
      .map(([entityId, state]) => ({
        value: entityId,
        label: state.attributes?.friendly_name || entityId,
        detail: state.state,
      }))
      .sort((a, b) => a.label.localeCompare(b.label, this._locale()));
  }

  _suggestionInput(name, value, suggestions, {
    required = false,
    placeholder = "",
    hint = "",
    pattern = "",
  } = {}) {
    const listId = `ht-${name}-suggestions`;
    const options = suggestions.map((item) =>
      `<option value="${this._e(item.value)}">${this._e(item.label)}${item.detail ? ` · ${this._e(item.detail)}` : ""}</option>`
    ).join("");
    return `<input name="${this._e(name)}" list="${listId}" value="${this._e(value || "")}"
      ${required ? "required" : ""} ${pattern ? `pattern="${this._e(pattern)}"` : ""}
      placeholder="${this._e(placeholder)}" autocomplete="off">
      <datalist id="${listId}">${options}</datalist>
      ${hint ? `<span class="hint">${this._e(hint)}</span>` : ""}`;
  }

  _entityInput(name, value, domains, options = {}) {
    return this._suggestionInput(
      name,
      value,
      this._entitySuggestions(domains),
      options,
    );
  }

  _notifyInput(value) {
    const suggestions = Object.entries(this._hass?.services?.notify || {})
      .map(([service, description]) => ({
        value: `notify.${service}`,
        label: description?.name || service.replaceAll("_", " "),
      }))
      .sort((a, b) => a.label.localeCompare(b.label, this._locale()));
    return this._suggestionInput("notify", value, suggestions, {
      required: true,
      pattern: "notify\\..+",
      placeholder: "notify.mobile_app_iphone",
      hint: "Wähle eine vorhandene notify-Aktion. Mobile-App-Ziele erscheinen nach der Anmeldung des Geräts.",
    });
  }

  _userInput(value) {
    const users = new Map();
    for (const user of this._references.users) {
      users.set(user.id, { value: user.id, label: user.name || user.username || user.id });
    }
    if (this._hass?.user?.id) {
      users.set(this._hass.user.id, {
        value: this._hass.user.id,
        label: `${this._hass.user.name || this._hass.user.id} (Ich selbst)`,
      });
    }
    for (const person of Object.values(this._data?.people || {})) {
      if (person.user_id && !users.has(person.user_id)) {
        users.set(person.user_id, { value: person.user_id, label: person.name || person.user_id });
      }
    }
    return this._suggestionInput("user_id", value, [...users.values()], {
      placeholder: "Optional",
      hint: "Verknüpft die Haushalts-Person mit einem Home-Assistant-Benutzer, z. B. für „Ich selbst“ und NFC.",
    });
  }

  _deviceInput(value) {
    const devices = this._references.devices.map((device) => ({
      value: device.id,
      label: device.name_by_user || device.name || device.id,
      detail: device.manufacturer || "",
    }));
    return this._suggestionInput("nfc_device_id", value, devices, {
      placeholder: "Optional",
      hint: "Nur als Fallback nötig, wenn ein NFC-Scan keinem Home-Assistant-Benutzer zugeordnet werden kann.",
    });
  }

  _tagInput(value) {
    const tags = this._references.tags.map((tag) => ({
      value: tag.tag_id || tag.id,
      label: tag.name || tag.tag_id || tag.id,
    }));
    const input = this._suggestionInput("nfc_tag_id", value, tags, {
      placeholder: "Tag scannen oder auswählen",
      hint: "Vorhandene Tags werden vorgeschlagen. Neue iPhone-Tags erscheinen nach dem ersten Scan in Home Assistant.",
    });
    return `<div class="nfc-tag-control"><div class="field-with-action">
      <label>NFC-Tag-ID (optional)${input}</label>
      <button type="button" class="field-action" data-toggle-tag-creator
        aria-label="${this._e(this._t("Neuen NFC-Tag anlegen"))}"
        title="${this._e(this._t("Neuen NFC-Tag anlegen"))}">+</button>
    </div>
    <div class="test-line"><button type="button" data-test-tag>Tag prüfen</button><output class="tag-test-result preview-result" aria-live="polite"></output></div>
    <div class="tag-creator hidden">
      <label>Tag-Name<input name="new_tag_name" autocomplete="off" placeholder="z. B. Waschmaschine erledigt"></label>
      <button type="button" class="primary" data-create-tag>Tag anlegen</button>
      <button type="button" data-cancel-tag>Abbrechen</button>
      <p class="hint">Der Tag wird in Home Assistant angelegt und sofort ausgewählt. In der Companion-App öffnet sich anschließend der NFC-Schreibdialog.</p>
    </div></div>`;
  }

  _writeTagWithCompanion(tagId, name) {
    const message = JSON.stringify({
      id: Date.now(),
      type: "tag/write",
      payload: { tag: tagId, name: name || null },
    });
    if (window.externalAppV2?.postMessage) {
      window.externalAppV2.postMessage(message);
      return true;
    }
    if (window.externalApp?.externalBus) {
      window.externalApp.externalBus(message);
      return true;
    }
    if (window.webkit?.messageHandlers?.externalBus?.postMessage) {
      window.webkit.messageHandlers.externalBus.postMessage(message);
      return true;
    }
    return false;
  }

  _bindTagCreator(modal) {
    const creator = modal.querySelector(".tag-creator");
    const toggle = modal.querySelector("[data-toggle-tag-creator]");
    if (!creator || !toggle) return;
    const nameInput = creator.querySelector("[name=new_tag_name]");
    const setOpen = (open) => {
      creator.classList.toggle("hidden", !open);
      toggle.setAttribute("aria-expanded", String(open));
      if (open) requestAnimationFrame(() => nameInput.focus());
    };
    toggle.setAttribute("aria-expanded", "false");
    toggle.onclick = () => setOpen(creator.classList.contains("hidden"));
    nameInput.oninput = () => nameInput.setCustomValidity("");
    creator.querySelector("[data-cancel-tag]").onclick = () => setOpen(false);
    creator.querySelector("[data-create-tag]").onclick = async () => {
      const name = nameInput.value.trim();
      if (!name) {
        nameInput.setCustomValidity(this._t("Bitte gib einen Namen für den NFC-Tag ein."));
        nameInput.reportValidity();
        return;
      }
      nameInput.setCustomValidity("");
      const button = creator.querySelector("[data-create-tag]");
      button.disabled = true;
      try {
        const created = await this._hass.callWS({ type: "tag/create", name });
        const tagId = created?.tag_id || created?.id;
        if (!tagId) throw new Error("Home Assistant hat keine Tag-ID zurückgegeben.");
        this._references.tags = [
          ...this._references.tags.filter((tag) => (tag.tag_id || tag.id) !== tagId),
          { id: tagId, tag_id: tagId, name },
        ];
        const tagInput = modal.querySelector("[name=nfc_tag_id]");
        tagInput.value = tagId;
        const list = modal.querySelector(`#${tagInput.getAttribute("list")}`);
        if (list) list.innerHTML = this._references.tags.map((tag) =>
          `<option value="${this._e(tag.tag_id || tag.id)}">${this._e(tag.name || tag.tag_id || tag.id)}</option>`
        ).join("");
        setOpen(false);
        const writing = this._writeTagWithCompanion(tagId, name);
        this._toast(writing
          ? "NFC-Tag angelegt. Halte das Smartphone jetzt an den Chip."
          : "NFC-Tag angelegt und ausgewählt. Beschreibe ihn anschließend mit der Home-Assistant-App.");
      } catch (error) {
        this._toast(this._errorText(error), true);
      } finally {
        button.disabled = false;
      }
    };
    modal.querySelector("[data-test-tag]")?.addEventListener("click", () => {
      const tagId = modal.querySelector("[name=nfc_tag_id]")?.value.trim();
      const output = modal.querySelector(".tag-test-result");
      const tag = this._references.tags.find((item) => (item.tag_id || item.id) === tagId);
      if (!tagId) output.textContent = "Noch kein Tag ausgewählt.";
      else if (!tag) output.textContent = "Tag-ID ist eingetragen, aber nicht in Home Assistant registriert.";
      else output.textContent = `Registriert als „${tag.name || tagId}“${tag.last_scanned ? ` · zuletzt ${new Date(tag.last_scanned).toLocaleString(this._locale())}` : ""}.`;
    });
  }

  _escalationRows(stages = []) {
    if (!stages.length) return `<p class="empty-row">Noch keine Eskalationsstufe angelegt.</p>`;
    return stages.map((stage) => `<div class="repeatable-row escalation-row">
      <label>Nach (HH:MM:SS)<input name="escalation_after" required value="${this._e(stage.after || "00:00:00")}" pattern="[0-9]+:[0-5][0-9]:[0-5][0-9]"></label>
      <label>Bezug<select name="escalation_relative_to">
        <option value="due" ${stage.relative_to !== "first_notification" ? "selected" : ""}>Fälligkeit</option>
        <option value="first_notification" ${stage.relative_to === "first_notification" ? "selected" : ""}>Erste Benachrichtigung</option>
      </select></label>
      <label>Empfänger<select name="escalation_recipients">
        <option value="assignee" ${stage.recipients !== "all" ? "selected" : ""}>Zuständige Person</option>
        <option value="all" ${stage.recipients === "all" ? "selected" : ""}>Alle Personen</option>
      </select></label>
      <label>Aktion<select name="escalation_action">
        <option value="notify" ${!stage.action || stage.action === "notify" ? "selected" : ""}>Benachrichtigen</option>
        <option value="delegate" ${stage.action === "delegate" ? "selected" : ""}>Weitergeben</option>
        <option value="open" ${stage.action === "open" ? "selected" : ""}>Zur Übernahme öffnen</option>
      </select></label>
      <label class="checkbox"><input name="escalation_presence" type="checkbox" ${stage.presence_required ? "checked" : ""}> Nur bei Anwesenheit</label>
      <button type="button" class="remove-row" title="Eskalationsstufe entfernen" aria-label="Eskalationsstufe entfernen">×</button>
    </div>`).join("");
  }

  _readEscalation(root) {
    return [...root.querySelectorAll(".escalation-row")].map((row) => {
      const stage = {
        after: row.querySelector("[name=escalation_after]").value,
        recipients: row.querySelector("[name=escalation_recipients]").value,
      };
      const relativeTo = row.querySelector("[name=escalation_relative_to]").value;
      const action = row.querySelector("[name=escalation_action]").value;
      if (relativeTo !== "due") stage.relative_to = relativeTo;
      if (action !== "notify") stage.action = action;
      if (row.querySelector("[name=escalation_presence]").checked) stage.presence_required = true;
      return stage;
    });
  }

  _bindEscalationEditor(root) {
    root.querySelectorAll(".escalation-editor").forEach((editor) => {
      const list = editor.querySelector(".repeatable-list");
      const bindRemovers = () => editor.querySelectorAll(".remove-row").forEach((button) => {
        button.onclick = () => {
          button.closest(".escalation-row").remove();
          if (!editor.querySelector(".escalation-row")) list.innerHTML = this._escalationRows([]);
        };
      });
      editor.querySelector("[data-add-escalation]").onclick = () => {
        const stages = this._readEscalation(editor);
        list.innerHTML = this._escalationRows([...stages, {
          after: stages.length ? "02:00:00" : "00:00:00",
          recipients: "assignee",
          relative_to: stages.length ? "first_notification" : "due",
        }]);
        this._localize(list);
        this._enhanceAccessibility(editor);
        bindRemovers();
        list.querySelector(".escalation-row:last-of-type input")?.focus();
      };
      bindRemovers();
    });
  }

  _resourceRows(resources = {}) {
    const entries = Object.entries(resources);
    if (!entries.length) return `<p class="empty-row">Noch keine Ressourcenregel angelegt.</p>`;
    const entityOptions = this._entitySuggestions(["sensor", "binary_sensor", "input_number", "input_select"]);
    return entries.map(([id, rule]) => `<article class="resource-row" data-original-id="${this._e(id)}">
      <div class="resource-head"><strong>${this._e(rule.task_name || id)}</strong>
        <button type="button" class="remove-row" data-remove-resource aria-label="Ressourcenregel entfernen">×</button></div>
      <div class="form-grid">
        <label>Regel-ID<input name="resource_id" required pattern="[a-z0-9_]+" value="${this._e(id)}"></label>
        <label class="checkbox"><input name="resource_enabled" type="checkbox" ${rule.enabled !== false ? "checked" : ""}> Regel aktiv</label>
        <label class="full">Sensor<select name="resource_entity_id" required><option value="">Bitte auswählen</option>${entityOptions.map((item) => `<option value="${this._e(item.value)}" ${rule.entity_id === item.value ? "selected" : ""}>${this._e(item.label)} · ${this._e(item.detail)}</option>`).join("")}</select></label>
        <label>Bedingung<select name="resource_condition">
          ${[["below","kleiner als"],["at_most","höchstens"],["above","größer als"],["at_least","mindestens"],["equals","gleich"],["not_equals","ungleich"]].map(([value,label]) => `<option value="${value}" ${rule.condition === value ? "selected" : ""}>${label}</option>`).join("")}
        </select></label>
        <label>Grenzwert<input name="resource_threshold" required value="${this._e(rule.threshold ?? "")}" placeholder="z. B. 20 oder low"></label>
        <label class="full">Aufgabenname<input name="resource_task_name" required value="${this._e(rule.task_name || "")}" placeholder="Vorrat auffüllen ({state} {unit})"></label>
        <label class="full">Beschreibung<textarea name="resource_description" rows="2">${this._e(rule.description || "")}</textarea></label>
        <label>Zuständig<select name="resource_assignee" required><option value="">Bitte auswählen</option>${Object.entries(this._data.people).map(([pid,p]) => `<option value="${this._e(pid)}" ${rule.assignee === pid ? "selected" : ""}>${this._e(p.name)}</option>`).join("")}</select></label>
        <label>Fällig nach<input name="resource_due_after" value="${this._e(rule.due_after || "00:00:00")}" pattern="[0-9]+:[0-5][0-9]:[0-5][0-9]"></label>
        <label>Cooldown<input name="resource_cooldown" value="${this._e(rule.cooldown || "24:00:00")}" pattern="[0-9]+:[0-5][0-9]:[0-5][0-9]"></label>
        <label class="checkbox"><input name="resource_auto_resolve" type="checkbox" ${rule.auto_resolve !== false ? "checked" : ""}> Nach Erholung erledigen</label>
        <label class="checkbox"><input name="resource_presence_required" type="checkbox" ${rule.presence_required ? "checked" : ""}> Anwesenheit berücksichtigen</label>
        <div class="full resource-test-line"><button type="button" data-test-resource>Aktuellen Wert prüfen</button><output class="preview-result" aria-live="polite"></output></div>
      </div>
    </article>`).join("");
  }

  _localize(root = this.shadowRoot) {
    localizeHouseholdTasksTree(root, this._hass);
  }

  _fieldHelp(formId, name) {
    const common = {
      id: "Technische, dauerhaft stabile ID. Erlaubt sind Kleinbuchstaben, Zahlen und Unterstriche.",
      name: "Der verständliche Name, der im Panel und in Benachrichtigungen angezeigt wird.",
      enabled: "Legt fest, ob diese Funktion aktiv ist.",
      description: "Zusätzlicher Kontext, der zusammen mit der Aufgabe angezeigt wird.",
      assignee: "Die Person, die die Aufgabe erhält und benachrichtigt wird.",
      time: "Lokale Uhrzeit, zu der die Aufgabe fällig wird oder die Aktion ausgeführt wird.",
    };
    const forms = {
      "person-form": {
        notify: "Home-Assistant-Aktion, über die diese Person Push-Mitteilungen erhält.",
        presence: "Statusquelle für die Entscheidung, ob die Person zuhause ist.",
        user_id: "Verknüpft die Person mit ihrem Home-Assistant-Konto.",
        nfc_device_id: "Optionales Gerät zur Erkennung der scannenden Person bei NFC-Ereignissen.",
      },
      "task-form": {
        assignment_type: "Bestimmt, ob die Zuständigkeit fest, rotierend, fair verteilt oder offen ist.",
        assignment_person: "Diese Personen dürfen bei Rotation, fairer Verteilung oder offener Zuweisung berücksichtigt werden.",
        presence_required: "Berücksichtigt bei automatischer Zuweisung nur aktuell anwesende Personen.",
        follow_up_task_id: "Vorlage, die nach Abschluss dieser Aufgabe automatisch erzeugt wird.",
        follow_up_delay: "Zeitspanne zwischen Abschluss und Erzeugung der Folgeaufgabe.",
        nfc_tag_id: "Optionaler Home-Assistant-Tag, mit dem die Aufgabe ausgelöst oder erledigt wird.",
        new_tag_name: "Verständlicher Name, unter dem der NFC-Tag in Home Assistant gespeichert wird.",
        nfc_action: "Aktion, die beim Scannen des zugeordneten NFC-Tags ausgeführt wird.",
        type: "Regel, nach der neue Vorkommen dieser Aufgabe erzeugt werden.",
        weekday: "Wochentage, an denen die Aufgabe fällig wird.",
        day: "Kalendertag des Monats; „last“ steht für den letzten Tag.",
        month: "Kalendermonat von 1 bis 12.",
        months: "Anzahl der Monate zwischen zwei Fälligkeiten.",
        start: "Zeitpunkt, ab dem die Wiederholung berechnet wird.",
        entity_id: "Home-Assistant-Entität, deren Kalender oder Zustand ausgewertet wird.",
        match: "Nur Kalendertermine, deren Titel dieses Suchmuster enthält, werden berücksichtigt.",
        offset: "Verschiebt die Fälligkeit relativ zum Kalendertermin; negative Werte liegen davor.",
        interval: "Zeitspanne vom tatsächlichen Abschluss bis zur nächsten Fälligkeit.",
        trigger_entity_id: "Home-Assistant-Entität, deren Zustandswechsel beobachtet wird.",
        trigger_from: "Optionaler Ausgangszustand; leer bedeutet, dass jeder Ausgangszustand erlaubt ist.",
        trigger_to: "Zielzustand, der den Auslöser aktiviert.",
        trigger_for: "Optionale Dauer, die der Zielzustand ununterbrochen bestehen muss.",
        due_after: "Zeitspanne vom Auslöser bis zur Fälligkeit der erzeugten Aufgabe.",
        cooldown: "Mindestabstand zwischen zwei Erzeugungen durch denselben Auslöser.",
        skip_if_open: "Verhindert ein weiteres Vorkommen, solange bereits eines offen ist.",
        custom_escalation: "Überschreibt für diese Vorlage die globalen Erinnerungsregeln.",
        escalation_after: "Zeitspanne ab dem gewählten Bezugspunkt bis zu dieser Eskalationsstufe.",
        escalation_relative_to: "Startpunkt der Wartezeit: Fälligkeit oder tatsächlich gesendete erste Nachricht.",
        escalation_recipients: "Empfänger dieser Stufe.",
        escalation_action: "Benachrichtigt, delegiert oder öffnet die Aufgabe zur freien Übernahme.",
        escalation_presence: "Führt die Stufe nur aus, wenn die zuständige Person zuhause ist.",
        inline_person_id: "Technische ID für die neue Person.",
        inline_person_name: "Anzeigename der neuen Person.",
        inline_person_presence: "Optionale Statusquelle für die Anwesenheit der neuen Person.",
        inline_follow_up_id: "Technische ID der neuen Folgevorlage.",
        inline_follow_up_name: "Anzeigename der neuen Folgevorlage.",
        inline_follow_up_assignee: "Standardzuständigkeit der neuen Folgevorlage.",
        esc_first: "Stunden nach Fälligkeit bis zur ersten Erinnerung.",
        esc_presence: "Sendet die erste Erinnerung nur, wenn die zuständige Person zuhause ist.",
        esc_second: "Stunden bis zur zweiten Eskalationsstufe.",
        esc_second_action: "Aktion, die in der zweiten Eskalationsstufe ausgeführt wird.",
        esc_final: "Stunden bis zur Benachrichtigung aller Haushaltsmitglieder.",
      },
      "quick-task-form": {
        due: "Datum und Uhrzeit, zu denen die einmalige Aufgabe fällig wird.",
        reminder_mode: "Legt fest, ob globale, eigene oder keine Erinnerungsregeln gelten.",
        esc_first: "Stunden nach Fälligkeit bis zur ersten Erinnerung.",
        esc_presence: "Sendet die erste Erinnerung nur, wenn die Person zuhause ist.",
        esc_second: "Stunden bis zur zweiten Erinnerung.",
        esc_final: "Stunden bis zur Benachrichtigung aller Haushaltsmitglieder.",
      },
      "handover-form": {
        to_person: "Person, die während der Übergabe bestehende und neue Aufgaben übernimmt.",
        until: "Optionales Ende der Übergabe; ohne Datum bleibt sie aktiv, bis sie manuell beendet wird.",
        reason: "Optionaler Hinweis, warum die Übergabe aktiv ist.",
      },
      "defaults-form": {
        escalation_after: "Zeitspanne ab dem gewählten Bezugspunkt, nach der diese Stufe ausgeführt wird.",
        escalation_relative_to: "Startpunkt der Wartezeit: ursprüngliche Fälligkeit oder tatsächlich gesendete erste Nachricht.",
        escalation_recipients: "Legt fest, ob die zuständige Person oder der gesamte Haushalt informiert wird.",
        escalation_action: "Benachrichtigt, delegiert an eine andere Person oder öffnet die Aufgabe zur Übernahme.",
        escalation_presence: "Wartet mit dieser Stufe, solange die betroffene Person nicht zuhause ist.",
      },
      "resources-form": {
        resource_id: "Dauerhaft stabile technische ID dieser Ressourcenregel.",
        resource_enabled: "Schaltet nur diese Ressourcenregel ein oder aus.",
        resource_entity_id: "Sensor oder Helfer, dessen aktueller Zustand mit dem Grenzwert verglichen wird.",
        resource_condition: "Vergleich, der die automatische Aufgabe auslöst.",
        resource_threshold: "Numerischer Wert oder Text, mit dem der Sensorzustand verglichen wird.",
        resource_task_name: "Name der automatisch erzeugten Aufgabe; {state} und {unit} werden ersetzt.",
        resource_description: "Zusätzliche Details für die automatisch erzeugte Aufgabe.",
        resource_assignee: "Person, die die automatisch erzeugte Aufgabe erhält.",
        resource_due_after: "Zeitspanne zwischen erkannter Bedingung und Fälligkeit.",
        resource_cooldown: "Mindestabstand, bevor dieselbe Regel erneut eine Aufgabe erzeugen darf.",
        resource_auto_resolve: "Erledigt die offene Aufgabe automatisch, sobald die Bedingung nicht mehr zutrifft.",
        resource_presence_required: "Bevorzugt eine anwesende Person aus den konfigurierten Ausweichpersonen.",
      },
      "weekly-summary-form": {
        weekday: "Wochentag, an dem der Rückblick gesendet wird.",
      },
      "nfc-feedback-form": {
        mode: "Bestimmt, ob nach NFC-Scans immer, nur bei Problemen oder nie bestätigt wird.",
        recipients: "Personen, die das NFC-Scanergebnis als Benachrichtigung erhalten.",
      },
      "printer-form": {
        enabled: "Erzeugt bei erkannten Druckerproblemen automatisch eine Aufgabe.",
      },
    };
    return forms[formId]?.[name] || common[name] || "Konfiguriert diesen Wert für die ausgewählte Funktion.";
  }

  _enhanceAccessibility(root = this.shadowRoot) {
    root.querySelectorAll("form").forEach((form) => {
      if (!form.hasAttribute("aria-label")) {
        const heading = form.closest(".modal-card")?.querySelector("h2")
          || form.closest(".settings-card")?.querySelector("h3");
        if (heading?.textContent) form.setAttribute("aria-label", heading.textContent.trim());
      }
      const controls = [...form.querySelectorAll("input,select,textarea,button")];
      controls.forEach((control, index) => {
        const fieldName = control.getAttribute("name");
        if (!fieldName || control.type === "hidden" || control.type === "submit" || control.type === "button") return;
        const label = control.closest("label");
        if (!label) return;
        const formId = form.getAttribute("id") || "form";
        if (!control.id) control.id = `${formId}-${fieldName}-${index}`;
        const relatedControls = controls.filter((item) => item.getAttribute("name") === fieldName);
        const group = relatedControls.length > 1 ? control.closest(".weekdays,.candidate-grid") : null;
        let helper = group?.parentElement?.querySelector(`.group-help[data-help-for="${fieldName}"]`)
          || label.querySelector(".hint");
        if (!helper) {
          helper = document.createElement("span");
          helper.className = `hint field-help${group ? " group-help" : ""}`;
          if (group) helper.dataset.helpFor = fieldName;
          helper.textContent = this._t(this._fieldHelp(formId, fieldName));
          if (group) group.insertAdjacentElement("afterend", helper);
          else label.append(helper);
        }
        if (!helper.id) helper.id = `${control.id}-help`;
        const describedBy = new Set((control.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean));
        describedBy.add(helper.id);
        control.setAttribute("aria-describedby", [...describedBy].join(" "));
        if (control.required) control.setAttribute("aria-required", "true");
        if (!control.dataset.accessibilityBound) {
          control.dataset.accessibilityBound = "true";
          control.addEventListener("invalid", () => {
            control.setAttribute("aria-invalid", "true");
            let error = label.querySelector(".field-error");
            if (!error) {
              error = document.createElement("span");
              error.className = "field-error";
              error.id = `${control.id}-error`;
              label.append(error);
            }
            error.textContent = control.validationMessage;
            const ids = new Set((control.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean));
            ids.add(error.id);
            control.setAttribute("aria-describedby", [...ids].join(" "));
          });
          control.addEventListener("input", () => {
            if (!control.validity.valid) return;
            control.removeAttribute("aria-invalid");
            const error = label.querySelector(".field-error");
            if (!error) return;
            const ids = (control.getAttribute("aria-describedby") || "").split(/\s+/).filter((id) => id && id !== error.id);
            control.setAttribute("aria-describedby", ids.join(" "));
            error.remove();
          });
        }
      });
    });
    root.querySelectorAll(".modal-card").forEach((dialog, index) => {
      dialog.setAttribute("role", "dialog");
      dialog.setAttribute("aria-modal", "true");
      const heading = dialog.querySelector("h2");
      if (heading) {
        if (!heading.id) heading.id = `dialog-title-${index}`;
        dialog.setAttribute("aria-labelledby", heading.id);
      }
    });
    root.querySelectorAll(".close").forEach((button) => button.setAttribute("aria-label", this._t("Dialog schließen")));
    root.querySelector(".refresh")?.setAttribute("aria-label", this._t("Aktualisieren"));
  }

  _activateDialog(modal, close) {
    this._enhanceAccessibility(modal);
    const dialog = modal.querySelector(".modal-card");
    dialog?.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        close();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = [...dialog.querySelectorAll(
        'button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled])'
      )].filter((item) => item.offsetParent !== null);
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable.at(-1);
      if (event.shiftKey && this.shadowRoot.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && this.shadowRoot.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    });
    requestAnimationFrame(() => dialog?.querySelector("[autofocus],input:not([readonly]),select,button")?.focus());
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
    this._enhanceAccessibility();
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
      ${this._data.is_admin ? `<div class="toolbar-actions"><button id="add-calendar-task">+ Kalenderregel</button><button class="primary" id="add-task">+ Aufgabe</button></div>` : ""}</div>
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
    return `<div class="toolbar"><div><h2>Einstellungen</h2><p>Globale Regeln und Datenquelle</p></div></div>
      <article class="settings-card">
        <h3>Standard-Eskalation</h3>
        <p>Diese Regeln gelten für alle Aufgaben ohne eigene Eskalation.</p>
        ${this._data.is_admin ? `<form id="defaults-form" class="form-grid">
          <div class="full repeatable-editor escalation-editor">
            <div class="repeatable-list">${this._escalationRows(stages)}</div>
            <button type="button" class="add-row" data-add-escalation>+ Eskalationsstufe</button>
            <p class="hint">Stufen werden in dieser Reihenfolge ausgeführt. Der Bezug „Erste Benachrichtigung“ wartet, bis eine vorherige Nachricht tatsächlich versendet wurde.</p>
          </div>
          <div class="full"><button class="primary" type="submit">Regeln speichern</button></div>
        </form>` : this._escalationSummary(stages)}
      </article>
      <article class="settings-card">
        <h3>Ressourcen und Verbrauch</h3>
        <p>Sensorwerte können Aufgaben erzeugen und nach Erholung automatisch abschließen – etwa für Vorräte, Füllstände, Batterien oder Filter.</p>
        ${this._data.is_admin ? `<form id="resources-form">
          <div class="resource-list">${this._resourceRows(resources)}</div>
          <button type="button" class="add-row" data-add-resource>+ Ressourcenregel</button>
          <p class="hint">Name und Beschreibung dürfen {state} und {unit} enthalten. „Aktuellen Wert prüfen“ verändert keine Aufgaben.</p>
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
    this.shadowRoot.querySelector("#add-calendar-task")?.addEventListener("click", () => this._showTaskEditor(null, { scheduleType: "calendar" }));
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
    this._bindEscalationEditor(this.shadowRoot);
    this._bindResourceEditor();
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
    const defaults = { ...this._data.defaults, escalation: this._readEscalation(event.target) };
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

  _bindResourceEditor() {
    const form = this.shadowRoot.querySelector("#resources-form");
    if (!form) return;
    const list = form.querySelector(".resource-list");
    const currentRules = () => {
      const result = {};
      list.querySelectorAll(".resource-row").forEach((row, index) => {
        const id = row.querySelector("[name=resource_id]").value || `neue_regel_${index + 1}`;
        result[id] = {
          enabled: row.querySelector("[name=resource_enabled]").checked,
          entity_id: row.querySelector("[name=resource_entity_id]").value,
          condition: row.querySelector("[name=resource_condition]").value,
          threshold: row.querySelector("[name=resource_threshold]").value,
          task_name: row.querySelector("[name=resource_task_name]").value,
          description: row.querySelector("[name=resource_description]").value,
          assignee: row.querySelector("[name=resource_assignee]").value,
          due_after: row.querySelector("[name=resource_due_after]").value,
          cooldown: row.querySelector("[name=resource_cooldown]").value,
          auto_resolve: row.querySelector("[name=resource_auto_resolve]").checked,
          presence_required: row.querySelector("[name=resource_presence_required]").checked,
        };
      });
      return result;
    };
    const bindRows = () => {
      list.querySelectorAll("[data-remove-resource]").forEach((button) => {
        button.onclick = () => {
          button.closest(".resource-row").remove();
          if (!list.querySelector(".resource-row")) list.innerHTML = this._resourceRows({});
        };
      });
      list.querySelectorAll("[data-test-resource]").forEach((button) => {
        button.onclick = () => {
          const row = button.closest(".resource-row");
          const entityId = row.querySelector("[name=resource_entity_id]").value.trim();
          const state = this._hass.states[entityId];
          const output = row.querySelector(".preview-result");
          if (!state) {
            output.textContent = "Entität nicht gefunden.";
            output.className = "preview-result error";
            return;
          }
          const condition = row.querySelector("[name=resource_condition]").value;
          const threshold = row.querySelector("[name=resource_threshold]").value;
          const numeric = ["below", "at_most", "above", "at_least"].includes(condition);
          const left = numeric ? Number(state.state) : String(state.state).toLocaleLowerCase();
          const right = numeric ? Number(threshold) : String(threshold).toLocaleLowerCase();
          const matches = {
            below: left < right, at_most: left <= right, above: left > right,
            at_least: left >= right, equals: left === right, not_equals: left !== right,
          }[condition] === true;
          const unit = state.attributes?.unit_of_measurement || "";
          output.textContent = `${state.state}${unit ? ` ${unit}` : ""} – Regel ${matches ? "trifft zu; Aufgabe würde erzeugt" : "trifft nicht zu"}.`;
          output.className = `preview-result ${matches ? "match" : ""}`;
        };
      });
    };
    form.querySelector("[data-add-resource]").onclick = () => {
      const rules = currentRules();
      let suffix = Object.keys(rules).length + 1;
      while (rules[`neue_regel_${suffix}`]) suffix += 1;
      rules[`neue_regel_${suffix}`] = {
        enabled: true, entity_id: "", condition: "below", threshold: "",
        task_name: "", assignee: Object.keys(this._data.people)[0] || "",
        due_after: "00:00:00", cooldown: "24:00:00", auto_resolve: true,
      };
      list.innerHTML = this._resourceRows(rules);
      this._localize(list);
      this._enhanceAccessibility(form);
      bindRows();
      list.querySelector(".resource-row:last-of-type [name=resource_id]")?.focus();
    };
    bindRows();
  }

  async _saveResources(event) {
    event.preventDefault();
    try {
      const resources = {};
      for (const row of event.target.querySelectorAll(".resource-row")) {
        const get = (name) => row.querySelector(`[name=${name}]`);
        const resourceId = get("resource_id").value.trim();
        if (resources[resourceId]) throw new Error(`Die Regel-ID „${resourceId}“ wird mehrfach verwendet.`);
        const originalId = row.dataset.originalId;
        const resource = structuredClone(this._data.monitors?.resources?.[originalId] || {});
        Object.assign(resource, {
          enabled: get("resource_enabled").checked,
          entity_id: get("resource_entity_id").value.trim(),
          condition: get("resource_condition").value,
          threshold: get("resource_threshold").value.trim(),
          task_name: get("resource_task_name").value.trim(),
          assignee: get("resource_assignee").value,
          due_after: get("resource_due_after").value || "00:00:00",
          cooldown: get("resource_cooldown").value || "24:00:00",
          auto_resolve: get("resource_auto_resolve").checked,
          presence_required: get("resource_presence_required").checked,
        });
        if (get("resource_description").value.trim()) resource.description = get("resource_description").value.trim();
        else delete resource.description;
        if (!this._hass.states[resource.entity_id]) {
          throw new Error(`Ressourcenregel „${resourceId}“: Die Entität „${resource.entity_id || ""}“ existiert nicht.`);
        }
        if (!this._data.people[resource.assignee]) {
          throw new Error(`Ressourcenregel „${resourceId}“: Die Person „${resource.assignee || ""}“ existiert nicht.`);
        }
        resources[resourceId] = resource;
      }
      await this._call("save_monitors", {
        monitors: { ...this._data.monitors, resources },
      });
      this._toast("Ressourcenregeln gespeichert");
    } catch (error) {
      this._toast(error?.message || "Die Ressourcenregeln konnten nicht gespeichert werden.", true);
    }
  }

  _showTaskEditor(id = null, options = {}) {
    const task = id ? structuredClone(this._data.tasks[id]) : {
      enabled: true, name: "", assignee: Object.keys(this._data.people)[0] || "",
      assignment: { type: "fixed", people: [] },
      description: "", schedule: options.scheduleType === "calendar"
        ? { type: "calendar", entity_id: "", match: "", offset: "-12:00:00" }
        : { type: "weekly", weekdays: ["mon"], time: "18:00:00" },
    };
    const s = task.schedule || { type: "manual" };
    const esc = task.escalation;
    const nfc = task.nfc || {};
    const assignmentType = task.assignment?.type || "fixed";
    const assignmentPeople = task.assignment?.people || Object.keys(this._data.people);
    const presenceRequired = task.assignment?.presence_required === true;
    const modal = this.shadowRoot.querySelector("#modal");
    const returnFocus = this.shadowRoot.activeElement;
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
        <div class="full inline-create">
          <button type="button" data-toggle-inline-person>+ Person direkt anlegen</button>
          <div class="inline-person-form form-grid hidden">
            <label>ID<input name="inline_person_id" pattern="[a-z0-9_]+" placeholder="vorname"></label>
            <label>Name<input name="inline_person_name" placeholder="Vorname"></label>
            <label class="full">Push-Aktion${this._notifyInput("")}</label>
            <label class="full">Anwesenheit${this._entityInput("inline_person_presence", "", ["person", "device_tracker", "binary_sensor"], { placeholder: "Optional" })}</label>
            <div class="full"><button type="button" class="primary" data-save-inline-person>Person übernehmen</button></div>
          </div>
        </div>
        <label class="full">Beschreibung<textarea name="description" rows="2">${this._e(task.description || "")}</textarea></label>
        <div class="full repeatable-editor follow-up-editor">
          <span class="field-label">Folgeaufgaben nach Erledigung</span>
          <div class="repeatable-list">${this._followUpRows(task.follow_ups || [], id)}</div>
          <button type="button" class="add-row" data-add-follow-up>+ Folgeaufgabe</button>
          <button type="button" class="add-row" data-toggle-inline-follow-up>+ Neue Vorlage direkt anlegen</button>
          <div class="inline-follow-up-form form-grid hidden">
            <label>ID<input name="inline_follow_up_id" pattern="[a-z0-9_]+" placeholder="filter_wechseln"></label>
            <label>Name<input name="inline_follow_up_name" placeholder="Filter wechseln"></label>
            <label class="full">Zuständig<select name="inline_follow_up_assignee">${Object.entries(this._data.people).map(([pid,p]) => `<option value="${this._e(pid)}">${this._e(p.name)}</option>`).join("")}</select></label>
            <div class="full"><button type="button" class="primary" data-save-inline-follow-up>Vorlage übernehmen</button></div>
          </div>
          <p class="hint">Wähle eine vorhandene Vorlage und den zeitlichen Abstand. Dadurch sind keine internen Aufgaben-IDs mehr nötig.</p>
        </div>
        <div class="full">${this._tagInput(nfc.tag_id || "")}</div>
        <label class="full">Beim Scannen<select name="nfc_action">
          <option value="create_or_complete" ${nfc.action === "create_or_complete" || !nfc.action ? "selected" : ""}>Erzeugen oder erledigen</option>
          <option value="create" ${nfc.action === "create" ? "selected" : ""}>Nur erzeugen</option>
          <option value="complete" ${nfc.action === "complete" ? "selected" : ""}>Nur erledigen</option>
        </select><span class="hint">Die Tag-ID findest du nach einem Scan unter Einstellungen → Tags in Home Assistant.</span></label>
        <label>Zeitplan<select name="type">
          ${[["manual","Manuell"],["weekly","Wöchentlich"],["monthly","Monatlich"],["yearly","Jährlich"],["interval_months","Alle N Monate"],["after_completion","Nach letzter Erledigung"],["calendar","Kalender / ICS"],["state_trigger","Bei Zustandswechsel"],["daily_after_state","Einmal täglich nach Gerätestatus"]].map(([v,l]) => `<option value="${v}" ${v === s.type ? "selected" : ""}>${l}</option>`).join("")}
        </select></label>
        <div class="full schedule-fields">${this._scheduleFields(s)}</div>
        <div class="full task-preview"><button type="button" data-preview-task>Regel testen / nächste Fälligkeit</button><output class="preview-result" aria-live="polite"></output></div>
        <label class="full checkbox"><input name="custom_escalation" type="checkbox" ${esc ? "checked" : ""}> Eigene Eskalationsregeln verwenden</label>
        <div class="full escalation-fields repeatable-editor escalation-editor ${esc ? "" : "hidden"}">
          <div class="repeatable-list">${this._escalationRows(esc || [])}</div>
          <button type="button" class="add-row" data-add-escalation>+ Eskalationsstufe</button>
        </div>
        <div class="full modal-actions"><button type="button" class="cancel">Abbrechen</button><button class="primary" type="submit">Speichern</button></div>
      </form></div></div>`;
    this._localize(modal);
    const close = () => { modal.innerHTML = ""; returnFocus?.focus(); };
    modal.querySelector(".close").onclick = close;
    modal.querySelector(".cancel").onclick = close;
    this._activateDialog(modal, close);
    modal.querySelector("[name=type]").onchange = (event) => {
      const fields = modal.querySelector(".schedule-fields");
      fields.innerHTML = this._scheduleFields({ type: event.target.value, time: "18:00:00" });
      this._localize(fields);
      this._bindTriggerEditor(modal);
      this._enhanceAccessibility(fields);
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
    this._bindRepeatableEditors(modal, id);
    this._bindEscalationEditor(modal);
    this._bindTagCreator(modal);
    this._bindInlineTaskCreates(modal, id);
    modal.querySelector("[data-preview-task]").onclick = async () => {
      const output = modal.querySelector(".task-preview .preview-result");
      try {
        const { value } = this._readTaskForm(modal.querySelector("#task-form"));
        const preview = await this._hass.callWS({ type: "household_tasks/preview_task", task: value });
        const parts = [];
        if (preview.next_due) parts.push(`Nächste Fälligkeit: ${new Date(preview.next_due).toLocaleString(this._locale())}`);
        if (preview.calendar_events?.length) parts.push(`${preview.calendar_events.length} passende Kalendertermine in den nächsten 90 Tagen`);
        if (preview.state_triggers?.length) parts.push(preview.state_triggers.map((item) =>
          `${item.entity_id}: aktuell „${item.current ?? "nicht verfügbar"}“, erwartet „${item.wanted}“${item.matches ? " ✓" : ""}`
        ).join(" · "));
        if (!parts.length) parts.push(preview.schedule_type === "manual"
          ? "Manuelle Regeln haben keine automatisch berechnete Fälligkeit."
          : "Im Vorschauzeitraum wurde keine Fälligkeit gefunden.");
        output.textContent = parts.join(" — ");
      } catch (error) {
        output.textContent = this._errorText(error);
        output.className = "preview-result error";
      }
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
    const returnFocus = this.shadowRoot.activeElement;
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
        <div class="full quick-escalation repeatable-editor escalation-editor hidden">
          <div class="repeatable-list">${this._escalationRows([
            { after: "00:00:00", recipients: "assignee", presence_required: true },
            { after: "02:00:00", recipients: "assignee", relative_to: "first_notification" },
            { after: "24:00:00", recipients: "all" },
          ])}</div>
          <button type="button" class="add-row" data-add-escalation>+ Eskalationsstufe</button>
        </div>
        <div class="full modal-actions"><button type="button" class="cancel">Abbrechen</button><button class="primary" type="submit">Aufgabe hinzufügen</button></div>
      </form></div></div>`;
    this._localize(modal);
    const close = () => { modal.innerHTML = ""; returnFocus?.focus(); };
    modal.querySelector(".close").onclick = close;
    modal.querySelector(".cancel").onclick = close;
    this._activateDialog(modal, close);
    modal.querySelector("[name=reminder_mode]").onchange = (event) => {
      modal.querySelector(".quick-escalation").classList.toggle("hidden", event.target.value !== "custom");
    };
    this._bindEscalationEditor(modal);
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
      if (f.get("reminder_mode") === "custom") payload.escalation = this._readEscalation(event.target.querySelector(".escalation-editor"));
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

  _followUpRows(followUps = [], currentTaskId = null) {
    const tasks = Object.entries(this._data?.tasks || {}).filter(([taskId]) => taskId !== currentTaskId);
    if (!followUps.length) return `<p class="empty-row">Noch keine Folgeaufgabe ausgewählt.</p>`;
    return followUps.map((followUp) => `<div class="repeatable-row follow-up-row">
      <label>Vorlage<select name="follow_up_task_id" required>
        <option value="">Bitte auswählen</option>
        ${tasks.map(([taskId, task]) => `<option value="${this._e(taskId)}" ${taskId === followUp.task_id ? "selected" : ""}>${this._e(task.name)} · ${this._e(taskId)}</option>`).join("")}
      </select></label>
      <label>Verzögerung (HH:MM:SS)<input name="follow_up_delay" required value="${this._e(followUp.delay || "00:00:00")}" pattern="-?[0-9]+:[0-5][0-9]:[0-5][0-9]"></label>
      <button type="button" class="remove-row" title="Folgeaufgabe entfernen" aria-label="Folgeaufgabe entfernen">×</button>
    </div>`).join("");
  }

  _triggerRows(triggers = []) {
    if (!triggers.length) return `<p class="empty-row">Noch kein Auslöser ausgewählt.</p>`;
    const entities = this._entitySuggestions();
    const options = entities.map((item) =>
      `<option value="${this._e(item.value)}">${this._e(item.label)} · ${this._e(item.detail)}</option>`
    ).join("");
    return `${triggers.map((trigger, index) => `<div class="repeatable-row trigger-row">
      <label>Entität<input name="trigger_entity_id" list="ht-trigger-entities" required value="${this._e(trigger.entity_id || "")}" placeholder="Entität suchen"></label>
      <label>Von<input name="trigger_from" value="${this._e(trigger.from || "")}" placeholder="optional"></label>
      <label>Nach<input name="trigger_to" required value="${this._e(trigger.to || "")}" placeholder="z. B. on"></label>
      <label>Für (HH:MM:SS)<input name="trigger_for" value="${this._e(trigger.for || "")}" pattern="[0-9]+:[0-5][0-9]:[0-5][0-9]" placeholder="optional"></label>
      <button type="button" class="remove-row" title="Auslöser entfernen" aria-label="Auslöser entfernen">×</button>
      ${index === 0 ? `<datalist id="ht-trigger-entities">${options}</datalist>` : ""}
    </div>`).join("")}`;
  }

  _bindTriggerEditor(modal) {
    const editor = modal.querySelector(".trigger-editor");
    if (!editor) return;
    const bindRemovers = () => {
      editor.querySelectorAll(".remove-row").forEach((button) => {
        button.onclick = () => {
          button.closest(".trigger-row").remove();
          if (!editor.querySelector(".trigger-row")) {
            editor.querySelector(".repeatable-list").innerHTML = `<p class="empty-row">${this._t("Noch kein Auslöser ausgewählt.")}</p>`;
          }
        };
      });
    };
    editor.querySelector("[data-add-trigger]").onclick = () => {
      const list = editor.querySelector(".repeatable-list");
      const existing = [...list.querySelectorAll(".trigger-row")].map((row) => ({
        entity_id: row.querySelector("[name=trigger_entity_id]").value,
        from: row.querySelector("[name=trigger_from]").value,
        to: row.querySelector("[name=trigger_to]").value,
        for: row.querySelector("[name=trigger_for]").value,
      }));
      list.innerHTML = this._triggerRows([...existing, { entity_id: "", from: "", to: "", for: "" }]);
      this._localize(list);
      this._enhanceAccessibility(modal);
      bindRemovers();
      list.querySelector(".trigger-row:last-of-type [name=trigger_entity_id]")?.focus();
    };
    bindRemovers();
  }

  _bindRepeatableEditors(modal, currentTaskId) {
    const editor = modal.querySelector(".follow-up-editor");
    const bindRemovers = () => {
      editor.querySelectorAll(".remove-row").forEach((button) => {
        button.onclick = () => {
          button.closest(".follow-up-row").remove();
          if (!editor.querySelector(".follow-up-row")) {
            editor.querySelector(".repeatable-list").innerHTML = `<p class="empty-row">${this._t("Noch keine Folgeaufgabe ausgewählt.")}</p>`;
          }
        };
      });
    };
    editor.querySelector("[data-add-follow-up]").onclick = () => {
      const list = editor.querySelector(".repeatable-list");
      const existing = [...list.querySelectorAll(".follow-up-row")].map((row) => ({
        task_id: row.querySelector("[name=follow_up_task_id]").value,
        delay: row.querySelector("[name=follow_up_delay]").value,
      }));
      list.innerHTML = this._followUpRows([...existing, { task_id: "", delay: "00:00:00" }], currentTaskId);
      this._localize(list);
      this._enhanceAccessibility(modal);
      bindRemovers();
      list.querySelector(".follow-up-row:last-of-type select")?.focus();
    };
    bindRemovers();
    this._bindTriggerEditor(modal);
  }

  _bindInlineTaskCreates(modal, currentTaskId) {
    const personPanel = modal.querySelector(".inline-person-form");
    modal.querySelector("[data-toggle-inline-person]").onclick = () => {
      personPanel.classList.toggle("hidden");
      if (!personPanel.classList.contains("hidden")) personPanel.querySelector("[name=inline_person_id]")?.focus();
    };
    modal.querySelector("[data-save-inline-person]").onclick = async () => {
      const personId = personPanel.querySelector("[name=inline_person_id]").value.trim();
      const name = personPanel.querySelector("[name=inline_person_name]").value.trim();
      const notify = personPanel.querySelector("[name=notify]").value.trim();
      const presence = personPanel.querySelector("[name=inline_person_presence]").value.trim();
      if (!personId || !name || !/^notify\..+/.test(notify)) {
        this._toast("Bitte ID, Name und eine vorhandene Push-Aktion angeben.", true);
        return;
      }
      if (this._data.people[personId]) {
        this._toast(`Die Person „${personId}“ existiert bereits.`, true);
        return;
      }
      const service = notify.slice("notify.".length);
      if (!this._hass.services.notify?.[service]) {
        this._toast("Bitte wähle eine vorhandene Push-Aktion aus.", true);
        return;
      }
      if (presence && !this._hass.states[presence]) {
        this._toast("Die Anwesenheits-Entität existiert nicht.", true);
        return;
      }
      const person = { name, notify };
      if (presence) person.presence = presence;
      try {
        const result = await this._hass.callWS({ type: "household_tasks/save_person", person_id: personId, person });
        this._data = result;
        const fixed = modal.querySelector("[name=assignee]");
        fixed.insertAdjacentHTML("beforeend", `<option value="${this._e(personId)}" selected>${this._e(name)}</option>`);
        modal.querySelector(".candidate-grid").insertAdjacentHTML("beforeend",
          `<label class="checkbox"><input type="checkbox" name="assignment_person" value="${this._e(personId)}" checked> ${this._e(name)}</label>`);
        modal.querySelector("[name=inline_follow_up_assignee]")?.insertAdjacentHTML("beforeend",
          `<option value="${this._e(personId)}">${this._e(name)}</option>`);
        personPanel.classList.add("hidden");
        this._toast("Person angelegt und ausgewählt.");
      } catch (error) {
        this._toast(this._errorText(error), true);
      }
    };

    const followUpPanel = modal.querySelector(".inline-follow-up-form");
    modal.querySelector("[data-toggle-inline-follow-up]").onclick = () => {
      followUpPanel.classList.toggle("hidden");
      if (!followUpPanel.classList.contains("hidden")) followUpPanel.querySelector("[name=inline_follow_up_id]")?.focus();
    };
    modal.querySelector("[data-save-inline-follow-up]").onclick = async () => {
      const taskId = followUpPanel.querySelector("[name=inline_follow_up_id]").value.trim();
      const name = followUpPanel.querySelector("[name=inline_follow_up_name]").value.trim();
      const assignee = followUpPanel.querySelector("[name=inline_follow_up_assignee]").value;
      if (!taskId || !name || !assignee) {
        this._toast("Bitte ID, Name und Zuständigkeit angeben.", true);
        return;
      }
      if (taskId === currentTaskId || this._data.tasks[taskId]) {
        this._toast(`Die Vorlagen-ID „${taskId}“ ist bereits vergeben.`, true);
        return;
      }
      try {
        const result = await this._hass.callWS({
          type: "household_tasks/save_task",
          task_id: taskId,
          task: { enabled: true, name, assignee, assignment: { type: "fixed" }, schedule: { type: "manual" } },
        });
        this._data = result;
        const editor = modal.querySelector(".follow-up-editor");
        const existing = [...editor.querySelectorAll(".follow-up-row")].map((row) => ({
          task_id: row.querySelector("[name=follow_up_task_id]").value,
          delay: row.querySelector("[name=follow_up_delay]").value,
        }));
        editor.querySelector(".repeatable-list").innerHTML = this._followUpRows(
          [...existing, { task_id: taskId, delay: "00:00:00" }], currentTaskId
        );
        this._bindRepeatableEditors(modal, currentTaskId);
        followUpPanel.classList.add("hidden");
        this._toast("Vorlage angelegt und als Folgeaufgabe ausgewählt.");
      } catch (error) {
        this._toast(this._errorText(error), true);
      }
    };
  }

  _scheduleFields(s) {
    const time = this._e(s.time || "18:00:00");
    if (s.type === "manual") return `<p class="hint">Diese Vorlage wird nur über „Jetzt erzeugen“ oder eine Automation angelegt.</p>`;
    if (s.type === "weekly") return `<div class="weekdays">${this._weekdays().map(([v,l]) => `<label><input type="checkbox" name="weekday" value="${v}" ${(s.weekdays || []).includes(v) ? "checked" : ""}><span>${l}</span></label>`).join("")}</div><label>Uhrzeit<input name="time" type="time" step="1" value="${time}"></label>`;
    if (s.type === "monthly") return `<div class="form-grid"><label>Tag (1–31 oder last)<input name="day" value="${this._e(s.day || 1)}"></label><label>Uhrzeit<input name="time" type="time" step="1" value="${time}"></label></div>`;
    if (s.type === "yearly") return `<div class="form-grid"><label>Monat<input name="month" type="number" min="1" max="12" value="${this._e(s.month || 1)}"></label><label>Tag<input name="day" value="${this._e(s.day || 1)}"></label><label>Uhrzeit<input name="time" type="time" step="1" value="${time}"></label></div>`;
    if (s.type === "interval_months") return `<div class="form-grid"><label>Intervall in Monaten<input name="months" type="number" min="1" value="${this._e(s.months || 6)}"></label><label>Startdatum<input name="start" type="date" value="${this._e(s.start || new Date().toISOString().slice(0,10))}"></label><label>Uhrzeit<input name="time" type="time" step="1" value="${time}"></label></div>`;
    if (s.type === "calendar") return `<div class="calendar-guide">
      <p class="hint"><strong>1. Kalender wählen</strong> · <strong>2. Termine filtern</strong> · <strong>3. Fälligkeit relativ zum Termin festlegen</strong> · anschließend unten die Regel testen.</p>
      <div class="form-grid"><label>1 · Kalender-Entität${this._entityInput("entity_id", s.entity_id || "", ["calendar"], { required: true, placeholder: "Kalender suchen", hint: "Wähle eine vorhandene Kalender-Entität aus Home Assistant." })}</label>
      <label>2 · Suchmuster<input name="match" value="${this._e(s.match || "")}" placeholder="Restmüll"><span class="hint">Leer berücksichtigt alle Termine. Groß-/Kleinschreibung spielt keine Rolle.</span></label>
      <label>3 · Versatz HH:MM:SS<input name="offset" value="${this._e(s.offset || "-12:00:00")}"><span class="hint">Negativ bedeutet vor dem Termin, z. B. −12 Stunden.</span></label></div></div>`;
    if (s.type === "after_completion") return `<div class="form-grid">
      <label>Erstmals fällig<input name="start" type="datetime-local" required value="${this._localDateTimeValue(s.start ? new Date(s.start) : new Date())}"></label>
      <label>Intervall nach Erledigung (HH:MM:SS)<input name="interval" required value="${this._e(s.interval || "168:00:00")}"></label>
      </div>`;
    if (s.type === "state_trigger") return `<div class="form-grid">
      <label>Fällig nach (HH:MM:SS)<input name="due_after" value="${this._e(s.due_after || "00:00:00")}"></label>
      <label>Cooldown (HH:MM:SS)<input name="cooldown" value="${this._e(s.cooldown || "00:00:00")}"></label>
      <label class="checkbox full"><input name="skip_if_open" type="checkbox" ${s.skip_if_open !== false ? "checked" : ""}> Nicht erneut erzeugen, solange die Aufgabe offen ist</label>
      <div class="full repeatable-editor trigger-editor"><span class="field-label">Auslöser</span>
        <div class="repeatable-list">${this._triggerRows(s.triggers || [{ entity_id: "", from: "off", to: "on" }])}</div>
        <button type="button" class="add-row" data-add-trigger>+ Auslöser</button>
        <p class="hint">Entität auswählen und Zielzustand angeben; Ausgangszustand und Dauer sind optional.</p>
      </div></div>`;
    return `<div class="form-grid"><label>Fälligkeit<input name="time" type="time" step="1" value="${time}"></label>
      <div class="full repeatable-editor trigger-editor"><span class="field-label">Auslöser</span>
        <div class="repeatable-list">${this._triggerRows(s.triggers || [{ entity_id: "", from: "on", to: "off" }])}</div>
        <button type="button" class="add-row" data-add-trigger>+ Auslöser</button>
        <p class="hint">Entität auswählen und Zielzustand angeben; Ausgangszustand und Dauer sind optional.</p>
      </div></div>`;
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
    if (type === "calendar") {
      schedule.entity_id = f.get("entity_id")?.trim();
      if (!this._hass.states[schedule.entity_id] || !schedule.entity_id.startsWith("calendar.")) {
        throw new Error("Bitte wähle eine vorhandene Kalender-Entität aus.");
      }
      schedule.match = f.get("match");
      schedule.offset = f.get("offset") || "00:00:00";
    }
    if (type === "after_completion") {
      schedule.start = new Date(f.get("start")).toISOString();
      schedule.interval = f.get("interval");
    }
    if (["daily_after_state", "state_trigger"].includes(type)) {
      const entityIds = f.getAll("trigger_entity_id");
      const fromStates = f.getAll("trigger_from");
      const toStates = f.getAll("trigger_to");
      const durations = f.getAll("trigger_for");
      if (!entityIds.length) throw new Error("Mindestens ein Auslöser ist erforderlich.");
      schedule.triggers = entityIds.map((entityId, index) => {
        const normalizedId = entityId.trim();
        if (!this._hass.states[normalizedId]) {
          throw new Error(`Die Entität „${normalizedId}“ existiert nicht in Home Assistant.`);
        }
        const trigger = { entity_id: normalizedId, to: toStates[index].trim() };
        if (fromStates[index]?.trim()) trigger.from = fromStates[index].trim();
        if (durations[index]?.trim()) trigger.for = durations[index].trim();
        return trigger;
      });
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
    const followUpIds = f.getAll("follow_up_task_id");
    const followUpDelays = f.getAll("follow_up_delay");
    if (followUpIds.length) {
      value.follow_ups = followUpIds.map((taskId, index) => {
        if (!this._data.tasks[taskId]) throw new Error(`Die Folgeaufgabe „${taskId}“ existiert nicht.`);
        return { task_id: taskId, delay: followUpDelays[index] || "00:00:00" };
      });
    }
    if (f.get("nfc_tag_id")?.trim()) value.nfc = {
      tag_id: f.get("nfc_tag_id").trim(),
      action: f.get("nfc_action") || "create_or_complete",
    };
    if (f.get("custom_escalation") === "on") value.escalation = this._readEscalation(form.querySelector(".escalation-editor"));
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
    const returnFocus = this.shadowRoot.activeElement;
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
    const close = () => { modal.innerHTML = ""; returnFocus?.focus(); };
    modal.querySelector(".close").onclick = close;
    modal.querySelector(".cancel").onclick = close;
    this._activateDialog(modal, close);
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
    const returnFocus = this.shadowRoot.activeElement;
    modal.innerHTML = `<div class="backdrop"><div class="modal-card small">
      <div class="modal-head"><div><div class="eyebrow">PERSON</div><h2>${id ? "Person bearbeiten" : "Neue Person"}</h2></div><button class="icon-button close">×</button></div>
      <form id="person-form" class="form-grid">
        <label>ID<input name="id" required pattern="[a-z0-9_]+" ${id ? "readonly" : ""} value="${this._e(id || "")}" placeholder="vorname"></label>
        <label>Name<input name="name" required value="${this._e(p.name)}"></label>
        <label class="full">Push-Aktion${this._notifyInput(p.notify)}</label>
        <label class="full">Anwesenheits-Entität${this._entityInput("presence", p.presence || "", ["person", "device_tracker", "binary_sensor"], {
          placeholder: "Person oder Tracker suchen",
          hint: "Vorgeschlagen werden Personen, Geräte-Tracker und Anwesenheitssensoren aus Home Assistant.",
        })}</label>
        <label class="full">Home-Assistant-Benutzer-ID${this._userInput(p.user_id || "")}</label>
        <label class="full">NFC-Geräte-ID (optional)${this._deviceInput(p.nfc_device_id || "")}</label>
        <div class="full test-actions">
          <button type="button" data-test-presence>Anwesenheit prüfen</button>
          <button type="button" data-test-notification>Testbenachrichtigung senden</button>
          <output class="preview-result" aria-live="polite"></output>
        </div>
        <div class="full modal-actions"><button type="button" class="cancel">Abbrechen</button><button class="primary" type="submit">Speichern</button></div>
      </form></div></div>`;
    this._localize(modal);
    const close = () => { modal.innerHTML = ""; returnFocus?.focus(); };
    modal.querySelector(".close").onclick = close; modal.querySelector(".cancel").onclick = close;
    this._activateDialog(modal, close);
    modal.querySelector("[data-test-presence]").onclick = () => {
      const entityId = modal.querySelector("[name=presence]").value.trim();
      const output = modal.querySelector(".test-actions output");
      const state = this._hass.states[entityId];
      output.textContent = !entityId ? "Keine Anwesenheits-Entität ausgewählt."
        : state ? `Aktueller Zustand: „${state.state}“${state.state === "home" ? " – anwesend" : ""}.`
        : "Entität wurde nicht gefunden.";
    };
    modal.querySelector("[data-test-notification]").onclick = async () => {
      const output = modal.querySelector(".test-actions output");
      try {
        if (id) {
          await this._hass.callWS({ type: "household_tasks/test_notification", person_id: id });
        } else {
          const notify = modal.querySelector("[name=notify]").value.trim();
          const service = notify.startsWith("notify.") ? notify.slice(7) : "";
          if (!service || !this._hass.services.notify?.[service]) throw new Error("Bitte zuerst eine vorhandene Push-Aktion auswählen.");
          await this._hass.callService("notify", service, {
            title: "Household Tasks",
            message: "Testbenachrichtigung erfolgreich zugestellt.",
            data: { tag: "household_tasks_test", url: "/haushaltsaufgaben" },
          });
        }
        output.textContent = "Testbenachrichtigung wurde gesendet.";
      } catch (error) {
        output.textContent = this._errorText(error);
        output.className = "preview-result error";
      }
    };
    modal.querySelector("#person-form").onsubmit = async (event) => {
      event.preventDefault();
      const f = new FormData(event.target);
      const notify = f.get("notify").trim();
      const notifyService = notify.startsWith("notify.") ? notify.slice("notify.".length) : "";
      if (!notifyService || !this._hass.services.notify?.[notifyService]) {
        this._toast("Bitte wähle eine vorhandene Push-Aktion aus.", true);
        return;
      }
      const person = { name: f.get("name").trim(), notify };
      if (f.get("presence")?.trim()) {
        const presence = f.get("presence").trim();
        if (!this._hass.states[presence]) {
          this._toast(`Die Anwesenheits-Entität „${presence}“ existiert nicht.`, true);
          return;
        }
        person.presence = presence;
      }
      if (f.get("user_id")?.trim()) person.user_id = f.get("user_id").trim();
      if (f.get("nfc_device_id")?.trim()) {
        const deviceId = f.get("nfc_device_id").trim();
        if (this._references.devices.length && !this._references.devices.some((device) => device.id === deviceId)) {
          this._toast("Bitte wähle ein vorhandenes Home-Assistant-Gerät aus.", true);
          return;
        }
        person.nfc_device_id = deviceId;
      }
      await this._call("save_person", { person_id: f.get("id").trim(), person });
      close(); this._toast("Person gespeichert");
    };
  }

  _styles() {
    return `
      :host{display:block;min-height:100%;background:var(--primary-background-color,#f5f6f8);color:var(--primary-text-color,#202124);font-family:var(--paper-font-body1_-_font-family,system-ui,sans-serif)}
      *{box-sizing:border-box}button,input,select,textarea{font:inherit;color:inherit}main{max-width:1180px;margin:auto;padding:24px}
      button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible,summary:focus-visible{outline:3px solid color-mix(in srgb,var(--primary-color,#03a9f4) 70%,white);outline-offset:2px}
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
      .toolbar{display:flex;align-items:end;justify-content:space-between;margin-bottom:18px}.toolbar p{margin:5px 0 0}.toolbar-actions{display:flex;gap:8px;flex-wrap:wrap}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:12px}.config-card,.settings-card,.card{background:var(--card-background-color,#fff);border:1px solid var(--divider-color,#ddd);border-radius:16px;padding:18px}.config-card.disabled{opacity:.65}.card-top{display:flex;align-items:center;gap:12px}.card-top>div:nth-child(2){flex:1}.card-top p{font-size:13px;margin:3px 0}.status{font-size:11px;font-weight:750;padding:4px 8px;border-radius:99px;background:var(--divider-color,#eee)}.status.home{background:#daf5df;color:#17752a}.description{font-size:14px}.actions{display:flex;gap:7px;margin-top:15px;flex-wrap:wrap}.danger-button{color:var(--error-color,#db4437)}dl{font-size:13px}dt{color:var(--secondary-text-color);margin-top:9px}dd{margin:2px 0;overflow-wrap:anywhere}
      .timeline{background:var(--card-background-color,#fff);border-radius:16px;border:1px solid var(--divider-color,#ddd);padding:4px 18px}.history-row{display:flex;gap:13px;align-items:center;padding:14px 0;border-bottom:1px solid var(--divider-color,#ddd)}.history-row:last-child{border:0}.history-row p{margin:4px 0 0;font-size:13px}.check{display:grid;place-items:center;border-radius:50%;width:34px;height:34px;background:#daf5df;color:#17752a;font-weight:800}
      .settings-card{max-width:760px;margin-bottom:14px}.settings-card>p{margin-top:6px}.info-row{display:flex;justify-content:space-between;gap:12px;padding:12px 0;border-top:1px solid var(--divider-color,#ddd);font-size:14px}.settings-card>.danger-button{margin-top:14px}
      .config-transfer{border-top:1px solid var(--divider-color,#ddd);margin-top:12px;padding-top:16px}.config-transfer p{font-size:13px}.metric-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:16px}.metric,.analytics-card{background:var(--card-background-color,#fff);border:1px solid var(--divider-color,#ddd);border-radius:16px;padding:18px}.metric{display:flex;flex-direction:column;gap:8px}.metric span{font-size:13px;color:var(--secondary-text-color)}.metric b{font-size:26px}.metric.danger b{color:var(--error-color,#db4437)}.analytics-card{margin-bottom:14px}.analytics-card h3{margin-bottom:12px}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;padding:10px;border-top:1px solid var(--divider-color,#ddd);white-space:nowrap}th{font-size:12px;color:var(--secondary-text-color)}.insight-list{display:grid;gap:8px}.insight{padding:11px 13px;border-radius:10px;background:var(--secondary-background-color,#f3f4f6);border-left:4px solid var(--primary-color)}.insight.warning{border-color:#f59e0b}.insight.critical{border-color:var(--error-color,#db4437)}.positive{color:#17752a}.handover-note{padding:9px 11px;border-radius:9px;background:color-mix(in srgb,var(--primary-color) 10%,transparent);font-size:13px}
      .monitor-form{margin-top:16px}.printer-list{display:flex;gap:7px;flex-wrap:wrap;margin:2px 0 10px}.printer-list span{font-size:12px;padding:5px 9px;border-radius:99px;background:var(--divider-color,#eee)}
      .reference-help{padding:12px;border:1px solid var(--divider-color,#bbb);border-radius:11px;background:var(--secondary-background-color,#f3f4f6)}.reference-controls{display:grid;grid-template-columns:1fr 1fr;gap:10px}
      .field-with-action{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px;align-items:start}.field-with-action>label{min-width:0}.field-action{width:42px;height:42px;padding:0;font-size:22px;align-self:start;margin-top:24px}.tag-creator{display:flex;align-items:flex-start;gap:8px;flex-wrap:wrap;margin-top:10px;padding:12px;border:1px solid var(--divider-color,#bbb);border-radius:10px;background:var(--secondary-background-color,#f3f4f6)}.tag-creator>label{flex:1 1 260px}.tag-creator>[data-create-tag],.tag-creator>[data-cancel-tag]{margin-top:24px}.tag-creator>.hint{flex-basis:100%}
      .test-line,.test-actions,.resource-test-line,.task-preview{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:8px}.preview-result{font-size:12px;color:var(--secondary-text-color);line-height:1.45}.preview-result.match{color:#17752a}.preview-result.error{color:var(--error-color,#db4437)}.inline-create{border-top:1px solid var(--divider-color,#ddd);padding-top:10px}.inline-person-form,.inline-follow-up-form{margin-top:10px;padding:12px;border-radius:10px;background:var(--secondary-background-color,#f3f4f6)}
      .resource-list{display:grid;gap:12px;margin:14px 0}.resource-row{border:1px solid var(--divider-color,#bbb);border-radius:12px;padding:13px;background:var(--secondary-background-color,#f3f4f6)}.resource-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}.resource-head .remove-row{width:38px;height:38px;padding:0;color:var(--error-color,#db4437);font-size:20px}
      .empty{text-align:center;padding:50px 20px}.big-icon{font-size:42px;color:#25a244}.spinner{width:32px;height:32px;border:3px solid var(--divider-color);border-top-color:var(--primary-color);border-radius:50%;animation:spin .8s linear infinite;margin:auto}@keyframes spin{to{transform:rotate(360deg)}}
      .backdrop{position:fixed;z-index:1000;inset:0;background:#0009;display:grid;place-items:center;padding:18px}.modal-card{width:min(760px,100%);max-height:92vh;overflow:auto;background:var(--card-background-color,#fff);border-radius:20px;padding:22px}.modal-card.small{width:min(580px,100%)}.modal-head{display:flex;justify-content:space-between;align-items:start;margin-bottom:20px}.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:15px}.form-grid label{display:flex;flex-direction:column;gap:6px;font-size:13px;font-weight:650}.form-grid .full{grid-column:1/-1}.form-grid .checkbox{flex-direction:row;flex-wrap:wrap;align-items:center;align-self:end;min-height:42px}.form-grid input,.form-grid select,.form-grid textarea{width:100%;padding:10px 11px;border:1px solid var(--divider-color,#bbb);border-radius:9px;background:var(--primary-background-color,#fafafa)}.form-grid [aria-invalid="true"]{border-color:var(--error-color,#db4437)}.checkbox input{width:auto}.checkbox .hint{flex-basis:100%;padding-left:24px}.modal-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:5px}.hidden{display:none!important}.hint{display:block;color:var(--secondary-text-color,#6b7280);font-size:12px;font-weight:400;line-height:1.45;margin:2px 0}.field-help{max-width:68ch}.field-error{display:block;color:var(--error-color,#db4437);font-size:12px;font-weight:650}.group-help{margin-top:-8px}.weekdays{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px}.weekdays input{position:absolute;width:1px;height:1px;margin:-1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap}.weekdays span{display:grid;place-items:center;width:40px;height:36px;border:1px solid var(--divider-color);border-radius:9px;cursor:pointer}.weekdays input:focus-visible+span{outline:3px solid color-mix(in srgb,var(--primary-color,#03a9f4) 70%,white);outline-offset:2px}.weekdays input:checked+span{background:var(--primary-color);color:#fff;border-color:var(--primary-color)}
      .field-label{display:block;font-size:13px;font-weight:650;margin-bottom:7px}.candidate-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:5px 12px;padding:7px 10px;border:1px solid var(--divider-color,#bbb);border-radius:9px}.candidate-grid .checkbox{min-height:32px;font-weight:500}
      .repeatable-editor{border:1px solid var(--divider-color,#bbb);border-radius:11px;padding:12px}.repeatable-list{display:grid;gap:9px}.repeatable-row{display:grid;grid-template-columns:minmax(150px,2fr) minmax(130px,1fr) auto;gap:9px;align-items:end;padding:10px;background:var(--secondary-background-color,#f3f4f6);border-radius:9px}.repeatable-row.trigger-row{grid-template-columns:minmax(180px,2fr) repeat(3,minmax(105px,1fr)) auto}.repeatable-row .remove-row{width:38px;height:38px;padding:0;color:var(--error-color,#db4437);font-size:20px}.add-row{margin-top:10px}.empty-row{margin:2px 0 8px;font-size:13px;font-style:italic}
      .repeatable-row.escalation-row{grid-template-columns:repeat(4,minmax(120px,1fr)) minmax(150px,1fr) auto}
      .toast{position:fixed;z-index:2000;left:50%;bottom:28px;transform:translateX(-50%);background:#263238;color:#fff;padding:12px 18px;border-radius:10px;box-shadow:0 4px 20px #0005}.toast.error{background:var(--error-color,#db4437)}
      @media(max-width:650px){main{padding:16px 12px}header{margin-bottom:8px}h1{font-size:24px}nav{margin-bottom:16px}.hero{padding:20px;align-items:flex-start}.hero h2{font-size:21px}.hero-side{flex-direction:column-reverse;align-items:flex-end;gap:10px}.hero-button{padding:8px 10px}.cards{grid-template-columns:1fr}.task-card{align-items:flex-start}.complete{padding:8px}.form-grid{grid-template-columns:1fr}.form-grid .full{grid-column:auto}.modal-card{padding:18px 15px}.toolbar{align-items:center}.people-grid{grid-template-columns:1fr}.ranking-row{grid-template-columns:27px 34px 1fr auto}.ranking-row .avatar{width:34px;height:34px}.month-points{display:none}.ranking-head>span{display:none}.repeatable-row,.repeatable-row.trigger-row,.repeatable-row.escalation-row,.reference-controls{grid-template-columns:1fr}.repeatable-row .remove-row{justify-self:end}}
    `;
  }
}

if (!customElements.get("household-tasks-panel")) {
  customElements.define("household-tasks-panel", HouseholdTasksPanel);
}
