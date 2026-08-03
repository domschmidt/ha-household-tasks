import { fixture } from "./fixture.js";
import "/custom_components/household_tasks/frontend/household-tasks-panel.js?v=playwright";

const clone = (value) => structuredClone(value);
let state = clone(fixture);
window.__householdTaskCalls = [];
window.__householdTaskSubscriptions = [];
const eventSubscribers = new Map();

const callWS = async (message) => {
  window.__householdTaskCalls.push(clone(message));
  if (message.type === "household_tasks/get") return clone(state);
  if (message.type === "household_tasks/week_preview") return clone(state.week_preview);
  if (message.type === "household_tasks/task_history") {
    return [{ type: "task_completed", occurred_at: "2026-07-28T18:12:00+02:00", actor: "alina", details: { status: "completed" } }];
  }
  if (message.type === "household_tasks/task_projection") return { risk: "low", message: "Eine Aufgabe" };
  if (message.type === "household_tasks/preview_task") return { matches: true, message: "Regel ist gültig." };
  if (message.type === "household_tasks/add_attachment_chunk") return { complete: true };
  if (message.type === "household_tasks/caldav_create_credential") {
    const result = clone(state);
    result.caldav.created_credential = {
      id: "credential-1",
      server_url: state.caldav.server_url,
      username: "dominik-12345678",
      password: "one-time-secret",
    };
    result.caldav.credentials.push({
      id: "credential-1",
      label: message.label,
      person_id: message.person_id,
      username: "dominik-12345678",
      permission: message.permission,
      scope: message.scope,
      created_at: "2026-08-03T12:00:00Z",
    });
    return result;
  }
  if (message.type === "household_tasks/attachment_content_chunk") {
    return { name: "tonne.jpg", mime_type: "image/jpeg", content: "aGVsbG8=", next_offset: 8, complete: true };
  }
  if (message.type.startsWith("household_tasks/")) return clone(state);
  if (message.type === "config/auth/list") return [{ id: "user-dominik", name: "Dominik" }];
  if (message.type === "config/device_registry/list") return [];
  if (message.type === "tag/list") return [{ id: "car-frost", tag_id: "car-frost", name: "Auto Frostschutz" }];
  if (message.type === "tag/create") return { tag_id: "new-tag" };
  return [];
};

const panel = document.querySelector("household-tasks-panel");
const connection = {
  async subscribeEvents(callback, eventType) {
    window.__householdTaskSubscriptions.push(eventType);
    eventSubscribers.set(eventType, callback);
    return () => eventSubscribers.delete(eventType);
  },
};
panel.hass = {
  user: { id: "user-dominik", name: "Dominik", is_admin: true },
  locale: { language: new URL(location.href).searchParams.get("lang") || "de" },
  language: "de",
  states: {
    "person.admin": { state: "home", attributes: { friendly_name: "Admin" } },
    "person.alina": { state: "not_home", attributes: { friendly_name: "Alina" } },
    "calendar.waste": { state: "on", attributes: { friendly_name: "Abfallkalender" } },
    "weather.home": { state: "cloudy", attributes: { friendly_name: "Zuhause", temperature: 4 } },
  },
  services: { notify: { mobile_app_iphone_von_dominik: { name: "Dominiks iPhone" } } },
  connection,
  callWS,
};

window.__setHouseholdTasksFixture = (next) => {
  state = { ...clone(fixture), ...clone(next) };
  panel._data = clone(state);
  panel._render();
};

window.__setHouseholdTasksServerState = (next) => {
  state = { ...clone(fixture), ...clone(next) };
};

window.__resetHouseholdTasksServerState = () => {
  state = clone(fixture);
};

window.__emitHouseholdTasksUpdated = () => {
  eventSubscribers.get("household_tasks_updated")?.({ data: {} });
};
