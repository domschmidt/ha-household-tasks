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
const HT_PANEL_VIEW_URLS = Object.freeze({
  today: "/haushaltsaufgaben?view=today",
  mine: "/haushaltsaufgaben?view=mine",
  week: "/haushaltsaufgaben?view=week",
  tasks: "/haushaltsaufgaben?view=tasks",
  people: "/haushaltsaufgaben?view=people",
  analytics: "/haushaltsaufgaben?view=analytics",
  ranking: "/haushaltsaufgaben?view=ranking",
  history: "/haushaltsaufgaben?view=history",
  settings: "/haushaltsaufgaben?view=settings",
});
const HT_PANEL_VIEWS = new Set(Object.keys(HT_PANEL_VIEW_URLS));
const HT_ATTACHMENT_MAX_BYTES = 20000000;
const HT_ATTACHMENT_TOTAL_MAX_BYTES = 100000000;
const HT_ATTACHMENT_CHUNK_BYTES = 512000;

class HouseholdTasksPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._data = null;
    this._view = "today";
    this._busy = false;
    this._references = { users: [], devices: [], tags: [] };
    this._referencesLoaded = false;
    this._lastRefreshAt = 0;
    this._refreshTimer = null;
    this._pauseTimer = null;
    this._updateSubscription = null;
    this._updateSubscriptionConnection = null;
    this._offlineActions = new Set(["complete", "snooze", "move_occurrence", "bulk"]);
    this._onlineHandler = () => {
      void this._flushOfflineQueue().finally(() => this._scheduleRefresh());
    };
    this._offlineHandler = () => this._render();
    this._resumeHandler = () => this._refreshAfterResume();
    this._viewportHandler = () => this._positionOpenActionMenus();
    this._popstateHandler = () => {
      const view = this._viewFromLocation();
      if (view === this._view) return;
      this._view = view;
      this._render();
      if (view === "week") void this._loadWeekPreview();
    };
    this._globalKeyHandler = (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLocaleLowerCase() === "k") {
        event.preventDefault();
        event.stopImmediatePropagation();
        if (this._data) this._showCommandPalette();
      }
    };
  }

  set hass(value) {
    const connectionChanged = this._hass?.connection !== value?.connection;
    this._hass = value;
    if (connectionChanged) void this._ensureUpdateSubscription();
    if (this.isConnected && !this._data && !this._busy) this._load();
  }
  get hass() { return this._hass; }

  connectedCallback() {
    // HA can assign properties before a cache-fresh custom element is upgraded.
    // Re-apply an own property so the prototype setter receives the value.
    if (!this._hass && Object.hasOwn(this, "hass")) {
      const value = this.hass;
      delete this.hass;
      this.hass = value;
    }
    window.removeEventListener("keydown", this._globalKeyHandler, true);
    window.addEventListener("keydown", this._globalKeyHandler, true);
    window.removeEventListener("online", this._onlineHandler);
    window.addEventListener("online", this._onlineHandler);
    window.removeEventListener("offline", this._offlineHandler);
    window.addEventListener("offline", this._offlineHandler);
    window.removeEventListener("popstate", this._popstateHandler);
    window.addEventListener("popstate", this._popstateHandler);
    window.removeEventListener("location-changed", this._popstateHandler);
    window.addEventListener("location-changed", this._popstateHandler);
    window.removeEventListener("focus", this._resumeHandler);
    window.addEventListener("focus", this._resumeHandler);
    window.removeEventListener("pageshow", this._resumeHandler);
    window.addEventListener("pageshow", this._resumeHandler);
    document.removeEventListener("visibilitychange", this._resumeHandler);
    document.addEventListener("visibilitychange", this._resumeHandler);
    window.removeEventListener("resize", this._viewportHandler);
    window.addEventListener("resize", this._viewportHandler);
    window.removeEventListener("scroll", this._viewportHandler, true);
    window.addEventListener("scroll", this._viewportHandler, true);
    this._view = this._viewFromLocation();
    this._render();
    const cached = localStorage.getItem("household_tasks_offline_snapshot");
    if (cached && !this._data) {
      try { this._data = JSON.parse(cached); } catch (_) { /* Ignore stale cache. */ }
    }
    if (this._hass) {
      void this._ensureUpdateSubscription();
      this._load();
    }
  }

  disconnectedCallback() {
    window.removeEventListener("keydown", this._globalKeyHandler, true);
    window.removeEventListener("online", this._onlineHandler);
    window.removeEventListener("offline", this._offlineHandler);
    window.removeEventListener("popstate", this._popstateHandler);
    window.removeEventListener("location-changed", this._popstateHandler);
    window.removeEventListener("focus", this._resumeHandler);
    window.removeEventListener("pageshow", this._resumeHandler);
    document.removeEventListener("visibilitychange", this._resumeHandler);
    window.removeEventListener("resize", this._viewportHandler);
    window.removeEventListener("scroll", this._viewportHandler, true);
    clearTimeout(this._refreshTimer);
    this._refreshTimer = null;
    clearTimeout(this._pauseTimer);
    this._pauseTimer = null;
    this._teardownUpdateSubscription();
  }

  _viewFromLocation() {
    const requested = new URL(window.location.href).searchParams.get("view") || "today";
    if (requested === "dashboard") return "ranking";
    return HT_PANEL_VIEWS.has(requested) ? requested : "today";
  }

  _navigateToView(requested) {
    const view = HT_PANEL_VIEWS.has(requested) ? requested : "today";
    const url = this._viewUrl(view);
    if (`${window.location.pathname}${window.location.search}` !== url) {
      window.history.pushState({}, "", url);
    }
    this._view = view;
    this._render();
    if (view === "week") void this._loadWeekPreview();
  }

  _viewUrl(requested) {
    return HT_PANEL_VIEW_URLS[requested] || HT_PANEL_VIEW_URLS.today;
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
        localStorage.setItem("household_tasks_offline_snapshot", JSON.stringify(this._data));
      }
      success = true;
      return result;
    } catch (error) {
      if (!navigator.onLine && this._offlineActions.has(type)) {
        const queue = JSON.parse(localStorage.getItem("household_tasks_offline_queue") || "[]");
        queue.push({ type, payload, queued_at: new Date().toISOString() });
        localStorage.setItem("household_tasks_offline_queue", JSON.stringify(queue.slice(-100)));
        this._toast("Offline vorgemerkt – wird bei Verbindung synchronisiert.");
        return this._data;
      }
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
      this._lastRefreshAt = Date.now();
      if (this._view === "week") await this._loadWeekPreview();
      await this._loadReferences();
    } catch (error) {
      console.debug("Household Tasks could not refresh its panel data.", error);
    }
  }

  _refreshAfterResume() {
    if (document.visibilityState !== "visible" || !navigator.onLine) return;
    if (Date.now() - this._lastRefreshAt < 1000) return;
    this._scheduleRefresh(50);
  }

  _scheduleRefresh(delay = 150) {
    if (!this.isConnected || !this._hass || !navigator.onLine) return;
    clearTimeout(this._refreshTimer);
    this._refreshTimer = setTimeout(() => {
      this._refreshTimer = null;
      if (this.shadowRoot.querySelector("#modal .backdrop")) {
        this._scheduleRefresh(1000);
        return;
      }
      if (this._busy) {
        this._scheduleRefresh(250);
        return;
      }
      void this._load();
    }, delay);
  }

  async _ensureUpdateSubscription() {
    const connection = this._hass?.connection;
    if (!this.isConnected || !connection?.subscribeEvents) return;
    if (this._updateSubscriptionConnection === connection) return;
    this._teardownUpdateSubscription();
    this._updateSubscriptionConnection = connection;
    try {
      const unsubscribe = await connection.subscribeEvents(
        () => this._scheduleRefresh(),
        "household_tasks_updated",
      );
      if (!this.isConnected || this._updateSubscriptionConnection !== connection) {
        unsubscribe();
        return;
      }
      this._updateSubscription = unsubscribe;
    } catch (error) {
      if (this._updateSubscriptionConnection === connection) {
        this._updateSubscriptionConnection = null;
      }
      console.debug("Household Tasks could not subscribe to live updates.", error);
    }
  }

  _teardownUpdateSubscription() {
    if (typeof this._updateSubscription === "function") this._updateSubscription();
    this._updateSubscription = null;
    this._updateSubscriptionConnection = null;
  }

  async _loadWeekPreview() {
    if (!this._hass || !this._data || !navigator.onLine) return;
    try {
      const preview = await this._hass.callWS({ type: "household_tasks/week_preview" });
      if (!Array.isArray(preview)) return;
      this._data.week_preview = preview;
      localStorage.setItem("household_tasks_offline_snapshot", JSON.stringify(this._data));
      if (this._view === "week") this._render();
    } catch (error) {
      console.debug("Household Tasks could not refresh its weekly preview.", error);
    }
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
    const options = suggestions.map((item) => {
      const detail = item.detail ? ` · ${this._e(item.detail)}` : "";
      return `<option value="${this._e(item.value)}">${this._e(item.label)}${detail}</option>`;
    }).join("");
    const requiredAttribute = required ? "required" : "";
    const patternAttribute = pattern ? `pattern="${this._e(pattern)}"` : "";
    const hintMarkup = hint ? `<span class="hint">${this._e(hint)}</span>` : "";
    return `<input name="${this._e(name)}" list="${listId}" value="${this._e(value || "")}"
      ${requiredAttribute} ${patternAttribute}
      placeholder="${this._e(placeholder)}" autocomplete="off">
      <datalist id="${listId}">${options}</datalist>
      ${hintMarkup}`;
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
      pattern: String.raw`notify\..+`,
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
        if (list) {
          list.replaceChildren(...this._references.tags.map((tag) => {
            const id = tag.tag_id || tag.id;
            return new Option(tag.name || id, id);
          }));
        }
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
      else {
        const lastScanned = tag.last_scanned
          ? ` · zuletzt ${new Date(tag.last_scanned).toLocaleString(this._locale())}`
          : "";
        output.textContent = `Registriert als „${tag.name || tagId}“${lastScanned}.`;
      }
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

  async _flushOfflineQueue() {
    if (!this._hass || !navigator.onLine) return;
    const queue = JSON.parse(localStorage.getItem("household_tasks_offline_queue") || "[]");
    if (!queue.length) return;
    const remaining = [];
    for (const entry of queue) {
      try {
        await this._hass.callWS({ type: `household_tasks/${entry.type}`, ...entry.payload });
      } catch (_) {
        remaining.push(entry);
      }
    }
    localStorage.setItem("household_tasks_offline_queue", JSON.stringify(remaining));
    if (!remaining.length) {
      this._toast(`${queue.length} Offline-Aktionen synchronisiert.`);
      await this._load();
    }
  }

  _emptyRepeatableRow(message) {
    const empty = document.createElement("p");
    empty.className = "empty-row";
    empty.textContent = this._t(message);
    return empty;
  }

  _removeRepeatableEmptyState(list) {
    list.querySelectorAll(".empty-row").forEach((empty) => empty.remove());
  }

  _labeledControl(text, control) {
    const label = document.createElement("label");
    label.append(document.createTextNode(text), control);
    return label;
  }

  _selectControl(name, options, selectedValue) {
    const select = document.createElement("select");
    select.name = name;
    for (const [value, text] of options) {
      const option = new Option(text, value);
      option.selected = value === selectedValue;
      select.add(option);
    }
    return select;
  }

  _removeRowButton(label) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "remove-row";
    button.title = label;
    button.setAttribute("aria-label", label);
    button.textContent = "×";
    return button;
  }

  _createEscalationRow(stage = {}) {
    const row = document.createElement("div");
    row.className = "repeatable-row escalation-row";

    const after = document.createElement("input");
    after.name = "escalation_after";
    after.required = true;
    after.value = stage.after || "00:00:00";
    after.pattern = "[0-9]+:[0-5][0-9]:[0-5][0-9]";

    const relativeTo = this._selectControl("escalation_relative_to", [
      ["due", "Fälligkeit"],
      ["first_notification", "Erste Benachrichtigung"],
    ], stage.relative_to === "first_notification" ? "first_notification" : "due");
    const recipients = this._selectControl("escalation_recipients", [
      ["assignee", "Zuständige Person"],
      ["all", "Alle Personen"],
    ], stage.recipients === "all" ? "all" : "assignee");
    const action = this._selectControl("escalation_action", [
      ["notify", "Benachrichtigen"],
      ["delegate", "Weitergeben"],
      ["open", "Zur Übernahme öffnen"],
    ], stage.action || "notify");

    const presenceLabel = document.createElement("label");
    presenceLabel.className = "checkbox";
    const presence = document.createElement("input");
    presence.name = "escalation_presence";
    presence.type = "checkbox";
    presence.checked = Boolean(stage.presence_required);
    presenceLabel.append(presence, document.createTextNode(" Nur bei Anwesenheit"));

    row.append(
      this._labeledControl("Nach (HH:MM:SS)", after),
      this._labeledControl("Bezug", relativeTo),
      this._labeledControl("Empfänger", recipients),
      this._labeledControl("Aktion", action),
      presenceLabel,
      this._removeRowButton("Eskalationsstufe entfernen"),
    );
    return row;
  }

  _bindEscalationEditor(root) {
    root.querySelectorAll(".escalation-editor").forEach((editor) => {
      const list = editor.querySelector(".repeatable-list");
      const bindRemovers = () => editor.querySelectorAll(".remove-row").forEach((button) => {
        button.onclick = () => {
          button.closest(".escalation-row").remove();
          if (!editor.querySelector(".escalation-row")) {
            list.replaceChildren(this._emptyRepeatableRow("Noch keine Eskalationsstufe angelegt."));
          }
        };
      });
      editor.querySelector("[data-add-escalation]").onclick = () => {
        const hasStages = Boolean(editor.querySelector(".escalation-row"));
        this._removeRepeatableEmptyState(list);
        const row = this._createEscalationRow({
          after: hasStages ? "02:00:00" : "00:00:00",
          recipients: "assignee",
          relative_to: hasStages ? "first_notification" : "due",
        });
        list.append(row);
        this._localize(row);
        this._enhanceAccessibility(editor);
        bindRemovers();
        row.querySelector("input")?.focus();
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
        paused_until: "Unterdrückt neue Aufgaben nur bis zu diesem Zeitpunkt und aktiviert die Vorlage danach automatisch wieder.",
        assignment_type: "Bestimmt, ob die Zuständigkeit fest, rotierend, fair verteilt oder offen ist.",
        assignment_person: "Diese Personen dürfen bei Rotation, fairer Verteilung oder offener Zuweisung berücksichtigt werden.",
        presence_required: "Berücksichtigt bei automatischer Zuweisung nur aktuell anwesende Personen.",
        absence_policy: "Legt bei fester Zuständigkeit ausdrücklich fest, ob gewartet, vertreten, geöffnet oder trotzdem zugewiesen wird.",
        fallback_person: "Nur diese Personen dürfen die feste Zuständigkeit bei Abwesenheit vertreten.",
        fallback_strategy: "Bestimmt, wie unter mehreren anwesenden Ersatzpersonen ausgewählt wird.",
        follow_up_task_id: "Vorlage, die nach Abschluss dieser Aufgabe automatisch erzeugt wird.",
        follow_up_delay: "Zeitspanne zwischen Abschluss und Erzeugung der Folgeaufgabe.",
        nfc_tag_id: "Optionaler Home-Assistant-Tag, mit dem die Aufgabe ausgelöst oder erledigt wird.",
        new_tag_name: "Verständlicher Name, unter dem der NFC-Tag in Home Assistant gespeichert wird.",
        nfc_action: "Aktion, die beim Scannen des zugeordneten NFC-Tags ausgeführt wird.",
        market_priority: "Bestimmt Sortierung und Verhalten im reduzierten Urlaubsmodus.",
        market_points: "Punkte, die der tatsächlich abschließenden Person gutgeschrieben werden.",
        automatic_completion: "Schließt eine unbestätigte Aufgabe nach der Kulanzzeit automatisch ab.",
        automatic_completion_person: "Erhält die Punkte nur dann, wenn vorher niemand selbst auf Erledigt gedrückt hat.",
        automatic_completion_after: "Zeitspanne ab Fälligkeit, in der eine andere Person die Erledigung noch selbst bestätigen kann.",
        market_reward: "Optionale sichtbare Anerkennung für die freiwillige Übernahme.",
        vacation_behavior: "Überschreibt die globale Urlaubsstrategie für diese Vorlage.",
        guest_only: "Erzeugt diese Aufgabe ausschließlich während des Gastmodus.",
        skip_in_guest: "Unterdrückt diese Aufgabe, solange der Gastmodus aktiv ist.",
        season_enabled: "Begrenzt automatische Erzeugungen auf Monate und optional einen Sensorzustand.",
        season_months: "Kalendermonate 1 bis 12, in denen die Vorlage automatisch laufen darf.",
        season_condition: "Optionaler Vergleich zwischen aktuellem Entitätszustand und Grenzwert.",
        season_entity_id: "Home-Assistant-Entität, deren Zustand die saisonale Bedingung liefert.",
        season_threshold: "Zahl oder Text, mit dem der aktuelle Zustand verglichen wird.",
        use_event_title: "Verwendet den Namen des passenden Kalendertermins als sichtbaren Aufgabennamen. Der Vorlagenname bleibt der Rückfallwert.",
        calendar_mapping_pattern: "Regulärer Ausdruck für den Kalendertitel. Groß- und Kleinschreibung wird ignoriert; die erste passende Zeile gewinnt.",
        calendar_mapping_task: "Verständlicher Aufgabenname, der für diesen Kalendertitel erzeugt wird.",
        ignore_unmapped_events: "Verhindert Aufgaben für Kalendertermine, die in keiner Zuordnungszeile stehen.",
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
        weather_logic: "UND verlangt alle Bedingungen; ODER genügt, sobald eine Bedingung zutrifft.",
        weather_entity_id: "Wetter-, Temperatur-, Feuchte-, Wind- oder anderer Home-Assistant-Sensor.",
        weather_attribute: "Optionales Attribut einer weather.*-Entität; leer vergleicht den normalen Zustand.",
        weather_condition: "Vergleich zwischen aktuellem Wetterwert und Grenzwert.",
        weather_threshold: "Numerischer Grenzwert oder Wetterzustand wie rainy oder snowy.",
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
      "task-pause-form": {
        paused_until: "Zeitpunkt, ab dem die Vorlage automatisch wieder neue Aufgaben erzeugen darf.",
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
      "setup-form": {
        person_id: "Technische ID der ersten Haushaltsperson.",
        person_name: "Anzeigename im Panel und in Benachrichtigungen.",
        notify: "Mobile-App-Dienst für Erinnerungen mit direkten Aktionen.",
        presence: "Optionale Entität für anwesenheitsabhängige Zuweisungen.",
        template_id: "Kuratierte Ausgangsvorlage aus der Galerie.",
        task_id: "Technische, später stabile ID der übernommenen Vorlage.",
        entity_id: "Auslöser für zustandsbasierte Starter-Vorlagen.",
      },
      "household-mode-form": {
        mode: "Aktueller Betriebszustand des gesamten Haushalts.",
        policy: "Standardverhalten automatischer Aufgaben während des Urlaubs.",
        delegate_to: "Person, die delegierte Urlaubsaufgaben erhält.",
        until: "Optionaler Zeitpunkt für die automatische Rückkehr zum Normalmodus.",
        note: "Nachvollziehbarer Grund für den temporären Modus.",
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
          if (group) group.after(helper);
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

  _commandEntries() {
    const entries = [];
    for (const [id, task] of Object.entries(this._data.tasks || {})) {
      entries.push({
        type: "Aufgabe",
        label: task.name,
        detail: id,
        keywords: `${task.name} ${id} ${task.description || ""}`,
        action: () => this._showTaskEditor(id),
      });
    }
    for (const [id, person] of Object.entries(this._data.people || {})) {
      entries.push({
        type: "Person",
        label: person.name,
        detail: id,
        keywords: `${person.name} ${id} ${person.presence || ""}`,
        action: () => this._showPersonEditor(id),
      });
    }
    for (const occurrence of this._data.occurrences || []) {
      entries.push({
        type: occurrence.resolved ? "Verlauf" : "Offen",
        label: this._plainTitle(occurrence.title),
        detail: this._formatDue(new Date(occurrence.due)),
        keywords: `${occurrence.title} ${occurrence.task_id} ${occurrence.assignee || ""}`,
        action: () => {
          this._view = occurrence.resolved ? "history" : "today";
          this._render();
        },
      });
    }
    for (const tag of this._references.tags || []) {
      const id = tag.tag_id || tag.id;
      entries.push({
        type: "NFC",
        label: tag.name || id,
        detail: id,
        keywords: `${tag.name || ""} ${id}`,
        action: () => this._copyReference(id),
      });
    }
    for (const [entityId, state] of Object.entries(this._hass.states || {})) {
      entries.push({
        type: "Entität",
        label: state.attributes?.friendly_name || entityId,
        detail: `${entityId} · ${state.state}`,
        keywords: `${entityId} ${state.attributes?.friendly_name || ""} ${state.state}`,
        action: () => this._copyReference(entityId),
      });
    }
    return entries;
  }

  async _copyReference(value) {
    try {
      await navigator.clipboard.writeText(value);
      this._toast(`„${value}“ kopiert.`);
    } catch (error) {
      console.debug("Household Tasks could not copy a reference.", error);
      this._toast(value);
    }
  }

  _showCommandPalette() {
    const modal = this.shadowRoot.querySelector("#modal");
    const returnFocus = this.shadowRoot.activeElement;
    modal.innerHTML = `<div class="backdrop"><div class="modal-card command-card" role="dialog" aria-modal="true" aria-label="Globale Suche">
      <div class="modal-head"><div><div class="eyebrow">SCHNELLZUGRIFF</div><h2>Suchen und öffnen</h2></div><button class="icon-button close" aria-label="Schließen">×</button></div>
      <input class="command-input" type="search" autofocus placeholder="Aufgabe, Person, NFC-Tag oder Entität …" aria-label="Globale Suche">
      <div class="command-results" role="listbox"></div>
      <p class="hint">Tipp: Mit Strg/⌘ + K jederzeit öffnen.</p>
    </div></div>`;
    const close = () => {
      modal.replaceChildren();
      returnFocus?.focus();
    };
    modal.querySelector(".close").onclick = close;
    this._activateDialog(modal, close);
    const input = modal.querySelector(".command-input");
    const results = modal.querySelector(".command-results");
    const entries = this._commandEntries();
    const render = () => {
      const query = input.value.trim().toLocaleLowerCase(this._locale());
      const matches = entries
        .filter((entry) => !query || entry.keywords.toLocaleLowerCase(this._locale()).includes(query))
        .slice(0, 30);
      results.replaceChildren(...matches.map((entry) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "command-result";
        const type = document.createElement("span");
        type.className = "command-type";
        type.textContent = entry.type;
        const text = document.createElement("span");
        const title = document.createElement("strong");
        title.textContent = entry.label;
        const detail = document.createElement("small");
        detail.textContent = entry.detail;
        text.append(title, detail);
        button.append(type, text);
        button.onclick = () => {
          close();
          entry.action();
        };
        return button;
      }));
      if (!matches.length) {
        results.append(this._emptyRepeatableRow("Keine passenden Einträge gefunden."));
      }
    };
    input.oninput = render;
    render();
  }

  _render() {
    const data = this._data;
    const modeBadge = this._modeBadge(data);
    this.shadowRoot.innerHTML = `
      <style>${this._styles()}</style>
      <main>
        <header>
          <div>
            <div class="eyebrow">ZUHAUSE</div>
            <h1>Haushaltsaufgaben</h1>
          </div>
          <div class="header-actions">
            ${!navigator.onLine ? `<span class="mode-badge">Offline</span>` : ""}
            ${modeBadge}
            ${data?.is_admin && data.undo?.length ? `<button class="undo-button" title="${this._e(data.undo[0].label)}">↶ Rückgängig</button>` : ""}
            <button class="search-button" title="Globale Suche (Strg/⌘ + K)">⌕ Suchen</button>
            <button class="icon-button refresh" title="Aktualisieren" ${this._busy ? "disabled" : ""}>↻</button>
          </div>
        </header>
        <nav>
          ${this._navButton("today", "Heute")}
          ${this._navButton("mine", "Meine Aufgaben")}
          ${this._navButton("week", "Wochenplan")}
          ${this._navButton("tasks", "Aufgaben")}
          ${this._navButton("people", "Personen")}
          ${this._navButton("analytics", "Auswertung")}
          ${this._navButton("history", "Verlauf")}
          ${this._navButton("ranking", "Ranking")}
          ${this._navButton("settings", "Einstellungen")}
        </nav>
        <section class="content">
          ${!data ? this._loading() : this._renderView()}
        </section>
        ${data && Object.keys(data.people || {}).length ? `<button class="context-add" aria-label="Kontextabhängig hinzufügen" title="Schnell hinzufügen">+</button>` : ""}
      </main>
      <div id="modal"></div>
    `;
    this._bind();
    this._localize();
    this._enhanceAccessibility();
    this._schedulePauseRefresh();
  }

  _schedulePauseRefresh() {
    clearTimeout(this._pauseTimer);
    this._pauseTimer = null;
    const next = Object.values(this._data?.tasks || {})
      .filter((task) => task.enabled !== false && task.paused_until)
      .map((task) => new Date(task.paused_until).getTime())
      .filter((timestamp) => Number.isFinite(timestamp) && timestamp > Date.now())
      .sort((left, right) => left - right)[0];
    if (!next) return;
    const delay = Math.min(next - Date.now() + 250, 2_147_000_000);
    this._pauseTimer = setTimeout(() => this._render(), delay);
  }

  _modeBadge(data) {
    const mode = data?.household_mode?.mode;
    if (!mode || mode === "normal") return "";
    const label = mode === "vacation" ? "Urlaub" : "Gäste";
    return `<span class="mode-badge">${label}</span>`;
  }

  _navButton(id, label) {
    return `<a href="${this._viewUrl(id)}" data-view="${id}" class="${this._view === id ? "active" : ""}">${label}</a>`;
  }

  _loading() {
    return `<div class="empty"><div class="spinner"></div><h2>Aufgaben werden geladen</h2></div>`;
  }

  _renderView() {
    if (this._view === "ranking") return this._renderRanking();
    if (this._view === "mine") return this._renderMine();
    if (this._view === "week") return this._renderWeek();
    if (this._view === "tasks") return this._renderTasks();
    if (this._view === "people") return this._renderPeople();
    if (this._view === "analytics") return this._renderAnalytics();
    if (this._view === "history") return this._renderHistory();
    if (this._view === "settings") return this._renderSettings();
    return this._renderToday();
  }

  _renderBulkToolbar(items) {
    if (!items.length) return "";
    return `<div class="bulk-toolbar">
      <label class="checkbox"><input type="checkbox" data-select-all> Alle auswählen</label>
      <span class="bulk-count">0 ausgewählt</span>
      <button data-bulk-action="complete" disabled>Erledigen</button>
      <button data-bulk-action="tomorrow" disabled>Auf morgen</button>
      <button data-bulk-action="help" disabled>Hilfe anfragen</button>
    </div>`;
  }

  _renderMine() {
    const personId = this._currentPersonId();
    if (!personId) {
      return `<div class="empty card"><h2>Meine Aufgaben</h2><p>Verknüpfe deine Haushaltsperson mit deinem Home-Assistant-Benutzer, damit diese Ansicht persönlich gefiltert werden kann.</p></div>`;
    }
    const mine = this._openOccurrences().filter((item) =>
      item.assignee === personId
      || item.helpers?.includes(personId)
      || (!item.assignee && (!(item.task?.assignment?.people || []).length || item.task.assignment.people.includes(personId)))
    );
    const favorites = (this._data.favorites?.[personId] || [])
      .map((id) => [id, this._data.tasks[id]]).filter(([, task]) => task);
    const favoriteStrip = this._favoriteStrip(favorites);
    const occurrenceList = this._selectableOccurrenceList(mine);
    return `<div class="toolbar"><div><div class="eyebrow">PERSÖNLICH</div><h2>Meine Aufgaben</h2><p>Zugewiesen, unterstützt oder zur Übernahme verfügbar.</p></div>
      <button class="primary" id="smart-quick-task">+ Smart erfassen</button></div>
      ${favoriteStrip}
      ${this._renderBulkToolbar(mine)}
      ${occurrenceList}`;
  }

  _favoriteStrip(favorites) {
    if (!favorites.length) return "";
    const buttons = favorites.map(([id, task]) =>
      `<button data-create="${this._e(id)}">+ ${this._e(task.name)}</button>`
    ).join("");
    return `<section class="favorite-strip"><strong>Favoriten</strong>${buttons}</section>`;
  }

  _selectableOccurrenceList(items) {
    if (!items.length) {
      return `<div class="empty card"><div class="big-icon">✓</div><h2>Nichts offen</h2><p>Für dich ist aktuell keine Aufgabe offen.</p></div>`;
    }
    const cards = items.map((item) => {
      const title = this._e(this._plainTitle(item.title));
      return `<div class="selectable-task"><input type="checkbox" data-select-occurrence="${this._e(item.id)}" aria-label="${title} auswählen">${this._occurrenceCard(item)}</div>`;
    }).join("");
    return `<div class="occurrences selectable">${cards}</div>`;
  }

  _renderWeek() {
    const open = this._openOccurrences();
    const previews = this._data.week_preview || [];
    const start = new Date();
    start.setHours(0, 0, 0, 0);
    const days = Array.from({ length: 7 }, (_, offset) => {
      const date = new Date(start);
      date.setDate(start.getDate() + offset);
      const key = date.toLocaleDateString("sv-SE");
      return {
        date,
        key,
        items: open.filter((item) => new Date(item.due).toLocaleDateString("sv-SE") === key),
        previews: previews.filter((item) => new Date(item.due).toLocaleDateString("sv-SE") === key),
      };
    });
    const visible = days.flatMap((day) => day.items);
    const dayMarkup = days.map((day) => this._weekDayMarkup(day)).join("");
    return `<div class="toolbar"><div><div class="eyebrow">PLANUNG</div><h2>Die nächsten sieben Tage</h2><p>Erzeugte Aufgaben und schreibgeschützte Vorschauen auf einen Blick.</p></div><button id="smart-quick-task" class="primary">+ Aufgabe</button></div>
      ${this._renderBulkToolbar(visible)}
      <div class="week-board">${dayMarkup}</div>`;
  }

  _weekDayMarkup(day) {
    const weekday = day.date.toLocaleDateString(this._locale(), { weekday: "short" });
    const date = day.date.toLocaleDateString(this._locale(), { day: "2-digit", month: "2-digit" });
    const actual = day.items.map((item) => {
        const time = new Date(item.due).toLocaleTimeString(this._locale(), { hour: "2-digit", minute: "2-digit" });
        const assignee = this._e(this._data.people[item.assignee]?.name || "Offen");
        return `<article draggable="true" data-drag-occurrence="${this._e(item.id)}"><input type="checkbox" data-select-occurrence="${this._e(item.id)}"><div><strong>${this._e(this._plainTitle(item.title))}</strong><small>${time} · ${assignee}</small></div></article>`;
      }).join("");
    const projected = day.previews.map((item) => {
      const time = new Date(item.due).toLocaleTimeString(this._locale(), { hour: "2-digit", minute: "2-digit" });
      const assignee = item.assignment_pending
        ? "Zuweisung bei Erzeugung"
        : this._data.people[item.assignee]?.name || "Offen";
      const hint = item.conditional ? "Bedingte Vorschau" : "Wird automatisch erzeugt";
      return `<article class="week-preview" aria-label="Vorschau: ${this._e(item.title)}"><div><strong>${this._e(item.title)}</strong><small>${time} · ${this._e(assignee)}</small></div><span title="${this._e(hint)}">Vorschau</span></article>`;
    }).join("");
    const contents = actual || projected ? `${actual}${projected}` : "<p>Hierher ziehen</p>";
    const total = day.items.length + day.previews.length;
    return `<section class="week-day" data-drop-date="${day.key}">
      <header><strong>${weekday}</strong><span>${date}</span><b>${total}</b></header>
      ${contents}
    </section>`;
  }

  _openOccurrences() {
    return this._data.occurrences
      .filter((item) => !["completed", "cancelled"].includes(item.status || (item.resolved ? "completed" : "open")))
      .sort((a, b) => new Date(a.due) - new Date(b.due));
  }

  _renderToday() {
    if (!Object.keys(this._data.people).length) {
      return `<div class="empty card"><div class="big-icon">⌂</div>
        <h2>Haushalt einrichten</h2>
        <p>Der Assistent verbindet Personen, Benachrichtigungen und eine erste geprüfte Vorlage.</p>
        ${this._data.is_admin ? `<button class="primary" id="setup-wizard">Einrichtung starten</button>` : `<p>Ein Administrator muss die Ersteinrichtung abschließen.</p>`}
      </div>`;
    }
    const open = this._openOccurrences();
    const now = new Date();
    const today = now.toLocaleDateString(this._locale());
    const dueToday = open.filter((o) => new Date(o.due).toLocaleDateString(this._locale()) === today);
    const overdue = open.filter((o) => new Date(o.due) < now && !dueToday.includes(o));
    const upcoming = open.filter((o) => !dueToday.includes(o) && !overdue.includes(o)).slice(0, 8);
    return `
      ${this._renderDayHero(open, dueToday, now)}
      ${this._renderContextualHome(open)}
      ${this._occurrenceSection("Überfällig", overdue, "danger")}
      ${this._occurrenceSection("Heute", dueToday)}
      ${this._occurrenceSection("Demnächst", upcoming, "muted")}
      ${!open.length ? `<div class="empty card"><div class="big-icon">✓</div><h2>Alles erledigt</h2><p>Im Moment ist keine Haushaltsaufgabe offen.</p></div>` : ""}
    `;
  }

  _renderRanking() {
    const open = this._openOccurrences();
    const personCounts = {};
    open.forEach((item) => { personCounts[item.assignee] = (personCounts[item.assignee] || 0) + 1; });
    return `
      ${this._renderMobileQuickActions(open)}
      ${this._renderTaskStacks()}
      ${this._peopleStrip(personCounts)}
      ${this._ranking()}
    `;
  }

  _renderDayHero(open, dueToday, now) {
    const heading = dueToday.length
      ? householdTasksLocale(this._hass) === "de"
        ? `${dueToday.length} ${dueToday.length === 1 ? "Aufgabe" : "Aufgaben"} heute`
        : `${dueToday.length} ${dueToday.length === 1 ? "task" : "tasks"} today`
      : this._t("Heute ist alles im Griff");
    return `<div class="hero">
      <div><div class="eyebrow">${now.toLocaleDateString(this._locale(), { weekday: "long", day: "2-digit", month: "long" })}</div>
      <h2>${heading}</h2></div>
      <div class="hero-side"><div class="score">${open.length}<small>offen</small></div>
      <button id="quick-task" class="hero-button">+ Schnellaufgabe</button></div>
    </div>`;
  }

  _renderContextualHome(open) {
    const context = this._data.home_context || {};
    const items = (context.occurrence_ids || [])
      .map((id) => open.find((item) => item.id === id))
      .filter(Boolean);
    return `<section class="context-home">
      <div><div class="eyebrow">PASSEND ZUM MOMENT</div><h2>${this._e(context.title || "Haushalt im Blick")}</h2>
      <p>${items.length ? items.map((item) => this._plainTitle(item.title)).join(" · ") : "Aktuell ist keine unmittelbare Aktion nötig."}</p></div>
      <button id="plan-today" class="primary">Heute planen</button>
    </section>`;
  }

  _renderTaskStacks() {
    const stacks = Object.entries(this._data.task_stacks || {});
    if (!stacks.length) return "";
    return `<section class="stack-strip"><strong>Aufgabenstapel</strong>
      ${stacks.map(([id, stack]) => `<button data-launch-stack="${this._e(id)}">▶ ${this._e(stack.name)} <small>${stack.task_ids.length}</small></button>`).join("")}
    </section>`;
  }

  _renderMobileQuickActions(open) {
    const ownPerson = this._currentPersonId();
    const priority = { critical: 4, high: 3, normal: 2, low: 1 };
    const ranked = [...open].sort((a, b) =>
      Number(b.assignee === ownPerson) - Number(a.assignee === ownPerson)
      || (priority[b.task?.market?.priority] || 2) - (priority[a.task?.market?.priority] || 2)
      || new Date(a.due) - new Date(b.due)
    ).slice(0, 3);
    if (!ranked.length) return "";
    return `<section class="mobile-quick" aria-label="Empfohlene Schnellaktionen">
      <div class="eyebrow">JETZT SINNVOLL</div>
      ${ranked.map((item) => `<button data-jump-occurrence="${this._e(item.id)}">
        <span>${this._e(this._plainTitle(item.title))}</span>
        <small>${this._formatDue(new Date(item.due))}</small>
      </button>`).join("")}
    </section>`;
  }

  _peopleStrip(counts) {
    return `<div class="people-strip" tabindex="0" aria-label="Personen und offene Aufgaben">${Object.entries(this._data.people).map(([id, p]) => `
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
          monthly[item.completed_by] = (monthly[item.completed_by] || 0) + Number(item.awarded_points || 1);
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
    const market = item.task?.market || {};
    const marketBits = [
      market.priority && market.priority !== "normal" ? market.priority.toLocaleUpperCase(this._locale()) : "",
      Number(market.points || 0) ? `${Number(market.points)} Punkte` : "",
      market.reward || "",
    ].filter(Boolean);
    const attachments = this._data.attachments?.[item.id] || [];
    const dueWindow = item.due_window
      ? `<p class="due-window">Flexibel: ${this._formatDue(new Date(item.due_window.earliest))} bis ${this._formatDue(new Date(item.due_window.latest))}</p>`
      : "";
    const reminder = this._reminderText(item);
    const marketBadges = this._marketBadges(marketBits);
    const attachmentCount = this._attachmentCount(attachments);
    const primaryAction = this._occurrencePrimaryAction(item, canClaim);
    const status = item.status || (item.resolved ? "completed" : "open");
    const statusLabels = { open: "Offen", in_progress: "In Arbeit", waiting: "Wartet", blocked: "Blockiert" };
    const checklist = (item.checklist || []).length ? `<div class="task-checklist" aria-label="Checkliste">${item.checklist.map((step) => `
      <label><input type="checkbox" data-checklist-occurrence="${this._e(item.id)}" data-checklist-item="${this._e(step.id)}" data-revision="${Number(item.revision || 1)}" ${step.completed ? "checked" : ""}> <span>${this._e(step.title)}</span></label>`).join("")}</div>` : "";
    const dependencyNames = (item.dependencies || []).map((dependencyId) => {
      const dependency = this._data.occurrences.find((entry) => entry.id === dependencyId);
      return dependency ? this._plainTitle(dependency.title) : dependencyId;
    });
    const dependencies = dependencyNames.length ? `<p class="task-dependencies">Abhängig von: ${dependencyNames.map((name) => this._e(name)).join(", ")}</p>` : "";
    return `<article class="task-card ${overdue ? "overdue" : ""}" data-card-occurrence="${this._e(item.id)}">
      <div class="avatar">${this._e(person.name).slice(0, 1)}</div>
      <div class="task-main">
        <h3>${this._e(this._plainTitle(item.title))}</h3>
        <p><span class="task-status status-${this._e(status)}">${statusLabels[status] || status}</span> ${this._e(person.name)} · ${this._formatDue(due)}${reminder}</p>
        ${marketBadges}
        ${item.assignment_reason ? `<details class="assignment-explanation"><summary>Warum wurde mir das zugewiesen?</summary><p>${this._e(this._assignmentReason(item.assignment_reason))}</p></details>` : ""}
        ${item.help_status === "requested" ? `<p class="help-status">Hilfe wurde im Haushalt angefragt.</p>` : ""}
        ${item.waiting_for ? `<p class="help-status">Wartet auf Anwesenheit.</p>` : ""}
        ${dueWindow}
        ${attachmentCount}
        ${dependencies}
        ${checklist}
      </div>
      <div class="occurrence-actions">
        ${primaryAction}
        <details class="more-actions"><summary aria-label="Weitere Aktionen" aria-haspopup="menu" aria-expanded="false">•••</summary><div role="menu">
          <button data-snooze="${this._e(item.id)}" data-choice="evening">Heute Abend</button>
          <button data-snooze="${this._e(item.id)}" data-choice="tomorrow">Morgen</button>
          <button data-help="${this._e(item.id)}">Hilfe anfordern</button>
          ${item.assignee ? `<button data-decline="${this._e(item.id)}">Heute nicht geschafft</button>` : ""}
          <button data-natural-move="${this._e(item.id)}">Natürlich verschieben …</button>
          <button data-attachment="${this._e(item.id)}">Foto oder Beleg …</button>
          ${status === "blocked" ? `<span class="blocked-action-hint">Voraussetzungen zuerst erledigen</span>` : status !== "in_progress" ? `<button data-task-status="${this._e(item.id)}" data-status="in_progress" data-revision="${Number(item.revision || 1)}">In Arbeit nehmen</button>` : `<button data-task-status="${this._e(item.id)}" data-status="open" data-revision="${Number(item.revision || 1)}">Zurück auf offen</button>`}
          ${status !== "blocked" ? `<button data-task-status="${this._e(item.id)}" data-status="waiting" data-revision="${Number(item.revision || 1)}">Auf wartend setzen</button>` : ""}
          <button data-task-history="${this._e(item.id)}">Verlauf anzeigen</button>
          <button data-task-status="${this._e(item.id)}" data-status="cancelled" data-revision="${Number(item.revision || 1)}">Abbrechen</button>
          ${item.task_id !== "__adhoc__" ? `<button data-device-file="${this._e(item.task_id)}">Geräteakte öffnen</button>` : ""}
        </div></details>
      </div>
    </article>`;
  }

  _reminderText(item) {
    if (!item.sent_steps?.length) return "";
    if (householdTasksLocale(this._hass) === "de") {
      return ` · ${item.sent_steps.length}. Hinweis gesendet`;
    }
    return ` · reminder ${item.sent_steps.length} sent`;
  }

  _marketBadges(bits) {
    if (!bits.length) return "";
    const badges = bits.map((bit) => `<span>${this._e(bit)}</span>`).join("");
    return `<div class="market-badges">${badges}</div>`;
  }

  _attachmentCount(attachments) {
    if (!attachments.length) return "";
    const suffix = attachments.length === 1 ? "" : "e";
    return `<p class="attachment-count">📎 ${attachments.length} Anhang${suffix}</p>`;
  }

  _occurrencePrimaryAction(item, canClaim) {
    const id = this._e(item.id);
    if (item.assignee) {
      const checklistRequired = item.task?.require_checklist_completion !== false;
      const incomplete = checklistRequired
        ? (item.checklist || []).filter((step) => !step.completed).length
        : 0;
      const blocked = item.status === "blocked";
      const disabled = blocked || incomplete ? "disabled" : "";
      const reason = blocked ? "Abhängigkeiten sind noch offen" : incomplete ? `${incomplete} Checklistenpunkt(e) offen` : "";
      return `<button class="complete" data-complete="${id}" ${disabled} title="${this._e(reason)}">Erledigt</button>`;
    }
    const disabled = canClaim ? "" : "disabled";
    return `<button class="complete" data-claim="${id}" ${disabled}>Übernehmen</button>`;
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
    if (reason.type === "absence_fallback") {
      if (reason.waiting) return de
        ? `${name(reason.original)} ist nicht zuhause; die Aufgabe wartet auf eine der festgelegten Ersatzpersonen.`
        : `${name(reason.original)} is away; the task is waiting for one of the configured substitutes.`;
      return de
        ? `${name(reason.original)} ist nicht zuhause. Deshalb wurde ${name(reason.selected)} aus dem festgelegten Ersatzpersonenkreis ausgewählt.`
        : `${name(reason.original)} is away, so ${name(reason.selected)} was selected from the configured substitutes.`;
    }
    if (reason.type === "absence_open") {
      return de
        ? `${name(reason.original)} ist nicht zuhause. Die Aufgabe wurde für die festgelegten Ersatzpersonen zur Übernahme geöffnet.`
        : `${name(reason.original)} is away. The task was opened for the configured substitutes to claim.`;
    }
    if (reason.type === "absence_assigned") {
      return de
        ? `${name(reason.selected)} ist nicht zuhause, bleibt laut Abwesenheitsregel aber fest zuständig.`
        : `${name(reason.selected)} is away but remains assigned according to the absence policy.`;
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
    const gallery = this._data.template_gallery || [];
    const subtitle = householdTasksLocale(this._hass) === "de"
      ? `${tasks.length} Regeln für euren Haushalt`
      : `${tasks.length} household rules`;
    const toolbarActions = this._data.is_admin
      ? '<div class="toolbar-actions"><button id="manage-stacks">Aufgabenstapel</button><button id="open-gallery">Vorlagengalerie</button><button id="add-calendar-task">+ Kalenderregel</button><button class="primary" id="add-task">+ Aufgabe</button></div>'
      : "";
    const galleryStrip = this._galleryStrip(gallery);
    const emptyState = tasks.length
      ? ""
      : '<div class="empty card"><h2>Noch keine Vorlagen</h2><p>Lege eine wiederkehrende Aufgabe an oder nutze eine Schnellaufgabe.</p></div>';
    const taskCards = tasks.map(([id, task]) => this._taskCard(id, task)).join("");
    return `
      <div class="toolbar"><div><h2>Aufgabenvorlagen</h2><p>${subtitle}</p></div>${toolbarActions}</div>
      ${galleryStrip}
      ${emptyState}
      <div class="cards">${taskCards}</div>`;
  }

  _galleryStrip(gallery) {
    if (!gallery.length) return "";
    const entries = gallery.slice(0, 3).map((entry) =>
      `<article><span>${this._e(entry.category)}</span><strong>${this._e(entry.name)}</strong><p>${this._e(entry.description)}</p><button data-gallery="${this._e(entry.id)}">Einrichten</button></article>`
    ).join("");
    return `<section class="gallery-strip" aria-label="Vorlagengalerie">${entries}</section>`;
  }

  _taskCard(id, task) {
    const assignment = this._assignmentLabel(task);
    const isFavorite = (this._data.favorites?.[this._currentPersonId()] || []).includes(id);
    const pausedUntil = this._taskPausedUntil(task);
    const unavailable = task.enabled === false || pausedUntil;
    const disabledClass = unavailable ? "disabled" : "";
    const nfcLabel = task.nfc?.tag_id ? " · NFC" : "";
    let status = this._t("Aktiv");
    if (task.enabled === false) status = this._t("Deaktiviert");
    else if (pausedUntil) status = `${this._t("Pausiert bis")} ${pausedUntil.toLocaleString(this._locale(), { dateStyle: "short", timeStyle: "short" })}`;
    const description = task.description ? `<p class="description">${this._e(task.description)}</p>` : "";
    const habit = this._taskHabit(id);
    const market = this._taskMarket(task.market);
    const automaticPerson = this._data.people[task.automatic_completion?.default_person]?.name || task.automatic_completion?.default_person || "unbekannt";
    const automaticCompletion = task.automatic_completion?.enabled
      ? `<p class="habit-tip">${householdTasksLocale(this._hass) === "de" ? "Automatische Gutschrift nach" : "Automatic credit after"} ${this._e(task.automatic_completion.after || "12:00:00")} ${householdTasksLocale(this._hass) === "de" ? "an" : "to"} ${this._e(automaticPerson)}</p>`
      : "";
    const favorite = this._taskFavoriteButton(id, isFavorite);
    const createDisabled = unavailable ? 'disabled title="Aufgabe ist pausiert"' : "";
    let pauseAction = "";
    if (pausedUntil) pauseAction = `<button data-resume-task="${this._e(id)}">Jetzt fortsetzen</button>`;
    else if (task.enabled !== false) pauseAction = `<button data-pause-task="${this._e(id)}">Temporär pausieren</button>`;
    const adminActions = this._data.is_admin
      ? `${pauseAction}<button data-edit-task="${this._e(id)}">Bearbeiten</button><button class="danger-button" data-delete-task="${this._e(id)}">Löschen</button>`
      : "";
    return `<article class="config-card ${disabledClass}">
      <div class="card-top"><span class="avatar">${this._e(assignment.icon)}</span>
      <div><h3>${this._e(task.name)}</h3><p>${this._e(assignment.label)} · ${this._e(this._scheduleLabel(task.schedule))}${nfcLabel}</p></div>
      <span class="status">${this._e(status)}</span></div>
      ${description}${habit}${market}${automaticCompletion}
      <div class="actions">${favorite}
        <button data-create="${this._e(id)}" ${createDisabled}>Jetzt erzeugen</button>
        <button data-explain-task="${this._e(id)}">Warum nicht?</button>
        ${adminActions}
      </div>
    </article>`;
  }

  _taskPausedUntil(task) {
    if (!task.paused_until) return null;
    const until = new Date(task.paused_until);
    return Number.isNaN(until.getTime()) || until <= new Date() ? null : until;
  }

  _activationMessage(activation) {
    if (activation.code === "template_paused" && activation.paused_until) {
      const until = new Date(activation.paused_until);
      return `${this._t("Pausiert bis")} ${until.toLocaleString(this._locale(), { dateStyle: "short", timeStyle: "short" })}`;
    }
    const messages = {
      template_disabled: "Die Aufgabenvorlage ist dauerhaft deaktiviert.",
      template_active: "Die Aufgabenvorlage ist aktiv.",
      template_pause_elapsed: "Die zeitlich begrenzte Pause ist beendet.",
      template_pause_invalid: "Der Pausenzeitpunkt ist ungültig.",
    };
    return this._t(messages[activation.code] || activation.message || "Die Aufgabenvorlage ist aktiv.");
  }

  _taskHabit(id) {
    const habit = this._data.habits?.[id];
    if (!habit) return "";
    const assignee = this._e(this._data.people[habit.assignee]?.name || "unbekannt");
    const applyButton = this._data.is_admin
      ? `<button data-apply-habit="${this._e(id)}">Vorschlag übernehmen</button>`
      : "";
    return `<p class="habit-tip">✨ Gelernt aus ${habit.samples} Erledigungen: meist ${assignee} gegen ${habit.hour}:00 Uhr. ${applyButton}</p>`;
  }

  _taskMarket(market) {
    if (!market) return "";
    const reward = market.reward ? `<span>${this._e(market.reward)}</span>` : "";
    return `<div class="market-badges"><span>${this._e(market.priority || "normal")}</span><span>${Number(market.points || 0)} Punkte</span>${reward}</div>`;
  }

  _taskFavoriteButton(id, favorite) {
    if (!this._currentPersonId()) return "";
    const label = favorite ? "★ Favorit" : "☆ Favorit";
    return `<button data-favorite="${this._e(id)}" aria-pressed="${favorite}" title="Favorit umschalten">${label}</button>`;
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
    if (type === "per_person") return { icon: "∀", label: `${this._t("Je Person")}: ${names.join(", ")}` };
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
    if (s.type === "flexible_after_completion") return de ? `${s.earliest_interval} bis ${s.latest_interval} nach Erledigung` : `${s.earliest_interval} to ${s.latest_interval} after completion`;
    if (s.type === "weather_trigger") return de ? "Bei Wetterbedingung" : "On weather condition";
    if (s.type === "forecast_trigger") return de ? `Wettervorhersage · ${s.lead_days ?? 1} Tag(e) Vorlauf` : `Weather forecast · ${s.lead_days ?? 1} day(s) lead`;
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
    const resolved = this._data.occurrences
      .filter((item) => item.resolved)
      .sort((a, b) => new Date(b.resolved_at) - new Date(a.resolved_at))
      .slice(0, 60);
    return `<div class="toolbar"><div><h2>Verlauf</h2><p>Erledigte Aufgaben der letzten 90 Tage</p></div></div>
      ${resolved.length ? `<div class="timeline">${resolved.map((item) => {
        const attachments = this._data.attachments?.[item.id] || [];
        const evidence = attachments.length
          ? `<span class="history-evidence has-evidence" aria-label="${attachments.length} Anhänge vorhanden">📎 ${attachments.length}</span>`
          : `<span class="history-evidence no-evidence">Ohne Anhang</span>`;
        const status = item.status === "cancelled" ? "Abgebrochen" : this._t("Erledigt");
        let completedBy = "";
        if (item.completed_by && this._data.people[item.completed_by]) {
          const preposition = householdTasksLocale(this._hass) === "de" ? "von" : "by";
          completedBy = ` · ${preposition} ${this._e(this._data.people[item.completed_by].name)}`;
        }
        if (item.completion_source === "automatic") completedBy += ` · ${this._t("automatisch gutgeschrieben")}`;
        return `<div class="history-row"><span class="check">✓</span><div class="history-main"><h3>${this._e(this._plainTitle(item.title))}</h3>
        <p>${status} ${new Date(item.resolved_at).toLocaleString(this._locale(), { dateStyle: "medium", timeStyle: "short" })}
        ${completedBy}</p></div>
        <div class="history-actions">${evidence}<button data-task-history="${this._e(item.id)}">Akte öffnen</button></div></div>`;
      }).join("")}</div>`
      : `<div class="empty card"><h2>Noch kein Verlauf</h2><p>Erledigte Aufgaben erscheinen hier.</p></div>`}`;
  }

  _renderSettings() {
    const stages = this._data.defaults?.escalation || [];
    const printer = this._data.monitors?.printers || {};
    const detectedPrinters = this._data.detected_printers || [];
    const nfcFeedback = this._data.defaults?.nfc_feedback || { mode: "always", recipients: "scanner" };
    const weeklySummary = this._data.defaults?.weekly_summary || { enabled: false, weekday: "sun", time: "18:00:00" };
    const resources = this._data.monitors?.resources || {};
    const digest = this._data.defaults?.notification_digest || { enabled: false, time: "17:30:00", minimum_tasks: 2 };
    const suggestions = this._data.discovery_suggestions || [];
    const householdMode = this._data.household_mode || { mode: "normal", policy: "pause" };
    const caldav = this._data.caldav || null;
    const health = structuredClone(this._data.configuration_health || { status: "ok", findings: [] });
    if (this._referencesLoaded) {
      const registeredTags = new Set((this._references.tags || []).map((tag) => tag.tag_id || tag.id));
      for (const [taskId, task] of Object.entries(this._data.tasks || {})) {
        if (task.nfc?.tag_id && !registeredTags.has(task.nfc.tag_id)) {
          health.findings.push({ severity: "warning", message: `NFC-Tag ${task.nfc.tag_id} aus „${task.name || taskId}“ ist nicht registriert.` });
        }
      }
      if (health.status === "ok" && health.findings.some((item) => item.severity === "warning")) health.status = "warning";
    }
    const modeControls = this._modeControls(householdMode);
    const healthMarkup = this._healthMarkup(health);
    const discoveryMarkup = this._discoveryMarkup(suggestions);
    const digestChecked = digest.enabled ? "checked" : "";
    return `<div class="toolbar"><div><h2>Einstellungen</h2><p>Globale Regeln und Datenquelle</p></div></div>
      <article class="settings-card mode-card">
        <h3>Urlaubs- und Gastmodus</h3>
        <p>Steuert zentral, ob automatische Aufgaben normal laufen, reduziert, pausiert oder an eine Vertretung gegeben werden.</p>
        ${modeControls}
      </article>
      <article class="settings-card health-card">
        <div class="settings-heading"><div><h3>Konfigurations-Gesundheit</h3><p>Prüft Entitäten, Benachrichtigungsdienste, NFC-Zuordnungen und Abhängigkeiten.</p></div>
        ${this._data.is_admin ? `<button id="refresh-health">Neu prüfen</button>` : ""}</div>
        ${healthMarkup}
      </article>
      ${this._caldavSettings(caldav)}
      <article class="settings-card">
        <h3>Home-Assistant-Autodiscovery</h3>
        <p>Lokale Entitäten werden auf mögliche Geräte-, Kalender-, Batterie- und Wartungsregeln geprüft. Es werden keine Daten übertragen.</p>
        ${discoveryMarkup}
      </article>
      <article class="settings-card">
        <h3>Intelligente Benachrichtigungsbündelung</h3>
        <p>Routinehinweise werden pro Person gesammelt. Kritische Aufgaben, Hilferufe und offene Übernahmen bleiben sofort sichtbar.</p>
        ${this._data.is_admin ? `<form id="notification-digest-form" class="form-grid">
          <label class="checkbox full"><input name="enabled" type="checkbox" ${digestChecked}> Routinehinweise bündeln</label>
          <label>Zustellzeit<input name="time" type="time" step="1" value="${this._e(digest.time || "17:30:00")}"></label>
          <label>Ab dieser Anzahl<input name="minimum_tasks" type="number" min="1" max="20" value="${Number(digest.minimum_tasks || 2)}"></label>
          <div class="full"><button class="primary" type="submit">Bündelung speichern</button></div>
        </form>` : ""}
      </article>
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
        <div class="info-row"><span>Aufgabenspeicher</span><code>nativ · Schema ${Number(this._data.task_store?.schema_version || this._data.task_schema_version || 1)}</code></div>
        <div class="info-row"><span>Letzte Prüfung</span><span>${this._data.last_check ? new Date(this._data.last_check).toLocaleString(this._locale()) : "noch nicht erfolgt"}</span></div>
        ${this._data.is_admin ? `<div class="config-transfer"><h3>Konfiguration sichern</h3>
          <p>Export enthält Personen, Vorlagen, Standardregeln und Monitore. Laufende Aufgaben und Verlauf bleiben unberührt.</p>
          <div class="actions"><button id="export-config">Exportieren</button><button id="import-config">Importieren</button>
          <input id="import-file" class="hidden" type="file" accept="application/json,.json"></div></div>` : ""}
        ${this._data.is_admin ? `<button id="reset-config" class="danger-button">Auf Ausgangswerte zurücksetzen</button>` : ""}
      </article>`;
  }

  _caldavCredentialRow(item) {
    let person = this._data.people[item.person_id]?.name || item.person_id || "–";
    if (item.scope === "household") person = "Gesamter Haushalt";
    const lastUsed = item.last_used_at ? new Date(item.last_used_at).toLocaleString(this._locale()) : "noch nie";
    const expires = item.expires_at ? new Date(item.expires_at).toLocaleString(this._locale()) : "ohne Ablauf";
    const permission = item.permission === "read_write" ? "Lesen und Schreiben" : "Nur Lesen";
    const scope = item.scope === "household" ? "Haushalt" : "Persönlich";
    const revoke = this._data.is_admin
      ? `<button type="button" class="danger-button" data-revoke-caldav="${this._e(item.id)}">Widerrufen</button>`
      : "";
    return `<div class="caldav-credential">
      <div><strong>${this._e(item.label)}</strong><small>${this._e(item.username)} · ${this._e(person)}</small><small>${permission} · ${scope}</small><small>Zuletzt verwendet: ${this._e(lastUsed)} · ${this._e(expires)}</small></div>
      ${revoke}
    </div>`;
  }

  _caldavAdminForms(settings, people) {
    if (!this._data.is_admin) return "";
    return `<details class="advanced-fields" open><summary>Serveroptionen</summary>
      <form id="caldav-settings-form" class="form-grid">
        <label class="checkbox full"><input name="enabled" type="checkbox" ${settings.enabled ? "checked" : ""}> CalDAV-Server aktivieren<span class="hint">Ohne gültiges App-Passwort ist auch ein aktiver Server nicht nutzbar.</span></label>
        <label class="checkbox full"><input name="require_tls" type="checkbox" ${settings.require_tls ? "checked" : ""}> HTTPS erzwingen<span class="hint">Für produktive Systeme unbedingt aktiviert lassen. Nur für isolierte lokale Tests abschalten.</span></label>
        <label>Listenname<input name="calendar_name" maxlength="100" value="${this._e(settings.calendar_name || "Household Tasks")}"></label>
        <label>Farbe<input name="calendar_color" pattern="#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?" value="${this._e(settings.calendar_color || "#03A9F4FF")}"></label>
        <label class="full">Beschreibung<input name="calendar_description" maxlength="500" value="${this._e(settings.calendar_description || "")}"></label>
        <label>Erledigte Aufgaben anzeigen<input name="expose_completed_days" type="number" min="0" max="3650" value="${Number(settings.expose_completed_days ?? 90)}"><span class="hint">Tage; 0 blendet abgeschlossene Aufgaben sofort aus.</span></label>
        <label>Standard-Erinnerung<input name="default_reminder_minutes" type="number" min="0" max="525600" value="${Number(settings.default_reminder_minutes || 0)}"><span class="hint">Minuten vor Fälligkeit; 0 erzeugt keinen zusätzlichen VALARM.</span></label>
        <label class="checkbox"><input name="allow_client_create" type="checkbox" ${settings.allow_client_create ? "checked" : ""}> Aufgaben im Client anlegen</label>
        <label class="checkbox"><input name="allow_client_update" type="checkbox" ${settings.allow_client_update ? "checked" : ""}> Aufgaben im Client ändern/erledigen</label>
        <label class="checkbox"><input name="allow_client_delete" type="checkbox" ${settings.allow_client_delete ? "checked" : ""}> Aufgaben im Client löschen<span class="hint">Löschen wird revisionssicher als Abbruch protokolliert.</span></label>
        <input name="delete_mode" type="hidden" value="cancel">
        <div class="full"><button class="primary" type="submit">CalDAV-Einstellungen speichern</button></div>
      </form>
    </details>
    <details class="advanced-fields"><summary>App-Passwort anlegen</summary>
      <form id="caldav-credential-form" class="form-grid">
        <label>Bezeichnung<input name="label" required maxlength="100" placeholder="Mein iPhone"></label>
        <label>Person<select name="person_id"><option value="">Keine feste Person</option>${people.map(([id, person]) => `<option value="${this._e(id)}">${this._e(person.name)}</option>`).join("")}</select><span class="hint">Für persönliche Listen und neue Aufgaben erforderlich.</span></label>
        <label>Umfang<select name="scope"><option value="personal">Persönliche und übernehmbare Aufgaben</option><option value="household">Alle Haushaltsaufgaben</option></select></label>
        <label>Berechtigung<select name="permission"><option value="read_write">Lesen und Schreiben</option><option value="read_only">Nur Lesen</option></select></label>
        <label>Ablauf (optional)<input name="expires_at" type="datetime-local"></label>
        <label class="checkbox"><input name="include_claimable" type="checkbox" checked> Offene übernehmbare Aufgaben anzeigen</label>
        <label class="checkbox full"><input name="complete_checklist_on_parent" type="checkbox" checked> Beim Erledigen der Hauptaufgabe offene Checklistenpunkte mit erledigen<span class="hint">Nützlich für Clients, die CalDAV-Unteraufgaben nicht vollständig darstellen.</span></label>
        <div class="full"><button class="primary" type="submit">App-Passwort erzeugen</button></div>
      </form>
    </details>`;
  }

  _caldavSettings(caldav) {
    if (!caldav) return `<article class="settings-card"><h3>CalDAV</h3><p>Der CalDAV-Dienst wird noch initialisiert.</p></article>`;
    const settings = caldav.settings || {};
    const credentials = caldav.credentials || [];
    const people = Object.entries(this._data.people || {});
    const credentialRows = credentials.length
      ? credentials.map((item) => this._caldavCredentialRow(item)).join("")
      : `<p class="hint">Noch kein App-Passwort angelegt. Ohne Zugangsdaten bleibt der Server von außen unzugänglich.</p>`;
    return `<article class="settings-card caldav-card">
      <div class="settings-heading"><div><h3>CalDAV für Apple Erinnerungen</h3><p>Bidirektionale, personengebundene VTODO-Synchronisation mit Offline-Konfliktschutz, Checklisten-Unteraufgaben und einmaligen App-Passwörtern.</p></div><span class="status ${settings.enabled ? "home" : ""}">${settings.enabled ? "Aktiv" : "Aus"}</span></div>
      <div class="info-row"><span>Server-URL</span><code>${this._e(caldav.server_url)}</code></div>
      <div class="info-row"><span>Protokoll</span><span>CalDAV · VTODO · Sync-Tokens · ETags</span></div>
      ${settings.require_tls && !String(caldav.server_url || "").startsWith("https://") ? `<div class="health-summary warning">HTTPS ist vorgeschrieben, aber die konfigurierte Home-Assistant-URL ist nicht HTTPS. Externer Zugriff wird abgewiesen.</div>` : ""}
      ${this._caldavAdminForms(settings, people)}
      <h4>Aktive Zugänge</h4><div class="caldav-credentials">${credentialRows}</div>
      <details class="advanced-fields"><summary>Einrichtung und Synchronisationsverhalten</summary>
        <ol class="setup-steps"><li>In iOS/iPadOS <strong>Einstellungen → Apps → Erinnerungen → Accounts → Account hinzufügen → Andere → CalDAV-Account</strong> öffnen.</li><li>Als Server die oben angezeigte HTTPS-URL und das einmalig erzeugte Paar aus Benutzername und App-Passwort verwenden.</li><li>SSL aktivieren. Änderungen werden offline vorgemerkt und nach Wiederverbindung synchronisiert.</li><li>Bei parallelen Änderungen verhindert der ETag eine stille Überschreibung; der Client lädt die aktuelle Fassung neu.</li></ol>
        <p class="hint">Jedes Gerät erhält ein eigenes, widerrufbares App-Passwort. Verwende niemals dein Home-Assistant-Kennwort.</p>
      </details>
    </article>`;
  }

  _selected(actual, expected) {
    return actual === expected ? "selected" : "";
  }

  _formText(values, name, fallback = "") {
    const value = values.get(name);
    return typeof value === "string" ? value : fallback;
  }

  _modeControls(mode) {
    if (!this._data.is_admin) {
      return `<div class="info-row"><span>Aktiv</span><strong>${this._e(mode.mode)}</strong></div>`;
    }
    const delegates = Object.entries(this._data.people).map(([id, person]) =>
      `<option value="${this._e(id)}" ${this._selected(mode.delegate_to, id)}>${this._e(person.name)}</option>`
    ).join("");
    const until = mode.until ? this._localDateTimeValue(new Date(mode.until)) : "";
    return `<form id="household-mode-form" class="form-grid">
      <label>Betriebsmodus<select name="mode">
        <option value="normal" ${this._selected(mode.mode, "normal")}>Normal</option>
        <option value="vacation" ${this._selected(mode.mode, "vacation")}>Urlaub</option>
        <option value="guest" ${this._selected(mode.mode, "guest")}>Gäste</option>
      </select><span class="hint">Im Gastmodus können eigene Gastaufgaben aktiv und private Routinen ausgeschaltet werden.</span></label>
      <label>Urlaubsstrategie<select name="policy">
        <option value="pause" ${this._selected(mode.policy, "pause")}>Automatik pausieren</option>
        <option value="reduce" ${this._selected(mode.policy, "reduce")}>Nur hohe Prioritäten</option>
        <option value="delegate" ${this._selected(mode.policy, "delegate")}>An Vertretung übergeben</option>
      </select><span class="hint">Einzelne Vorlagen können diese Strategie überschreiben.</span></label>
      <label>Vertretung<select name="delegate_to"><option value="">Keine</option>${delegates}</select></label>
      <label>Automatisch beenden<input type="datetime-local" name="until" value="${until}"></label>
      <label class="full">Notiz<input name="note" value="${this._e(mode.note || "")}" placeholder="Sommerurlaub, Besuch am Wochenende …"></label>
      <div class="full"><button class="primary" type="submit">Modus anwenden</button></div>
    </form>`;
  }

  _healthMarkup(health) {
    const summary = health.status === "ok"
      ? "Keine Probleme erkannt"
      : `${health.findings.length} Hinweise gefunden`;
    if (!health.findings.length) {
      return `<div class="health-summary ${this._e(health.status)}">${summary}</div>`;
    }
    const findings = health.findings.map((finding, index) => {
      const action = finding.action ? `<button data-health-fix="${index}">Beheben</button>` : "";
      return `<div class="${this._e(finding.severity)}"><strong>${this._e(finding.severity)}</strong><span>${this._e(finding.message)}</span>${action}</div>`;
    }).join("");
    return `<div class="health-summary ${this._e(health.status)}">${summary}</div><div class="health-list">${findings}</div>`;
  }

  _discoveryMarkup(suggestions) {
    if (!suggestions.length) return '<p class="positive">Keine neuen passenden Entitäten erkannt.</p>';
    const entries = suggestions.slice(0, 12).map((item) => {
      const action = this._data.is_admin
        ? `<button data-install-discovery="${this._e(item.id)}">Einrichten</button>`
        : "";
      return `<div><span><strong>${this._e(item.name)}</strong><small>${this._e(item.entity_id)} · ${this._e(item.reason)}</small></span>${action}</div>`;
    }).join("");
    return `<div class="discovery-list">${entries}</div>`;
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
    this.shadowRoot.querySelectorAll("[data-view]").forEach((link) => link.onclick = (event) => {
      if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      this._navigateToView(link.dataset.view);
    });
    this.shadowRoot.querySelector(".refresh")?.addEventListener("click", () => this._load());
    this.shadowRoot.querySelector(".search-button")?.addEventListener("click", () => this._showCommandPalette());
    this.shadowRoot.querySelector(".undo-button")?.addEventListener("click", async () => {
      const result = await this._call("undo");
      this._toast(`${result?.undone || "Aktion"} rückgängig gemacht`);
    });
    this.shadowRoot.querySelector("#setup-wizard")?.addEventListener("click", () => {
      try {
        this._showSetupWizard();
      } catch (error) {
        this._toast(this._errorText(error), true);
      }
    });
    this.shadowRoot.querySelector("#quick-task")?.addEventListener("click", () => this._showQuickTask());
    this.shadowRoot.querySelector("#smart-quick-task")?.addEventListener("click", () => this._showQuickTask(true));
    this.shadowRoot.querySelector("#plan-today")?.addEventListener("click", () => this._showTodayPlanner());
    this.shadowRoot.querySelector(".context-add")?.addEventListener("click", () => {
      if (this._view === "tasks" && this._data.is_admin) this._showTaskEditor();
      else if (this._view === "people" && this._data.is_admin) this._showPersonEditor();
      else this._showQuickTask(true);
    });
    this._bindBulkActions();
    this._bindWeekDragDrop();
    this.shadowRoot.querySelectorAll("[data-launch-stack]").forEach((button) => button.onclick = async () => {
      const result = await this._call("launch_task_stack", { stack_id: button.dataset.launchStack });
      this._toast(`${result.stack_created?.length || 0} Aufgaben gestartet`);
    });
    this.shadowRoot.querySelectorAll("[data-natural-move]").forEach((button) => {
      button.onclick = () => this._showNaturalMove(button.dataset.naturalMove);
    });
    this.shadowRoot.querySelectorAll("[data-attachment]").forEach((button) => {
      button.onclick = () => this._showAttachments(button.dataset.attachment);
    });
    this.shadowRoot.querySelectorAll("[data-device-file]").forEach((button) => {
      button.onclick = () => this._showDeviceFile(button.dataset.deviceFile);
    });
    this.shadowRoot.querySelectorAll("[data-card-occurrence]").forEach((card) => {
      card.addEventListener("contextmenu", (event) => {
        event.preventDefault();
        card.querySelector(".more-actions").open = true;
      });
      let longPress;
      card.addEventListener("pointerdown", (event) => {
        if (event.pointerType !== "touch") return;
        longPress = setTimeout(() => { card.querySelector(".more-actions").open = true; }, 550);
      });
      ["pointerup", "pointercancel", "pointermove"].forEach((name) => card.addEventListener(name, () => clearTimeout(longPress)));
    });
    this._bindActionMenus();
    this.shadowRoot.querySelectorAll("[data-jump-occurrence]").forEach((button) => button.onclick = () => {
      const card = this.shadowRoot.querySelector(`[data-complete="${CSS.escape(button.dataset.jumpOccurrence)}"],[data-claim="${CSS.escape(button.dataset.jumpOccurrence)}"]`)?.closest(".task-card");
      card?.scrollIntoView({ behavior: "smooth", block: "center" });
      card?.classList.add("highlight");
      setTimeout(() => card?.classList.remove("highlight"), 1600);
    });
    this.shadowRoot.querySelectorAll("[data-complete]").forEach((b) => b.onclick = async () => {
      if (await this._confirm("Aufgabe als erledigt markieren?")) {
        await this._call("complete", { occurrence_id: b.dataset.complete });
        this._toast("Aufgabe erledigt");
      }
    });
    this.shadowRoot.querySelectorAll("[data-checklist-occurrence]").forEach((input) => input.onchange = async () => {
      try {
        await this._call("set_checklist_item", {
          occurrence_id: input.dataset.checklistOccurrence,
          item_id: input.dataset.checklistItem,
          completed: input.checked,
          expected_revision: Number(input.dataset.revision),
        });
        this._toast(input.checked ? "Checklistenpunkt erledigt" : "Checklistenpunkt wieder geöffnet");
      } catch (error) {
        input.checked = !input.checked;
        this._toast(this._errorText(error), true);
      }
    });
    this.shadowRoot.querySelectorAll("[data-task-status]").forEach((button) => button.onclick = async () => {
      if (button.dataset.status === "cancelled" && !await this._confirm("Aufgabe wirklich abbrechen?")) return;
      await this._call("set_status", {
        occurrence_id: button.dataset.taskStatus,
        status: button.dataset.status,
        expected_revision: Number(button.dataset.revision),
      });
      this._toast("Aufgabenstatus aktualisiert");
    });
    this.shadowRoot.querySelectorAll("[data-task-history]").forEach((button) => {
      button.onclick = () => this._showTaskHistory(button.dataset.taskHistory);
    });
    this.shadowRoot.querySelectorAll("[data-claim]").forEach((b) => b.onclick = async () => {
      await this._call("claim", { occurrence_id: b.dataset.claim });
      this._toast("Aufgabe übernommen");
    });
    this.shadowRoot.querySelectorAll("[data-snooze]").forEach((b) => b.onclick = async () => {
      await this._call("snooze", { occurrence_id: b.dataset.snooze, choice: b.dataset.choice });
      this._toast("Aufgabe verschoben");
    });
    this.shadowRoot.querySelectorAll("[data-help]").forEach((b) => b.onclick = async () => {
      await this._call("request_help", { occurrence_id: b.dataset.help });
      this._toast("Hilfe wurde angefragt");
    });
    this.shadowRoot.querySelectorAll("[data-decline]").forEach((b) => b.onclick = async () => {
      await this._call("decline", { occurrence_id: b.dataset.decline });
      this._toast("Aufgabe zur Übernahme weitergegeben");
    });
    this.shadowRoot.querySelectorAll("[data-create]").forEach((b) => b.onclick = async () => {
      await this._call("create", { task_id: b.dataset.create });
      this._toast("Aufgabe wurde erzeugt");
    });
    this.shadowRoot.querySelector("#add-task")?.addEventListener("click", () => this._showTaskEditor());
    this.shadowRoot.querySelector("#manage-stacks")?.addEventListener("click", () => this._showTaskStackEditor());
    this.shadowRoot.querySelector("#add-calendar-task")?.addEventListener("click", () => this._showTaskEditor(null, { scheduleType: "calendar" }));
    this.shadowRoot.querySelector("#open-gallery")?.addEventListener("click", () => {
      try {
        this._showGallery();
      } catch (error) {
        this._toast(this._errorText(error), true);
      }
    });
    this.shadowRoot.querySelectorAll("[data-favorite]").forEach((b) => b.onclick = async () => {
      const result = await this._call("toggle_favorite", { task_id: b.dataset.favorite });
      this._toast(result.favorite_enabled ? "Favorit hinzugefügt" : "Favorit entfernt");
    });
    this.shadowRoot.querySelectorAll("[data-apply-habit]").forEach((button) => button.onclick = async () => {
      const taskId = button.dataset.applyHabit;
      const habit = this._data.habits[taskId];
      const task = structuredClone(this._data.tasks[taskId]);
      if (habit.assignee) {
        task.assignee = habit.assignee;
        task.assignment = { type: "fixed" };
      }
      if (habit.hour != null && ["weekly", "monthly", "yearly", "interval_months", "daily_after_state"].includes(task.schedule?.type)) {
        task.schedule.time = `${String(habit.hour).padStart(2, "0")}:00:00`;
      }
      await this._call("save_task", { task_id: taskId, task });
      this._toast("Gelernte Empfehlung übernommen");
    });
    this.shadowRoot.querySelectorAll("[data-gallery]").forEach((b) => b.onclick = () => {
      try {
        this._showGallery(b.dataset.gallery);
      } catch (error) {
        this._toast(this._errorText(error), true);
      }
    });
    this.shadowRoot.querySelectorAll("[data-explain-task]").forEach((b) => b.onclick = () => this._showWhyNot(b.dataset.explainTask));
    this.shadowRoot.querySelectorAll("[data-edit-task]").forEach((b) => b.onclick = () => this._showTaskEditor(b.dataset.editTask));
    this.shadowRoot.querySelectorAll("[data-pause-task]").forEach((b) => b.onclick = () => this._showTaskPauseDialog(b.dataset.pauseTask));
    this.shadowRoot.querySelectorAll("[data-resume-task]").forEach((b) => b.onclick = async () => {
      const taskId = b.dataset.resumeTask;
      const task = structuredClone(this._data.tasks[taskId]);
      delete task.paused_until;
      await this._call("save_task", { task_id: taskId, task });
      this._toast(this._t("Aufgabe wieder aktiviert"));
    });
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
    this.shadowRoot.querySelector("#household-mode-form")?.addEventListener("submit", (event) => this._saveHouseholdMode(event));
    this.shadowRoot.querySelector("#notification-digest-form")?.addEventListener("submit", (event) => this._saveNotificationDigest(event));
    this.shadowRoot.querySelector("#caldav-settings-form")?.addEventListener("submit", (event) => this._saveCalDAVSettings(event));
    this.shadowRoot.querySelector("#caldav-credential-form")?.addEventListener("submit", (event) => this._createCalDAVCredential(event));
    this.shadowRoot.querySelectorAll("[data-revoke-caldav]").forEach((button) => button.onclick = async () => {
      if (!await this._confirm("Dieses CalDAV-App-Passwort sofort widerrufen? Das Gerät kann danach nicht mehr synchronisieren.")) return;
      await this._call("caldav_revoke_credential", { credential_id: button.dataset.revokeCaldav });
      this._toast("CalDAV-Zugang widerrufen");
    });
    this.shadowRoot.querySelector("#refresh-health")?.addEventListener("click", () => this._load());
    this.shadowRoot.querySelectorAll("[data-health-fix]").forEach((button) => button.onclick = () => {
      const finding = this._data.configuration_health?.findings?.[Number(button.dataset.healthFix)];
      const action = finding?.action;
      if (action?.type === "edit_person") this._showPersonEditor(action.person_id);
      else if (action?.type === "edit_task") this._showTaskEditor(action.task_id);
      else if (action?.type === "open_integration") window.location.href = "/config/integrations/integration/household_tasks";
    });
    this.shadowRoot.querySelectorAll("[data-install-discovery]").forEach((button) => button.onclick = () => this._showDiscoveryInstall(button.dataset.installDiscovery));
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

  _bindActionMenus() {
    this.shadowRoot.querySelectorAll("details.more-actions").forEach((details) => {
      details.addEventListener("toggle", () => {
        const summary = details.querySelector("summary");
        summary?.setAttribute("aria-expanded", String(details.open));
        if (!details.open) {
          details.classList.remove("opens-up");
          details.querySelector(":scope > div")?.style.removeProperty("max-height");
          return;
        }
        this.shadowRoot.querySelectorAll("details.more-actions[open]").forEach((other) => {
          if (other !== details) other.open = false;
        });
        requestAnimationFrame(() => this._positionActionMenu(details));
      });
    });
  }

  _positionOpenActionMenus() {
    this.shadowRoot.querySelectorAll("details.more-actions[open]").forEach((details) => {
      this._positionActionMenu(details);
    });
  }

  _positionActionMenu(details) {
    const summary = details.querySelector("summary");
    const menu = details.querySelector(":scope > div");
    if (!details.open || !summary || !menu) return;
    const trigger = summary.getBoundingClientRect();
    const gap = 6;
    const viewportMargin = 8;
    const roomBelow = window.innerHeight - trigger.bottom - gap - viewportMargin;
    const roomAbove = trigger.top - gap - viewportMargin;
    const opensUp = roomBelow < menu.scrollHeight && roomAbove > roomBelow;
    details.classList.toggle("opens-up", opensUp);
    const available = Math.max(120, opensUp ? roomAbove : roomBelow);
    menu.style.maxHeight = `${Math.floor(available)}px`;
  }

  _bindBulkActions() {
    const selected = () => [...this.shadowRoot.querySelectorAll("[data-select-occurrence]:checked")]
      .map((input) => input.dataset.selectOccurrence);
    const update = () => {
      const count = selected().length;
      const output = this.shadowRoot.querySelector(".bulk-count");
      if (output) output.textContent = `${count} ausgewählt`;
      this.shadowRoot.querySelectorAll("[data-bulk-action]").forEach((button) => { button.disabled = count === 0; });
    };
    this.shadowRoot.querySelector("[data-select-all]")?.addEventListener("change", (event) => {
      this.shadowRoot.querySelectorAll("[data-select-occurrence]").forEach((input) => { input.checked = event.target.checked; });
      update();
    });
    this.shadowRoot.querySelectorAll("[data-select-occurrence]").forEach((input) => input.onchange = update);
    this.shadowRoot.querySelectorAll("[data-bulk-action]").forEach((button) => button.onclick = async () => {
      const ids = selected();
      if (!ids.length) return;
      const result = await this._call("bulk", { occurrence_ids: ids, action: button.dataset.bulkAction });
      const completed = result.bulk_result?.completed?.length || 0;
      const failed = Object.keys(result.bulk_result?.failed || {}).length;
      const failedSuffix = failed ? `, ${failed} fehlgeschlagen` : "";
      this._toast(`${completed} Aufgaben verarbeitet${failedSuffix}`);
    });
  }

  _bindWeekDragDrop() {
    this.shadowRoot.querySelectorAll("[data-drag-occurrence]").forEach((card) => {
      card.ondragstart = (event) => {
        event.dataTransfer.setData("text/plain", card.dataset.dragOccurrence);
        event.dataTransfer.effectAllowed = "move";
      };
    });
    this.shadowRoot.querySelectorAll("[data-drop-date]").forEach((column) => {
      column.ondragover = (event) => {
        event.preventDefault();
        column.classList.add("drag-over");
      };
      column.ondragleave = () => column.classList.remove("drag-over");
      column.ondrop = async (event) => {
        event.preventDefault();
        column.classList.remove("drag-over");
        const occurrenceId = event.dataTransfer.getData("text/plain");
        if (!occurrenceId) return;
        const item = this._data.occurrences.find((entry) => entry.id === occurrenceId);
        const clock = new Date(item?.due || Date.now()).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
        await this._call("move_occurrence", {
          occurrence_id: occurrenceId,
          instruction: `${column.dataset.dropDate} ${clock}`,
        });
        this._toast("Aufgabe neu eingeplant");
      };
    });
  }

  _showNaturalMove(occurrenceId) {
    const modal = this.shadowRoot.querySelector("#modal");
    const returnFocus = this.shadowRoot.activeElement;
    modal.innerHTML = `<div class="backdrop"><div class="modal-card small">
      <div class="modal-head"><div><div class="eyebrow">VERSCHIEBEN</div><h2>Wann passt es besser?</h2></div><button class="icon-button close">×</button></div>
      <form id="natural-move-form" class="form-grid">
        <label class="full">Natürlich formulieren<input name="instruction" autofocus required placeholder="morgen nach dem Essen oder wenn Alex zuhause ist"></label>
        <p class="hint full">Versteht unter anderem heute Abend, morgen 18 Uhr, Wochenende, nächste Woche und Anwesenheit.</p>
        <div class="quick-presets full"><button type="button" data-move-preset="heute Abend">Heute Abend</button><button type="button" data-move-preset="morgen 9 Uhr">Morgen</button><button type="button" data-move-preset="am Wochenende 10 Uhr">Wochenende</button></div>
        <div class="full modal-actions"><button type="button" class="cancel">Abbrechen</button><button class="primary">Verschieben</button></div>
      </form></div></div>`;
    const close = () => { modal.innerHTML = ""; returnFocus?.focus(); };
    modal.querySelector(".close").onclick = close;
    modal.querySelector(".cancel").onclick = close;
    this._activateDialog(modal, close);
    modal.querySelectorAll("[data-move-preset]").forEach((button) => button.onclick = () => {
      modal.querySelector("[name=instruction]").value = button.dataset.movePreset;
    });
    modal.querySelector("#natural-move-form").onsubmit = async (event) => {
      event.preventDefault();
      const instruction = new FormData(event.target).get("instruction").trim();
      const result = await this._call("move_occurrence", { occurrence_id: occurrenceId, instruction });
      close();
      this._toast(result.move_result?.label || "Aufgabe verschoben");
    };
  }

  _showTodayPlanner() {
    const open = this._openOccurrences().slice(0, 20);
    const modal = this.shadowRoot.querySelector("#modal");
    const returnFocus = this.shadowRoot.activeElement;
    modal.innerHTML = `<div class="backdrop"><div class="modal-card">
      <div class="modal-head"><div><div class="eyebrow">TAGESPLANUNG</div><h2>Heute gemeinsam planen</h2></div><button class="icon-button close">×</button></div>
      <form id="today-plan-form">
        <p class="hint">Überfällige und nächste Aufgaben bekommen hier mit einem Schritt ein realistisches Zeitfenster.</p>
        <div class="planner-list">${open.map((item) => `<label class="planner-row">
          <span><strong>${this._e(this._plainTitle(item.title))}</strong><small>${this._e(this._data.people[item.assignee]?.name || "Offen")}</small></span>
          <input type="datetime-local" name="${this._e(item.id)}" value="${this._localDateTimeValue(new Date(item.due))}">
        </label>`).join("") || "<p>Keine offenen Aufgaben.</p>"}</div>
        <div class="modal-actions"><button type="button" class="cancel">Abbrechen</button><button class="primary">Plan übernehmen</button></div>
      </form></div></div>`;
    const close = () => { modal.innerHTML = ""; returnFocus?.focus(); };
    modal.querySelector(".close").onclick = close;
    modal.querySelector(".cancel").onclick = close;
    this._activateDialog(modal, close);
    modal.querySelector("#today-plan-form").onsubmit = async (event) => {
      event.preventDefault();
      const form = new FormData(event.target);
      let changed = 0;
      for (const item of open) {
        const formValue = form.get(item.id);
        const value = typeof formValue === "string" ? formValue : "";
        if (!value || new Date(value).getTime() === new Date(item.due).getTime()) continue;
        await this._hass.callWS({
          type: "household_tasks/move_occurrence",
          occurrence_id: item.id,
          instruction: `${value.slice(0, 10)} ${value.slice(11, 16)}`,
        });
        changed += 1;
      }
      close();
      await this._load();
      this._toast(`${changed} Aufgaben neu geplant`);
    };
  }

  _showBatchCapture() {
    const modal = this.shadowRoot.querySelector("#modal");
    const returnFocus = this.shadowRoot.activeElement;
    modal.innerHTML = `<div class="backdrop"><div class="modal-card">
      <div class="modal-head"><div><div class="eyebrow">MEHRFACHSCHNELLERFASSUNG</div><h2>Mehrere Aufgaben auf einmal</h2></div><button class="icon-button close">×</button></div>
      <form id="batch-task-form">
        <label>Eine Aufgabe pro Zeile oder mit Semikolon getrennt<textarea name="text" rows="7" autofocus required placeholder="Müll morgen 18 Uhr an Alex&#10;Filter Samstag an Sam&#10;Pflanzen heute Abend"></textarea></label>
        <button type="button" data-preview-batch>Vorschau erzeugen</button>
        <div class="batch-preview" aria-live="polite"></div>
        <div class="modal-actions"><button type="button" class="cancel">Abbrechen</button><button class="primary">Alle gültigen anlegen</button></div>
      </form></div></div>`;
    const close = () => { modal.innerHTML = ""; returnFocus?.focus(); };
    modal.querySelector(".close").onclick = close;
    modal.querySelector(".cancel").onclick = close;
    this._activateDialog(modal, close);
    const preview = async () => {
      const text = modal.querySelector("[name=text]").value;
      const rows = await this._hass.callWS({ type: "household_tasks/task_batch_preview", text });
      modal.querySelector(".batch-preview").innerHTML = rows.map((row) => `<div class="${row.missing.length ? "error" : "ok"}"><strong>${this._e(row.name || "Unvollständig")}</strong><span>${this._e(row.assignee_name || "Person fehlt")} · ${new Date(row.due).toLocaleString(this._locale())}</span></div>`).join("");
      return rows;
    };
    modal.querySelector("[data-preview-batch]").onclick = preview;
    modal.querySelector("#batch-task-form").onsubmit = async (event) => {
      event.preventDefault();
      await preview();
      const result = await this._call("create_batch", { text: new FormData(event.target).get("text") });
      close();
      this._toast(`${result.batch_result?.created?.length || 0} Aufgaben angelegt`);
    };
  }

  async _showTaskHistory(occurrenceId) {
    const occurrence = this._data.occurrences.find((item) => item.id === occurrenceId);
    if (!occurrence) return this._toast("Aufgabe nicht gefunden.", true);
    const events = await this._hass.callWS({ type: "household_tasks/task_history", occurrence_id: occurrenceId });
    const attachments = this._data.attachments?.[occurrenceId] || [];
    const modal = this.shadowRoot.querySelector("#modal");
    const returnFocus = this.shadowRoot.activeElement;
    const labels = {
      task_created: "Aufgabe erstellt",
      task_status_changed: "Status geändert",
      checklist_item_completed: "Checklistenpunkt erledigt",
      checklist_item_reopened: "Checklistenpunkt wieder geöffnet",
      task_dependencies_changed: "Abhängigkeiten geändert",
      task_completed: "Aufgabe erledigt",
      task_cancelled: "Aufgabe abgebrochen",
      task_claimed: "Aufgabe übernommen",
    };
    const assignee = this._data.people[occurrence.assignee]?.name || "Offen";
    const completedBy = this._data.people[occurrence.completed_by]?.name || occurrence.completed_by || "–";
    const description = occurrence.description || occurrence.task?.description || "Keine Beschreibung hinterlegt.";
    const checklistMarkup = this._historyChecklistMarkup(occurrence.checklist || []);
    const eventMarkup = this._historyEventsMarkup(events, labels);
    const attachmentMarkup = this._historyAttachmentsMarkup(attachments);
    modal.innerHTML = `<div class="backdrop"><div class="modal-card history-record">
      <div class="modal-head"><div><div class="eyebrow">AUFGABENAKTE</div><h2>${this._e(this._plainTitle(occurrence?.title || "Aufgabe"))}</h2></div><button class="icon-button close">×</button></div>
      <div class="history-record-grid">
        <section><h3>Abschlussdetails</h3><dl class="history-facts">
          <div><dt>Status</dt><dd>${occurrence.status === "cancelled" ? "Abgebrochen" : "Erledigt"}</dd></div>
          <div><dt>Zuständig</dt><dd>${this._e(assignee)}</dd></div>
          <div><dt>Abgeschlossen von</dt><dd>${this._e(completedBy)}</dd></div>
          <div><dt>Abschlussart</dt><dd>${occurrence.completion_source === "automatic" ? "Automatische Gutschrift" : "Manuell bestätigt"}</dd></div>
          <div><dt>Erstellt</dt><dd>${occurrence.created_at ? new Date(occurrence.created_at).toLocaleString(this._locale()) : "–"}</dd></div>
          <div><dt>Fällig</dt><dd>${occurrence.due ? new Date(occurrence.due).toLocaleString(this._locale()) : "–"}</dd></div>
          <div><dt>Abgeschlossen</dt><dd>${occurrence.resolved_at ? new Date(occurrence.resolved_at).toLocaleString(this._locale()) : "–"}</dd></div>
        </dl></section>
        <section><h3>Beschreibung</h3><p class="preserve-lines">${this._e(description)}</p></section>
      </div>
      <section><h3>Checkliste</h3><div class="history-checklist">${checklistMarkup}</div></section>
      <section><h3>Fotos und Belege <span class="history-count">${attachments.length}</span></h3><div class="attachment-list readonly-attachments">${attachmentMarkup}</div></section>
      <section><h3>Änderungsverlauf</h3><div class="task-event-list">${eventMarkup}</div></section>
      <div class="modal-actions"><button type="button" class="close-bottom">Schließen</button></div>
    </div></div>`;
    const close = () => { modal.innerHTML = ""; returnFocus?.focus(); };
    modal.querySelector(".close").onclick = close;
    modal.querySelector(".close-bottom").onclick = close;
    this._activateDialog(modal, close);
    modal.querySelectorAll("[data-open-attachment]").forEach((button) => button.onclick = () => this._openAttachment(occurrenceId, button.dataset.openAttachment));
  }

  _historyEventsMarkup(events, labels) {
    return [...events].reverse().map((event) => {
      const details = this._historyEventDetails(event.details);
      const actor = event.actor ? this._data.people[event.actor]?.name || event.actor : "";
      const actorMarkup = actor ? `<small>${this._e(actor)}</small>` : "";
      const detailsMarkup = details ? `<small class="event-details">${this._e(details)}</small>` : "";
      return `<article><strong>${this._e(labels[event.type] || event.type)}</strong><time>${new Date(event.occurred_at).toLocaleString(this._locale())}</time>${actorMarkup}${detailsMarkup}</article>`;
    }).join("") || "<p>Noch keine Verlaufsdaten.</p>";
  }

  _historyAttachmentsMarkup(attachments) {
    return attachments.map((item) => `<div><button data-open-attachment="${this._e(item.id)}">${this._e(item.name)} <small>${this._formatFileSize(item.size)}</small></button></div>`).join("") || "<p>Für diese Aufgabe wurde kein Anhang hinterlegt.</p>";
  }

  _historyChecklistMarkup(checklist) {
    return checklist.map((item) => {
      const completedAt = item.completed_at ? new Date(item.completed_at).toLocaleString(this._locale()) : "";
      const completedAtMarkup = completedAt ? `<small>${completedAt}</small>` : "";
      return `<div><span aria-hidden="true">${item.completed ? "✓" : "○"}</span><span>${this._e(item.title)}</span>${completedAtMarkup}</div>`;
    }).join("") || "<p>Keine Checkliste hinterlegt.</p>";
  }

  _historyEventDetails(details) {
    if (!details || typeof details !== "object") return "";
    const labels = { status: "Status", item_id: "Checklistenpunkt", dependencies: "Abhängigkeiten", due: "Fällig", from: "Von", to: "An", reason: "Grund", instruction: "Verschoben", kind: "Art", task_id: "Vorlage", completed_dependency: "Erledigte Abhängigkeit" };
    return Object.entries(details).map(([key, value]) => {
      const rendered = Array.isArray(value) ? value.join(", ") : String(value ?? "");
      return `${labels[key] || key}: ${rendered}`;
    }).join(" · ");
  }

  _formatFileSize(size) {
    const bytes = Number(size || 0);
    return bytes >= 1000000 ? `${(bytes / 1000000).toFixed(1)} MB` : `${Math.ceil(bytes / 1000)} KB`;
  }

  async _openAttachment(occurrenceId, attachmentId) {
    let offset = 0;
    let item;
    const chunks = [];
    do {
      item = await this._hass.callWS({ type: "household_tasks/attachment_content_chunk", occurrence_id: occurrenceId, attachment_id: attachmentId, offset });
      chunks.push(item.content);
      offset = item.next_offset;
    } while (!item.complete);
    const binary = atob(chunks.join(""));
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.codePointAt(index);
    const url = URL.createObjectURL(new Blob([bytes], { type: item.mime_type }));
    window.open(url, "_blank", "noopener");
    window.setTimeout(() => URL.revokeObjectURL(url), 60000);
  }

  _attachmentChunkBase64(bytes) {
    let binary = "";
    for (let offset = 0; offset < bytes.length; offset += 32768) {
      binary += String.fromCodePoint(...bytes.subarray(offset, offset + 32768));
    }
    return btoa(binary);
  }

  async _uploadAttachment(occurrenceId, file) {
    const bytes = new Uint8Array(await file.arrayBuffer());
    const totalChunks = Math.ceil(bytes.length / HT_ATTACHMENT_CHUNK_BYTES);
    const uploadId = globalThis.crypto.randomUUID();
    for (let chunkIndex = 0; chunkIndex < totalChunks; chunkIndex += 1) {
      const start = chunkIndex * HT_ATTACHMENT_CHUNK_BYTES;
      const content = this._attachmentChunkBase64(bytes.subarray(start, start + HT_ATTACHMENT_CHUNK_BYTES));
      await this._hass.callWS({
        type: "household_tasks/add_attachment_chunk",
        occurrence_id: occurrenceId,
        upload_id: uploadId,
        name: file.name,
        mime_type: file.type,
        chunk_index: chunkIndex,
        total_chunks: totalChunks,
        content,
      });
    }
    await this._load();
  }

  _showAttachments(occurrenceId) {
    const items = this._data.attachments?.[occurrenceId] || [];
    const modal = this.shadowRoot.querySelector("#modal");
    const returnFocus = this.shadowRoot.activeElement;
    modal.innerHTML = `<div class="backdrop"><div class="modal-card small">
      <div class="modal-head"><div><div class="eyebrow">DOKUMENTATION</div><h2>Fotos und Belege</h2></div><button class="icon-button close">×</button></div>
      <div class="attachment-list">${items.map((item) => `<div><button data-open-attachment="${this._e(item.id)}">${this._e(item.name)} <small>${this._formatFileSize(item.size)}</small></button><button data-delete-attachment="${this._e(item.id)}" aria-label="Anhang löschen">×</button></div>`).join("") || "<p>Noch keine Anhänge.</p>"}</div>
      <label>Datei hinzufügen<input type="file" accept="image/jpeg,image/png,image/webp,application/pdf" data-attachment-file></label>
      <p class="hint">Lokal in Home Assistant gespeichert, maximal 20 MB pro Datei, 100 MB und zehn Dateien je Aufgabe.</p>
      <div class="modal-actions"><button type="button" class="close-bottom">Schließen</button></div>
    </div></div>`;
    const close = () => { modal.innerHTML = ""; returnFocus?.focus(); };
    modal.querySelector(".close").onclick = close;
    modal.querySelector(".close-bottom").onclick = close;
    this._activateDialog(modal, close);
    modal.querySelector("[data-attachment-file]").onchange = async (event) => {
      const file = event.target.files[0];
      if (!file) return;
      if (file.size > HT_ATTACHMENT_MAX_BYTES) return this._toast("Datei ist größer als 20 MB.", true);
      const totalSize = items.reduce((total, item) => total + Number(item.size || 0), 0);
      if (totalSize + file.size > HT_ATTACHMENT_TOTAL_MAX_BYTES) return this._toast("Alle Anhänge dieser Aufgabe dürfen zusammen höchstens 100 MB groß sein.", true);
      await this._uploadAttachment(occurrenceId, file);
      close();
      this._showAttachments(occurrenceId);
    };
    modal.querySelectorAll("[data-open-attachment]").forEach((button) => button.onclick = () => this._openAttachment(occurrenceId, button.dataset.openAttachment));
    modal.querySelectorAll("[data-delete-attachment]").forEach((button) => button.onclick = async () => {
      await this._call("delete_attachment", { occurrence_id: occurrenceId, attachment_id: button.dataset.deleteAttachment });
      close();
      this._showAttachments(occurrenceId);
    });
  }

  _showDeviceFile(taskId) {
    const task = structuredClone(this._data.tasks[taskId]);
    const device = task.device || {};
    const state = device.entity_id ? this._hass.states[device.entity_id] : null;
    const history = this._data.occurrences
      .filter((item) => item.task_id === taskId && item.resolved)
      .sort((a, b) => new Date(b.resolved_at) - new Date(a.resolved_at))
      .slice(0, 5);
    const modal = this.shadowRoot.querySelector("#modal");
    const returnFocus = this.shadowRoot.activeElement;
    modal.innerHTML = `<div class="backdrop"><div class="modal-card small">
      <div class="modal-head"><div><div class="eyebrow">GERÄTEAKTE</div><h2>${this._e(task.name)}</h2></div><button class="icon-button close">×</button></div>
      <div class="device-summary"><p><strong>Aktueller Zustand:</strong> ${this._e(state?.state || "keine Entität verknüpft")} ${this._e(state?.attributes?.unit_of_measurement || "")}</p>
      <p><strong>Letzte Erledigungen:</strong> ${history.length ? history.map((item) => new Date(item.resolved_at).toLocaleDateString(this._locale())).join(", ") : "noch keine"}</p>
      ${device.manual_url ? `<p><a href="${this._e(device.manual_url)}" target="_blank" rel="noopener">Handbuch öffnen</a></p>` : ""}</div>
      <form id="device-file-form" class="form-grid">
        <label class="full">Home-Assistant-Entität${this._entityInput("entity_id", device.entity_id || "", [], { placeholder: "sensor.gerät_status" })}</label>
        <label>Modell<input name="model" value="${this._e(device.model || "")}"></label>
        <label>Ersatzteil<input name="replacement_part" value="${this._e(device.replacement_part || "")}"></label>
        <label class="full">Handbuch-URL<input name="manual_url" type="url" pattern="https://.*" placeholder="https://…" value="${this._e(device.manual_url || "")}"></label>
        <label class="full">Notizen<textarea name="notes" rows="3">${this._e(device.notes || "")}</textarea></label>
        <div class="full modal-actions"><button type="button" class="cancel">Abbrechen</button><button class="primary">Speichern</button></div>
      </form></div></div>`;
    const close = () => { modal.innerHTML = ""; returnFocus?.focus(); };
    modal.querySelector(".close").onclick = close;
    modal.querySelector(".cancel").onclick = close;
    this._activateDialog(modal, close);
    this._enhanceAccessibility(modal);
    modal.querySelector("#device-file-form").onsubmit = async (event) => {
      event.preventDefault();
      const form = new FormData(event.target);
      task.device = Object.fromEntries(["entity_id", "model", "replacement_part", "manual_url", "notes"].map((key) => [key, form.get(key).trim()]));
      await this._call("save_task", { task_id: taskId, task });
      close();
      this._toast("Geräteakte gespeichert");
    };
  }

  _showTaskStackEditor() {
    const stacks = Object.entries(this._data.task_stacks || {});
    const modal = this.shadowRoot.querySelector("#modal");
    const returnFocus = this.shadowRoot.activeElement;
    modal.innerHTML = `<div class="backdrop"><div class="modal-card">
      <div class="modal-head"><div><div class="eyebrow">ROUTINEN</div><h2>Aufgabenstapel</h2></div><button class="icon-button close">×</button></div>
      <div class="stack-list">${stacks.map(([id, stack]) => `<article><div><strong>${this._e(stack.name)}</strong><small>${stack.task_ids.map((taskId) => this._data.tasks[taskId]?.name || taskId).join(" → ")}</small></div><button data-delete-stack="${this._e(id)}">Löschen</button></article>`).join("") || "<p>Noch keine Aufgabenstapel.</p>"}</div>
      <form id="stack-form" class="form-grid">
        <label>ID<input name="id" pattern="[a-z0-9_]+" required placeholder="abendrunde"></label>
        <label>Name<input name="name" required placeholder="Abendrunde"></label>
        <fieldset class="full"><legend>Vorlagen in Reihenfolge</legend>${Object.entries(this._data.tasks).map(([id, task], index) => `<label class="checkbox"><input type="checkbox" name="task_id" value="${this._e(id)}"> ${index + 1}. ${this._e(task.name)}</label>`).join("")}</fieldset>
        <div class="full modal-actions"><button type="button" class="cancel">Schließen</button><button class="primary">Stapel speichern</button></div>
      </form></div></div>`;
    const close = () => { modal.innerHTML = ""; returnFocus?.focus(); };
    modal.querySelector(".close").onclick = close;
    modal.querySelector(".cancel").onclick = close;
    this._activateDialog(modal, close);
    modal.querySelector("#stack-form").onsubmit = async (event) => {
      event.preventDefault();
      const form = new FormData(event.target);
      await this._call("save_task_stack", {
        stack_id: form.get("id"),
        stack: { name: form.get("name"), task_ids: form.getAll("task_id") },
      });
      close();
      this._showTaskStackEditor();
    };
    modal.querySelectorAll("[data-delete-stack]").forEach((button) => button.onclick = async () => {
      await this._call("save_task_stack", { stack_id: button.dataset.deleteStack, stack: null });
      close();
      this._showTaskStackEditor();
    });
  }

  async _saveNotificationDigest(event) {
    event.preventDefault();
    const form = new FormData(event.target);
    const defaults = {
      ...this._data.defaults,
      notification_digest: {
        enabled: form.get("enabled") === "on",
        time: form.get("time") || "17:30:00",
        minimum_tasks: Number(form.get("minimum_tasks") || 2),
      },
    };
    await this._call("save_defaults", { defaults });
    this._toast("Benachrichtigungsbündelung gespeichert");
  }

  async _saveCalDAVSettings(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    await this._call("caldav_save_settings", { settings: {
      enabled: values.get("enabled") === "on",
      require_tls: values.get("require_tls") === "on",
      calendar_name: this._formText(values, "calendar_name", "Household Tasks").trim(),
      calendar_description: this._formText(values, "calendar_description").trim(),
      calendar_color: this._formText(values, "calendar_color", "#03A9F4FF").trim(),
      allow_client_create: values.get("allow_client_create") === "on",
      allow_client_update: values.get("allow_client_update") === "on",
      allow_client_delete: values.get("allow_client_delete") === "on",
      delete_mode: "cancel",
      expose_completed_days: Number(values.get("expose_completed_days") || 0),
      default_reminder_minutes: Number(values.get("default_reminder_minutes") || 0),
    } });
    this._toast("CalDAV-Einstellungen gespeichert");
  }

  async _createCalDAVCredential(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    const scope = this._formText(values, "scope", "personal");
    const personId = this._formText(values, "person_id") || null;
    if (scope === "personal" && !personId) {
      this._toast("Für einen persönlichen CalDAV-Zugang muss eine Person ausgewählt werden.", true);
      return;
    }
    const localExpiry = this._formText(values, "expires_at");
    this._busy = true;
    try {
      const result = await this._hass.callWS({
        type: "household_tasks/caldav_create_credential",
        person_id: personId,
        label: this._formText(values, "label").trim(),
        permission: this._formText(values, "permission", "read_write"),
        scope,
        include_claimable: values.get("include_claimable") === "on",
        complete_checklist_on_parent: values.get("complete_checklist_on_parent") === "on",
        expires_at: localExpiry ? new Date(localExpiry).toISOString() : null,
      });
      const created = result?.caldav?.created_credential;
      if (!created) throw new Error("Home Assistant hat keine Zugangsdaten zurückgegeben.");
      delete result.caldav.created_credential;
      this._data = result;
      if (this._hass.user) this._data.is_admin = this._hass.user.is_admin;
      localStorage.setItem("household_tasks_offline_snapshot", JSON.stringify(this._data));
      this._render();
      this._showCalDAVCredential(created);
    } catch (error) {
      this._toast(this._errorText(error), true);
    } finally {
      this._busy = false;
    }
  }

  _showCalDAVCredential(created) {
    const modal = this.shadowRoot.querySelector("#modal");
    modal.innerHTML = `<div class="backdrop"><div class="modal-card" role="dialog" aria-modal="true" aria-label="CalDAV-Zugang angelegt">
      <div class="modal-head"><div><div class="eyebrow">NUR EINMAL SICHTBAR</div><h2>CalDAV-Zugang angelegt</h2></div><button class="icon-button close" aria-label="Schließen">×</button></div>
      <div class="health-summary warning">Kopiere das App-Passwort jetzt. Es wird ausschließlich gehasht gespeichert und kann später nicht erneut angezeigt werden.</div>
      <dl class="credential-secret">
        <dt>Server</dt><dd><code>${this._e(created.server_url)}</code><button type="button" data-copy-secret="server_url">Kopieren</button></dd>
        <dt>Benutzername</dt><dd><code>${this._e(created.username)}</code><button type="button" data-copy-secret="username">Kopieren</button></dd>
        <dt>App-Passwort</dt><dd><code>${this._e(created.password)}</code><button type="button" data-copy-secret="password">Kopieren</button></dd>
      </dl>
      <p>In Apple Erinnerungen einen CalDAV-Account mit SSL anlegen. Als Server kann die vollständige URL verwendet werden.</p>
      <div class="actions"><button type="button" class="primary close-bottom">Ich habe die Daten gespeichert</button></div>
    </div></div>`;
    const close = () => modal.replaceChildren();
    modal.querySelector(".close").onclick = close;
    modal.querySelector(".close-bottom").onclick = close;
    modal.querySelectorAll("[data-copy-secret]").forEach((button) => button.onclick = () => this._copyReference(created[button.dataset.copySecret]));
    this._activateDialog(modal, close);
  }

  _showDiscoveryInstall(suggestionId) {
    const suggestion = (this._data.discovery_suggestions || []).find((item) => item.id === suggestionId);
    if (!suggestion) return;
    const modal = this.shadowRoot.querySelector("#modal");
    const returnFocus = this.shadowRoot.activeElement;
    const suggestedId = String(suggestion.name).toLocaleLowerCase("de")
      .normalize("NFKD").replace(/[\u0300-\u036f]/g, "")
      .replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "").slice(0, 48) || "entdeckte_aufgabe";
    modal.innerHTML = `<div class="backdrop"><div class="modal-card small">
      <div class="modal-head"><div><div class="eyebrow">AUTODISCOVERY</div><h2>${this._e(suggestion.name)}</h2></div><button class="icon-button close" aria-label="Schließen">×</button></div>
      <p>${this._e(suggestion.reason)}</p><div class="info-row"><span>Entität</span><code>${this._e(suggestion.entity_id)}</code></div>
      <form id="discovery-form" class="form-grid">
        <label>ID<input name="task_id" required pattern="[a-z0-9_]+" value="${this._e(suggestedId)}"></label>
        <label>Zuständig<select name="assignee">${Object.entries(this._data.people).map(([id, person]) => `<option value="${this._e(id)}">${this._e(person.name)}</option>`).join("")}</select></label>
        <div class="full modal-actions"><button type="button" class="cancel">Abbrechen</button><button class="primary" type="submit">Regel einrichten</button></div>
      </form></div></div>`;
    const close = () => { modal.replaceChildren(); returnFocus?.focus(); };
    modal.querySelector(".close").onclick = close;
    modal.querySelector(".cancel").onclick = close;
    this._activateDialog(modal, close);
    modal.querySelector("#discovery-form").onsubmit = async (event) => {
      event.preventDefault();
      const form = new FormData(event.target);
      await this._call("install_discovery", {
        suggestion_id: suggestionId,
        task_id: form.get("task_id").trim(),
        assignee: form.get("assignee"),
      });
      close();
      this._toast("Entdeckte Regel eingerichtet");
    };
  }

  async _saveHouseholdMode(event) {
    event.preventDefault();
    const form = new FormData(event.target);
    const payload = {
      mode: form.get("mode"),
      policy: form.get("policy"),
    };
    if (form.get("delegate_to")) payload.delegate_to = form.get("delegate_to");
    if (form.get("until")) payload.until = new Date(form.get("until")).toISOString();
    if (form.get("note")?.trim()) payload.note = form.get("note").trim();
    await this._call("set_household_mode", payload);
    this._toast("Haushaltsmodus aktualisiert");
  }

  _showSetupWizard() {
    const gallery = this._data.template_gallery || [];
    const modal = this.shadowRoot.querySelector("#modal");
    const returnFocus = this.shadowRoot.activeElement;
    modal.innerHTML = `<div class="backdrop"><div class="modal-card">
      <div class="modal-head"><div><div class="eyebrow">EINRICHTUNGSASSISTENT</div><h2>Haushalt startklar machen</h2></div><button class="icon-button close" aria-label="Schließen">×</button></div>
      <form id="setup-form" class="form-grid">
        <p class="full setup-step"><b>1</b><span><strong>Erste Person</strong><small>Verknüpft Aufgaben, Anwesenheit und Aktions-Benachrichtigungen.</small></span></p>
        <label>ID<input name="person_id" required pattern="[a-z0-9_]+" placeholder="alex"></label>
        <label>Name<input name="person_name" required placeholder="Alex"></label>
        <label class="full">Push-Aktion${this._notifyInput("")}</label>
        <label class="full">Anwesenheit${this._entityInput("presence", "", ["person", "device_tracker", "binary_sensor"], { placeholder: "Optional" })}</label>
        <p class="full setup-step"><b>2</b><span><strong>Starter-Vorlage</strong><small>Vor dem Speichern siehst du genau, was angelegt wird.</small></span></p>
        <label>Vorlage<select name="template_id">${gallery.map((entry) => `<option value="${this._e(entry.id)}">${this._e(entry.name)}</option>`).join("")}</select></label>
        <label>Vorlagen-ID<input name="task_id" required pattern="[a-z0-9_]+" value="${this._e(gallery[0]?.id || "erste_aufgabe")}"></label>
        <label class="full wizard-entity">Auslöser-Entität${this._entityInput("entity_id", "", [], { placeholder: "Bei zustandsbasierten Vorlagen erforderlich" })}</label>
        <div class="full wizard-preview" aria-live="polite"></div>
        <div class="full modal-actions"><button type="button" class="cancel">Abbrechen</button><button class="primary" type="submit">Einrichtung abschließen</button></div>
      </form></div></div>`;
    this._localize(modal);
    const close = () => { modal.replaceChildren(); returnFocus?.focus(); };
    modal.querySelector(".close").onclick = close;
    modal.querySelector(".cancel").onclick = close;
    this._activateDialog(modal, close);
    const form = modal.querySelector("#setup-form");
    const templateSelect = form.querySelector("[name=template_id]");
    const taskIdInput = form.querySelector("[name=task_id]");
    const personNameInput = form.querySelector("[name=person_name]");
    const updatePreview = () => {
      const entry = gallery.find((item) => item.id === templateSelect.value);
      if (!entry) return;
      taskIdInput.value = taskIdInput.dataset.edited ? taskIdInput.value : entry.id;
      const needsEntity = entry.task.schedule?.triggers?.some((trigger) => !trigger.entity_id)
        || entry.task.weather?.conditions?.some((condition) => !condition.entity_id);
      modal.querySelector(".wizard-entity").classList.toggle("hidden", !needsEntity);
      const preview = modal.querySelector(".wizard-preview");
      const heading = document.createElement("strong");
      const description = document.createElement("p");
      heading.textContent = "Vorschau";
      description.textContent = `Person „${personNameInput.value || "…"}“ und Aufgabe „${entry.task.name}“ werden angelegt. `
        + `Zeitplan: ${this._scheduleLabel(entry.task.schedule)}; `
        + `Priorität: ${entry.task.market?.priority || "normal"}; `
        + `${Number(entry.task.market?.points || 0)} Punkte.`;
      preview.replaceChildren(heading, description);
    };
    templateSelect.onchange = updatePreview;
    personNameInput.oninput = updatePreview;
    taskIdInput.oninput = () => { taskIdInput.dataset.edited = "true"; };
    updatePreview();
    form.onsubmit = async (event) => {
      event.preventDefault();
      const data = new FormData(form);
      const notifyValue = data.get("notify");
      const notify = (typeof notifyValue === "string" ? notifyValue : "").trim();
      const service = notify.removeprefix?.("notify.") || notify.replace(/^notify\./, "");
      if (!service || !this._hass.services.notify?.[service]) {
        this._toast("Bitte wähle eine vorhandene Push-Aktion aus.", true);
        return;
      }
      const person = { name: data.get("person_name").trim(), notify };
      if (data.get("presence")?.trim()) person.presence = data.get("presence").trim();
      await this._call("save_person", { person_id: data.get("person_id").trim(), person });
      const payload = {
        template_id: data.get("template_id"),
        task_id: data.get("task_id").trim(),
        assignee: data.get("person_id").trim(),
      };
      if (data.get("entity_id")?.trim()) payload.entity_id = data.get("entity_id").trim();
      await this._call("install_gallery_template", payload);
      close();
      this._toast("Einrichtung abgeschlossen");
    };
  }

  _showGallery(selectedId = null) {
    const gallery = this._data.template_gallery || [];
    const selected = gallery.find((entry) => entry.id === selectedId) || gallery[0];
    const modal = this.shadowRoot.querySelector("#modal");
    const returnFocus = this.shadowRoot.activeElement;
    modal.innerHTML = `<div class="backdrop"><div class="modal-card">
      <div class="modal-head"><div><div class="eyebrow">VORLAGENGALERIE</div><h2>Bewährte Routinen übernehmen</h2></div><button class="icon-button close" aria-label="Schließen">×</button></div>
      <div class="gallery-modal">${gallery.map((entry) => `<button type="button" data-pick-template="${this._e(entry.id)}" class="${entry.id === selected?.id ? "selected" : ""}"><span>${this._e(entry.category)}</span><strong>${this._e(entry.name)}</strong><small>${this._e(entry.description)}</small></button>`).join("")}</div>
      <form id="gallery-form" class="form-grid">
        <input type="hidden" name="template_id" value="${this._e(selected?.id || "")}">
        <label>Eigene ID<input name="task_id" required pattern="[a-z0-9_]+" value="${this._e(selected?.id || "")}"></label>
        <label class="gallery-assignee">Zuständig<select name="assignee">${Object.entries(this._data.people).map(([id, person]) => `<option value="${this._e(id)}">${this._e(person.name)}</option>`).join("")}</select></label>
        <div class="full gallery-people hidden"><span class="field-label">Je eine Aufgabe für</span><div class="candidate-grid">${Object.entries(this._data.people).map(([id, person]) => `<label class="checkbox"><input type="checkbox" name="people" value="${this._e(id)}" checked> ${this._e(person.name)}</label>`).join("")}</div><p class="hint">Jede ausgewählte Person erhält eine eigene Aufgabe und saisonale Sperre.</p></div>
        <label class="full gallery-entity">Auslöser-Entität${this._entityInput("entity_id", "", [], { placeholder: "Sensor oder Warnungs-Entität auswählen" })}</label>
        <div class="full gallery-preview"></div>
        <div class="full modal-actions"><button type="button" class="cancel">Abbrechen</button><button class="primary" type="submit">Vorlage übernehmen</button></div>
      </form></div></div>`;
    this._localize(modal);
    const close = () => { modal.replaceChildren(); returnFocus?.focus(); };
    modal.querySelector(".close").onclick = close;
    modal.querySelector(".cancel").onclick = close;
    this._activateDialog(modal, close);
    const form = modal.querySelector("#gallery-form");
    const templateInput = form.querySelector("[name=template_id]");
    const taskIdInput = form.querySelector("[name=task_id]");
    const select = (id) => {
      const entry = gallery.find((item) => item.id === id);
      if (!entry) return;
      templateInput.value = id;
      taskIdInput.value = id;
      modal.querySelectorAll("[data-pick-template]").forEach((button) => button.classList.toggle("selected", button.dataset.pickTemplate === id));
      const needsEntity = entry.task.schedule?.triggers?.some((trigger) => !trigger.entity_id)
        || entry.task.weather?.conditions?.some((condition) => !condition.entity_id);
      modal.querySelector(".gallery-entity").classList.toggle("hidden", !needsEntity);
      const perPerson = entry.task.assignment?.type === "per_person";
      modal.querySelector(".gallery-assignee").classList.toggle("hidden", perPerson);
      modal.querySelector(".gallery-people").classList.toggle("hidden", !perPerson);
      modal.querySelector(".gallery-preview").innerHTML = `<strong>${this._e(entry.task.name)}</strong><p>${this._e(this._scheduleLabel(entry.task.schedule))} · ${this._e(entry.task.market?.priority || "normal")} · ${Number(entry.task.market?.points || 0)} Punkte</p>`;
    };
    modal.querySelectorAll("[data-pick-template]").forEach((button) => button.onclick = () => select(button.dataset.pickTemplate));
    select(selected?.id);
    form.onsubmit = async (event) => {
      event.preventDefault();
      const data = new FormData(form);
      const payload = Object.fromEntries(["template_id", "task_id", "assignee"].map((key) => [key, data.get(key)]));
      const entry = gallery.find((item) => item.id === data.get("template_id"));
      if (entry?.task?.assignment?.type === "per_person") {
        payload.people = data.getAll("people");
        delete payload.assignee;
        if (!payload.people.length) throw new Error("Bitte wähle mindestens eine Person aus.");
      }
      if (data.get("entity_id")?.trim()) payload.entity_id = data.get("entity_id").trim();
      await this._call("install_gallery_template", payload);
      close();
      this._toast("Vorlage übernommen");
    };
  }

  async _showWhyNot(taskId) {
    try {
      const explanation = await this._hass.callWS({ type: "household_tasks/explain_task", task_id: taskId });
      const modal = this.shadowRoot.querySelector("#modal");
      const returnFocus = this.shadowRoot.activeElement;
      const excluded = explanation.excluded_candidates || [];
      const activation = explanation.activation || { allowed: true, code: "template_active" };
      const negation = activation.allowed && explanation.mode.allowed && explanation.season.allowed ? "" : "nicht ";
      const forecast = this._whyNotForecast(explanation.forecast_trace);
      const excludedMarkup = this._whyNotExcluded(excluded);
      const recent = this._whyNotRecent(explanation.recent_decisions);
      modal.innerHTML = `<div class="backdrop"><div class="modal-card small">
        <div class="modal-head"><div><div class="eyebrow">ENTSCHEIDUNGSHILFE</div><h2>Warum wird die Aufgabe ${negation}erzeugt?</h2></div><button class="icon-button close" aria-label="Schließen">×</button></div>
        <div class="decision-line ${activation.allowed ? "ok" : "blocked"}"><strong>Vorlagenstatus</strong><span>${this._e(this._activationMessage(activation))}</span></div>
        <div class="decision-line ${explanation.mode.allowed ? "ok" : "blocked"}"><strong>Haushaltsmodus</strong><span>${this._e(explanation.mode.message)}</span></div>
        <div class="decision-line ${explanation.season.allowed ? "ok" : "blocked"}"><strong>Saison</strong><span>${this._e(explanation.season.message)}</span></div>
        ${forecast}
        <h3>Berücksichtigte Personen</h3>
        <p>${(explanation.eligible_candidates || []).map((id) => this._e(this._data.people[id]?.name || id)).join(", ") || "Keine geeignete Person"}</p>
        ${excludedMarkup}
        ${recent}
      </div></div>`;
      const close = () => { modal.replaceChildren(); returnFocus?.focus(); };
      modal.querySelector(".close").onclick = close;
      this._activateDialog(modal, close);
    } catch (error) {
      this._toast(this._errorText(error), true);
    }
  }

  _whyNotForecast(trace) {
    if (!trace) return "";
    const status = trace.allowed ? "ok" : "blocked";
    const activation = trace.activation_at
      ? ` · Aktivierung ${new Date(trace.activation_at).toLocaleString(this._locale())}`
      : "";
    return `<div class="decision-line ${status}"><strong>Wettervorhersage</strong><span>${this._e(trace.message)}${activation}</span></div>`;
  }

  _whyNotExcluded(excluded) {
    if (!excluded.length) return "";
    const items = excluded.map((item) =>
      `<li>${this._e(this._data.people[item.person_id]?.name || item.person_id)}: ${this._e(item.reason)}</li>`
    ).join("");
    return `<h3>Nicht berücksichtigt</h3><ul>${items}</ul>`;
  }

  _whyNotRecent(decisions) {
    if (!decisions?.length) return "";
    const items = decisions.map((item) =>
      `<li>${new Date(item.checked_at).toLocaleString(this._locale())}: ${this._e(item.message)}</li>`
    ).join("");
    return `<details><summary>Letzte ausgelassene Erzeugungen</summary><ul>${items}</ul></details>`;
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
          const formattedUnit = unit ? ` ${unit}` : "";
          const resultText = matches
            ? "trifft zu; Aufgabe würde erzeugt"
            : "trifft nicht zu";
          output.textContent = `${state.state}${formattedUnit} – Regel ${resultText}.`;
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

  _showTaskPauseDialog(taskId) {
    const task = this._data.tasks[taskId];
    if (!task) return;
    const modal = this.shadowRoot.querySelector("#modal");
    const returnFocus = this.shadowRoot.activeElement;
    const suggested = new Date(Date.now() + 24 * 60 * 60 * 1000);
    const minimum = new Date(Date.now() + 60 * 1000);
    modal.innerHTML = `<div class="backdrop"><div class="modal-card small">
      <div class="modal-head"><div><div class="eyebrow">AUFGABENVORLAGE</div><h2>„${this._e(task.name)}“ pausieren</h2></div><button class="icon-button close" aria-label="Schließen">×</button></div>
      <p>Während der Pause werden keine neuen Aufgaben erzeugt. Bereits vorhandene offene Aufgaben bleiben erhalten.</p>
      <form id="task-pause-form" class="form-grid">
        <label class="full">Pausiert bis<input name="paused_until" type="datetime-local" required min="${this._localDateTimeValue(minimum)}" value="${this._localDateTimeValue(suggested)}"><span class="hint">Die Vorlage wird nach diesem Zeitpunkt automatisch wieder aktiv.</span></label>
        <div class="full quick-presets" aria-label="Schnelle Pausendauer">
          <button type="button" data-pause-hours="12">12 Stunden</button>
          <button type="button" data-pause-hours="24">1 Tag</button>
          <button type="button" data-pause-hours="168">1 Woche</button>
        </div>
        <div class="full modal-actions"><button type="button" class="cancel">Abbrechen</button><button class="primary" type="submit">Pausieren</button></div>
      </form></div></div>`;
    this._localize(modal);
    const close = () => { modal.replaceChildren(); returnFocus?.focus(); };
    modal.querySelector(".close").onclick = close;
    modal.querySelector(".cancel").onclick = close;
    this._activateDialog(modal, close);
    modal.querySelectorAll("[data-pause-hours]").forEach((button) => {
      button.onclick = () => {
        const until = new Date(Date.now() + Number(button.dataset.pauseHours) * 60 * 60 * 1000);
        modal.querySelector("[name=paused_until]").value = this._localDateTimeValue(until);
      };
    });
    modal.querySelector("#task-pause-form").onsubmit = async (event) => {
      event.preventDefault();
      const rawUntil = new FormData(event.currentTarget).get("paused_until");
      const until = new Date(rawUntil);
      if (Number.isNaN(until.getTime()) || until <= new Date()) {
        this._toast(this._t("Bitte einen zukünftigen Zeitpunkt auswählen"), true);
        return;
      }
      const updated = structuredClone(task);
      updated.enabled = true;
      updated.paused_until = until.toISOString();
      await this._call("save_task", { task_id: taskId, task: updated });
      close();
      this._toast(this._t("Aufgabe temporär pausiert"));
    };
  }

  _showTaskEditor(id = null, options = {}) {
    const guided = true;
    const task = this._editorTask(id, options);
    const s = task.schedule || { type: "manual" };
    const esc = task.escalation;
    const nfc = task.nfc || {};
    const market = task.market || { priority: "normal", points: 1, reward: "" };
    const automaticCompletion = task.automatic_completion || {};
    const automaticCompletionPerson = automaticCompletion.default_person || task.assignee || Object.keys(this._data.people)[0] || "";
    const modes = task.modes || {};
    const season = task.season || {};
    const weather = task.weather || {};
    const assignmentType = task.assignment?.type || "fixed";
    const assignmentPeople = task.assignment?.people || Object.keys(this._data.people);
    const presenceRequired = task.assignment?.presence_required === true;
    const absencePolicy = task.assignment?.absence_policy || "wait";
    const fallbackPeople = task.assignment?.fallback_people || [];
    const fallbackStrategy = task.assignment?.fallback_strategy || "fair";
    const pausedUntil = this._taskPausedUntil(task);
    const modal = this.shadowRoot.querySelector("#modal");
    const returnFocus = this.shadowRoot.activeElement;
    modal.innerHTML = `<div class="backdrop"><div class="modal-card task-editor ${guided ? "task-editor--wizard" : "task-editor--direct"}">
      <div class="modal-head"><div><div class="eyebrow">AUFGABENVORLAGE</div><h2>${id ? "Aufgabe bearbeiten" : "Neue Aufgabe"}</h2></div><button class="icon-button close">×</button></div>
      ${guided ? `<ol class="task-wizard-steps" aria-label="Schritte der Aufgabenanlage">
        ${["Grundlagen", "Zuständigkeit", "Auslöser", "Optionen", "Prüfen"].map((label, index) => `<li><button type="button" data-task-wizard-step="${index + 1}" ${!id && index ? "disabled" : ""}><span>${index + 1}</span>${label}</button></li>`).join("")}
      </ol>` : ""}
      <form id="task-form" class="form-grid">
        <input name="id" type="hidden" required pattern="[a-z0-9_]+" value="${this._e(id || "")}">
        <label class="full" data-task-step="1">Name<input name="name" required autofocus value="${this._e(task.name)}" placeholder="Bad putzen"><span class="hint">Die technische ID wird automatisch erzeugt.</span></label>
        <label data-task-step="2">Zuweisung<select name="assignment_type">
          ${[["fixed","Fest"],["rotation","Rotation"],["fair","Fair"],["open","Offen"],["per_person","Je Person eine Aufgabe"]].map(([value, label]) =>
            `<option value="${value}" ${value === assignmentType ? "selected" : ""}>${label}</option>`
          ).join("")}
        </select></label>
        <label class="checkbox" data-task-step="1"><input name="enabled" type="checkbox" ${task.enabled !== false ? "checked" : ""}> Aufgabe aktiv</label>
        <label class="full" data-task-step="1">Temporär pausiert bis<input name="paused_until" type="datetime-local" value="${pausedUntil ? this._localDateTimeValue(pausedUntil) : ""}"><span class="hint">Optional. Bis zu diesem Zeitpunkt werden keine neuen Aufgaben erzeugt; bestehende offene Aufgaben bleiben erhalten.</span></label>
        <label class="full fixed-assignee" data-task-step="2">Zuständig<select name="assignee">${Object.entries(this._data.people).map(([pid, p]) => `<option value="${this._e(pid)}" ${pid === task.assignee ? "selected" : ""}>${this._e(p.name)}</option>`).join("")}</select></label>
        <div class="full assignment-candidates" data-task-step="2">
          <span class="field-label">Teilnehmende Personen</span>
          <div class="candidate-grid">${Object.entries(this._data.people).map(([pid, person]) =>
            `<label class="checkbox"><input type="checkbox" name="assignment_person" value="${this._e(pid)}" ${assignmentPeople.includes(pid) ? "checked" : ""}> ${this._e(person.name)}</label>`
          ).join("")}</div>
          <p class="hint assignment-hint"></p>
        </div>
        <label class="full checkbox presence-required" data-task-step="2"><input name="presence_required" type="checkbox" ${presenceRequired ? "checked" : ""}> Anwesenheit bei der Zuweisung berücksichtigen</label>
        <div class="full fixed-absence-settings form-grid" data-task-step="2">
          <label class="full">Wenn die fest zuständige Person nicht zuhause ist<select name="absence_policy">
            ${[["wait","Warten, bis die Person zurück ist"],["fallback","An eine Ersatzperson zuweisen"],["open","Für Ersatzpersonen zur Übernahme öffnen"],["assign_anyway","Trotzdem fest zuweisen"]].map(([value, label]) => `<option value="${value}" ${value === absencePolicy ? "selected" : ""}>${label}</option>`).join("")}
          </select></label>
          <div class="full fallback-candidates">
            <span class="field-label">Mögliche Ersatzpersonen</span>
            <div class="candidate-grid">${Object.entries(this._data.people).map(([pid, person]) =>
              `<label class="checkbox"><input type="checkbox" name="fallback_person" value="${this._e(pid)}" ${fallbackPeople.includes(pid) ? "checked" : ""}> ${this._e(person.name)}</label>`
            ).join("") || `<span class="hint">Keine weitere Person vorhanden.</span>`}</div>
            <p class="hint">Nur ausdrücklich ausgewählte und aktuell anwesende Personen werden berücksichtigt. Die fest zuständige Person wird automatisch aus der Ersatzliste entfernt.</p>
          </div>
          <label class="full fallback-strategy">Auswahl unter mehreren Ersatzpersonen<select name="fallback_strategy">
            <option value="fair" ${fallbackStrategy === "fair" ? "selected" : ""}>Fair nach bisheriger und offener Last</option>
            <option value="rotation" ${fallbackStrategy === "rotation" ? "selected" : ""}>Der Reihe nach rotieren</option>
          </select></label>
        </div>
        <div class="full inline-create" data-task-step="2">
          <button type="button" data-toggle-inline-person>+ Person direkt anlegen</button>
          <div class="inline-person-form form-grid hidden">
            <label>ID<input name="inline_person_id" pattern="[a-z0-9_]+" placeholder="vorname"></label>
            <label>Name<input name="inline_person_name" placeholder="Vorname"></label>
            <label class="full">Push-Aktion${this._notifyInput("")}</label>
            <label class="full">Anwesenheit${this._entityInput("inline_person_presence", "", ["person", "device_tracker", "binary_sensor"], { placeholder: "Optional" })}</label>
            <div class="full"><button type="button" class="primary" data-save-inline-person>Person übernehmen</button></div>
          </div>
        </div>
        <label class="full" data-task-step="1">Beschreibung<textarea name="description" rows="2">${this._e(task.description || "")}</textarea></label>
        <label class="full" data-task-step="1">Checkliste<textarea name="checklist" rows="4" placeholder="Ein Schritt pro Zeile">${this._e((task.checklist || []).map((item) => typeof item === "string" ? item : item.title).join("\n"))}</textarea><span class="hint">Jede Zeile wird zu einem einzeln abhakbaren Schritt. Standardmäßig kann die Aufgabe erst abgeschlossen werden, wenn alle Schritte erledigt sind.</span></label>
        <label class="full checkbox" data-task-step="1"><input name="require_checklist_completion" type="checkbox" ${task.require_checklist_completion !== false ? "checked" : ""}> Vollständige Checkliste vor Abschluss verlangen</label>
        <label data-task-step="3">Zeitplan<select name="type">
          ${[["manual","Manuell"],["weekly","Wöchentlich"],["monthly","Monatlich"],["yearly","Jährlich"],["interval_months","Alle N Monate"],["after_completion","Nach letzter Erledigung"],["flexible_after_completion","Flexibel nach Erledigung"],["calendar","Kalender / ICS"],["weather_trigger","Aktuelle Wetterregel"],["forecast_trigger","Wettervorhersage"],["state_trigger","Bei Zustandswechsel"],["daily_after_state","Einmal täglich nach Gerätestatus"]].map(([v,l]) => `<option value="${v}" ${v === s.type ? "selected" : ""}>${l}</option>`).join("")}
        </select></label>
        <div class="full schedule-fields" data-task-step="3">${this._scheduleFields(s, weather)}</div>
        <div class="full task-preview" data-task-step="3"><button type="button" data-preview-task>Regel testen / nächste Fälligkeit</button>${id && task.repeat?.mode === "once_per_season" ? `<button type="button" data-reset-season>Saisonsperren zurücksetzen</button>` : ""}<output class="preview-result" aria-live="polite"></output></div>
        <details class="full advanced-fields" data-task-step="4">
          <summary>Expertenoptionen: Markt, Saison, NFC, Abhängigkeiten und Eskalation</summary>
          <div class="form-grid advanced-grid">
        <div class="full repeatable-editor">
          <span class="field-label">Vorlagen-Abhängigkeiten</span>
          <div class="candidate-grid">${Object.entries(this._data.tasks).filter(([taskId]) => taskId !== id).map(([taskId, dependency]) => `<label class="checkbox"><input type="checkbox" name="depends_on" value="${this._e(taskId)}" ${(task.depends_on || []).includes(taskId) ? "checked" : ""}> ${this._e(dependency.name)}</label>`).join("") || "<span class=\"hint\">Noch keine weitere Vorlage vorhanden.</span>"}</div>
          <p class="hint">Eine neue Aufgabe bleibt blockiert, solange eine offene Aufgabe der gewählten Vorlage existiert.</p>
        </div>
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
        <label>Priorität<select name="market_priority">${[["low","Niedrig"],["normal","Normal"],["high","Hoch"],["critical","Kritisch"]].map(([value, label]) => `<option value="${value}" ${market.priority === value ? "selected" : ""}>${label}</option>`).join("")}</select><span class="hint">Hohe und kritische Aufgaben bleiben bei reduziertem Urlaubsmodus aktiv.</span></label>
        <label>Punkte<input name="market_points" type="number" min="0" max="100" value="${Number(market.points ?? 1)}"><span class="hint">Wer übernimmt und erledigt, erhält diese Punkte.</span></label>
        <div class="full automatic-completion-editor form-grid">
          <label class="full checkbox"><input name="automatic_completion" type="checkbox" ${automaticCompletion.enabled ? "checked" : ""}> Ohne Bestätigung automatisch gutschreiben</label>
          <div class="full automatic-completion-settings form-grid ${automaticCompletion.enabled ? "" : "hidden"}">
            <label>Standardperson<select name="automatic_completion_person">${Object.entries(this._data.people).map(([pid,p]) => `<option value="${this._e(pid)}" ${pid === automaticCompletionPerson ? "selected" : ""}>${this._e(p.name)}</option>`).join("")}</select><span class="hint">Bekommt die Punkte, wenn niemand vorher Erledigt drückt.</span></label>
            <label>Kulanzzeit nach Fälligkeit<input name="automatic_completion_after" value="${this._e(automaticCompletion.after || "12:00:00")}" pattern="[0-9]+:[0-5][0-9]:[0-5][0-9]" placeholder="12:00:00"><span class="hint">Format HH:MM:SS. Innerhalb dieser Zeit zählt eine manuelle Bestätigung.</span></label>
          </div>
        </div>
        <label class="full">Belohnung (optional)<input name="market_reward" value="${this._e(market.reward || "")}" placeholder="Film aussuchen, Wunschessen …"></label>
        <label>Im Urlaub<select name="vacation_behavior">${[["pause","Pausieren"],["reduce","Nur bei hoher Priorität"],["delegate","Delegieren"],["always","Immer ausführen"]].map(([value, label]) => `<option value="${value}" ${modes.vacation === value ? "selected" : ""}>${label}</option>`).join("")}</select></label>
        <div>
          <label class="checkbox"><input name="guest_only" type="checkbox" ${modes.guest_only ? "checked" : ""}> Nur im Gastmodus</label>
          <label class="checkbox"><input name="skip_in_guest" type="checkbox" ${modes.skip_in_guest ? "checked" : ""}> Im Gastmodus auslassen</label>
        </div>
        <label class="full checkbox"><input name="season_enabled" type="checkbox" ${task.season ? "checked" : ""}> Saisonale Einschränkung aktivieren</label>
        <label>Saisonmonate<input name="season_months" value="${this._e((season.months || []).join(","))}" placeholder="10,11,12,1,2,3"><span class="hint">Monatsnummern 1–12, durch Komma getrennt.</span></label>
        <label class="checkbox"><input name="once_per_season" type="checkbox" ${task.repeat?.mode === "once_per_season" ? "checked" : ""}> Nur einmal je Saison und Zielperson<span class="hint">Wird automatisch mit Beginn der nächsten Saison zurückgesetzt.</span></label>
        <label>Saisonbedingung<select name="season_condition"><option value="">Nur Monate</option>${[["below","Unter"],["at_most","Höchstens"],["above","Über"],["at_least","Mindestens"],["equals","Ist gleich"],["not_equals","Ist ungleich"]].map(([value, label]) => `<option value="${value}" ${season.condition === value ? "selected" : ""}>${label}</option>`).join("")}</select></label>
        <label class="full">Saison-Entität${this._entityInput("season_entity_id", season.entity_id || "", [], { placeholder: "Optionaler Sensor oder Warnstatus" })}</label>
        <label>Grenzwert<input name="season_threshold" value="${this._e(season.threshold ?? "")}" placeholder="2 oder high"></label>
        <label class="full checkbox"><input name="custom_escalation" type="checkbox" ${esc ? "checked" : ""}> Eigene Eskalationsregeln verwenden</label>
        <div class="full escalation-fields repeatable-editor escalation-editor ${esc ? "" : "hidden"}">
          <div class="repeatable-list">${this._escalationRows(esc || [])}</div>
          <button type="button" class="add-row" data-add-escalation>+ Eskalationsstufe</button>
        </div>
          </div>
        </details>
        ${guided ? `<section class="full task-wizard-review" data-task-step="5" aria-live="polite">
          <div class="eyebrow">ZUSAMMENFASSUNG</div><h3>So wird die Aufgabe angelegt</h3>
          <dl class="wizard-review-facts"><div><dt>Aufgabe</dt><dd data-review-name>–</dd></div><div><dt>Zuständigkeit</dt><dd data-review-assignment>–</dd></div><div><dt>Auslöser</dt><dd data-review-schedule>–</dd></div></dl>
          <div class="wizard-review-preview"><strong>Regelprüfung</strong><output data-review-preview>Wird beim Öffnen dieses Schritts berechnet.</output></div>
        </section>` : ""}
        <div class="full modal-actions task-editor-actions"><button type="button" class="cancel">Abbrechen</button>${guided ? `<button type="button" data-wizard-prev>Zurück</button><button type="button" class="primary" data-wizard-next>Weiter</button>` : ""}<button class="primary save-task" type="submit">Speichern</button></div>
      </form></div></div>`;
    this._localize(modal);
    const form = modal.querySelector("#task-form");
    let dirty = false;
    const close = async (force = false) => {
      if (!force && dirty && !await this._confirm("Ungespeicherte Änderungen verwerfen?")) return;
      modal.innerHTML = "";
      returnFocus?.focus();
    };
    modal.querySelector(".close").onclick = () => { void close(); };
    modal.querySelector(".cancel").onclick = () => { void close(); };
    this._activateDialog(modal, () => { void close(); });
    modal.querySelector("[name=type]").onchange = (event) => {
      const fields = modal.querySelector(".schedule-fields");
      fields.innerHTML = this._scheduleFields({ type: event.target.value, time: "18:00:00" }, {});
      this._localize(fields);
      this._bindTriggerEditor(modal);
      this._bindWeatherEditor(modal);
      this._bindCalendarMappingEditor(modal);
      this._enhanceAccessibility(fields);
    };
    const updateAssignmentFields = () => {
      const type = modal.querySelector("[name=assignment_type]").value;
      const presenceRequired = modal.querySelector("[name=presence_required]").checked;
      const absencePolicy = modal.querySelector("[name=absence_policy]").value;
      modal.querySelector(".fixed-assignee").classList.toggle("hidden", type !== "fixed");
      modal.querySelector(".assignment-candidates").classList.toggle("hidden", type === "fixed");
      modal.querySelector(".fixed-absence-settings").classList.toggle("hidden", type !== "fixed" || !presenceRequired);
      modal.querySelector(".fallback-candidates").classList.toggle("hidden", !["fallback", "open"].includes(absencePolicy));
      modal.querySelector(".fallback-strategy").classList.toggle("hidden", absencePolicy !== "fallback");
      const hints = {
        rotation: this._t("Die Aufgabe wandert bei jeder Erzeugung zur nächsten ausgewählten Person."),
        fair: this._t("Gewählt wird, wer bisher am wenigsten Zuweisungen und aktuell die geringste offene Last hat."),
        open: this._t("Alle ausgewählten Personen erhalten Übernehmen. Ohne Auswahl ist die Aufgabe für alle offen."),
        per_person: this._t("Für jede ausgewählte Person wird eine eigene, unabhängig abschließbare Aufgabe erzeugt."),
      };
      modal.querySelector(".assignment-hint").textContent = hints[type] || "";
    };
    modal.querySelector("[name=assignment_type]").onchange = updateAssignmentFields;
    modal.querySelector("[name=presence_required]").onchange = updateAssignmentFields;
    modal.querySelector("[name=absence_policy]").onchange = updateAssignmentFields;
    updateAssignmentFields();
    modal.querySelector("[name=custom_escalation]").onchange = (event) => {
      modal.querySelector(".escalation-fields").classList.toggle("hidden", !event.target.checked);
    };
    modal.querySelector("[name=automatic_completion]").onchange = (event) => {
      modal.querySelector(".automatic-completion-settings").classList.toggle("hidden", !event.target.checked);
    };
    this._bindRepeatableEditors(modal, id);
    this._bindEscalationEditor(modal);
    this._bindTagCreator(modal);
    this._bindInlineTaskCreates(modal, id);
    const runPreview = this._bindTaskPreview(modal, id);
    if (guided) this._bindTaskWizard(modal, runPreview, id);
    form.addEventListener("input", () => { dirty = true; });
    form.addEventListener("change", () => { dirty = true; });
    form.onsubmit = async (event) => {
      event.preventDefault();
      try {
        const { taskId, value } = this._readTaskForm(event.target);
        this._preserveTaskDevice(value, task);
        const projection = await this._hass.callWS({ type: "household_tasks/task_projection", task: value });
        if (projection.risk === "high" && !await this._confirm(`${projection.message} Trotzdem speichern?`)) return;
        await this._call("save_task", { task_id: taskId, task: value });
        await close(true); this._toast("Aufgabe gespeichert");
      } catch (error) {
        if (!error?.message?.includes("household_tasks")) this._toast(this._errorText(error), true);
      }
    };
  }

  _preserveTaskDevice(value, task) {
    if (task.device) value.device = task.device;
  }

  _bindTaskWizard(modal, runPreview, existingId = null) {
    const form = modal.querySelector("#task-form");
    const stepButtons = [...modal.querySelectorAll("[data-task-wizard-step]")];
    const previous = modal.querySelector("[data-wizard-prev]");
    const next = modal.querySelector("[data-wizard-next]");
    const nameInput = form.elements.name;
    const state = { current: 1, furthest: existingId ? 5 : 1 };

    const generateId = () => {
      form.elements.id.value = this._generatedTaskId(nameInput.value);
    };

    const showStep = async (step) => {
      state.current = step;
      state.furthest = Math.max(state.furthest, step);
      this._displayTaskWizardStep(modal, state, existingId);
      if (step === 5) await this._updateTaskWizardReview(modal, form, runPreview);
      this._focusTaskWizardStep(modal, form, step);
    };

    if (!existingId) {
      nameInput.addEventListener("input", generateId);
      generateId();
    }
    previous.onclick = () => { void showStep(Math.max(1, state.current - 1)); };
    next.onclick = () => {
      if (!this._validateTaskWizardStep(form, state.current)) return;
      void showStep(Math.min(5, state.current + 1));
    };
    stepButtons.forEach((button) => {
      button.onclick = () => {
        const target = Number(button.dataset.taskWizardStep);
        if (target > state.current && !this._validateTaskWizardStep(form, state.current)) return;
        void showStep(target);
      };
    });
    void showStep(1);
  }

  _generatedTaskId(name) {
    const normalized = String(name || "").normalize("NFKD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "") || "aufgabe";
    let candidate = normalized;
    let suffix = 2;
    while (this._data.tasks[candidate]) candidate = `${normalized}_${suffix++}`;
    return candidate;
  }

  _validateTaskWizardStep(form, step) {
    const selector = `[data-task-step="${step}"] input,[data-task-step="${step}"] select,[data-task-step="${step}"] textarea`;
    const controls = [...form.querySelectorAll(selector)]
      .filter((control) => control.offsetParent !== null && !control.disabled);
    const invalid = controls.find((control) => !control.checkValidity());
    if (!invalid) return true;
    invalid.setAttribute("aria-invalid", "true");
    invalid.reportValidity();
    invalid.focus();
    return false;
  }

  _displayTaskWizardStep(modal, state, existingId) {
    modal.querySelectorAll("[data-task-step]").forEach((section) => {
      section.classList.toggle("wizard-step-hidden", Number(section.dataset.taskStep) !== state.current);
    });
    modal.querySelectorAll("[data-task-wizard-step]").forEach((button) => {
      const buttonStep = Number(button.dataset.taskWizardStep);
      button.disabled = buttonStep > state.furthest;
      button.toggleAttribute("aria-current", buttonStep === state.current);
      if (buttonStep === state.current) button.setAttribute("aria-current", "step");
    });
    modal.querySelector("[data-wizard-prev]").classList.toggle("hidden", state.current === 1);
    modal.querySelector("[data-wizard-next]").classList.toggle("hidden", state.current === 5);
    modal.querySelector(".save-task").classList.toggle("hidden", !existingId && state.current !== 5);
    modal.querySelector(".task-editor--wizard").classList.add("wizard-ready");
  }

  async _updateTaskWizardReview(modal, form, runPreview) {
    const assignment = form.elements.assignment_type;
    const schedule = form.elements.type;
    modal.querySelector("[data-review-name]").textContent = form.elements.name.value.trim() || "Noch kein Name";
    modal.querySelector("[data-review-assignment]").textContent = assignment.options[assignment.selectedIndex]?.textContent || "–";
    modal.querySelector("[data-review-schedule]").textContent = schedule.options[schedule.selectedIndex]?.textContent || "–";
    const reviewPreview = modal.querySelector("[data-review-preview]");
    reviewPreview.textContent = "Regel wird geprüft …";
    await runPreview();
    const source = modal.querySelector(".preview-result");
    reviewPreview.textContent = source.textContent || "Für diese Konfiguration ist noch keine Vorschau verfügbar.";
    reviewPreview.classList.toggle("error", source.classList.contains("error"));
  }

  _focusTaskWizardStep(modal, form, step) {
    modal.querySelector(".task-editor--wizard").scrollTo({ top: 0, behavior: "smooth" });
    const selector = `[data-task-step="${step}"] input:not([type=hidden]),[data-task-step="${step}"] select,[data-task-step="${step}"] textarea,[data-task-step="${step}"] button`;
    requestAnimationFrame(() => form.querySelector(selector)?.focus());
  }

  _editorTask(id, options) {
    if (id) return structuredClone(this._data.tasks[id]);
    return this._newTask(options);
  }

  _newTask(options) {
    let schedule = { type: "weekly", weekdays: ["mon"], time: "18:00:00" };
    if (options.scheduleType === "calendar") {
      schedule = { type: "calendar", entity_id: "", match: "", offset: "-12:00:00" };
    }
    return {
      enabled: true,
      name: "",
      assignee: Object.keys(this._data.people)[0] || "",
      assignment: { type: "fixed", people: [] },
      description: "",
      schedule,
    };
  }

  _bindTaskPreview(modal, taskId) {
    const runPreview = async () => {
      const output = modal.querySelector(".task-preview .preview-result");
      try {
        const { value } = this._readTaskForm(modal.querySelector("#task-form"));
        const scenarioValues = [...modal.querySelectorAll("[name=weather_scenario]")]
          .map((input) => input.value.trim());
        const scenario = this._previewScenario(modal, scenarioValues);
        const previewPayload = { type: "household_tasks/preview_task", task: value };
        if (taskId) previewPayload.task_id = taskId;
        if (scenario) previewPayload.scenario = scenario;
        const [preview, projection] = await Promise.all([
          this._hass.callWS(previewPayload),
          this._hass.callWS({ type: "household_tasks/task_projection", task: value }),
        ]);
        output.textContent = this._taskPreviewParts(preview, projection).join(" — ");
        output.className = "preview-result";
      } catch (error) {
        output.textContent = this._errorText(error);
        output.className = "preview-result error";
      }
    };
    modal.querySelector("[data-preview-task]").onclick = runPreview;
    return runPreview;
  }

  _previewScenario(modal, values) {
    if (!values.some((item) => item !== "")) return null;
    return {
      values,
      date: modal.querySelector("[name=scenario_date]")?.value || undefined,
    };
  }

  _taskPreviewParts(preview, projection) {
    const parts = [projection.message];
    const optionalParts = [
      projection.risk === "high" ? "Warnung: Diese Regel könnte ungewöhnlich viele Aufgaben erzeugen." : null,
      preview.next_due ? `Nächste Fälligkeit: ${new Date(preview.next_due).toLocaleString(this._locale())}` : null,
      this._calendarEventPreview(preview.calendar_events, preview.calendar_ignored_events),
      this._stateTriggerPreview(preview.state_triggers),
      preview.mode?.message,
      preview.season?.message,
      this._weatherPreview(preview.weather),
      preview.forecast?.matched_period
        ? `Erster passender Vorhersagetag: ${new Date(preview.forecast.matched_period.datetime).toLocaleDateString(this._locale())}`
        : null,
      preview.forecast?.scenario ? "Testszenario – es wurden keine Live-Vorhersagedaten verwendet." : null,
      this._plannedOccurrencePreview(preview.planned_occurrences),
      this._tracePreview(preview.trace),
      this._creationPreview(preview.would_create),
    ].filter(Boolean);
    parts.push(...optionalParts);
    if (parts.length === 1 && !parts[0]) {
      return [preview.schedule_type === "manual"
        ? "Manuelle Regeln haben keine automatisch berechnete Fälligkeit."
        : "Im Vorschauzeitraum wurde keine Fälligkeit gefunden."];
    }
    return parts.filter(Boolean);
  }

  _stateTriggerPreview(triggers) {
    if (!triggers?.length) return null;
    return triggers.map((item) => {
      const match = item.matches ? " ✓" : "";
      return `${item.entity_id}: aktuell „${item.current ?? "nicht verfügbar"}“, erwartet „${item.wanted}“${match}`;
    }).join(" · ");
  }

  _calendarEventPreview(events, ignored) {
    if (!events?.length && !ignored?.length) return null;
    const included = (events || []).slice(0, 4)
      .map((item) => `${item.summary} → ${item.task_name}`)
      .join(" · ");
    const ignoredTitles = (ignored || []).slice(0, 4)
      .map((item) => item.summary)
      .join(", ");
    const parts = [];
    if (included) parts.push(`Übernommen: ${included}`);
    if (ignoredTitles) parts.push(`Ignoriert, weil nicht zugeordnet: ${ignoredTitles}`);
    return parts.join(" · ");
  }

  _weatherPreview(weather) {
    if (!weather?.conditions?.length) return null;
    const conditions = weather.conditions.map((item) => {
      const attribute = item.attribute ? `.${item.attribute}` : "";
      const match = item.matches ? "✓" : "✗";
      return `${item.entity_id}${attribute}: ${item.current ?? "nicht verfügbar"} ${match}`;
    }).join(" · ");
    return `${weather.message} · ${conditions}`;
  }

  _plannedOccurrencePreview(occurrences) {
    if (!occurrences?.length) return null;
    return occurrences.map((item) => {
      const result = item.would_create ? "würde erzeugt" : item.repetition.message;
      return `${item.target_name} → ${item.assignee_name}: ${result}`;
    }).join(" · ");
  }

  _tracePreview(trace) {
    if (!trace?.length) return null;
    return trace.map((step) => `${step.passed ? "✓" : "✕"} ${step.message}`).join(" · ");
  }

  _creationPreview(wouldCreate) {
    if (wouldCreate === false) return "Diese Regel würde aktuell keine Aufgabe erzeugen.";
    if (wouldCreate === true) return "Diese Regel würde aktuell eine Aufgabe erzeugen.";
    return null;
  }

  _showQuickTask(smart = false) {
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
        ${smart ? `<div class="full smart-capture">
          <label>Natürlich eingeben<input name="smart_text" autofocus placeholder="Müll morgen 18 Uhr an Alex, dringend, 2 Punkte"></label>
          <button type="button" data-parse-smart>Auswerten</button>
          <output class="preview-result" aria-live="polite">Erkennt Aufgabe, Person, Termin, Priorität und Punkte lokal.</output>
        </div>` : ""}
        <label class="full">Was ist zu tun?<input name="name" required ${smart ? "" : "autofocus"} placeholder="Paket zur Post bringen"></label>
        <label>Für wen?<select name="assignee">${people.map(([id, person]) =>
          `<option value="${this._e(id)}" ${id === ownPerson ? "selected" : ""}>${this._e(person.name)}${id === ownPerson ? (householdTasksLocale(this._hass) === "de" ? " (Ich)" : " (me)") : ""}</option>`
        ).join("")}</select></label>
        <label>Fällig<input name="due" type="datetime-local" required value="${this._localDateTimeValue(due)}"></label>
        <label class="full">Notiz<textarea name="description" rows="2" placeholder="Optional"></textarea></label>
        <label>Priorität<select name="priority"><option value="low">Niedrig</option><option value="normal" selected>Normal</option><option value="high">Hoch</option><option value="critical">Kritisch</option></select></label>
        <label>Punkte<input name="points" type="number" min="0" max="100" value="1"></label>
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
        <div class="full modal-actions"><button type="button" data-batch-capture>Mehrere Aufgaben</button><button type="button" class="cancel">Abbrechen</button><button class="primary" type="submit">Aufgabe hinzufügen</button></div>
      </form></div></div>`;
    this._localize(modal);
    const close = () => { modal.innerHTML = ""; returnFocus?.focus(); };
    modal.querySelector(".close").onclick = close;
    modal.querySelector(".cancel").onclick = close;
    modal.querySelector("[data-batch-capture]").onclick = () => {
      close();
      this._showBatchCapture();
    };
    const resetSeason = modal.querySelector("[data-reset-season]");
    if (resetSeason) resetSeason.onclick = async () => {
      if (!await this._confirm("Alle bisherigen Saisonsperren dieser Regel zurücksetzen? Die Regel kann anschließend erneut Aufgaben erzeugen.")) return;
      const result = await this._call("reset_seasonal_executions", { task_id: id });
      this._toast(`${result.seasonal_reset_count || 0} Saisonsperre(n) zurückgesetzt.`);
    };
    this._activateDialog(modal, close);
    modal.querySelector("[name=reminder_mode]").onchange = (event) => {
      modal.querySelector(".quick-escalation").classList.toggle("hidden", event.target.value !== "custom");
    };
    this._bindEscalationEditor(modal);
    modal.querySelector("[data-parse-smart]")?.addEventListener("click", async () => {
      const text = modal.querySelector("[name=smart_text]").value.trim();
      if (!text) return;
      try {
        const preview = await this._hass.callWS({ type: "household_tasks/smart_task_preview", text });
        if (preview.name) modal.querySelector("[name=name]").value = preview.name;
        if (preview.assignee) modal.querySelector("[name=assignee]").value = preview.assignee;
        modal.querySelector("[name=due]").value = this._localDateTimeValue(new Date(preview.due));
        modal.querySelector("[name=priority]").value = preview.priority;
        modal.querySelector("[name=points]").value = preview.points;
        modal.querySelector(".smart-capture output").textContent = preview.missing.length
          ? `Bitte noch prüfen: ${preview.missing.join(", ")}.`
          : `Erkannt: ${preview.name} · ${preview.assignee_name} · ${new Date(preview.due).toLocaleString(this._locale())}.`;
      } catch (error) {
        modal.querySelector(".smart-capture output").textContent = this._errorText(error);
      }
    });
    modal.querySelector("#quick-task-form").onsubmit = async (event) => {
      event.preventDefault();
      const f = new FormData(event.target);
      const payload = {
        name: f.get("name").trim(),
        assignee: f.get("assignee"),
        due: new Date(f.get("due")).toISOString(),
        priority: f.get("priority"),
        points: Number(f.get("points") || 0),
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
    return triggers.map((trigger, index) => {
      const datalist = index === 0
        ? `<datalist id="ht-trigger-entities">${options}</datalist>`
        : "";
      return `<div class="repeatable-row trigger-row">
        <label>Entität<input name="trigger_entity_id" list="ht-trigger-entities" required value="${this._e(trigger.entity_id || "")}" placeholder="Entität suchen"></label>
        <label>Von<input name="trigger_from" value="${this._e(trigger.from || "")}" placeholder="optional"></label>
        <label>Nach<input name="trigger_to" required value="${this._e(trigger.to || "")}" placeholder="z. B. on"></label>
        <label>Für (HH:MM:SS)<input name="trigger_for" value="${this._e(trigger.for || "")}" pattern="[0-9]+:[0-5][0-9]:[0-5][0-9]" placeholder="optional"></label>
        <button type="button" class="remove-row" title="Auslöser entfernen" aria-label="Auslöser entfernen">×</button>
        ${datalist}
      </div>`;
    }).join("");
  }

  _weatherRows(conditions = []) {
    const items = conditions.length ? conditions : [{}];
    const options = this._entitySuggestions(["weather", "sensor", "binary_sensor"])
      .map((item) => `<option value="${this._e(item.value)}">${this._e(item.label)} · ${this._e(item.detail)}</option>`)
      .join("");
    return items.map((condition, index) => `<div class="repeatable-row weather-row">
      <label>Entität<input name="weather_entity_id" list="ht-weather-entities" required value="${this._e(condition.entity_id || "")}" placeholder="weather.home oder sensor.aussentemperatur"></label>
      <label>Attribut<input name="weather_attribute" value="${this._e(condition.attribute || "")}" placeholder="leer = Zustand, z. B. temperature"></label>
      <label>Vergleich<select name="weather_condition">
        ${[["below","kleiner als"],["at_most","höchstens"],["above","größer als"],["at_least","mindestens"],["equals","gleich"],["not_equals","ungleich"]].map(([value,label]) => `<option value="${value}" ${condition.condition === value ? "selected" : ""}>${label}</option>`).join("")}
      </select></label>
      <label>Grenzwert<input name="weather_threshold" required value="${this._e(condition.threshold ?? "")}" placeholder="2, 28 oder rainy"></label>
      <label>Testwert (optional)<input name="weather_scenario" placeholder="z. B. -3"><span class="hint">Nur für die Vorschau; wird nicht gespeichert.</span></label>
      <button type="button" class="remove-row" title="Wetterbedingung entfernen" aria-label="Wetterbedingung entfernen">×</button>
      ${index === 0 ? `<datalist id="ht-weather-entities">${options}</datalist>` : ""}
    </div>`).join("");
  }

  _calendarMappingRows(mappings = []) {
    if (!mappings.length) return `<p class="empty-row">Noch keine Titelzuordnung angelegt.</p>`;
    return mappings.map((mapping) => `<div class="repeatable-row calendar-mapping-row">
      <label>Titel-Muster (Regex)<input name="calendar_mapping_pattern" required value="${this._e(mapping.pattern || "")}" placeholder="gelb|gelber sack"></label>
      <label>Aufgabenname<input name="calendar_mapping_task" required value="${this._e(mapping.task_title || "")}" placeholder="Gelbe Tonne rausstellen"></label>
      <button type="button" class="remove-row" title="Titelzuordnung entfernen" aria-label="Titelzuordnung entfernen">×</button>
    </div>`).join("");
  }

  _createCalendarMappingRow(mapping = {}) {
    const template = document.createElement("template");
    template.innerHTML = this._calendarMappingRows([mapping]);
    return template.content.querySelector(".calendar-mapping-row");
  }

  _createFollowUpRow(followUp = {}, currentTaskId = null) {
    const row = document.createElement("div");
    row.className = "repeatable-row follow-up-row";

    const taskSelect = document.createElement("select");
    taskSelect.name = "follow_up_task_id";
    taskSelect.required = true;
    taskSelect.add(new Option("Bitte auswählen", ""));
    for (const [taskId, task] of Object.entries(this._data?.tasks || {})) {
      if (taskId === currentTaskId) continue;
      const option = new Option(`${task.name} · ${taskId}`, taskId);
      option.selected = taskId === followUp.task_id;
      taskSelect.add(option);
    }

    const delay = document.createElement("input");
    delay.name = "follow_up_delay";
    delay.required = true;
    delay.value = followUp.delay || "00:00:00";
    delay.pattern = "-?[0-9]+:[0-5][0-9]:[0-5][0-9]";

    row.append(
      this._labeledControl("Vorlage", taskSelect),
      this._labeledControl("Verzögerung (HH:MM:SS)", delay),
      this._removeRowButton("Folgeaufgabe entfernen"),
    );
    return row;
  }

  _createTriggerRow(trigger = {}) {
    const row = document.createElement("div");
    row.className = "repeatable-row trigger-row";

    const entity = document.createElement("input");
    entity.name = "trigger_entity_id";
    entity.setAttribute("list", "ht-trigger-entities");
    entity.required = true;
    entity.value = trigger.entity_id || "";
    entity.placeholder = "Entität suchen";

    const from = document.createElement("input");
    from.name = "trigger_from";
    from.value = trigger.from || "";
    from.placeholder = "optional";

    const to = document.createElement("input");
    to.name = "trigger_to";
    to.required = true;
    to.value = trigger.to || "";
    to.placeholder = "z. B. on";

    const duration = document.createElement("input");
    duration.name = "trigger_for";
    duration.value = trigger.for || "";
    duration.pattern = "[0-9]+:[0-5][0-9]:[0-5][0-9]";
    duration.placeholder = "optional";

    row.append(
      this._labeledControl("Entität", entity),
      this._labeledControl("Von", from),
      this._labeledControl("Nach", to),
      this._labeledControl("Für (HH:MM:SS)", duration),
      this._removeRowButton("Auslöser entfernen"),
    );
    return row;
  }

  _ensureTriggerDatalist(list) {
    if (list.querySelector("#ht-trigger-entities")) return;
    const datalist = document.createElement("datalist");
    datalist.id = "ht-trigger-entities";
    for (const item of this._entitySuggestions()) {
      datalist.append(new Option(`${item.label} · ${item.detail}`, item.value));
    }
    list.append(datalist);
  }

  _bindTriggerEditor(modal) {
    const editor = modal.querySelector(".trigger-editor");
    if (!editor) return;
    const bindRemovers = () => {
      editor.querySelectorAll(".remove-row").forEach((button) => {
        button.onclick = () => {
          button.closest(".trigger-row").remove();
          if (!editor.querySelector(".trigger-row")) {
            editor.querySelector(".repeatable-list").replaceChildren(
              this._emptyRepeatableRow("Noch kein Auslöser ausgewählt."),
            );
          } else {
            this._ensureTriggerDatalist(editor.querySelector(".repeatable-list"));
          }
        };
      });
    };
    editor.querySelector("[data-add-trigger]").onclick = () => {
      const list = editor.querySelector(".repeatable-list");
      this._removeRepeatableEmptyState(list);
      const row = this._createTriggerRow();
      list.append(row);
      this._ensureTriggerDatalist(list);
      this._localize(row);
      this._enhanceAccessibility(modal);
      bindRemovers();
      row.querySelector("[name=trigger_entity_id]")?.focus();
    };
    this._ensureTriggerDatalist(editor.querySelector(".repeatable-list"));
    bindRemovers();
  }

  _bindWeatherEditor(modal) {
    const editor = modal.querySelector(".weather-editor");
    if (!editor) return;
    const bindRemovers = () => {
      editor.querySelectorAll(".remove-row").forEach((button) => {
        button.onclick = () => {
          if (editor.querySelectorAll(".weather-row").length <= 1) return;
          button.closest(".weather-row").remove();
        };
      });
    };
    editor.querySelector("[data-add-weather]").onclick = () => {
      const template = document.createElement("template");
      template.innerHTML = this._weatherRows([{}]);
      const row = template.content.querySelector(".weather-row");
      row.querySelector("datalist")?.remove();
      editor.querySelector(".repeatable-list").append(row);
      this._localize(row);
      this._enhanceAccessibility(modal);
      bindRemovers();
      row.querySelector("[name=weather_entity_id]")?.focus();
    };
    bindRemovers();
  }

  _bindCalendarMappingEditor(modal) {
    const editor = modal.querySelector(".calendar-mapping-editor");
    if (!editor) return;
    const list = editor.querySelector(".repeatable-list");
    const bindRemovers = () => {
      editor.querySelectorAll(".remove-row").forEach((button) => {
        button.onclick = () => {
          button.closest(".calendar-mapping-row").remove();
          if (!editor.querySelector(".calendar-mapping-row")) {
            list.replaceChildren(this._emptyRepeatableRow("Noch keine Titelzuordnung angelegt."));
          }
        };
      });
    };
    editor.querySelector("[data-add-calendar-mapping]").onclick = () => {
      this._removeRepeatableEmptyState(list);
      const row = this._createCalendarMappingRow();
      list.append(row);
      this._localize(row);
      this._enhanceAccessibility(row);
      bindRemovers();
      row.querySelector("[name=calendar_mapping_pattern]")?.focus();
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
            editor.querySelector(".repeatable-list").replaceChildren(
              this._emptyRepeatableRow("Noch keine Folgeaufgabe ausgewählt."),
            );
          }
        };
      });
    };
    editor.querySelector("[data-add-follow-up]").onclick = () => {
      const list = editor.querySelector(".repeatable-list");
      this._removeRepeatableEmptyState(list);
      const row = this._createFollowUpRow({}, currentTaskId);
      list.append(row);
      this._localize(row);
      this._enhanceAccessibility(modal);
      bindRemovers();
      row.querySelector("select")?.focus();
    };
    bindRemovers();
    this._bindTriggerEditor(modal);
    this._bindWeatherEditor(modal);
    this._bindCalendarMappingEditor(modal);
  }

  _bindInlineTaskCreates(modal, currentTaskId) {
    const personPanel = modal.querySelector(".inline-person-form");
    const personToggle = modal.querySelector("[data-toggle-inline-person]");
    this._setInlinePanelOpen(personPanel, personToggle, false);
    personToggle.onclick = () => {
      const open = personPanel.classList.contains("hidden");
      this._setInlinePanelOpen(personPanel, personToggle, open);
      if (open) personPanel.querySelector("[name=inline_person_id]")?.focus();
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
        fixed.add(new Option(name, personId, true, true));

        const candidate = document.createElement("label");
        candidate.className = "checkbox";
        const candidateInput = document.createElement("input");
        candidateInput.type = "checkbox";
        candidateInput.name = "assignment_person";
        candidateInput.value = personId;
        candidateInput.checked = true;
        candidate.append(candidateInput, document.createTextNode(` ${name}`));
        modal.querySelector(".candidate-grid").append(candidate);

        const followUpAssignee = modal.querySelector("[name=inline_follow_up_assignee]");
        followUpAssignee?.add(new Option(name, personId));
        this._setInlinePanelOpen(personPanel, personToggle, false);
        this._toast("Person angelegt und ausgewählt.");
      } catch (error) {
        this._toast(this._errorText(error), true);
      }
    };

    const followUpPanel = modal.querySelector(".inline-follow-up-form");
    const followUpToggle = modal.querySelector("[data-toggle-inline-follow-up]");
    this._setInlinePanelOpen(followUpPanel, followUpToggle, false);
    followUpToggle.onclick = () => {
      const open = followUpPanel.classList.contains("hidden");
      this._setInlinePanelOpen(followUpPanel, followUpToggle, open);
      if (open) followUpPanel.querySelector("[name=inline_follow_up_id]")?.focus();
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
        for (const select of editor.querySelectorAll("[name=follow_up_task_id]")) {
          if (![...select.options].some((option) => option.value === taskId)) {
            select.add(new Option(`${name} · ${taskId}`, taskId));
          }
        }
        const list = editor.querySelector(".repeatable-list");
        this._removeRepeatableEmptyState(list);
        const row = this._createFollowUpRow({ task_id: taskId, delay: "00:00:00" }, currentTaskId);
        list.append(row);
        this._localize(row);
        this._bindRepeatableEditors(modal, currentTaskId);
        this._setInlinePanelOpen(followUpPanel, followUpToggle, false);
        this._toast("Vorlage angelegt und als Folgeaufgabe ausgewählt.");
      } catch (error) {
        this._toast(this._errorText(error), true);
      }
    };
  }

  _setInlinePanelOpen(panel, toggle, open) {
    panel.classList.toggle("hidden", !open);
    panel.toggleAttribute("inert", !open);
    panel.setAttribute("aria-hidden", String(!open));
    toggle.setAttribute("aria-expanded", String(open));
    for (const control of panel.querySelectorAll("input, select, textarea, button")) {
      control.disabled = !open;
    }
  }

  _scheduleFields(s, weather = {}) {
    const renderers = {
      manual: () => '<p class="hint">Diese Vorlage wird nur über „Jetzt erzeugen“ oder eine Automation angelegt.</p>',
      weekly: () => this._weeklyScheduleFields(s),
      monthly: () => this._monthlyScheduleFields(s),
      yearly: () => this._yearlyScheduleFields(s),
      interval_months: () => this._intervalScheduleFields(s),
      calendar: () => this._calendarScheduleFields(s),
      after_completion: () => this._afterCompletionFields(s),
      flexible_after_completion: () => this._flexibleCompletionFields(s),
      weather_trigger: () => this._weatherTriggerFields(s, weather),
      forecast_trigger: () => this._forecastTriggerFields(s, weather),
      state_trigger: () => this._stateTriggerFields(s),
      daily_after_state: () => this._dailyStateFields(s),
    };
    return (renderers[s.type] || renderers.daily_after_state)();
  }

  _scheduleTime(schedule) {
    return this._e(schedule.time || "18:00:00");
  }

  _weeklyScheduleFields(s) {
    const weekdays = this._weekdays().map(([value, label]) => {
      const checked = (s.weekdays || []).includes(value) ? "checked" : "";
      return `<label><input type="checkbox" name="weekday" value="${value}" ${checked}><span>${label}</span></label>`;
    }).join("");
    return `<div class="weekdays">${weekdays}</div><label>Uhrzeit<input name="time" type="time" step="1" value="${this._scheduleTime(s)}"></label>`;
  }

  _monthlyScheduleFields(s) {
    return `<div class="form-grid"><label>Tag (1–31 oder last)<input name="day" value="${this._e(s.day || 1)}"></label><label>Uhrzeit<input name="time" type="time" step="1" value="${this._scheduleTime(s)}"></label></div>`;
  }

  _yearlyScheduleFields(s) {
    return `<div class="form-grid"><label>Monat<input name="month" type="number" min="1" max="12" value="${this._e(s.month || 1)}"></label><label>Tag<input name="day" value="${this._e(s.day || 1)}"></label><label>Uhrzeit<input name="time" type="time" step="1" value="${this._scheduleTime(s)}"></label></div>`;
  }

  _intervalScheduleFields(s) {
    const start = this._e(s.start || new Date().toISOString().slice(0, 10));
    return `<div class="form-grid"><label>Intervall in Monaten<input name="months" type="number" min="1" value="${this._e(s.months || 6)}"></label><label>Startdatum<input name="start" type="date" value="${start}"></label><label>Uhrzeit<input name="time" type="time" step="1" value="${this._scheduleTime(s)}"></label></div>`;
  }

  _calendarScheduleFields(s) {
    const entity = this._entityInput("entity_id", s.entity_id || "", ["calendar"], { required: true, placeholder: "Kalender suchen", hint: "Wähle eine vorhandene Kalender-Entität aus Home Assistant." });
    return `<div class="calendar-guide">
      <p class="hint"><strong>1. Kalender wählen</strong> · <strong>2. Termine filtern</strong> · <strong>3. Fälligkeit relativ zum Termin festlegen</strong> · anschließend unten die Regel testen.</p>
      <div class="form-grid"><label>1 · Kalender-Entität${entity}</label>
      <label>2 · Suchmuster<input name="match" value="${this._e(s.match || "")}" placeholder="Restmüll"><span class="hint">Leer berücksichtigt alle Termine. Groß-/Kleinschreibung spielt keine Rolle.</span></label>
      <label>3 · Versatz HH:MM:SS<input name="offset" value="${this._e(s.offset || "-12:00:00")}"><span class="hint">Negativ bedeutet vor dem Termin, z. B. −12 Stunden.</span></label>
      <label class="checkbox full"><input name="use_event_title" type="checkbox" ${s.use_event_title ? "checked" : ""}> Kalendertitel für Aufgabenname verwenden<span class="hint">Ohne passende Zuordnungszeile kann der unveränderte Kalendertitel verwendet werden.</span></label>
      <div class="full repeatable-editor calendar-mapping-editor">
        <span class="field-label">Kalendertitel zuordnen</span>
        <div class="repeatable-list">${this._calendarMappingRows(s.title_mappings || [])}</div>
        <button type="button" class="add-row" data-add-calendar-mapping>+ Titelzuordnung</button>
        <label class="checkbox"><input name="ignore_unmapped_events" type="checkbox" ${s.ignore_unmapped_events !== false ? "checked" : ""}> Nicht zugeordnete Termine ignorieren</label>
        <p class="hint">Die Muster sind reguläre Ausdrücke, werden ohne Beachtung der Groß-/Kleinschreibung geprüft und von oben nach unten ausgewertet. Beispiel: <code>gelb|gelber sack</code>.</p>
      </div></div></div>`;
  }

  _completionStart(s) {
    return this._localDateTimeValue(s.start ? new Date(s.start) : new Date());
  }

  _afterCompletionFields(s) {
    return `<div class="form-grid">
      <label>Erstmals fällig<input name="start" type="datetime-local" required value="${this._completionStart(s)}"></label>
      <label>Intervall nach Erledigung (HH:MM:SS)<input name="interval" required value="${this._e(s.interval || "168:00:00")}"></label>
      </div>`;
  }

  _flexibleCompletionFields(s) {
    return `<div class="form-grid">
      <label class="full">Erstmals fällig<input name="start" type="datetime-local" required value="${this._completionStart(s)}"></label>
      <label>Frühestens danach<input name="earliest_interval" required value="${this._e(s.earliest_interval || "120:00:00")}"><span class="hint">HH:MM:SS</span></label>
      <label>Bevorzugt danach<input name="preferred_interval" required value="${this._e(s.preferred_interval || "168:00:00")}"><span class="hint">HH:MM:SS</span></label>
      <label>Spätestens danach<input name="latest_interval" required value="${this._e(s.latest_interval || "240:00:00")}"><span class="hint">HH:MM:SS</span></label>
      <p class="hint full">Die Aufgabe wird zum bevorzugten Zeitpunkt fällig; frühestes und spätestes Fenster bleiben sichtbar.</p>
      </div>`;
  }

  _weatherLogicOptions(weather) {
    const all = weather.logic !== "any" ? "selected" : "";
    const any = weather.logic === "any" ? "selected" : "";
    return `<option value="all" ${all}>Alle Bedingungen (UND)</option><option value="any" ${any}>Mindestens eine (ODER)</option>`;
  }

  _weatherTriggerFields(s, weather) {
    const rows = this._weatherRows(weather.conditions || [{}]);
    const checked = s.skip_if_open !== false ? "checked" : "";
    return `<div class="form-grid">
      <label>Verknüpfung<select name="weather_logic">${this._weatherLogicOptions(weather)}</select></label>
      <label>Fällig nach (HH:MM:SS)<input name="due_after" value="${this._e(s.due_after || "00:00:00")}"></label>
      <label>Cooldown (HH:MM:SS)<input name="cooldown" value="${this._e(s.cooldown || "24:00:00")}"></label>
      <label class="checkbox"><input name="skip_if_open" type="checkbox" ${checked}> Nicht erneut erzeugen, solange offen</label>
      <div class="full repeatable-editor weather-editor"><span class="field-label">Wetterbedingungen</span>
        <div class="repeatable-list">${rows}</div>
        <button type="button" class="add-row" data-add-weather>+ Wetterbedingung</button>
        <p class="hint">Sensorzustände funktionieren ohne Attribut. Bei weather.* können beispielsweise temperature, humidity, wind_speed oder precipitation_probability verwendet werden.</p>
      </div></div>`;
  }

  _forecastTriggerFields(s, weather) {
    const rows = this._weatherRows(weather.conditions || [{ attribute: "templow", condition: "below", threshold: 0 }]);
    const daily = s.forecast_type !== "hourly" ? "selected" : "";
    const hourly = s.forecast_type === "hourly" ? "selected" : "";
    const checked = s.skip_if_open !== false ? "checked" : "";
    return `<div class="form-grid">
      <label>Vorhersagetyp<select name="forecast_type"><option value="daily" ${daily}>Täglich</option><option value="hourly" ${hourly}>Stündlich</option></select></label>
      <label>Prüfzeitraum in Stunden<input name="horizon_hours" type="number" min="1" max="240" value="${Number(s.horizon_hours || 48)}"><span class="hint">Wie weit die Home-Assistant-Vorhersage voraus geprüft wird.</span></label>
      <label>Tage Vorlauf<input name="lead_days" type="number" min="0" max="7" value="${Number(s.lead_days ?? 1)}"><span class="hint">1 bedeutet: Aufgabe am Vortag bereitstellen.</span></label>
      <label>Bereitstellen um<input name="time" type="time" step="1" value="${this._scheduleTime(s)}"></label>
      <label>Verknüpfung<select name="weather_logic">${this._weatherLogicOptions(weather)}</select></label>
      <label>Cooldown (HH:MM:SS)<input name="cooldown" value="${this._e(s.cooldown || "24:00:00")}"></label>
      <label class="checkbox full"><input name="skip_if_open" type="checkbox" ${checked}> Nicht erneut für eine Person erzeugen, solange ihre Aufgabe offen ist</label>
      <label class="full">Testdatum (optional)<input name="scenario_date" type="date"><span class="hint">Zusammen mit den Testwerten kannst du ein hypothetisches Szenario prüfen, ohne Aufgaben anzulegen.</span></label>
      <div class="full repeatable-editor weather-editor"><span class="field-label">Vorhersagebedingungen</span>
        <div class="repeatable-list">${rows}</div>
        <button type="button" class="add-row" data-add-weather>+ Vorhersagebedingung</button>
        <p class="hint">Wähle eine weather.*-Entität. Typische tägliche Werte sind templow, temperature, precipitation_probability und wind_speed.</p>
      </div></div>`;
  }

  _stateTriggerFields(s) {
    const rows = this._triggerRows(s.triggers || [{ entity_id: "", from: "off", to: "on" }]);
    const checked = s.skip_if_open !== false ? "checked" : "";
    return `<div class="form-grid">
      <label>Fällig nach (HH:MM:SS)<input name="due_after" value="${this._e(s.due_after || "00:00:00")}"></label>
      <label>Cooldown (HH:MM:SS)<input name="cooldown" value="${this._e(s.cooldown || "00:00:00")}"></label>
      <label class="checkbox full"><input name="skip_if_open" type="checkbox" ${checked}> Nicht erneut erzeugen, solange die Aufgabe offen ist</label>
      <div class="full repeatable-editor trigger-editor"><span class="field-label">Auslöser</span>
        <div class="repeatable-list">${rows}</div>
        <button type="button" class="add-row" data-add-trigger>+ Auslöser</button>
        <p class="hint">Entität auswählen und Zielzustand angeben; Ausgangszustand und Dauer sind optional.</p>
      </div></div>`;
  }

  _dailyStateFields(s) {
    const rows = this._triggerRows(s.triggers || [{ entity_id: "", from: "on", to: "off" }]);
    return `<div class="form-grid"><label>Fälligkeit<input name="time" type="time" step="1" value="${this._scheduleTime(s)}"></label>
      <div class="full repeatable-editor trigger-editor"><span class="field-label">Auslöser</span>
        <div class="repeatable-list">${rows}</div>
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
      if (f.get("use_event_title") === "on") schedule.use_event_title = true;
      const mappingPatterns = f.getAll("calendar_mapping_pattern");
      const mappingTitles = f.getAll("calendar_mapping_task");
      if (mappingPatterns.length) {
        const seenPatterns = new Set();
        schedule.title_mappings = mappingPatterns.map((patternValue, index) => {
          const titleValue = mappingTitles[index];
          if (typeof patternValue !== "string" || typeof titleValue !== "string") {
            throw new Error("Die Titelzuordnung enthält ungültige Formulardaten.");
          }
          const pattern = patternValue.trim();
          const taskTitle = titleValue.trim();
          if (!pattern || !taskTitle) throw new Error("Jede Titelzuordnung benötigt ein Muster und einen Aufgabennamen.");
          try { new RegExp(pattern, "i"); } catch (_error) { throw new Error(`Ungültiger regulärer Ausdruck: ${pattern}`); }
          const normalized = pattern.toLowerCase();
          if (seenPatterns.has(normalized)) throw new Error(`Das Titel-Muster „${pattern}“ ist doppelt vorhanden.`);
          seenPatterns.add(normalized);
          return { pattern, task_title: taskTitle };
        });
        schedule.ignore_unmapped_events = f.get("ignore_unmapped_events") === "on";
      }
    }
    if (type === "after_completion") {
      schedule.start = new Date(f.get("start")).toISOString();
      schedule.interval = f.get("interval");
    }
    if (type === "flexible_after_completion") {
      schedule.start = new Date(f.get("start")).toISOString();
      schedule.earliest_interval = f.get("earliest_interval");
      schedule.preferred_interval = f.get("preferred_interval");
      schedule.latest_interval = f.get("latest_interval");
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
    let weather = null;
    if (["weather_trigger", "forecast_trigger"].includes(type)) {
      if (type === "weather_trigger") schedule.due_after = f.get("due_after") || "00:00:00";
      schedule.cooldown = f.get("cooldown") || "24:00:00";
      schedule.skip_if_open = f.get("skip_if_open") === "on";
      if (type === "forecast_trigger") {
        schedule.forecast_type = f.get("forecast_type") || "daily";
        schedule.horizon_hours = Number(f.get("horizon_hours") || 48);
        schedule.lead_days = Number(f.get("lead_days") || 1);
        schedule.time = f.get("time") || "18:00:00";
      }
      const entityIds = f.getAll("weather_entity_id");
      const attributes = f.getAll("weather_attribute");
      const conditions = f.getAll("weather_condition");
      const thresholds = f.getAll("weather_threshold");
      weather = {
        logic: f.get("weather_logic") || "all",
        conditions: entityIds.map((entityId, index) => {
          const normalizedId = entityId.trim();
          if (!this._hass.states[normalizedId]) throw new Error(`Die Wetter-Entität „${normalizedId}“ existiert nicht.`);
          if (type === "forecast_trigger" && !normalizedId.startsWith("weather.")) throw new Error("Vorhersageregeln benötigen eine weather.*-Entität.");
          if (type === "forecast_trigger" && !attributes[index]?.trim()) throw new Error("Für Vorhersagen muss ein Attribut wie templow ausgewählt sein.");
          return {
            entity_id: normalizedId,
            attribute: attributes[index]?.trim() || "",
            condition: conditions[index] || "below",
            threshold: thresholds[index]?.trim(),
          };
        }),
      };
    }
    const assignmentType = f.get("assignment_type") || "fixed";
    const assignmentPeople = f.getAll("assignment_person");
    if (["rotation", "fair", "per_person"].includes(assignmentType) && !assignmentPeople.length) {
      throw new Error("Für diese Zuweisung muss mindestens eine Person ausgewählt sein.");
    }
    const value = {
      enabled: f.get("enabled") === "on", name: f.get("name").trim(),
      schedule, assignment: { type: assignmentType },
    };
    const rawPausedUntil = f.get("paused_until");
    if (value.enabled && rawPausedUntil) {
      const pausedUntil = new Date(rawPausedUntil);
      if (Number.isNaN(pausedUntil.getTime()) || pausedUntil <= new Date()) {
        throw new Error("Die temporäre Pause muss in der Zukunft enden.");
      }
      value.paused_until = pausedUntil.toISOString();
    }
    if (f.get("presence_required") === "on") value.assignment.presence_required = true;
    if (assignmentType === "fixed") {
      value.assignee = f.get("assignee");
      if (value.assignment.presence_required) {
        const absencePolicy = f.get("absence_policy") || "wait";
        const fallbackPeople = f.getAll("fallback_person").filter((personId) => personId !== value.assignee);
        if (["fallback", "open"].includes(absencePolicy) && !fallbackPeople.length) {
          throw new Error("Bitte mindestens eine ausdrückliche Ersatzperson auswählen.");
        }
        value.assignment.absence_policy = absencePolicy;
        if (fallbackPeople.length) value.assignment.fallback_people = fallbackPeople;
        if (absencePolicy === "fallback") value.assignment.fallback_strategy = f.get("fallback_strategy") || "fair";
      }
    }
    else if (assignmentPeople.length) value.assignment.people = assignmentPeople;
    if (f.get("description")?.trim()) value.description = f.get("description").trim();
    const checklist = String(f.get("checklist") || "").split("\n").map((line) => line.trim()).filter(Boolean);
    if (checklist.length) value.checklist = checklist.map((title, index) => ({ id: `step_${index + 1}`, title }));
    value.require_checklist_completion = f.get("require_checklist_completion") === "on";
    const dependencies = f.getAll("depends_on").map(String);
    if (dependencies.length) value.depends_on = dependencies;
    const followUpIds = f.getAll("follow_up_task_id");
    const followUpDelays = f.getAll("follow_up_delay");
    if (followUpIds.length) {
      value.follow_ups = followUpIds.map((taskIdValue, index) => {
        const taskId = String(taskIdValue);
        if (!this._data.tasks[taskId]) throw new Error(`Die Folgeaufgabe „${taskId}“ existiert nicht.`);
        return { task_id: taskId, delay: followUpDelays[index] || "00:00:00" };
      });
    }
    if (f.get("nfc_tag_id")?.trim()) value.nfc = {
      tag_id: f.get("nfc_tag_id").trim(),
      action: f.get("nfc_action") || "create_or_complete",
    };
    if (weather) value.weather = weather;
    value.market = {
      priority: f.get("market_priority") || "normal",
      points: Math.max(0, Number(f.get("market_points") || 0)),
    };
    if (f.get("automatic_completion") === "on") {
      const defaultPerson = f.get("automatic_completion_person");
      const after = String(f.get("automatic_completion_after") || "12:00:00").trim();
      if (!this._data.people[defaultPerson]) throw new Error("Bitte eine vorhandene Standardperson für die automatische Gutschrift auswählen.");
      if (!/^\d+:[0-5]\d:[0-5]\d$/.test(after)) throw new Error("Die Kulanzzeit muss im Format HH:MM:SS angegeben werden.");
      value.automatic_completion = { enabled: true, default_person: defaultPerson, after };
    }
    if (f.get("market_reward")?.trim()) value.market.reward = f.get("market_reward").trim();
    value.modes = { vacation: f.get("vacation_behavior") || "pause" };
    if (f.get("guest_only") === "on") value.modes.guest_only = true;
    if (f.get("skip_in_guest") === "on") value.modes.skip_in_guest = true;
    if (f.get("season_enabled") === "on") {
      const seasonMonths = f.get("season_months");
      const months = (typeof seasonMonths === "string" ? seasonMonths : "")
        .split(",").map((month) => Number(month.trim())).filter(Boolean);
      if (months.some((month) => month < 1 || month > 12)) throw new Error("Saisonmonate müssen zwischen 1 und 12 liegen.");
      value.season = { months };
      if (f.get("season_condition")) {
        const entityId = f.get("season_entity_id")?.trim();
        if (!entityId || !this._hass.states[entityId]) throw new Error("Bitte wähle eine vorhandene Saison-Entität aus.");
        value.season.entity_id = entityId;
        value.season.condition = f.get("season_condition");
        value.season.threshold = f.get("season_threshold")?.trim() || "";
      }
    }
    if (f.get("once_per_season") === "on") {
      if (!value.season?.months?.length) throw new Error("Einmal pro Saison benötigt mindestens einen Saisonmonat.");
      value.repeat = { mode: "once_per_season" };
    }
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
      if (!entityId) {
        output.textContent = "Keine Anwesenheits-Entität ausgewählt.";
      } else if (state) {
        const presence = state.state === "home" ? " – anwesend" : "";
        output.textContent = `Aktueller Zustand: „${state.state}“${presence}.`;
      } else {
        output.textContent = "Entität wurde nicht gefunden.";
      }
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
      *{box-sizing:border-box}button,input,select,textarea{font:inherit;color:inherit}main{--ht-accent-text:color-mix(in srgb,var(--primary-color,#03a9f4) 55%,var(--primary-text-color,#202124));max-width:1180px;margin:auto;padding:24px}
      button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible,summary:focus-visible,.people-strip:focus-visible{outline:3px solid color-mix(in srgb,var(--primary-color,#03a9f4) 70%,white);outline-offset:2px}
      header{display:flex;align-items:center;justify-content:space-between;margin-bottom:18px}h1{font-size:30px;margin:2px 0}h2{margin:0;font-size:24px}h3{margin:0}p{color:var(--secondary-text-color,#6b7280)}.header-actions{display:flex;align-items:center;gap:8px}.mode-badge{padding:6px 9px;border-radius:99px;background:#fff1bf;color:#765600;font-size:12px;font-weight:800}
      .eyebrow{font-size:11px;letter-spacing:.13em;font-weight:800;color:var(--ht-accent-text);text-transform:uppercase}
      nav{display:flex;gap:4px;border-bottom:1px solid var(--divider-color,#ddd);overflow:auto;margin-bottom:24px}nav a{border:0;background:none;padding:12px 16px;color:var(--secondary-text-color);cursor:pointer;white-space:nowrap;border-bottom:3px solid transparent;text-decoration:none}nav a.active{color:var(--ht-accent-text);border-color:var(--primary-color);font-weight:700}
      button{border:1px solid var(--divider-color,#d6d9de);border-radius:10px;padding:9px 14px;background:var(--card-background-color,#fff);cursor:pointer;font-weight:650}button:hover{filter:brightness(.97)}button:disabled{opacity:.55}.primary{background:color-mix(in srgb,var(--primary-color,#03a9f4) 88%,#000);color:var(--text-primary-color,#fff);border-color:transparent}.icon-button{width:44px;height:44px;border-radius:50%;font-size:22px;padding:0}
      .context-add{position:fixed;z-index:20;right:28px;bottom:28px;width:56px;height:56px;border:0;border-radius:50%;background:var(--primary-color);color:#fff;font-size:28px;box-shadow:0 6px 20px #0004}
      .hero{display:flex;justify-content:space-between;align-items:center;border-radius:20px;padding:24px 28px;background:linear-gradient(135deg,var(--primary-color,#03a9f4),#536dfe);color:#fff;margin-bottom:16px}.hero .eyebrow,.hero p{color:#e9f7ff}.hero h2{font-size:28px}.hero-side{display:flex;align-items:center;gap:18px}.hero-button{background:#fff;color:#3347ba;border:0}.score{font-size:36px;font-weight:800;text-align:center}.score small{display:block;font-size:12px;font-weight:500}
      .people-strip{display:flex;gap:9px;overflow:auto;padding:4px 0 18px}.person-chip{display:flex;align-items:center;gap:8px;background:var(--card-background-color,#fff);border:1px solid var(--divider-color,#ddd);border-radius:99px;padding:6px 10px 6px 6px;white-space:nowrap}.person-chip b{color:var(--secondary-text-color)}
      .context-home{display:flex;align-items:center;justify-content:space-between;gap:18px;padding:16px 18px;margin-bottom:14px;border-radius:16px;background:linear-gradient(135deg,color-mix(in srgb,var(--primary-color) 15%,var(--card-background-color)),var(--card-background-color));border:1px solid color-mix(in srgb,var(--primary-color) 25%,var(--divider-color))}.context-home h2{margin:2px 0}.context-home p{margin:4px 0 0;color:var(--secondary-text-color)}.stack-strip{display:flex;align-items:center;gap:8px;overflow:auto;margin-bottom:14px;padding:10px;border:1px solid var(--divider-color);border-radius:13px}.stack-strip>*{white-space:nowrap}.stack-strip small{margin-left:5px}.habit-tip,.due-window,.attachment-count{font-size:12px;color:var(--secondary-text-color)}.planner-list{display:grid;gap:8px;max-height:55vh;overflow:auto;margin:14px 0}.planner-row{display:grid;grid-template-columns:1fr minmax(210px,auto);align-items:center;gap:12px;padding:10px;border:1px solid var(--divider-color);border-radius:11px}.planner-row span{display:grid}.planner-row small{color:var(--secondary-text-color)}.batch-preview{display:grid;gap:6px;margin:12px 0}.batch-preview>div{display:flex;justify-content:space-between;gap:10px;padding:9px;border-radius:9px;background:var(--secondary-background-color)}.batch-preview .error{border-left:3px solid var(--error-color)}.attachment-list,.stack-list{display:grid;gap:8px;margin:12px 0}.attachment-list>div,.stack-list article{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:9px;border:1px solid var(--divider-color);border-radius:10px}.stack-list article>div{display:grid}.week-day.drag-over{outline:3px solid var(--primary-color);background:color-mix(in srgb,var(--primary-color) 10%,var(--card-background-color))}.week-day [draggable=true]{cursor:grab}.quick-presets{display:flex;gap:6px;flex-wrap:wrap}
      .favorite-strip{display:flex;align-items:center;gap:8px;overflow:auto;margin-bottom:14px;padding:11px;border:1px solid var(--divider-color);border-radius:13px;background:var(--card-background-color)}.favorite-strip>*{white-space:nowrap}.bulk-toolbar{position:sticky;top:0;z-index:10;display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:10px;margin-bottom:12px;border:1px solid var(--divider-color);border-radius:12px;background:var(--card-background-color);box-shadow:0 2px 8px #0001}.bulk-count{margin-right:auto;color:var(--secondary-text-color);font-size:12px}.selectable-task{display:grid;grid-template-columns:auto 1fr;align-items:center;gap:8px}.selectable-task>.task-card{min-width:0}.week-board{display:grid;grid-template-columns:repeat(7,minmax(150px,1fr));gap:8px;overflow:auto;padding-bottom:8px}.week-day{min-height:230px;padding:10px;border:1px solid var(--divider-color);border-radius:13px;background:var(--card-background-color)}.week-day>header{display:grid;grid-template-columns:1fr auto;gap:2px;margin-bottom:10px}.week-day>header span{font-size:11px;color:var(--secondary-text-color)}.week-day>header b{grid-row:1/3;grid-column:2;align-self:center}.week-day>article{display:flex;gap:6px;padding:8px;margin-bottom:6px;border-radius:9px;background:var(--secondary-background-color)}.week-day>article div{display:grid;min-width:0;flex:1}.week-day>article strong{font-size:12px;overflow-wrap:anywhere}.week-day>article small{color:var(--secondary-text-color)}.week-day>article.week-preview{align-items:center;border:1px dashed var(--divider-color);cursor:default;opacity:.82;background:color-mix(in srgb,var(--secondary-background-color) 86%,transparent)}.week-preview>span{font-size:9px;font-weight:800;text-transform:uppercase;color:var(--secondary-text-color);border:1px solid var(--divider-color);border-radius:99px;padding:3px 5px}
      .ranking-card{background:var(--card-background-color,#fff);border:1px solid var(--divider-color,#ddd);border-radius:16px;padding:17px 18px;margin-bottom:18px}.ranking-head{display:flex;justify-content:space-between;align-items:end;margin-bottom:10px}.ranking-head>span{font-size:12px;color:var(--secondary-text-color)}.ranking-list{display:grid;gap:4px}.ranking-row{display:grid;grid-template-columns:30px 38px minmax(100px,1fr) auto auto;align-items:center;gap:10px;padding:7px 2px;border-top:1px solid var(--divider-color,#ddd)}.ranking-row:first-child{border-top:0}.rank{display:grid;place-items:center;width:26px;height:26px;border-radius:50%;font-size:12px;font-weight:800;background:var(--divider-color,#eee)}.rank.top-1{background:#ffe08a;color:#6d4c00}.rank.top-2{background:#e2e5e9;color:#46505a}.rank.top-3{background:#e8c3a3;color:#70401f}.month-points{font-size:12px;color:var(--secondary-text-color)}.ranking-row>b{min-width:72px;text-align:right;color:var(--ht-accent-text)}
      .avatar{display:grid;place-items:center;flex:none;width:38px;height:38px;border-radius:50%;background:color-mix(in srgb,var(--primary-color,#03a9f4) 16%,var(--card-background-color,#fff));color:var(--ht-accent-text);font-weight:800}.avatar.large{width:52px;height:52px;font-size:20px}
      .section-title{display:flex;align-items:center;gap:8px;margin:20px 2px 9px}.section-title span{font-size:12px;background:var(--divider-color,#ddd);padding:2px 7px;border-radius:99px}.section-title.danger h3{color:var(--error-color,#db4437)}
      .occurrences{display:grid;gap:8px}.task-card{display:flex;align-items:center;gap:13px;background:var(--card-background-color,#fff);padding:13px 15px;border-radius:14px;border:1px solid var(--divider-color,#ddd);box-shadow:0 1px 2px #0000000a;transition:box-shadow .2s}.task-card.highlight{box-shadow:0 0 0 3px var(--primary-color)}.task-card.overdue{border-left:4px solid var(--error-color,#db4437)}.task-main{flex:1;min-width:0}.task-main h3{font-size:16px}.task-main p{font-size:13px;margin:4px 0 0}.complete{color:var(--ht-accent-text);border-color:color-mix(in srgb,var(--primary-color) 35%,transparent)}.occurrence-actions{display:flex;align-items:center;gap:6px}.more-actions{position:relative}.more-actions summary{list-style:none;cursor:pointer;padding:9px;border:1px solid var(--divider-color);border-radius:9px}.more-actions>div{position:absolute;z-index:5;right:0;top:calc(100% + 6px);display:grid;gap:5px;width:190px;padding:8px;overflow-y:auto;overscroll-behavior:contain;background:var(--card-background-color);border:1px solid var(--divider-color);border-radius:11px;box-shadow:0 8px 25px #0003}.more-actions.opens-up>div{top:auto;bottom:calc(100% + 6px);box-shadow:0 -8px 25px #0003}.more-actions button{text-align:left}.help-status{color:var(--ht-accent-text)!important}.market-badges{display:flex;gap:5px;flex-wrap:wrap;margin-top:7px}.market-badges span{font-size:11px;padding:3px 7px;border-radius:99px;background:color-mix(in srgb,var(--primary-color) 10%,transparent);color:var(--ht-accent-text);font-weight:700}
      .assignment-explanation{margin-top:6px;font-size:12px;color:var(--secondary-text-color)}.assignment-explanation summary{cursor:pointer}.assignment-explanation p{margin:5px 0 0}
      .task-status{display:inline-flex;padding:2px 7px;margin-right:5px;border-radius:99px;background:var(--divider-color,#eee);color:var(--primary-text-color,#202124);font-size:10px;font-weight:800;text-transform:uppercase}.status-in_progress{background:#dceeff;color:#075c9c}.status-waiting{background:#fff1c7;color:#735700}.status-blocked{background:#ffe1df;color:#9b1c16}.task-checklist{display:grid;gap:5px;margin-top:10px;padding:9px 10px;border-radius:10px;background:color-mix(in srgb,var(--primary-color) 5%,transparent)}.task-checklist label{display:flex;align-items:flex-start;gap:7px;font-size:13px}.task-checklist input{width:auto;margin-top:2px}.task-checklist input:checked+span{text-decoration:line-through;color:var(--secondary-text-color)}.task-dependencies{color:var(--warning-color,#b26a00)!important}.task-event-list{display:grid;gap:0;max-height:55vh;overflow:auto}.task-event-list article{display:grid;grid-template-columns:1fr auto;gap:3px 12px;padding:11px 2px;border-bottom:1px solid var(--divider-color)}.task-event-list time,.task-event-list small{font-size:12px;color:var(--secondary-text-color)}
      .toolbar{display:flex;align-items:end;justify-content:space-between;margin-bottom:18px}.toolbar p{margin:5px 0 0}.toolbar-actions{display:flex;gap:8px;flex-wrap:wrap}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:12px}.config-card,.settings-card,.card{background:var(--card-background-color,#fff);border:1px solid var(--divider-color,#ddd);border-radius:16px;padding:18px}.config-card.disabled{opacity:.65}.card-top{display:flex;align-items:center;gap:12px}.card-top>div:nth-child(2){flex:1}.card-top p{font-size:13px;margin:3px 0}.status{font-size:11px;font-weight:750;padding:4px 8px;border-radius:99px;background:var(--divider-color,#eee)}.status.home{background:#daf5df;color:#17752a}.description{font-size:14px}.actions{display:flex;gap:7px;margin-top:15px;flex-wrap:wrap}.danger-button{color:var(--error-color,#db4437)}dl{font-size:13px}dt{color:var(--secondary-text-color);margin-top:9px}dd{margin:2px 0;overflow-wrap:anywhere}.gallery-strip{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:18px}.gallery-strip article{display:flex;flex-direction:column;align-items:flex-start;padding:14px;border:1px solid var(--divider-color);border-radius:13px;background:var(--card-background-color)}.gallery-strip span,.gallery-modal span{font-size:10px;font-weight:800;color:var(--primary-color);text-transform:uppercase}.gallery-strip strong{margin:5px 0}.gallery-strip p{font-size:12px;flex:1}.gallery-modal{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:8px;margin-bottom:18px}.gallery-modal>button{display:flex;flex-direction:column;align-items:flex-start;text-align:left;gap:5px}.gallery-modal>button.selected{border-color:var(--primary-color);box-shadow:0 0 0 1px var(--primary-color)}
      .timeline{background:var(--card-background-color,#fff);border-radius:16px;border:1px solid var(--divider-color,#ddd);padding:4px 18px}.history-row{display:flex;gap:13px;align-items:center;padding:14px 0;border-bottom:1px solid var(--divider-color,#ddd)}.history-row:last-child{border:0}.history-row p{margin:4px 0 0;font-size:13px}.check{display:grid;place-items:center;border-radius:50%;width:34px;height:34px;background:#daf5df;color:#17752a;font-weight:800}
      .settings-card{max-width:760px;margin-bottom:14px}.settings-card>p{margin-top:6px}.info-row{display:flex;justify-content:space-between;gap:12px;padding:12px 0;border-top:1px solid var(--divider-color,#ddd);font-size:14px}.settings-card>.danger-button{margin-top:14px}.settings-heading{display:flex;justify-content:space-between;align-items:start;gap:12px}.settings-heading p{margin:5px 0}.health-summary{padding:10px 12px;border-radius:9px;background:#daf5df;color:#17752a;font-weight:700}.health-summary.warning{background:#fff1bf;color:#765600}.health-summary.critical{background:#fee2e2;color:#991b1b}.health-list{display:grid;gap:7px;margin-top:10px}.health-list>div{display:flex;gap:10px;padding:9px;border-left:4px solid var(--primary-color);background:var(--secondary-background-color)}.health-list>.warning{border-color:#f59e0b}.health-list>.critical{border-color:var(--error-color)}.health-list strong{text-transform:uppercase;font-size:10px}.decision-line{display:grid;gap:5px;padding:12px;margin-bottom:9px;border-left:4px solid #25a244;background:var(--secondary-background-color)}.decision-line.blocked{border-color:var(--error-color)}
      .health-list span{flex:1}.health-list button{padding:5px 9px}.discovery-list{display:grid;gap:7px}.discovery-list>div{display:flex;align-items:center;gap:10px;padding:9px;border-radius:9px;background:var(--secondary-background-color)}.discovery-list span{display:grid;flex:1}.discovery-list small{color:var(--secondary-text-color);overflow-wrap:anywhere}.smart-capture{display:grid;grid-template-columns:1fr auto;align-items:end;gap:8px;padding:12px;border:1px solid color-mix(in srgb,var(--primary-color) 40%,var(--divider-color));border-radius:12px;background:color-mix(in srgb,var(--primary-color) 6%,transparent)}.smart-capture output{grid-column:1/-1}
      .caldav-card code{overflow-wrap:anywhere}.caldav-credentials{display:grid;gap:8px}.caldav-credential{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px;border:1px solid var(--divider-color);border-radius:10px;background:var(--secondary-background-color)}.caldav-credential>div{display:grid;gap:3px;min-width:0}.caldav-credential small{color:var(--secondary-text-color);overflow-wrap:anywhere}.setup-steps{padding-left:22px}.setup-steps li{margin:8px 0}.credential-secret dd{display:flex;align-items:center;gap:8px;margin:4px 0 12px}.credential-secret code{flex:1;padding:9px;background:var(--secondary-background-color);border-radius:7px;overflow-wrap:anywhere;user-select:all}
      .config-transfer{border-top:1px solid var(--divider-color,#ddd);margin-top:12px;padding-top:16px}.config-transfer p{font-size:13px}.metric-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:16px}.metric,.analytics-card{background:var(--card-background-color,#fff);border:1px solid var(--divider-color,#ddd);border-radius:16px;padding:18px}.metric{display:flex;flex-direction:column;gap:8px}.metric span{font-size:13px;color:var(--secondary-text-color)}.metric b{font-size:26px}.metric.danger b{color:var(--error-color,#db4437)}.analytics-card{margin-bottom:14px}.analytics-card h3{margin-bottom:12px}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;padding:10px;border-top:1px solid var(--divider-color,#ddd);white-space:nowrap}th{font-size:12px;color:var(--secondary-text-color)}.insight-list{display:grid;gap:8px}.insight{padding:11px 13px;border-radius:10px;background:var(--secondary-background-color,#f3f4f6);border-left:4px solid var(--primary-color)}.insight.warning{border-color:#f59e0b}.insight.critical{border-color:var(--error-color,#db4437)}.positive{color:#17752a}.handover-note{padding:9px 11px;border-radius:9px;background:color-mix(in srgb,var(--primary-color) 10%,transparent);font-size:13px}
      .monitor-form{margin-top:16px}.printer-list{display:flex;gap:7px;flex-wrap:wrap;margin:2px 0 10px}.printer-list span{font-size:12px;padding:5px 9px;border-radius:99px;background:var(--divider-color,#eee)}
      .reference-help{padding:12px;border:1px solid var(--divider-color,#bbb);border-radius:11px;background:var(--secondary-background-color,#f3f4f6)}.reference-controls{display:grid;grid-template-columns:1fr 1fr;gap:10px}
      .field-with-action{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px;align-items:start}.field-with-action>label{min-width:0}.field-action{width:42px;height:42px;padding:0;font-size:22px;align-self:start;margin-top:19px}.tag-creator{display:flex;align-items:flex-start;gap:8px;flex-wrap:wrap;margin-top:10px;padding:12px;border:1px solid var(--divider-color,#bbb);border-radius:10px;background:var(--secondary-background-color,#f3f4f6)}.tag-creator>label{flex:1 1 260px}.tag-creator>[data-create-tag],.tag-creator>[data-cancel-tag]{margin-top:24px}.tag-creator>.hint{flex-basis:100%}
      .test-line,.test-actions,.resource-test-line,.task-preview{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:8px}.preview-result{font-size:12px;color:var(--secondary-text-color);line-height:1.45}.preview-result.match{color:#17752a}.preview-result.error{color:var(--error-color,#db4437)}.inline-create{border-top:1px solid var(--divider-color,#ddd);padding-top:10px}.inline-person-form,.inline-follow-up-form{margin-top:10px;padding:12px;border-radius:10px;background:var(--secondary-background-color,#f3f4f6)}
      .resource-list{display:grid;gap:12px;margin:14px 0}.resource-row{border:1px solid var(--divider-color,#bbb);border-radius:12px;padding:13px;background:var(--secondary-background-color,#f3f4f6)}.resource-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}.resource-head .remove-row{width:38px;height:38px;padding:0;color:var(--error-color,#db4437);font-size:20px}
      .empty{text-align:center;padding:50px 20px}.big-icon{font-size:42px;color:#25a244}.spinner{width:32px;height:32px;border:3px solid var(--divider-color);border-top-color:var(--primary-color);border-radius:50%;animation:spin .8s linear infinite;margin:auto}@keyframes spin{to{transform:rotate(360deg)}}
      .backdrop{position:fixed;z-index:1000;inset:0;background:#0009;display:grid;place-items:center;padding:18px}.modal-card{width:min(760px,100%);max-height:92vh;overflow:auto;background:var(--card-background-color,#fff);border-radius:20px;padding:22px}.modal-card.small{width:min(580px,100%)}.modal-head{display:flex;justify-content:space-between;align-items:start;margin-bottom:20px}.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:15px}.form-grid label{display:flex;flex-direction:column;gap:6px;font-size:13px;font-weight:650}.form-grid .full{grid-column:1/-1}.form-grid .checkbox{flex-direction:row;flex-wrap:wrap;align-items:center;align-self:end;min-height:42px}.form-grid input,.form-grid select,.form-grid textarea{width:100%;padding:10px 11px;border:1px solid var(--divider-color,#bbb);border-radius:9px;background:var(--primary-background-color,#fafafa)}.form-grid [aria-invalid="true"]{border-color:var(--error-color,#db4437)}.checkbox input{width:auto}.checkbox .hint{flex-basis:100%;padding-left:24px}.modal-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:5px}.hidden{display:none!important}.hint{display:block;color:var(--secondary-text-color,#6b7280);font-size:12px;font-weight:400;line-height:1.45;margin:2px 0}.field-help{max-width:68ch}.field-error{display:block;color:var(--error-color,#db4437);font-size:12px;font-weight:650}.group-help{margin-top:-8px}.weekdays{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px}.weekdays input{position:absolute;width:1px;height:1px;margin:-1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap}.weekdays span{display:grid;place-items:center;width:40px;height:36px;border:1px solid var(--divider-color);border-radius:9px;cursor:pointer}.weekdays input:focus-visible+span{outline:3px solid color-mix(in srgb,var(--primary-color,#03a9f4) 70%,white);outline-offset:2px}.weekdays input:checked+span{background:color-mix(in srgb,var(--primary-color,#03a9f4) 88%,#000);color:#fff;border-color:var(--primary-color)}.advanced-fields{border:1px solid var(--divider-color);border-radius:12px;padding:12px}.advanced-fields>summary{cursor:pointer;font-weight:750}.advanced-grid{margin-top:15px}.setup-step{display:flex!important;flex-direction:row!important;align-items:center;gap:10px!important;border-bottom:1px solid var(--divider-color);padding-bottom:8px}.setup-step>b{display:grid;place-items:center;width:30px;height:30px;border-radius:50%;background:var(--primary-color);color:#fff}.setup-step span{display:grid}.setup-step small{font-weight:400;color:var(--secondary-text-color)}.wizard-preview,.gallery-preview{padding:12px;border-radius:10px;background:var(--secondary-background-color)}.wizard-preview p,.gallery-preview p{margin:5px 0}
      .field-label{display:block;font-size:13px;font-weight:650;margin-bottom:7px}.candidate-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:5px 12px;padding:7px 10px;border:1px solid var(--divider-color,#bbb);border-radius:9px}.candidate-grid .checkbox{min-height:32px;font-weight:500}
      .task-wizard-steps{display:grid;grid-template-columns:repeat(5,1fr);gap:6px;padding:0;margin:0 0 22px;list-style:none}.task-wizard-steps button{display:flex;align-items:center;justify-content:center;gap:7px;width:100%;padding:8px 6px;border-color:transparent;background:var(--secondary-background-color);font-size:12px}.task-wizard-steps button span{display:grid;place-items:center;flex:none;width:24px;height:24px;border-radius:50%;background:var(--divider-color);font-weight:800}.task-wizard-steps button[aria-current=step]{border-color:var(--primary-color);background:color-mix(in srgb,var(--primary-color) 10%,var(--card-background-color));color:var(--ht-accent-text)}.task-wizard-steps button[aria-current=step] span{background:var(--primary-color);color:#fff}.task-wizard-steps button:disabled{cursor:not-allowed}.task-editor--wizard:not(.wizard-ready) [data-task-step]:not([data-task-step="1"]),.wizard-step-hidden{display:none!important}.task-editor-actions{position:sticky;z-index:4;bottom:-22px;margin-top:14px;padding:14px 0 0;background:var(--card-background-color);border-top:1px solid var(--divider-color)}.task-wizard-review{display:grid;gap:14px}.task-wizard-review h3{font-size:22px}.wizard-review-facts{display:grid;gap:0;margin:0;border:1px solid var(--divider-color);border-radius:12px;overflow:hidden}.wizard-review-facts div{display:grid;grid-template-columns:minmax(120px,1fr) 2fr;gap:16px;padding:12px 14px;border-top:1px solid var(--divider-color)}.wizard-review-facts div:first-child{border-top:0}.wizard-review-facts dt{margin:0}.wizard-review-facts dd{margin:0;font-weight:700;text-align:right}.wizard-review-preview{display:grid;gap:7px;padding:14px;border-radius:12px;background:var(--secondary-background-color)}.wizard-review-preview output{line-height:1.5}.wizard-review-preview output.error{color:var(--error-color)}
      .repeatable-editor{border:1px solid var(--divider-color,#bbb);border-radius:11px;padding:12px}.repeatable-list{display:grid;gap:9px}.repeatable-row{display:grid;grid-template-columns:minmax(150px,2fr) minmax(130px,1fr) auto;gap:9px;align-items:end;padding:10px;background:var(--secondary-background-color,#f3f4f6);border-radius:9px}.repeatable-row.trigger-row{grid-template-columns:minmax(180px,2fr) repeat(3,minmax(105px,1fr)) auto}.repeatable-row .remove-row{width:38px;height:38px;padding:0;color:var(--error-color,#db4437);font-size:20px}.add-row{margin-top:10px}.empty-row{margin:2px 0 8px;font-size:13px;font-style:italic}
      .repeatable-row.escalation-row{grid-template-columns:repeat(4,minmax(120px,1fr)) minmax(150px,1fr) auto}
      .command-card{width:min(680px,100%)}.command-input{width:100%;padding:13px;border:1px solid var(--divider-color);border-radius:11px;background:var(--primary-background-color)}.command-results{display:grid;gap:4px;margin-top:10px;max-height:55vh;overflow:auto}.command-result{display:grid;grid-template-columns:80px 1fr;align-items:center;text-align:left}.command-result>span:last-child{display:grid}.command-result small{color:var(--secondary-text-color);overflow-wrap:anywhere}.command-type{font-size:10px;color:var(--ht-accent-text);font-weight:800}.mobile-quick{display:none}
      .toast{position:fixed;z-index:2000;left:50%;bottom:28px;transform:translateX(-50%);background:#263238;color:#fff;padding:12px 18px;border-radius:10px;box-shadow:0 4px 20px #0005}.toast.error{background:var(--error-color,#db4437)}
      .history-main{flex:1;min-width:0}.history-actions{display:flex;align-items:center;justify-content:flex-end;gap:8px;flex-wrap:wrap}.history-evidence,.history-count{display:inline-flex;padding:3px 8px;border-radius:99px;font-size:12px}.history-count,.has-evidence{background:color-mix(in srgb,var(--primary-color) 15%,transparent);color:var(--ht-accent-text)}.no-evidence{background:var(--secondary-background-color);color:var(--secondary-text-color)}.check{flex:0 0 34px}.history-record>section,.history-record-grid>section{margin:16px 0;padding:14px;border:1px solid var(--divider-color);border-radius:12px}.history-record-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.history-record-grid>section{margin:0}.history-facts{display:grid;gap:7px;margin:0}.history-facts div{display:flex;justify-content:space-between;gap:12px}.history-facts dt{color:var(--secondary-text-color)}.history-facts dd{margin:0;text-align:right}.preserve-lines{white-space:pre-wrap}.history-checklist{display:grid;gap:7px}.history-checklist>div{display:grid;grid-template-columns:auto 1fr auto;gap:8px;align-items:center}.history-checklist small,.task-event-list .event-details{color:var(--secondary-text-color)}.task-event-list .event-details{grid-column:1/-1}.readonly-attachments>div{justify-content:flex-start}
      @media(max-width:650px){.history-record-grid{grid-template-columns:1fr}.history-row{align-items:flex-start;flex-wrap:wrap}.history-actions{width:100%;padding-left:47px;justify-content:flex-start}.history-checklist>div{grid-template-columns:auto 1fr}.history-checklist small{grid-column:2}}
      @media(max-width:650px){main{padding:16px 12px 82px}header{margin-bottom:8px;align-items:flex-start}h1{font-size:24px}.header-actions{flex-wrap:wrap;justify-content:flex-end}.search-button{font-size:0}.search-button::after{content:"⌕";font-size:20px}.mode-badge{order:-1}nav{margin-bottom:16px}.hero{padding:20px;align-items:flex-start}.hero h2{font-size:21px}.hero-side{flex-direction:column-reverse;align-items:flex-end;gap:10px}.hero-button{padding:8px 10px}.context-add{right:18px;bottom:18px}.mobile-quick{display:grid;gap:6px;background:var(--card-background-color);border:1px solid var(--divider-color);border-radius:14px;padding:13px;margin-bottom:14px}.mobile-quick button{display:flex;justify-content:space-between;gap:8px;text-align:left}.mobile-quick small{color:var(--secondary-text-color)}.cards,.gallery-strip{grid-template-columns:1fr}.task-card{align-items:flex-start}.occurrence-actions{flex-direction:column;align-items:stretch}.complete{padding:8px}.form-grid{grid-template-columns:1fr}.form-grid .full{grid-column:auto}.modal-card{padding:18px 15px}.toolbar{align-items:flex-start;flex-direction:column;gap:12px}.toolbar-actions{width:100%}.people-grid{grid-template-columns:1fr}.ranking-row{grid-template-columns:27px 34px 1fr auto}.ranking-row .avatar{width:34px;height:34px}.month-points{display:none}.ranking-head>span{display:none}.repeatable-row,.repeatable-row.trigger-row,.repeatable-row.escalation-row,.reference-controls,.smart-capture{grid-template-columns:1fr}.repeatable-row .remove-row{justify-self:end}.bulk-toolbar{position:static}.week-board{grid-template-columns:repeat(7,80vw)}.task-wizard-steps{grid-template-columns:repeat(5,44px);justify-content:space-between;gap:2px}.task-wizard-steps button{width:44px;height:44px;padding:0;font-size:0}.task-wizard-steps button span{font-size:12px}.task-editor-actions{bottom:-18px;display:grid;grid-template-columns:1fr 1fr;padding-bottom:1px}.task-editor-actions .cancel{grid-column:1}.task-editor-actions .primary:last-child{grid-column:2}.wizard-review-facts div{grid-template-columns:1fr}.wizard-review-facts dd{text-align:left}}
    `;
  }
}

if (!customElements.get("household-tasks-panel")) {
  customElements.define("household-tasks-panel", HouseholdTasksPanel);
}
