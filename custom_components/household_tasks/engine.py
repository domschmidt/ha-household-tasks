"""Runtime engine and integration lifecycle for Household Tasks."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from collections.abc import Iterable
from copy import deepcopy
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import voluptuous as vol
from homeassistant.components.frontend import (
    async_register_built_in_panel,
    async_remove_panel,
)
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Context, Event, HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .analytics import build_analytics
from .assignment import select_fair_candidate
from .bootstrap import initial_config
from .config_io import build_export, parse_import
from .const import (
    ACTION_PREFIX,
    CLAIM_HELP_PREFIX,
    CLAIM_TASK_PREFIX,
    DECLINE_PREFIX,
    DOMAIN,
    FRONTEND_PATH,
    HELP_PREFIX,
    INTEGRATION_VERSION,
    PANEL_URL,
    SNOOZE_EVENING_PREFIX,
    SNOOZE_TOMORROW_PREFIX,
    STORAGE_KEY,
    STORAGE_VERSION,
)
from .nfc import (
    NFC_ACTIONS,
    NFC_FEEDBACK_MODES,
    NFC_FEEDBACK_RECIPIENTS,
    feedback_recipients,
    open_occurrence_for_task,
    task_for_tag,
)
from .resources import (
    RESOURCE_CONDITIONS,
    render_resource_text,
    resource_condition_matches,
)
from .scheduling import month_day, parse_duration, parse_time
from .workflows import (
    completion_due,
    state_trigger_allowed,
    weekly_summary_due,
)

_LOGGER = logging.getLogger(__name__)

WEEKDAYS = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}


def get_loaded_engine(hass: HomeAssistant) -> HouseholdTaskEngine | None:
    """Return the engine for the loaded config entry."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            engine = getattr(entry, "runtime_data", None)
            if isinstance(engine, HouseholdTaskEngine):
                return engine
    return None


def _frontend_version() -> str:
    """Return a deterministic cache key for the shipped frontend assets."""
    frontend = Path(__file__).parent / "frontend"
    digest = hashlib.sha256()
    for filename in (
        "household-tasks-panel.js",
        "household-tasks-translations.js",
    ):
        digest.update((frontend / filename).read_bytes())
    return f"{INTEGRATION_VERSION}-{digest.hexdigest()[:12]}"


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up the integration package."""
    from .ui import async_register_websocket_commands

    async_register_websocket_commands(hass)
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                FRONTEND_PATH,
                str(Path(__file__).parent / "frontend"),
                cache_headers=False,
            )
        ]
    )

    async def _async_create(call: ServiceCall) -> None:
        engine = get_loaded_engine(hass)
        if engine is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="not_loaded",
            )
        try:
            await engine.async_create_manual(call.data["task_id"], call.context)
        except vol.Invalid as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_task",
                translation_placeholders={"task_id": call.data["task_id"]},
            ) from err

    async def _async_scan_now(call: ServiceCall) -> None:
        engine = get_loaded_engine(hass)
        if engine is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="not_loaded",
            )
        await engine.async_scan()

    async def _async_set_handover(call: ServiceCall) -> None:
        engine = get_loaded_engine(hass)
        if engine is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="not_loaded",
            )
        if call.context.user_id:
            user = await hass.auth.async_get_user(call.context.user_id)
            if user is None or not user.is_admin:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="admin_required",
                )
        await engine.async_set_handover(
            call.data["from_person"],
            call.data["to_person"],
            until=call.data.get("until"),
            reason=call.data.get("reason"),
        )

    async def _async_clear_handover(call: ServiceCall) -> None:
        engine = get_loaded_engine(hass)
        if engine is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="not_loaded",
            )
        if call.context.user_id:
            user = await hass.auth.async_get_user(call.context.user_id)
            if user is None or not user.is_admin:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="admin_required",
                )
        await engine.async_clear_handover(call.data["from_person"])

    hass.services.async_register(
        DOMAIN,
        "create",
        _async_create,
        schema=vol.Schema({vol.Required("task_id"): str}),
    )
    hass.services.async_register(DOMAIN, "scan_now", _async_scan_now)
    hass.services.async_register(
        DOMAIN,
        "set_handover",
        _async_set_handover,
        schema=vol.Schema(
            {
                vol.Required("from_person"): str,
                vol.Required("to_person"): str,
                vol.Optional("until"): str,
                vol.Optional("reason"): str,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        "clear_handover",
        _async_clear_handover,
        schema=vol.Schema({vol.Required("from_person"): str}),
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Household Tasks from a UI config entry."""
    todo_entity = entry.data["todo_entity"]
    if hass.states.get(todo_entity) is None:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="todo_unavailable",
            translation_placeholders={"entity_id": todo_entity},
        )
    engine = HouseholdTaskEngine(hass, initial_config(todo_entity))
    await engine.async_setup()
    entry.runtime_data = engine
    if "haushaltsaufgaben" not in hass.data.get("frontend_panels", {}):
        frontend_version = await hass.async_add_executor_job(_frontend_version)
        async_register_built_in_panel(
            hass,
            component_name="custom",
            sidebar_title="Household Tasks",
            sidebar_icon="mdi:clipboard-check-outline",
            frontend_url_path=PANEL_URL,
            config={
                "_panel_custom": {
                    "name": "household-tasks-panel",
                    "embed_iframe": False,
                    "trust_external": False,
                    "module_url": (
                        f"{FRONTEND_PATH}/household-tasks-panel.js?v={frontend_version}"
                    ),
                }
            },
        )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Household Tasks config entry."""
    engine = getattr(entry, "runtime_data", None)
    if isinstance(engine, HouseholdTaskEngine):
        await engine.async_shutdown()
    entry.runtime_data = None
    async_remove_panel(hass, PANEL_URL)
    return True


class HouseholdTaskEngine:
    """Create, track, notify, and escalate household tasks."""

    def __init__(self, hass: HomeAssistant, config: dict[str, Any]) -> None:
        self.hass = hass
        self.config = config
        self.initial_config = deepcopy(config)
        self.todo_entity: str = config["todo_entity"]
        self.people: dict[str, dict[str, Any]] = config["people"]
        self.tasks: dict[str, dict[str, Any]] = config["tasks"]
        self.defaults: dict[str, Any] = config.get("defaults", {})
        self.monitors: dict[str, Any] = config.get("monitors", {})
        self.store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self.state: dict[str, Any] = {
            "last_check": None,
            "occurrences": {},
            "daily_triggers": {},
            "state_triggers": {},
            "weekly_summaries": {},
            "printer_issues": {},
            "resource_issues": {},
            "resource_last_created": {},
            "handovers": {},
            "scores": {},
            "assignment_counts": {},
            "rotation_cursors": {},
            "ui_config": None,
        }
        self.lock = asyncio.Lock()
        self.remove_interval = None
        self.remove_state_listener = None
        self.remove_notification_listener = None
        self.remove_tag_listener = None
        self.remove_stop_listener = None
        self.watched_entities: tuple[str, ...] = ()
        self.pending_state_checks: dict[tuple[str, str], Any] = {}

    async def async_setup(self) -> None:
        """Load state, validate configuration, and register listeners."""
        stored = await self.store.async_load()
        if stored:
            self.state.update(stored)
        self.state.setdefault("daily_triggers", {})
        self.state.setdefault("state_triggers", {})
        self.state.setdefault("weekly_summaries", {})
        self.state.setdefault("printer_issues", {})
        self.state.setdefault("resource_issues", {})
        self.state.setdefault("resource_last_created", {})
        self.state.setdefault("handovers", {})
        self.state.setdefault("scores", {})
        self.state.setdefault("assignment_counts", {})
        self.state.setdefault("rotation_cursors", {})
        self.state.setdefault("ui_config", None)
        if self.state["ui_config"]:
            self._apply_editable_config(self.state["ui_config"])
        if stored and "scores" not in stored:
            migrated_scores: dict[str, int] = {}
            for occurrence in self.state.get("occurrences", {}).values():
                if not occurrence.get("resolved"):
                    continue
                person_id = occurrence.get("completed_by") or occurrence.get("assignee")
                if person_id not in self.people:
                    continue
                occurrence["completed_by"] = person_id
                migrated_scores[person_id] = migrated_scores.get(person_id, 0) + 1
            self.state["scores"] = migrated_scores
        if stored and "assignment_counts" not in stored:
            migrated_assignments: dict[str, int] = {}
            for occurrence in self.state.get("occurrences", {}).values():
                person_id = occurrence.get("assignee")
                if person_id not in self.people:
                    continue
                migrated_assignments[person_id] = (
                    migrated_assignments.get(person_id, 0) + 1
                )
            self.state["assignment_counts"] = migrated_assignments
        self._validate_config()
        if self.state["ui_config"] is None:
            self.state["ui_config"] = self._editable_config()
            await self._save()

        self.remove_notification_listener = self.hass.bus.async_listen(
            "mobile_app_notification_action", self._handle_notification_action
        )
        self.remove_tag_listener = self.hass.bus.async_listen(
            "tag_scanned", self._handle_tag_scanned
        )
        self.remove_interval = async_track_time_interval(
            self.hass,
            self._scheduled_scan,
            timedelta(seconds=self.config.get("scan_interval_seconds", 60)),
        )
        self._refresh_state_listener()
        self.remove_stop_listener = self.hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP, self._handle_stop
        )
        self.hass.async_create_task(self.async_scan())
        _LOGGER.info(
            "Household Tasks loaded with %d people and %d tasks",
            len(self.people),
            len(self.tasks),
        )

    def _validate_config(self) -> None:
        """Raise a useful configuration error before starting."""
        errors: list[str] = []
        configured_tags: dict[str, str] = {}
        if not self.todo_entity.startswith("todo."):
            errors.append("todo_entity must start with 'todo.'")

        for person_id, person in self.people.items():
            if not isinstance(person, dict):
                errors.append(f"person '{person_id}' must be a mapping")
                continue
            if not person.get("name"):
                errors.append(f"person '{person_id}' needs a name")
            if not str(person.get("notify", "")).startswith("notify."):
                errors.append(f"person '{person_id}' needs a notify.* action")

        for task_id, task in self.tasks.items():
            if not isinstance(task, dict):
                errors.append(f"task '{task_id}' must be a mapping")
                continue
            if not task.get("name"):
                errors.append(f"task '{task_id}' needs a name")
            nfc = task.get("nfc")
            if nfc is not None:
                if not isinstance(nfc, dict):
                    errors.append(f"task '{task_id}' NFC settings must be a mapping")
                else:
                    tag_id = str(nfc.get("tag_id", "")).strip()
                    if not tag_id:
                        errors.append(f"task '{task_id}' NFC tag_id must not be empty")
                    elif tag_id in configured_tags:
                        errors.append(
                            f"task '{task_id}' uses the same NFC tag as "
                            f"'{configured_tags[tag_id]}'"
                        )
                    else:
                        configured_tags[tag_id] = task_id
                    if nfc.get("action", "create_or_complete") not in NFC_ACTIONS:
                        errors.append(f"task '{task_id}' has an invalid NFC action")
            assignment = task.get("assignment", {})
            assignment_type = assignment.get("type", "fixed")
            if assignment_type not in {"fixed", "rotation", "fair", "open"}:
                errors.append(f"task '{task_id}' has an invalid assignment type")
            if assignment_type == "fixed" and task.get("assignee") not in self.people:
                errors.append(f"task '{task_id}' has an unknown assignee")
            if assignment_type in {"rotation", "fair"}:
                candidates = assignment.get("people", [])
                if not candidates:
                    errors.append(f"task '{task_id}' needs assignment candidates")
                unknown_candidates = set(candidates) - set(self.people)
                if unknown_candidates:
                    errors.append(f"task '{task_id}' has unknown assignment candidates")
            if assignment_type == "open":
                unknown_candidates = set(assignment.get("people", [])) - set(
                    self.people
                )
                if unknown_candidates:
                    errors.append(f"task '{task_id}' has unknown assignment candidates")
            if assignment.get("presence_required") not in {None, True, False}:
                errors.append(f"task '{task_id}' presence_required must be a boolean")
            schedule = task.get("schedule", {})
            schedule_type = schedule.get("type")
            if schedule_type not in {
                "manual",
                "weekly",
                "monthly",
                "yearly",
                "interval_months",
                "calendar",
                "daily_after_state",
                "state_trigger",
                "after_completion",
            }:
                errors.append(f"task '{task_id}' has an invalid schedule type")
            if schedule_type == "weekly":
                if not schedule.get("weekdays"):
                    errors.append(f"task '{task_id}' needs at least one weekday")
                invalid = set(schedule.get("weekdays", [])) - set(WEEKDAYS)
                if invalid:
                    errors.append(
                        f"task '{task_id}' has invalid weekdays: {sorted(invalid)}"
                    )
            if schedule_type in {
                "weekly",
                "monthly",
                "yearly",
                "interval_months",
                "daily_after_state",
            }:
                try:
                    self._parse_time(schedule.get("time", ""))
                except ValueError:
                    errors.append(f"task '{task_id}' needs a valid time")
            if schedule_type in {"monthly", "yearly"}:
                day = schedule.get("day")
                if str(day).lower() != "last":
                    try:
                        valid_day = 1 <= int(day) <= 31
                    except (TypeError, ValueError):
                        valid_day = False
                    if not valid_day:
                        errors.append(f"task '{task_id}' needs day 1..31 or last")
            if schedule_type == "calendar" and not schedule.get("entity_id"):
                errors.append(f"task '{task_id}' needs calendar entity_id")
            if schedule_type == "calendar":
                try:
                    self._parse_duration(schedule.get("offset", "00:00:00"))
                except (TypeError, ValueError, vol.Invalid):
                    errors.append(f"task '{task_id}' has an invalid offset")
            if schedule_type == "interval_months":
                if not schedule.get("start"):
                    errors.append(f"task '{task_id}' needs start")
                else:
                    try:
                        date.fromisoformat(str(schedule["start"]))
                    except ValueError:
                        errors.append(f"task '{task_id}' needs a valid start date")
                try:
                    valid_month_interval = int(schedule.get("months", 0)) >= 1
                except (TypeError, ValueError):
                    valid_month_interval = False
                if not valid_month_interval:
                    errors.append(f"task '{task_id}' needs months >= 1")
            if schedule_type == "yearly":
                try:
                    valid_month = 1 <= int(schedule.get("month", 0)) <= 12
                except (TypeError, ValueError):
                    valid_month = False
                if not valid_month:
                    errors.append(f"task '{task_id}' needs month from 1 to 12")
            if schedule_type == "daily_after_state":
                if not schedule.get("triggers"):
                    errors.append(f"task '{task_id}' needs state triggers")
                for trigger in schedule.get("triggers", []):
                    if not isinstance(trigger, dict):
                        errors.append(
                            f"task '{task_id}' state trigger must be a mapping"
                        )
                        continue
                    if not str(trigger.get("entity_id", "")).startswith(
                        ("binary_sensor.", "sensor.", "switch.")
                    ):
                        errors.append(
                            f"task '{task_id}' has an invalid trigger entity_id"
                        )
                    if "to" not in trigger:
                        errors.append(
                            f"task '{task_id}' state trigger needs a to state"
                        )
                    if "for" in trigger:
                        try:
                            self._parse_duration(trigger["for"])
                        except (TypeError, ValueError, vol.Invalid):
                            errors.append(
                                f"task '{task_id}' state trigger has invalid for"
                            )
            if schedule_type == "state_trigger":
                if not schedule.get("triggers"):
                    errors.append(f"task '{task_id}' needs state triggers")
                for trigger in schedule.get("triggers", []):
                    if not isinstance(trigger, dict):
                        errors.append(
                            f"task '{task_id}' state trigger must be a mapping"
                        )
                        continue
                    if not str(trigger.get("entity_id", "")).startswith(
                        ("binary_sensor.", "sensor.", "switch.", "input_boolean.")
                    ):
                        errors.append(
                            f"task '{task_id}' has an invalid trigger entity_id"
                        )
                    if "to" not in trigger:
                        errors.append(
                            f"task '{task_id}' state trigger needs a to state"
                        )
                    try:
                        self._parse_duration(trigger.get("for", "00:00:00"))
                    except (TypeError, ValueError, vol.Invalid):
                        errors.append(f"task '{task_id}' state trigger has invalid for")
                for field in ("due_after", "cooldown"):
                    try:
                        duration = self._parse_duration(schedule.get(field, "00:00:00"))
                        if duration < timedelta(0):
                            raise ValueError
                    except (TypeError, ValueError, vol.Invalid):
                        errors.append(
                            f"task '{task_id}' state trigger has invalid {field}"
                        )
            if schedule_type == "after_completion":
                try:
                    interval = self._parse_duration(schedule.get("interval", ""))
                    if interval <= timedelta(0):
                        raise ValueError
                except (TypeError, ValueError, vol.Invalid):
                    errors.append(
                        f"task '{task_id}' completion interval must be positive"
                    )
                if not dt_util.parse_datetime(str(schedule.get("start", ""))):
                    errors.append(
                        f"task '{task_id}' needs a valid first completion due date"
                    )

            follow_ups = task.get("follow_ups", [])
            if not isinstance(follow_ups, list):
                errors.append(f"task '{task_id}' follow-ups must be a list")
                follow_ups = []
            for follow_up in follow_ups:
                if not isinstance(follow_up, dict):
                    errors.append(f"task '{task_id}' follow-up must be a mapping")
                    continue
                target_id = follow_up.get("task_id")
                if target_id not in self.tasks or target_id == task_id:
                    errors.append(f"task '{task_id}' has an invalid follow-up task")
                try:
                    delay = self._parse_duration(follow_up.get("delay", "00:00:00"))
                    if delay < timedelta(0):
                        raise ValueError
                except (TypeError, ValueError, vol.Invalid):
                    errors.append(f"task '{task_id}' follow-up delay is invalid")

            escalation = task.get("escalation", self.defaults.get("escalation", []))
            for index, step in enumerate(escalation):
                try:
                    self._parse_duration(step["after"])
                except (KeyError, TypeError, ValueError, vol.Invalid):
                    errors.append(
                        f"task '{task_id}' escalation {index + 1} needs after: HH:MM:SS"
                    )
                if step.get("relative_to", "due") not in {
                    "due",
                    "first_notification",
                }:
                    errors.append(
                        f"task '{task_id}' escalation {index + 1} "
                        "has invalid relative_to"
                    )
                if step.get("action", "notify") not in {
                    "delegate",
                    "notify",
                    "open",
                }:
                    errors.append(
                        f"task '{task_id}' escalation {index + 1} has invalid action"
                    )

        printer_monitor = self.monitors.get("printers", {})
        if printer_monitor.get("enabled"):
            assignee = printer_monitor.get("assignee")
            if assignee not in self.people:
                errors.append("printer monitoring needs a configured assignee")

        resources = self.monitors.get("resources", {})
        if not isinstance(resources, dict):
            errors.append("resource monitors must be a mapping")
        else:
            for resource_id, monitor in resources.items():
                if not isinstance(monitor, dict):
                    errors.append(f"resource monitor '{resource_id}' must be a mapping")
                    continue
                if not monitor.get("enabled", True):
                    continue
                if not str(monitor.get("entity_id", "")).startswith(
                    ("sensor.", "binary_sensor.", "input_number.", "input_select.")
                ):
                    errors.append(
                        f"resource monitor '{resource_id}' has an invalid entity_id"
                    )
                if monitor.get("condition", "below") not in RESOURCE_CONDITIONS:
                    errors.append(
                        f"resource monitor '{resource_id}' has an invalid condition"
                    )
                elif monitor.get("condition", "below") in {
                    "above",
                    "at_least",
                    "below",
                    "at_most",
                }:
                    try:
                        float(monitor.get("threshold"))
                    except (TypeError, ValueError):
                        errors.append(
                            f"resource monitor '{resource_id}' needs a numeric threshold"
                        )
                elif monitor.get("threshold") is None:
                    errors.append(f"resource monitor '{resource_id}' needs a threshold")
                if not str(monitor.get("task_name", "")).strip():
                    errors.append(f"resource monitor '{resource_id}' needs a task_name")
                assignee = monitor.get("assignee")
                if assignee not in self.people:
                    errors.append(
                        f"resource monitor '{resource_id}' has an unknown assignee"
                    )
                unknown_fallbacks = set(monitor.get("fallback_people", [])) - set(
                    self.people
                )
                if unknown_fallbacks:
                    errors.append(
                        f"resource monitor '{resource_id}' has unknown fallback people"
                    )
                for field in ("cooldown", "due_after"):
                    try:
                        duration = self._parse_duration(monitor.get(field, "00:00:00"))
                        if duration < timedelta(0):
                            raise ValueError
                    except (TypeError, ValueError, vol.Invalid):
                        errors.append(
                            f"resource monitor '{resource_id}' has invalid {field}"
                        )

        nfc_feedback = self.defaults.get("nfc_feedback", {})
        if not isinstance(nfc_feedback, dict):
            errors.append("NFC feedback settings must be a mapping")
        else:
            if nfc_feedback.get("mode", "always") not in NFC_FEEDBACK_MODES:
                errors.append("NFC feedback has an invalid mode")
            if nfc_feedback.get("recipients", "scanner") not in NFC_FEEDBACK_RECIPIENTS:
                errors.append("NFC feedback has invalid recipients")

        weekly_summary = self.defaults.get("weekly_summary", {})
        if not isinstance(weekly_summary, dict):
            errors.append("Weekly summary settings must be a mapping")
        else:
            if weekly_summary.get("weekday", "sun") not in WEEKDAYS:
                errors.append("Weekly summary has an invalid weekday")
            try:
                self._parse_time(weekly_summary.get("time", "18:00:00"))
            except ValueError:
                errors.append("Weekly summary has an invalid time")

        if errors:
            raise vol.Invalid("; ".join(errors))

    def _apply_editable_config(self, editable: dict[str, Any]) -> None:
        """Apply the UI-managed part of the configuration."""
        for key in ("people", "tasks", "defaults", "monitors"):
            if key in editable:
                self.config[key] = deepcopy(editable[key])
        self.people = self.config["people"]
        self.tasks = self.config["tasks"]
        self.defaults = self.config.get("defaults", {})
        self.monitors = self.config.get("monitors", {})

    def _editable_config(self) -> dict[str, Any]:
        """Return a serializable snapshot of UI-editable configuration."""
        return deepcopy(
            {
                "people": self.people,
                "tasks": self.tasks,
                "defaults": self.defaults,
                "monitors": self.monitors,
            }
        )

    async def async_save_task(self, task_id: str, task: dict[str, Any]) -> None:
        """Create or update a task template from the UI."""
        async with self.lock:
            previous = deepcopy(self.tasks)
            self.tasks[task_id] = deepcopy(task)
            try:
                self._validate_config()
            except vol.Invalid:
                self.tasks = previous
                self.config["tasks"] = self.tasks
                raise
            self.config["tasks"] = self.tasks
            self.state["ui_config"] = self._editable_config()
            self._refresh_state_listener()
            await self._save()

    async def async_delete_task(self, task_id: str) -> None:
        """Delete a task template from UI-managed configuration."""
        async with self.lock:
            if task_id not in self.tasks:
                raise vol.Invalid(f"Unknown task_id: {task_id}")
            if any(
                follow_up.get("task_id") == task_id
                for other_id, task in self.tasks.items()
                if other_id != task_id
                for follow_up in task.get("follow_ups", [])
                if isinstance(follow_up, dict)
            ):
                raise vol.Invalid("Die Vorlage wird noch als Folgeaufgabe verwendet.")
            self.tasks.pop(task_id)
            self.config["tasks"] = self.tasks
            self.state["ui_config"] = self._editable_config()
            self._refresh_state_listener()
            await self._save()

    async def async_save_person(self, person_id: str, person: dict[str, Any]) -> None:
        """Create or update a person mapping from the UI."""
        async with self.lock:
            previous = deepcopy(self.people)
            self.people[person_id] = deepcopy(person)
            try:
                self._validate_config()
            except vol.Invalid:
                self.people = previous
                self.config["people"] = self.people
                raise
            self.config["people"] = self.people
            self.state["ui_config"] = self._editable_config()
            await self._save()

    async def async_save_defaults(self, defaults: dict[str, Any]) -> None:
        """Update global defaults from the UI."""
        async with self.lock:
            previous = deepcopy(self.defaults)
            self.defaults = deepcopy(defaults)
            self.config["defaults"] = self.defaults
            try:
                self._validate_config()
            except vol.Invalid:
                self.defaults = previous
                self.config["defaults"] = self.defaults
                raise
            self.state["ui_config"] = self._editable_config()
            await self._save()

    async def async_save_monitors(self, monitors: dict[str, Any]) -> None:
        """Update automatic device monitors from the UI."""
        async with self.lock:
            previous = deepcopy(self.monitors)
            self.monitors = deepcopy(monitors)
            self.config["monitors"] = self.monitors
            try:
                self._validate_config()
            except vol.Invalid:
                self.monitors = previous
                self.config["monitors"] = self.monitors
                raise
            self.state["ui_config"] = self._editable_config()
            self._refresh_state_listener()
            await self._save()

    async def async_set_handover(
        self,
        from_person: str,
        to_person: str,
        *,
        until: str | None = None,
        reason: str | None = None,
    ) -> None:
        """Temporarily transfer current and future responsibility."""
        async with self.lock:
            if from_person not in self.people or to_person not in self.people:
                raise vol.Invalid("Unknown handover person")
            if from_person == to_person:
                raise vol.Invalid("Handover source and target must differ")
            parsed_until = dt_util.parse_datetime(until) if until else None
            if until and parsed_until is None:
                raise vol.Invalid("Handover until must be an ISO date-time")
            if parsed_until and dt_util.as_utc(parsed_until) <= dt_util.utcnow():
                raise vol.Invalid("Handover until must be in the future")
            handovers = self.state.setdefault("handovers", {})
            previous = deepcopy(handovers)
            handovers[from_person] = {
                "to": to_person,
                "until": parsed_until.isoformat() if parsed_until else None,
                "reason": str(reason or "").strip() or None,
                "started_at": dt_util.utcnow().isoformat(),
            }
            if self._active_handover_target(to_person) == from_person:
                self.state["handovers"] = previous
                raise vol.Invalid("Handover would create a cycle")

            effective_target = self._active_handover_target(from_person)
            for occurrence_id, occurrence in self.state["occurrences"].items():
                if (
                    occurrence.get("resolved")
                    or occurrence.get("assignee") != from_person
                ):
                    continue
                if occurrence.get("task", {}).get("assignment", {}).get(
                    "presence_required", False
                ) and not self._is_home(effective_target):
                    await self._open_occurrence_for_claim(
                        occurrence_id,
                        occurrence,
                        reason_type="presence",
                        notify=False,
                    )
                    occurrence["assignment_reason"].update(
                        {
                            "selected": None,
                            "original": from_person,
                            "candidates": [],
                            "waiting": True,
                        }
                    )
                    continue
                await self._reassign_occurrence_to(
                    occurrence_id,
                    occurrence,
                    effective_target,
                    reason_type="handover",
                    reason=reason,
                )
            await self._save()

    async def async_clear_handover(self, from_person: str) -> None:
        """Stop applying a temporary responsibility handover."""
        async with self.lock:
            if from_person not in self.people:
                raise vol.Invalid("Unknown handover person")
            self.state.setdefault("handovers", {}).pop(from_person, None)
            await self._save()

    async def async_delete_person(self, person_id: str) -> None:
        """Delete an unused person mapping."""
        async with self.lock:
            if any(
                task.get("assignee") == person_id
                or person_id in task.get("assignment", {}).get("people", [])
                for task in self.tasks.values()
            ):
                raise vol.Invalid(
                    "Die Person ist noch mindestens einer Aufgabe zugeordnet."
                )
            if (
                self.monitors.get("printers", {}).get("enabled")
                and self.monitors["printers"].get("assignee") == person_id
            ):
                raise vol.Invalid("Die Person ist noch für Druckerprobleme zuständig.")
            if any(
                monitor.get("assignee") == person_id
                for monitor in self.monitors.get("resources", {}).values()
                if isinstance(monitor, dict)
            ):
                raise vol.Invalid(
                    "Die Person ist noch einem Ressourcenmonitor zugeordnet."
                )
            handovers = self.state.get("handovers", {})
            if person_id in handovers or any(
                handover.get("to") == person_id
                for handover in handovers.values()
                if isinstance(handover, dict)
            ):
                raise vol.Invalid("Die Person wird noch in einer Übergabe verwendet.")
            if person_id not in self.people:
                raise vol.Invalid(f"Unknown person_id: {person_id}")
            self.people.pop(person_id)
            self.config["people"] = self.people
            self.state["ui_config"] = self._editable_config()
            await self._save()

    async def async_reset_ui_config(self) -> None:
        """Discard UI overrides and restore the initial values."""
        async with self.lock:
            self.config = deepcopy(self.initial_config)
            self.people = self.config["people"]
            self.tasks = self.config["tasks"]
            self.defaults = self.config.get("defaults", {})
            self.monitors = self.config.get("monitors", {})
            self._validate_config()
            self.state["ui_config"] = self._editable_config()
            self._refresh_state_listener()
            await self._save()

    def export_ui_config(self) -> dict[str, Any]:
        """Return a portable, versioned copy of the editable configuration."""
        return build_export(self._editable_config(), exported_at=dt_util.utcnow())

    async def async_import_ui_config(self, payload: Any) -> None:
        """Atomically replace editable configuration from a portable export."""
        try:
            editable = parse_import(payload)
        except ValueError as err:
            raise vol.Invalid(str(err)) from err
        async with self.lock:
            previous = self._editable_config()
            try:
                self._apply_editable_config(editable)
                self._validate_config()
            except Exception as err:
                self._apply_editable_config(previous)
                if isinstance(err, vol.Invalid):
                    raise
                raise vol.Invalid(f"Invalid imported configuration: {err}") from err
            self.state["ui_config"] = self._editable_config()
            self._refresh_state_listener()
            await self._save()

    def _refresh_state_listener(self) -> None:
        """Rebuild state listeners after a task template change."""
        if self.remove_state_listener:
            self.remove_state_listener()
            self.remove_state_listener = None
        watched_entities = self._watched_state_entities()
        self.watched_entities = tuple(watched_entities)
        if watched_entities:
            self.remove_state_listener = async_track_state_change_event(
                self.hass, watched_entities, self._handle_state_change
            )

    def ui_data(self) -> dict[str, Any]:
        """Return data used by the household task panel."""
        occurrences = []
        for occurrence_id, occurrence in list(self.state["occurrences"].items()):
            item = deepcopy(occurrence)
            item["id"] = occurrence_id
            occurrences.append(item)
        occurrences.sort(key=lambda item: item.get("due", ""), reverse=True)
        return {
            "people": deepcopy(self.people),
            "tasks": deepcopy(self.tasks),
            "defaults": deepcopy(self.defaults),
            "monitors": deepcopy(self.monitors),
            "detected_printers": self._printer_entities(),
            "scores": deepcopy(self.state.get("scores", {})),
            "handovers": deepcopy(self.state.get("handovers", {})),
            "occurrences": occurrences,
            "todo_entity": self.todo_entity,
            "using_ui_config": self.state.get("ui_config") is not None,
            "last_check": self.state.get("last_check"),
            "analytics": build_analytics(
                self.state["occurrences"],
                self.people,
                now=dt_util.now(),
            ),
        }

    async def async_preview_task(self, task: dict[str, Any]) -> dict[str, Any]:
        """Preview a task without creating an occurrence or changing state."""
        schedule = task.get("schedule", {})
        schedule_type = schedule.get("type", "manual")
        now = dt_util.now().replace(microsecond=0)
        result: dict[str, Any] = {
            "schedule_type": schedule_type,
            "next_due": None,
            "calendar_events": [],
            "state_triggers": [],
        }

        if schedule_type in {
            "weekly",
            "monthly",
            "yearly",
            "interval_months",
        }:
            end = now + timedelta(days=400)
            result["next_due"] = next(
                (
                    due.isoformat()
                    for due in self._scheduled_times(
                        schedule, now - timedelta(microseconds=1), end
                    )
                ),
                None,
            )
        elif schedule_type == "after_completion":
            start = dt_util.parse_datetime(str(schedule.get("start", "")))
            if start is not None and start.tzinfo is None:
                start = start.replace(tzinfo=ZoneInfo(self.hass.config.time_zone))
            if start is not None and start > now:
                result["next_due"] = start.isoformat()
        elif schedule_type == "calendar":
            result["calendar_events"] = await self._preview_calendar_events(
                schedule, now
            )
            if result["calendar_events"]:
                result["next_due"] = result["calendar_events"][0]["due"]

        if schedule_type in {"state_trigger", "daily_after_state"}:
            for trigger in schedule.get("triggers", []):
                entity_id = str(trigger.get("entity_id", ""))
                state = self.hass.states.get(entity_id)
                current = state.state if state is not None else None
                wanted = str(trigger.get("to", ""))
                result["state_triggers"].append(
                    {
                        "entity_id": entity_id,
                        "current": current,
                        "wanted": wanted,
                        "matches": current == wanted,
                        "available": state is not None
                        and current not in {"unknown", "unavailable"},
                    }
                )
        return result

    async def _preview_calendar_events(
        self, schedule: dict[str, Any], now: datetime
    ) -> list[dict[str, str]]:
        """Return matching calendar events for the preview window."""
        offset = self._parse_duration(schedule.get("offset", "00:00:00"))
        query_end = now + timedelta(days=90) - offset
        entity_id = str(schedule.get("entity_id", ""))
        response = await self.hass.services.async_call(
            "calendar",
            "get_events",
            {
                "entity_id": entity_id,
                "start_date_time": (now - offset).isoformat(),
                "end_date_time": query_end.isoformat(),
            },
            blocking=True,
            return_response=True,
        )
        pattern = schedule.get("match")
        matches: list[dict[str, str]] = []
        for calendar_event in (response or {}).get(entity_id, {}).get("events", []):
            summary = str(calendar_event.get("summary", ""))
            if pattern and re.search(str(pattern), summary, re.IGNORECASE) is None:
                continue
            event_start = self._parse_calendar_datetime(calendar_event.get("start"))
            if event_start is None:
                continue
            due = event_start + offset
            if due < now:
                continue
            matches.append(
                {
                    "summary": summary,
                    "start": event_start.isoformat(),
                    "due": due.isoformat(),
                }
            )
        matches.sort(key=lambda item: item["due"])
        return matches[:10]

    async def async_test_notification(self, person_id: str) -> None:
        """Send an explicit, harmless test notification to one person."""
        person = self.people.get(person_id)
        if person is None:
            raise vol.Invalid(f"Unknown person_id: {person_id}")
        service = str(person["notify"]).removeprefix("notify.")
        await self.hass.services.async_call(
            "notify",
            service,
            {
                "title": "Household Tasks",
                "message": "Testbenachrichtigung erfolgreich zugestellt.",
                "data": {"tag": "household_tasks_test", "url": "/haushaltsaufgaben"},
            },
            blocking=True,
        )

    async def async_create_manual(
        self, task_id: str, context: Context | None = None
    ) -> None:
        """Create an occurrence from the panel."""
        async with self.lock:
            task = self.tasks.get(task_id)
            if task is None:
                raise vol.Invalid(f"Unknown task_id: {task_id}")
            if not task.get("enabled", True):
                raise vol.Invalid(f"Task is disabled: {task_id}")
            await self._create_occurrence(
                task_id, task, dt_util.now(), manual=True, context=context
            )
            await self._save()

    async def async_create_ad_hoc(
        self,
        name: str,
        assignee: str,
        due: str,
        *,
        description: str | None = None,
        escalation: list[dict[str, Any]] | None = None,
        context: Context | None = None,
    ) -> None:
        """Create a one-off task without adding a reusable template."""
        async with self.lock:
            clean_name = name.strip()
            if not clean_name:
                raise vol.Invalid("Die Aufgabe benötigt einen Namen.")
            if assignee not in self.people:
                raise vol.Invalid("Die ausgewählte Person ist unbekannt.")
            parsed_due = dt_util.parse_datetime(due)
            if parsed_due is None:
                raise vol.Invalid("Die Fälligkeit ist ungültig.")
            if parsed_due.tzinfo is None:
                parsed_due = parsed_due.replace(
                    tzinfo=ZoneInfo(self.hass.config.time_zone)
                )
            task: dict[str, Any] = {
                "enabled": True,
                "name": clean_name,
                "assignee": assignee,
                "schedule": {"type": "manual"},
            }
            if description and description.strip():
                task["description"] = description.strip()
            if escalation is not None:
                task["escalation"] = deepcopy(escalation)
                for index, step in enumerate(escalation):
                    try:
                        self._parse_duration(step["after"])
                    except (KeyError, TypeError, ValueError, vol.Invalid) as err:
                        raise vol.Invalid(
                            f"Eskalation {index + 1} ist ungültig."
                        ) from err
            await self._create_occurrence(
                "__adhoc__",
                task,
                parsed_due,
                manual=True,
                context=context,
            )
            await self._save()

    async def async_complete_occurrence(
        self, occurrence_id: str, context: Context | None = None
    ) -> None:
        """Complete a tracked native to-do from the panel."""
        async with self.lock:
            occurrence = self.state["occurrences"].get(occurrence_id)
            if not occurrence or occurrence.get("resolved"):
                raise vol.Invalid("Die Aufgabe ist nicht mehr offen.")
            uid = occurrence.get("uid")
            if not uid:
                await self._refresh_open_items()
                uid = occurrence.get("uid")
            if not uid:
                raise vol.Invalid("Die To-do-ID konnte nicht ermittelt werden.")
            await self.hass.services.async_call(
                "todo",
                "update_item",
                {
                    "entity_id": self.todo_entity,
                    "item": uid,
                    "status": "completed",
                },
                blocking=True,
                context=context,
            )
            await self._resolve_occurrence(
                occurrence_id,
                occurrence,
                completed_by=self._person_for_context(context),
            )
            await self._save()

    async def _scheduled_scan(self, now: datetime) -> None:
        await self.async_scan(now)

    async def _handle_scan(self, call: ServiceCall) -> None:
        await self.async_scan()

    async def _handle_create(self, call: ServiceCall) -> None:
        task_id = call.data["task_id"]
        task = self.tasks.get(task_id)
        if task is None:
            raise vol.Invalid(f"Unknown task_id: {task_id}")
        if not task.get("enabled", True):
            raise vol.Invalid(f"Task is disabled: {task_id}")
        await self._create_occurrence(
            task_id,
            task,
            dt_util.now(),
            manual=True,
            context=call.context,
        )

    def _watched_state_entities(self) -> list[str]:
        """Return task trigger and automatically discovered printer entities."""
        entities: set[str] = set()
        for task in self.tasks.values():
            if not task.get("enabled", True):
                continue
            schedule = task.get("schedule", {})
            if schedule.get("type") not in {
                "daily_after_state",
                "state_trigger",
            }:
                continue
            entities.update(
                trigger["entity_id"] for trigger in schedule.get("triggers", [])
            )
        entities.update(
            person["presence"]
            for person in self.people.values()
            if person.get("presence")
        )
        entities.update(
            monitor["entity_id"]
            for monitor in self.monitors.get("resources", {}).values()
            if isinstance(monitor, dict)
            and monitor.get("enabled", True)
            and monitor.get("entity_id")
        )
        if self.monitors.get("printers", {}).get("enabled"):
            entities.update(
                printer["entity_id"] for printer in self._printer_entities()
            )
        return sorted(entities)

    def _printer_entities(self) -> list[dict[str, str]]:
        """Discover IPP primary status sensors without storing device IDs."""
        registry = er.async_get(self.hass)
        printers: list[dict[str, str]] = []
        for entry in registry.entities.values():
            if (
                entry.platform != "ipp"
                or entry.disabled_by is not None
                or not entry.entity_id.startswith("sensor.")
            ):
                continue
            state = self.hass.states.get(entry.entity_id)
            if state is None:
                continue
            attributes = state.attributes
            if attributes.get("device_class") != "enum":
                continue
            if not (
                attributes.get("info")
                or "state_reason" in attributes
                or "state_message" in attributes
            ):
                continue
            printers.append(
                {
                    "entity_id": entry.entity_id,
                    "name": str(
                        attributes.get("info")
                        or attributes.get("friendly_name")
                        or entry.original_name
                        or entry.entity_id
                    ),
                }
            )
        return sorted(printers, key=lambda item: item["name"].casefold())

    async def _handle_tag_scanned(self, event: Event) -> None:
        """Create or complete a configured task after an NFC tag scan."""
        tag_id = str(event.data.get("tag_id", "")).strip()
        match = task_for_tag(self.tasks, tag_id)
        if not tag_id or match is None:
            return
        task_id, task = match
        action = task.get("nfc", {}).get("action", "create_or_complete")
        open_match = open_occurrence_for_task(self.state["occurrences"], task_id)
        result = "ignored"
        occurrence_id: str | None = None
        assignee: str | None = None
        try:
            if action in {"create_or_complete", "complete"} and open_match:
                occurrence_id = open_match[0]
                assignee = open_match[1].get("assignee")
                await self.async_complete_occurrence(occurrence_id, event.context)
                result = "completed"
            elif action in {"create_or_complete", "create"}:
                before = set(self.state["occurrences"])
                await self.async_create_manual(task_id, event.context)
                created = set(self.state["occurrences"]) - before
                occurrence_id = next(iter(created), None)
                if occurrence_id:
                    assignee = self.state["occurrences"][occurrence_id].get("assignee")
                result = "created"
            elif task.get("assignee") in self.people:
                assignee = task["assignee"]
        except Exception:
            _LOGGER.exception(
                "Could not process NFC tag %s for task %s", tag_id, task_id
            )
            result = "error"
        self.hass.bus.async_fire(
            f"{DOMAIN}_nfc_action",
            {
                "tag_id": tag_id,
                "task_id": task_id,
                "occurrence_id": occurrence_id,
                "result": result,
            },
            context=event.context,
        )
        await self._send_nfc_feedback(
            task=task,
            tag_id=tag_id,
            device_id=event.data.get("device_id"),
            assignee=assignee,
            result=result,
            context=event.context,
        )

    async def _send_nfc_feedback(
        self,
        *,
        task: dict[str, Any],
        tag_id: str,
        device_id: str | None,
        assignee: str | None,
        result: str,
        context: Context,
    ) -> None:
        """Send a short NFC result notification to configured recipients."""
        settings = self.defaults.get("nfc_feedback", {})
        mode = settings.get("mode", "always")
        if mode == "off" or (mode == "errors" and result not in {"ignored", "error"}):
            return
        recipients = feedback_recipients(
            self.people,
            user_id=context.user_id,
            device_id=device_id,
            assignee=assignee,
            recipient_mode=settings.get("recipients", "scanner"),
        )
        task_name = task.get("name", "Aufgabe")
        is_german = str(getattr(self.hass.config, "language", "de")).startswith("de")
        if is_german:
            title = "NFC-Aufgabe"
            messages = {
                "created": f"{task_name} wurde erstellt.",
                "completed": f"{task_name} wurde erledigt.",
                "ignored": f"Keine offene Aufgabe für {task_name} gefunden.",
                "error": f"{task_name} konnte nicht verarbeitet werden.",
            }
        else:
            title = "NFC task"
            messages = {
                "created": f"{task_name} was created.",
                "completed": f"{task_name} was completed.",
                "ignored": f"No open task found for {task_name}.",
                "error": f"{task_name} could not be processed.",
            }
        for person_id in recipients:
            service = self.people[person_id]["notify"].removeprefix("notify.")
            try:
                await self.hass.services.async_call(
                    "notify",
                    service,
                    {
                        "title": title,
                        "message": messages[result],
                        "data": {
                            "tag": f"household_tasks_nfc_{tag_id}",
                            "url": "/haushaltsaufgaben",
                        },
                    },
                    blocking=True,
                    context=context,
                )
            except Exception:
                _LOGGER.exception("Could not send NFC feedback to %s", person_id)

    async def _handle_state_change(self, event: Event) -> None:
        """Latch matching state transitions once per local calendar day."""
        entity_id = event.data["entity_id"]
        old_state = event.data.get("old_state")
        new_state = event.data.get("new_state")
        if new_state is None:
            return
        presence_returned = any(
            person.get("presence") == entity_id and new_state.state == "home"
            for person in self.people.values()
        )
        if presence_returned:
            async with self.lock:
                await self._assign_waiting_presence()
                await self._save()

        resource_entities = {
            monitor.get("entity_id")
            for monitor in self.monitors.get("resources", {}).values()
            if isinstance(monitor, dict) and monitor.get("enabled", True)
        }
        if entity_id in resource_entities:
            async with self.lock:
                await self._scan_resource_monitors()
                await self._process_escalations(
                    dt_util.now().replace(second=0, microsecond=0)
                )
                await self._save()

        if entity_id in {printer["entity_id"] for printer in self._printer_entities()}:
            async with self.lock:
                await self._scan_printer_problems()
                await self._process_escalations(
                    dt_util.now().replace(second=0, microsecond=0)
                )
                await self._save()

        for task_id, task in self.tasks.items():
            if not task.get("enabled", True):
                continue
            schedule = task.get("schedule", {})
            if schedule.get("type") not in {
                "daily_after_state",
                "state_trigger",
            }:
                continue
            for trigger in schedule.get("triggers", []):
                if trigger["entity_id"] != entity_id:
                    continue

                key = (task_id, entity_id)
                expected_from = trigger.get("from")
                expected_to = str(trigger["to"])
                pending = self.pending_state_checks.get(key)
                if pending and new_state.state != expected_to:
                    cancel = self.pending_state_checks.pop(key, None)
                    if cancel:
                        cancel()
                if (
                    old_state is None
                    or (expected_from is not None and old_state.state != expected_from)
                    or new_state.state != expected_to
                ):
                    continue
                if cancel := self.pending_state_checks.pop(key, None):
                    cancel()

                delay = self._parse_duration(trigger.get("for", "00:00:00"))
                if delay <= timedelta(0):
                    await self._process_state_trigger(
                        task_id, task, new_state.last_changed
                    )
                    continue

                def _confirm(
                    now: datetime,
                    *,
                    expected_task_id: str = task_id,
                    expected_task: dict[str, Any] = task,
                    expected_entity: str = entity_id,
                    expected_to: str = expected_to,
                ) -> None:
                    self.pending_state_checks.pop(
                        (expected_task_id, expected_entity), None
                    )
                    state = self.hass.states.get(expected_entity)
                    if state is not None and state.state == expected_to:
                        self.hass.async_create_task(
                            self._process_state_trigger(
                                expected_task_id, expected_task, now
                            )
                        )

                self.pending_state_checks[key] = async_call_later(
                    self.hass, delay.total_seconds(), _confirm
                )

    async def _process_state_trigger(
        self, task_id: str, task: dict[str, Any], happened_at: datetime
    ) -> None:
        """Dispatch a daily or unrestricted state-driven occurrence."""
        if task.get("schedule", {}).get("type") == "daily_after_state":
            await self._latch_daily_trigger(task_id, task, happened_at)
            return
        await self._create_state_trigger_occurrence(task_id, task, happened_at)

    async def _create_state_trigger_occurrence(
        self, task_id: str, task: dict[str, Any], happened_at: datetime
    ) -> None:
        """Create an occurrence after a matching state change."""
        async with self.lock:
            schedule = task["schedule"]
            local_time = dt_util.as_local(happened_at)
            previous = dt_util.parse_datetime(
                self.state["state_triggers"].get(task_id, "")
            )
            cooldown = self._parse_duration(schedule.get("cooldown", "00:00:00"))
            has_open = any(
                occurrence.get("task_id") == task_id and not occurrence.get("resolved")
                for occurrence in self.state["occurrences"].values()
            )
            if not state_trigger_allowed(
                happened_at=local_time,
                previous=dt_util.as_local(previous) if previous else None,
                cooldown=cooldown,
                has_open_occurrence=has_open,
                skip_if_open=schedule.get("skip_if_open", True),
            ):
                return
            due = local_time + self._parse_duration(
                schedule.get("due_after", "00:00:00")
            )
            await self._create_occurrence(task_id, task, due, manual=True)
            self.state["state_triggers"][task_id] = local_time.isoformat()
            await self._save()

    async def _latch_daily_trigger(
        self, task_id: str, task: dict[str, Any], happened_at: datetime
    ) -> None:
        """Remember one matching event per source day across restarts."""
        async with self.lock:
            local_time = dt_util.as_local(happened_at)
            source_day = local_time.date().isoformat()
            task_days = self.state["daily_triggers"].setdefault(task_id, {})
            if source_day in task_days:
                return

            cutoff = self._parse_time(task["schedule"].get("time", "18:00:00"))
            target_day = local_time.date()
            if local_time.time().replace(tzinfo=None) >= cutoff:
                target_day += timedelta(days=1)
            task_days[source_day] = target_day.isoformat()
            due = datetime.combine(
                target_day,
                cutoff,
                ZoneInfo(self.hass.config.time_zone),
            )
            if await self._create_occurrence(task_id, task, due):
                task_days[source_day] = None
            await self._save()
            _LOGGER.info(
                "Created daily state task %s for %s after event on %s",
                task_id,
                target_day,
                source_day,
            )

    async def _handle_notification_action(self, event: Event) -> None:
        action = str(event.data.get("action", ""))
        prefixes = (
            ACTION_PREFIX,
            SNOOZE_EVENING_PREFIX,
            SNOOZE_TOMORROW_PREFIX,
            DECLINE_PREFIX,
            HELP_PREFIX,
            CLAIM_HELP_PREFIX,
            CLAIM_TASK_PREFIX,
        )
        prefix = next((item for item in prefixes if action.startswith(item)), None)
        if prefix is None:
            return
        payload = action.removeprefix(prefix)
        helper_id = None
        completed_by = None
        if prefix in {CLAIM_HELP_PREFIX, CLAIM_TASK_PREFIX}:
            occurrence_id, separator, helper_id = payload.partition("_")
            if not separator or helper_id not in self.people:
                return
        elif prefix == ACTION_PREFIX:
            occurrence_id, separator, actor_id = payload.partition("_")
            if separator and actor_id in self.people:
                completed_by = actor_id
        else:
            occurrence_id = payload
        async with self.lock:
            occurrence = self.state["occurrences"].get(occurrence_id)
            if not occurrence or occurrence.get("resolved"):
                return
            if prefix == SNOOZE_EVENING_PREFIX:
                now = dt_util.now()
                evening = datetime.combine(
                    now.date(),
                    time(18, 0),
                    ZoneInfo(self.hass.config.time_zone),
                )
                await self._snooze_occurrence(
                    occurrence_id,
                    occurrence,
                    evening if evening > now else now + timedelta(hours=2),
                )
                await self._save()
                return
            if prefix == SNOOZE_TOMORROW_PREFIX:
                now = dt_util.now()
                tomorrow = datetime.combine(
                    now.date() + timedelta(days=1),
                    time(9, 0),
                    ZoneInfo(self.hass.config.time_zone),
                )
                await self._snooze_occurrence(occurrence_id, occurrence, tomorrow)
                await self._save()
                return
            if prefix == DECLINE_PREFIX:
                await self._delegate_occurrence(occurrence_id, occurrence)
                await self._save()
                return
            if prefix == HELP_PREFIX:
                helpers = [
                    person for person in self.people if person != occurrence["assignee"]
                ]
                occurrence["help_requested_at"] = dt_util.utcnow().isoformat()
                for helper in helpers:
                    await self._notify(
                        occurrence_id,
                        occurrence,
                        [helper],
                        "Hilfe benötigt",
                        f"{occurrence['title']}: Kannst du unterstützen?",
                        claim_help_for=helper,
                    )
                await self._save()
                return
            if prefix == CLAIM_HELP_PREFIX and helper_id is not None:
                helpers = occurrence.setdefault("helpers", [])
                if helper_id not in helpers:
                    helpers.append(helper_id)
                await self._clear_notification_for(occurrence_id, helper_id)
                await self._notify(
                    occurrence_id,
                    occurrence,
                    [occurrence["assignee"]],
                    "Hilfe zugesagt",
                    (
                        f"{self.people[helper_id]['name']} hilft bei "
                        f"{occurrence['title']}."
                    ),
                )
                await self._save()
                return
            if prefix == CLAIM_TASK_PREFIX and helper_id is not None:
                await self._claim_occurrence(occurrence_id, occurrence, helper_id)
                await self._save()
                return
            uid = occurrence.get("uid")
            if not uid:
                await self._refresh_open_items()
                uid = occurrence.get("uid")
            if not uid:
                _LOGGER.warning(
                    "Cannot complete occurrence %s because its to-do UID is unknown",
                    occurrence_id,
                )
                return
            await self.hass.services.async_call(
                "todo",
                "update_item",
                {"entity_id": self.todo_entity, "item": uid, "status": "completed"},
                blocking=True,
            )
            await self._resolve_occurrence(
                occurrence_id,
                occurrence,
                completed_by=completed_by,
            )
            await self._save()

    async def async_claim_occurrence(
        self, occurrence_id: str, context: Context | None = None
    ) -> None:
        """Claim an open task from the panel using the current HA user."""
        async with self.lock:
            occurrence = self.state["occurrences"].get(occurrence_id)
            if not occurrence or occurrence.get("resolved"):
                raise vol.Invalid("Die Aufgabe ist nicht mehr offen.")
            person_id = self._person_for_context(context)
            if person_id is None:
                raise vol.Invalid(
                    "Dem Home-Assistant-Benutzer ist keine Person zugeordnet."
                )
            await self._claim_occurrence(
                occurrence_id, occurrence, person_id, context=context
            )
            await self._save()

    async def _claim_occurrence(
        self,
        occurrence_id: str,
        occurrence: dict[str, Any],
        person_id: str,
        *,
        context: Context | None = None,
    ) -> None:
        """Assign an open occurrence to one eligible person."""
        if occurrence.get("assignee") is not None:
            raise vol.Invalid("Die Aufgabe wurde bereits übernommen.")
        task = occurrence.get("task", {})
        candidates = self._assignment_candidates(task)
        if person_id not in candidates:
            raise vol.Invalid("Diese Person darf die Aufgabe nicht übernehmen.")
        task_name = re.sub(r"^\[[^\]]+\]\s*", "", occurrence["title"])
        title = f"[{self.people[person_id]['name']}] {task_name}"
        uid = occurrence.get("uid")
        if not uid:
            await self._refresh_open_items()
            uid = occurrence.get("uid")
        if not uid:
            raise vol.Invalid("Die To-do-ID konnte nicht ermittelt werden.")
        await self.hass.services.async_call(
            "todo",
            "update_item",
            {
                "entity_id": self.todo_entity,
                "item": uid,
                "rename": title,
            },
            blocking=True,
            context=context,
        )
        await self._clear_notifications(occurrence_id, occurrence)
        occurrence["assignee"] = person_id
        occurrence["title"] = title
        occurrence["claimed_at"] = dt_util.utcnow().isoformat()
        occurrence["assignment_reason"] = {
            "type": "claimed",
            "selected": person_id,
            "candidates": candidates,
        }
        self._record_assignment(person_id)
        await self._notify(
            occurrence_id,
            occurrence,
            [person_id],
            "Aufgabe übernommen",
            title,
        )

    async def _reassign_occurrence_to(
        self,
        occurrence_id: str,
        occurrence: dict[str, Any],
        person_id: str,
        *,
        reason_type: str,
        reason: str | None = None,
    ) -> None:
        """Move an existing occurrence and retain an auditable history."""
        if person_id not in self.people:
            return
        previous = occurrence.get("assignee")
        task_name = re.sub(r"^\[[^\]]+\]\s*", "", occurrence["title"])
        title = f"[{self.people[person_id]['name']}] {task_name}"
        await self._update_native_occurrence(occurrence, title=title)
        await self._clear_notifications(occurrence_id, occurrence)
        occurrence["assignee"] = person_id
        occurrence["title"] = title
        occurrence["assignment_reason"] = {
            "type": reason_type,
            "selected": person_id,
            "previous": previous,
            "reason": str(reason or "").strip() or None,
        }
        occurrence.setdefault("assignment_history", []).append(
            {
                "type": reason_type,
                "from": previous,
                "to": person_id,
                "reason": str(reason or "").strip() or None,
                "at": dt_util.utcnow().isoformat(),
            }
        )
        self._record_assignment(person_id)
        await self._notify(
            occurrence_id,
            occurrence,
            [person_id],
            "Aufgabe übergeben",
            title,
        )

    async def _assign_waiting_presence(self) -> None:
        """Assign previously open occurrences when an eligible person returns."""
        for occurrence_id, occurrence in self.state["occurrences"].items():
            if (
                occurrence.get("resolved")
                or occurrence.get("assignee") is not None
                or not occurrence.get("assignment_reason", {}).get("waiting")
            ):
                continue
            task = occurrence.get("task", {})
            assignee, reason = self._select_assignee_with_reason(
                str(occurrence.get("task_id", "")),
                task,
            )
            if assignee is None:
                continue
            await self._reassign_occurrence_to(
                occurrence_id,
                occurrence,
                assignee,
                reason_type="presence",
            )
            occurrence["assignment_reason"].update(reason)

    async def _snooze_occurrence(
        self,
        occurrence_id: str,
        occurrence: dict[str, Any],
        due: datetime,
    ) -> None:
        """Move a native to-do and restart its escalation clock."""
        due = dt_util.as_local(due).replace(second=0, microsecond=0)
        await self._update_native_occurrence(occurrence, due=due)
        await self._clear_notifications(occurrence_id, occurrence)
        occurrence["due"] = due.isoformat()
        occurrence["snoozed_until"] = due.isoformat()
        occurrence["sent_steps"] = []
        occurrence["first_notification_at"] = None

    async def _delegate_occurrence(
        self,
        occurrence_id: str,
        occurrence: dict[str, Any],
        *,
        reset_escalation: bool = True,
    ) -> bool:
        """Pass a task to the next suitable household member."""
        current = occurrence["assignee"]
        candidates = [
            person
            for person in self._assignment_candidates(occurrence.get("task", {}))
            if person != current
        ]
        if not candidates:
            _LOGGER.warning("Cannot delegate %s: no other person", occurrence_id)
            return False
        history = occurrence.setdefault("delegation_history", [current])
        fresh = [person for person in candidates if person not in history]
        pool = fresh or candidates
        home = [person for person in pool if self._is_home(person)]
        assignee = (home or pool)[0]
        old_name = self.people[current]["name"]
        task_name = re.sub(r"^\[[^\]]+\]\s*", "", occurrence["title"])
        title = f"[{self.people[assignee]['name']}] {task_name}"
        await self._update_native_occurrence(occurrence, title=title)
        await self._clear_notifications(occurrence_id, occurrence)
        occurrence["assignee"] = assignee
        occurrence["title"] = title
        occurrence["assignment_reason"] = {
            "type": "delegated",
            "selected": assignee,
            "previous": current,
            "preferred_home": assignee in home,
            "candidates": pool,
        }
        if reset_escalation:
            occurrence["sent_steps"] = []
            occurrence["first_notification_at"] = None
        history.append(assignee)
        occurrence["delegated_at"] = dt_util.utcnow().isoformat()
        self._record_assignment(assignee)
        await self._notify(
            occurrence_id,
            occurrence,
            [assignee],
            f"Von {old_name} weitergegeben",
            title,
        )
        return True

    async def _open_occurrence_for_claim(
        self,
        occurrence_id: str,
        occurrence: dict[str, Any],
        *,
        reason_type: str = "escalated_open",
        notify: bool = True,
    ) -> None:
        """Turn an assigned occurrence into a household claim task."""
        previous = occurrence.get("assignee")
        task_name = re.sub(r"^\[[^\]]+\]\s*", "", occurrence["title"])
        title = f"[Offen] {task_name}"
        await self._update_native_occurrence(occurrence, title=title)
        await self._clear_notifications(occurrence_id, occurrence)
        occurrence["assignee"] = None
        occurrence["title"] = title
        occurrence["assignment_reason"] = {
            "type": reason_type,
            "previous": previous,
            "candidates": self._assignment_candidates(occurrence.get("task", {})),
        }
        occurrence.setdefault("assignment_history", []).append(
            {
                "type": reason_type,
                "from": previous,
                "to": None,
                "at": dt_util.utcnow().isoformat(),
            }
        )
        if notify:
            await self._notify(
                occurrence_id,
                occurrence,
                self._assignment_candidates(occurrence.get("task", {})),
                "Aufgabe zur Übernahme",
                title,
            )

    async def _update_native_occurrence(
        self,
        occurrence: dict[str, Any],
        *,
        due: datetime | None = None,
        title: str | None = None,
    ) -> None:
        uid = occurrence.get("uid")
        if not uid:
            await self._refresh_open_items()
            uid = occurrence.get("uid")
        if not uid:
            raise vol.Invalid("Die To-do-ID konnte nicht ermittelt werden.")
        data: dict[str, Any] = {
            "entity_id": self.todo_entity,
            "item": uid,
        }
        if due is not None:
            data["due_datetime"] = due.isoformat()
        if title is not None:
            data["rename"] = title
        await self.hass.services.async_call("todo", "update_item", data, blocking=True)

    async def _handle_stop(self, event: Event) -> None:
        # A one-shot listener removes itself before invoking the callback.
        self.remove_stop_listener = None
        await self.async_shutdown()

    async def async_shutdown(self) -> None:
        """Remove listeners and persist state."""
        if self.remove_interval:
            self.remove_interval()
            self.remove_interval = None
        if self.remove_state_listener:
            self.remove_state_listener()
            self.remove_state_listener = None
        if self.remove_notification_listener:
            self.remove_notification_listener()
            self.remove_notification_listener = None
        if self.remove_tag_listener:
            self.remove_tag_listener()
            self.remove_tag_listener = None
        if self.remove_stop_listener:
            self.remove_stop_listener()
            self.remove_stop_listener = None
        for cancel in self.pending_state_checks.values():
            cancel()
        self.pending_state_checks.clear()
        await self._save()

    async def async_scan(self, now: datetime | None = None) -> None:
        """Create due items and process completions/escalations."""
        async with self.lock:
            current = dt_util.as_local(now or dt_util.utcnow()).replace(
                second=0, microsecond=0
            )
            if tuple(self._watched_state_entities()) != self.watched_entities:
                self._refresh_state_listener()
            previous = self._previous_check(current)
            zone = ZoneInfo(self.hass.config.time_zone)
            planning_start = datetime.combine(
                previous.date(), time.min, zone
            ) - timedelta(microseconds=1)
            planning_end = datetime.combine(current.date(), time.max, zone)

            await self._create_scheduled_occurrences(planning_start, planning_end)
            await self._ensure_after_completion_starts(planning_end)
            await self._create_daily_state_occurrences()
            await self._create_calendar_occurrences(planning_start, planning_end)
            await self._scan_printer_problems()
            await self._scan_resource_monitors()
            await self._refresh_open_items()
            await self._process_escalations(current)
            await self._process_weekly_summary(current)

            self.state["last_check"] = current.isoformat()
            self._prune_handovers(current)
            self._prune_state(current)
            await self._save()

    async def _scan_resource_monitors(self) -> None:
        """Create and recover tasks from generic sensor threshold rules."""
        issues = self.state.setdefault("resource_issues", {})
        last_created = self.state.setdefault("resource_last_created", {})
        now = dt_util.now()
        for resource_id, monitor in self.monitors.get("resources", {}).items():
            if not isinstance(monitor, dict) or not monitor.get("enabled", True):
                continue
            entity_id = monitor.get("entity_id")
            state = self.hass.states.get(entity_id)
            if state is None or state.state in {"unknown", "unavailable"}:
                continue
            matches = resource_condition_matches(
                state.state,
                monitor.get("condition", "below"),
                monitor.get("threshold"),
            )
            active = issues.get(resource_id)
            occurrence = (
                self.state["occurrences"].get(active.get("occurrence_id"))
                if isinstance(active, dict)
                else None
            )
            if not matches:
                if (
                    occurrence
                    and not occurrence.get("resolved")
                    and monitor.get("auto_resolve", True)
                ):
                    uid = occurrence.get("uid")
                    if uid:
                        await self.hass.services.async_call(
                            "todo",
                            "update_item",
                            {
                                "entity_id": self.todo_entity,
                                "item": uid,
                                "status": "completed",
                            },
                            blocking=True,
                        )
                    await self._resolve_occurrence(
                        active["occurrence_id"],
                        occurrence,
                        award_point=False,
                        resolution_reason="resource_recovered",
                    )
                if occurrence is None or occurrence.get("resolved"):
                    issues.pop(resource_id, None)
                continue
            if occurrence and not occurrence.get("resolved"):
                continue
            issues.pop(resource_id, None)
            previous = dt_util.parse_datetime(last_created.get(resource_id, ""))
            cooldown = self._parse_duration(monitor.get("cooldown", "00:00:00"))
            if previous and now - dt_util.as_local(previous) < cooldown:
                continue

            unit = state.attributes.get("unit_of_measurement")
            task_name = render_resource_text(
                monitor["task_name"],
                state.state,
                unit,
            )
            description_template = monitor.get(
                "description",
                f"{entity_id}: {{state}} {{unit}}",
            )
            task: dict[str, Any] = {
                "enabled": True,
                "name": task_name,
                "description": render_resource_text(
                    description_template,
                    state.state,
                    unit,
                ),
                "assignee": monitor["assignee"],
                "assignment": {
                    "type": "fixed",
                    "people": list(monitor.get("fallback_people", [])),
                    "presence_required": bool(monitor.get("presence_required", False)),
                },
                "schedule": {"type": "manual"},
            }
            if monitor.get("escalation") is not None:
                task["escalation"] = deepcopy(monitor["escalation"])
            occurrence_id = await self._create_occurrence(
                f"__resource__{resource_id}",
                task,
                now + self._parse_duration(monitor.get("due_after", "00:00:00")),
                manual=True,
            )
            issues[resource_id] = {
                "occurrence_id": occurrence_id,
                "state": state.state,
                "created_at": now.isoformat(),
            }
            last_created[resource_id] = now.isoformat()

    async def _scan_printer_problems(self) -> None:
        """Create one task per active IPP problem and close it after recovery."""
        monitor = self.monitors.get("printers", {})
        if not monitor.get("enabled"):
            return
        assignee = monitor.get("assignee")
        if assignee not in self.people:
            return
        issues = self.state.setdefault("printer_issues", {})
        for printer in self._printer_entities():
            entity_id = printer["entity_id"]
            state = self.hass.states.get(entity_id)
            if state is None:
                continue
            problem = self._printer_problem(state)
            active = issues.get(entity_id)
            if problem is None:
                if active:
                    occurrence = self.state["occurrences"].get(
                        active.get("occurrence_id")
                    )
                    if occurrence and not occurrence.get("resolved"):
                        uid = occurrence.get("uid")
                        if uid:
                            await self.hass.services.async_call(
                                "todo",
                                "update_item",
                                {
                                    "entity_id": self.todo_entity,
                                    "item": uid,
                                    "status": "completed",
                                },
                                blocking=True,
                            )
                        await self._resolve_occurrence(
                            active["occurrence_id"],
                            occurrence,
                            award_point=False,
                            resolution_reason="printer_recovered",
                        )
                    issues.pop(entity_id, None)
                continue
            signature, detail = problem
            if active and active.get("signature") == signature:
                continue
            if active:
                occurrence = self.state["occurrences"].get(active.get("occurrence_id"))
                if occurrence and not occurrence.get("resolved"):
                    uid = occurrence.get("uid")
                    if uid:
                        await self.hass.services.async_call(
                            "todo",
                            "update_item",
                            {
                                "entity_id": self.todo_entity,
                                "item": uid,
                                "description": detail,
                            },
                            blocking=True,
                        )
                    occurrence["task"]["description"] = detail
                    active["signature"] = signature
                    await self._notify(
                        active["occurrence_id"],
                        occurrence,
                        [assignee],
                        "Druckerproblem aktualisiert",
                        f"{occurrence['title']}: {detail}",
                    )
                    continue
            task = {
                "enabled": True,
                "name": f"Druckerproblem: {printer['name']}",
                "description": detail,
                "assignee": assignee,
                "schedule": {"type": "manual"},
            }
            occurrence_id = await self._create_occurrence(
                "__printer_problem__",
                task,
                dt_util.now(),
                manual=True,
            )
            issues[entity_id] = {
                "signature": signature,
                "occurrence_id": occurrence_id,
            }

    @staticmethod
    def _printer_problem(state: Any) -> tuple[str, str] | None:
        """Return a stable problem signature and a human-readable description."""
        current = str(state.state or "").strip().casefold()
        attributes = state.attributes
        values: list[str] = []
        for key in ("state_reason", "state_message"):
            raw = attributes.get(key)
            if isinstance(raw, list | tuple | set):
                values.extend(str(item).strip() for item in raw if item)
            elif raw:
                values.append(str(raw).strip())
        benign = {
            "",
            "none",
            "no-error",
            "no_error",
            "unknown",
            "other",
        }
        details = [value for value in values if value.casefold() not in benign]
        problem_states = {"stopped", "error", "problem"}
        if current not in problem_states and not details:
            return None
        detail = " · ".join(dict.fromkeys(details))
        if not detail:
            detail = f"Druckerstatus: {state.state}"
        signature = f"{current}|{'|'.join(details).casefold()}"
        return signature, detail

    def _previous_check(self, now: datetime) -> datetime:
        raw = self.state.get("last_check")
        if not raw:
            return now - timedelta(minutes=2)
        parsed = dt_util.parse_datetime(raw)
        if not parsed:
            return now - timedelta(minutes=2)
        parsed = dt_util.as_local(parsed)
        maximum = timedelta(days=self.config.get("catch_up_days", 7))
        return max(parsed, now - maximum)

    async def _create_scheduled_occurrences(
        self, start: datetime, end: datetime
    ) -> None:
        for task_id, task in self.tasks.items():
            if not task.get("enabled", True):
                continue
            schedule = task["schedule"]
            if schedule.get("type") in {
                "manual",
                "calendar",
                "daily_after_state",
                "state_trigger",
                "after_completion",
            }:
                continue
            for due in self._scheduled_times(schedule, start, end):
                await self._create_occurrence(task_id, task, due)

    async def _ensure_after_completion_starts(self, planning_end: datetime) -> None:
        """Create the initial occurrence for completion-relative schedules."""
        for task_id, task in self.tasks.items():
            schedule = task.get("schedule", {})
            if (
                not task.get("enabled", True)
                or schedule.get("type") != "after_completion"
                or any(
                    occurrence.get("task_id") == task_id
                    for occurrence in self.state["occurrences"].values()
                )
            ):
                continue
            first_due = dt_util.parse_datetime(str(schedule.get("start", "")))
            if first_due is None:
                continue
            if first_due.tzinfo is None:
                first_due = first_due.replace(
                    tzinfo=ZoneInfo(self.hass.config.time_zone)
                )
            if dt_util.as_local(first_due) <= planning_end:
                await self._create_occurrence(task_id, task, first_due)

    async def _process_weekly_summary(self, now: datetime) -> None:
        """Send one household summary in the configured local week."""
        settings = self.defaults.get("weekly_summary", {})
        if not settings.get("enabled", False):
            return
        summary_time = self._parse_time(settings.get("time", "18:00:00"))
        summary_id = weekly_summary_due(
            now=now,
            weekday=WEEKDAYS[settings.get("weekday", "sun")],
            scheduled_time=summary_time,
            last_summary_id=self.state["weekly_summaries"].get("last"),
        )
        if summary_id is None:
            return
        analytics = build_analytics(
            self.state["occurrences"],
            self.people,
            now=now,
            period_days=7,
        )
        insight_count = len(analytics.get("retrospective", []))
        is_german = str(getattr(self.hass.config, "language", "de")).startswith("de")
        if is_german:
            title = "Haushalts-Wochenabschluss"
            message = (
                f"{analytics['completed']} erledigt · "
                f"{analytics['open']} offen · "
                f"{analytics['overdue']} überfällig · "
                f"{analytics['on_time_rate'] if analytics['on_time_rate'] is not None else 'k. A.'} % pünktlich · "
                f"{insight_count} Retrospektiv-Hinweise"
            )
        else:
            title = "Household weekly summary"
            message = (
                f"{analytics['completed']} completed · "
                f"{analytics['open']} open · "
                f"{analytics['overdue']} overdue · "
                f"{analytics['on_time_rate'] if analytics['on_time_rate'] is not None else 'n/a'}% on time · "
                f"{insight_count} retrospective insights"
            )
        for person in self.people.values():
            service = person["notify"].removeprefix("notify.")
            try:
                await self.hass.services.async_call(
                    "notify",
                    service,
                    {
                        "title": title,
                        "message": message,
                        "data": {
                            "tag": f"household_tasks_weekly_{summary_id}",
                            "url": "/haushaltsaufgaben",
                        },
                    },
                    blocking=True,
                )
            except Exception:
                _LOGGER.exception(
                    "Could not send weekly summary using %s",
                    person["notify"],
                )
        self.state["weekly_summaries"]["last"] = summary_id

    async def _create_daily_state_occurrences(self) -> None:
        """Recover any latched daily tasks not yet created."""
        zone = ZoneInfo(self.hass.config.time_zone)
        for task_id, source_days in self.state["daily_triggers"].items():
            task = self.tasks.get(task_id)
            if not task or not task.get("enabled", True):
                continue
            schedule = task["schedule"]
            task_time = self._parse_time(schedule.get("time", "18:00:00"))
            for source_day, target_day in list(source_days.items()):
                if target_day is None:
                    continue
                due = datetime.combine(date.fromisoformat(target_day), task_time, zone)
                if await self._create_occurrence(task_id, task, due):
                    source_days[source_day] = None

    def _scheduled_times(
        self, schedule: dict[str, Any], start: datetime, end: datetime
    ) -> Iterable[datetime]:
        schedule_type = schedule["type"]
        task_time = self._parse_time(schedule.get("time", "09:00:00"))
        zone = ZoneInfo(self.hass.config.time_zone)
        current_date = start.date()
        while current_date <= end.date():
            matches = False
            if schedule_type == "weekly":
                wanted = {WEEKDAYS[item] for item in schedule["weekdays"]}
                matches = current_date.weekday() in wanted
            elif schedule_type == "monthly":
                matches = current_date.day == self._month_day(
                    current_date.year,
                    current_date.month,
                    schedule.get("day", 1),
                )
            elif schedule_type == "yearly":
                matches = current_date.month == int(
                    schedule["month"]
                ) and current_date.day == self._month_day(
                    current_date.year,
                    int(schedule["month"]),
                    schedule.get("day", 1),
                )
            elif schedule_type == "interval_months":
                anchor = date.fromisoformat(schedule["start"])
                month_delta = (
                    (current_date.year - anchor.year) * 12
                    + current_date.month
                    - anchor.month
                )
                target_day = self._month_day(
                    current_date.year, current_date.month, anchor.day
                )
                matches = (
                    month_delta >= 0
                    and month_delta % int(schedule["months"]) == 0
                    and current_date.day == target_day
                )

            candidate = datetime.combine(current_date, task_time, zone)
            if matches and start < candidate <= end:
                yield candidate
            current_date += timedelta(days=1)

    async def _create_calendar_occurrences(
        self, start: datetime, end: datetime
    ) -> None:
        for task_id, task in self.tasks.items():
            if not task.get("enabled", True):
                continue
            schedule = task["schedule"]
            if schedule.get("type") != "calendar":
                continue

            offset = self._parse_duration(schedule.get("offset", "00:00:00"))
            query_start = start - offset
            query_end = end - offset
            entity_id = schedule["entity_id"]
            try:
                response = await self.hass.services.async_call(
                    "calendar",
                    "get_events",
                    {
                        "entity_id": entity_id,
                        "start_date_time": query_start.isoformat(),
                        "end_date_time": query_end.isoformat(),
                    },
                    blocking=True,
                    return_response=True,
                )
            except Exception:  # Home Assistant integrations expose varied errors.
                _LOGGER.exception("Could not read calendar %s", entity_id)
                continue

            events = (response or {}).get(entity_id, {}).get("events", [])
            pattern = schedule.get("match")
            for calendar_event in events:
                summary = str(calendar_event.get("summary", ""))
                if pattern and re.search(str(pattern), summary, re.IGNORECASE) is None:
                    continue
                event_start = self._parse_calendar_datetime(calendar_event.get("start"))
                if event_start is None:
                    continue
                due = event_start + offset
                if start < due <= end:
                    await self._create_occurrence(
                        task_id,
                        task,
                        due,
                        event_summary=summary,
                    )

    def _active_handover_target(
        self,
        person_id: str,
        now: datetime | None = None,
    ) -> str:
        """Resolve an active handover chain without following cycles."""
        current = person_id
        visited = {person_id}
        reference = now or dt_util.utcnow()
        for _ in range(len(self.people)):
            handover = self.state.get("handovers", {}).get(current)
            if not isinstance(handover, dict):
                break
            until = dt_util.parse_datetime(str(handover.get("until", "")))
            if until is not None and dt_util.as_utc(until) <= dt_util.as_utc(reference):
                break
            target = handover.get("to")
            if target not in self.people or target in visited:
                break
            current = target
            visited.add(current)
        return current

    def _assignment_candidates(self, task: dict[str, Any]) -> list[str]:
        configured = task.get("assignment", {}).get("people", [])
        candidates = configured or list(self.people)
        effective = [
            self._active_handover_target(person)
            for person in candidates
            if person in self.people
        ]
        effective = list(dict.fromkeys(effective))
        if task.get("assignment", {}).get("presence_required", False):
            effective = [person for person in effective if self._is_home(person)]
        return effective

    def _select_assignee_with_reason(
        self, task_id: str, task: dict[str, Any]
    ) -> tuple[str | None, dict[str, Any]]:
        """Resolve assignment and preserve the inputs behind the decision."""
        assignment_type = task.get("assignment", {}).get("type", "fixed")
        if assignment_type == "fixed":
            original = task.get("assignee")
            assignee = (
                self._active_handover_target(original)
                if original in self.people
                else original
            )
            if (
                task.get("assignment", {}).get("presence_required", False)
                and assignee in self.people
                and not self._is_home(assignee)
            ):
                configured = task.get("assignment", {}).get("people", [])
                fallback_task = deepcopy(task)
                fallback_task["assignment"] = {
                    "type": "fair",
                    "people": configured or list(self.people),
                    "presence_required": True,
                }
                candidates = self._assignment_candidates(fallback_task)
                counts = self.state.setdefault("assignment_counts", {})
                open_load = {
                    person: sum(
                        1
                        for occurrence in self.state["occurrences"].values()
                        if not occurrence.get("resolved")
                        and occurrence.get("assignee") == person
                    )
                    for person in candidates
                }
                selected = select_fair_candidate(candidates, counts, open_load)
                return selected, {
                    "type": "presence",
                    "selected": selected,
                    "original": original,
                    "candidates": candidates,
                    "waiting": selected is None,
                }
            return assignee, {
                "type": "handover" if assignee != original else "fixed",
                "selected": assignee,
                "original": original,
            }
        if assignment_type == "open":
            return None, {
                "type": "open",
                "candidates": self._assignment_candidates(task),
            }
        candidates = self._assignment_candidates(task)
        if not candidates:
            return None, {
                "type": "presence"
                if task.get("assignment", {}).get("presence_required", False)
                else assignment_type,
                "candidates": [],
                "waiting": True,
            }
        if assignment_type == "rotation":
            cursors = self.state.setdefault("rotation_cursors", {})
            cursor = int(cursors.get(task_id, 0))
            assignee = candidates[cursor % len(candidates)]
            cursors[task_id] = cursor + 1
            return assignee, {
                "type": "rotation",
                "selected": assignee,
                "position": cursor % len(candidates),
                "candidates": candidates,
            }

        counts = self.state.setdefault("assignment_counts", {})
        open_load = {
            person: sum(
                1
                for occurrence in self.state["occurrences"].values()
                if not occurrence.get("resolved")
                and occurrence.get("assignee") == person
            )
            for person in candidates
        }
        assignee = select_fair_candidate(candidates, counts, open_load)
        return assignee, {
            "type": "fair",
            "selected": assignee,
            "candidates": candidates,
            "assignment_counts": {
                person: int(counts.get(person, 0)) for person in candidates
            },
            "open_load": open_load,
        }

    def _record_assignment(self, person_id: str | None) -> None:
        if person_id not in self.people:
            return
        counts = self.state.setdefault("assignment_counts", {})
        counts[person_id] = int(counts.get(person_id, 0)) + 1

    async def _create_occurrence(
        self,
        task_id: str,
        task: dict[str, Any],
        due: datetime,
        *,
        event_summary: str | None = None,
        manual: bool = False,
        context: Context | None = None,
    ) -> str:
        due = dt_util.as_local(due).replace(microsecond=0)
        identity = f"{task_id}|{due.astimezone(dt_util.UTC).isoformat()}"
        if manual:
            identity += f"|{dt_util.utcnow().isoformat()}"
        occurrence_id = hashlib.sha256(identity.encode()).hexdigest()[:20]
        if occurrence_id in self.state["occurrences"]:
            return occurrence_id

        assignee, assignment_reason = self._select_assignee_with_reason(task_id, task)
        assignee_name = (
            self.people[assignee]["name"] if assignee in self.people else "Offen"
        )
        title = f"[{assignee_name}] {task['name']}"
        description = task.get("description")
        if event_summary and description:
            description = f"{description}\nKalender: {event_summary}"
        elif event_summary:
            description = f"Kalender: {event_summary}"

        before = await self._get_open_items()
        data: dict[str, Any] = {
            "entity_id": self.todo_entity,
            "item": title,
            "due_datetime": due.isoformat(),
        }
        if description:
            data["description"] = description
        await self.hass.services.async_call(
            "todo", "add_item", data, blocking=True, context=context
        )
        after = await self._get_open_items()
        new_uids = set(after) - set(before)
        uid = next(
            (
                item_uid
                for item_uid in new_uids
                if after[item_uid].get("summary") == title
            ),
            next(iter(new_uids), None),
        )

        occurrence = {
            "task_id": task_id,
            "task": deepcopy(task),
            "title": title,
            "assignee": assignee,
            "assignment_reason": assignment_reason,
            "due": due.isoformat(),
            "uid": uid,
            "sent_steps": [],
            "notified_people": [],
            "first_notification_at": None,
            "resolved": False,
        }
        self.state["occurrences"][occurrence_id] = occurrence
        self._record_assignment(assignee)

        if self._task_value(task, "notify_on_create", False):
            await self._notify(
                occurrence_id,
                occurrence,
                [assignee] if assignee else self._assignment_candidates(task),
                "Neue Aufgabe",
                title,
            )
        _LOGGER.info("Created task %s as occurrence %s", task_id, occurrence_id)
        return occurrence_id

    async def _refresh_open_items(self) -> None:
        open_items = await self._get_open_items()
        open_uids = set(open_items)
        for occurrence_id, occurrence in list(self.state["occurrences"].items()):
            if occurrence.get("resolved"):
                continue
            uid = occurrence.get("uid")
            if uid and uid not in open_uids:
                await self._resolve_occurrence(
                    occurrence_id,
                    occurrence,
                    completed_by=occurrence.get("assignee"),
                )
                continue
            if uid:
                continue
            due = dt_util.parse_datetime(occurrence["due"])
            for item_uid, item in open_items.items():
                item_due = self._parse_calendar_datetime(item.get("due"))
                if (
                    item.get("summary") == occurrence["title"]
                    and item_due
                    and due
                    and abs((item_due - due).total_seconds()) < 2
                ):
                    occurrence["uid"] = item_uid
                    break

    async def _get_open_items(self) -> dict[str, dict[str, Any]]:
        response = await self.hass.services.async_call(
            "todo",
            "get_items",
            {"entity_id": self.todo_entity, "status": "needs_action"},
            blocking=True,
            return_response=True,
        )
        items = (response or {}).get(self.todo_entity, {}).get("items", [])
        return {str(item["uid"]): item for item in items if item.get("uid") is not None}

    async def _process_escalations(self, now: datetime) -> None:
        for occurrence_id, occurrence in self.state["occurrences"].items():
            if occurrence.get("resolved"):
                continue
            task = occurrence.get("task") or self.tasks.get(occurrence["task_id"])
            if task is None:
                continue
            due = dt_util.parse_datetime(occurrence["due"])
            if due is None:
                continue
            escalation = task.get("escalation", self.defaults.get("escalation", []))
            for index, step in enumerate(escalation):
                if index in occurrence["sent_steps"]:
                    continue
                relative_to = step.get("relative_to", "due")
                if relative_to == "first_notification":
                    first_notification_at = occurrence.get("first_notification_at")
                    first_notification = (
                        dt_util.parse_datetime(first_notification_at)
                        if first_notification_at
                        else None
                    )
                    if first_notification is None:
                        continue
                    reference = dt_util.as_local(first_notification)
                else:
                    reference = dt_util.as_local(due)
                age = now - reference
                if age < self._parse_duration(step["after"]):
                    continue
                assignee = occurrence.get("assignee")
                if (
                    assignee
                    and step.get("presence_required", False)
                    and not self._is_home(assignee)
                ):
                    continue
                action = step.get("action", "notify")
                if action == "delegate" and assignee is not None:
                    delegated = await self._delegate_occurrence(
                        occurrence_id,
                        occurrence,
                        reset_escalation=False,
                    )
                    if not delegated:
                        continue
                    if occurrence.get("first_notification_at") is None:
                        occurrence["first_notification_at"] = now.isoformat()
                    occurrence["sent_steps"].append(index)
                    continue
                if action == "open" and assignee is not None:
                    await self._open_occurrence_for_claim(
                        occurrence_id,
                        occurrence,
                    )
                    if occurrence.get("first_notification_at") is None:
                        occurrence["first_notification_at"] = now.isoformat()
                    occurrence["sent_steps"].append(index)
                    continue
                recipients = step.get("recipients", "assignee")
                if assignee is None and recipients == "assignee":
                    people = self._assignment_candidates(task)
                else:
                    people = self._resolve_recipients(recipients, assignee)
                after = self._parse_duration(step["after"])
                if after <= timedelta(0):
                    notification_title = step.get("title", "Aufgabe fällig")
                    notification_message = step.get("message", occurrence["title"])
                else:
                    hours = max(1, int(age.total_seconds() // 3600))
                    notification_title = step.get("title", "Aufgabe überfällig")
                    notification_message = step.get(
                        "message",
                        f"{occurrence['title']} ist seit {hours} h offen.",
                    )
                delivered = await self._notify(
                    occurrence_id,
                    occurrence,
                    people,
                    notification_title,
                    notification_message,
                )
                if delivered:
                    if occurrence.get("first_notification_at") is None:
                        occurrence["first_notification_at"] = now.isoformat()
                    occurrence["sent_steps"].append(index)

    def _is_home(self, person_id: str) -> bool:
        """Return presence, defaulting safely to available if not configured."""
        entity_id = self.people[person_id].get("presence")
        if not entity_id:
            return True
        state = self.hass.states.get(entity_id)
        if state is None or state.state in {"unknown", "unavailable"}:
            _LOGGER.warning(
                "Presence entity %s for %s is unavailable; not deferring notification",
                entity_id,
                person_id,
            )
            return True
        return state.state == "home"

    def _resolve_recipients(
        self, recipients: str | list[str], assignee: str | None
    ) -> list[str]:
        if recipients == "assignee":
            return [assignee] if assignee in self.people else list(self.people)
        if recipients == "all":
            return list(self.people)
        if isinstance(recipients, str):
            recipients = [recipients]
        unknown = set(recipients) - set(self.people)
        if unknown:
            _LOGGER.warning("Ignoring unknown recipients: %s", sorted(unknown))
        return [person for person in recipients if person in self.people]

    async def _notify(
        self,
        occurrence_id: str,
        occurrence: dict[str, Any],
        people: list[str],
        title: str,
        message: str,
        *,
        claim_help_for: str | None = None,
    ) -> bool:
        tag = f"household_task_{occurrence_id}"
        delivered = False
        for person_id in dict.fromkeys(people):
            notify_action = self.people[person_id]["notify"]
            service = notify_action.removeprefix("notify.")
            try:
                await self.hass.services.async_call(
                    "notify",
                    service,
                    {
                        "title": title,
                        "message": message,
                        "data": {
                            "tag": tag,
                            "url": "/todo",
                            "actions": (
                                [
                                    {
                                        "action": (
                                            f"{CLAIM_HELP_PREFIX}"
                                            f"{occurrence_id}_{claim_help_for}"
                                        ),
                                        "title": "Ich helfe",
                                        "activationMode": "background",
                                    },
                                    {
                                        "action": "URI",
                                        "title": "Aufgabe öffnen",
                                        "uri": "/todo",
                                    },
                                ]
                                if claim_help_for
                                else [
                                    {
                                        "action": (
                                            f"{CLAIM_TASK_PREFIX}"
                                            f"{occurrence_id}_{person_id}"
                                        ),
                                        "title": "Übernehmen",
                                        "activationMode": "background",
                                    },
                                    {
                                        "action": "URI",
                                        "title": "Aufgabe öffnen",
                                        "uri": "/todo",
                                    },
                                ]
                                if occurrence.get("assignee") is None
                                else [
                                    {
                                        "action": (
                                            f"{ACTION_PREFIX}{occurrence_id}_{person_id}"
                                        ),
                                        "title": "Erledigt",
                                        "activationMode": "background",
                                    },
                                    {
                                        "action": (
                                            f"{SNOOZE_EVENING_PREFIX}{occurrence_id}"
                                        ),
                                        "title": "Heute Abend",
                                        "activationMode": "background",
                                    },
                                    {
                                        "action": (
                                            f"{SNOOZE_TOMORROW_PREFIX}{occurrence_id}"
                                        ),
                                        "title": "Morgen",
                                        "activationMode": "background",
                                    },
                                    {
                                        "action": f"{DECLINE_PREFIX}{occurrence_id}",
                                        "title": "Kann ich nicht",
                                        "activationMode": "background",
                                    },
                                    {
                                        "action": f"{HELP_PREFIX}{occurrence_id}",
                                        "title": "Hilfe benötigt",
                                        "activationMode": "background",
                                    },
                                ]
                            ),
                        },
                    },
                    blocking=True,
                )
                if person_id not in occurrence["notified_people"]:
                    occurrence["notified_people"].append(person_id)
                delivered = True
            except Exception:
                _LOGGER.exception(
                    "Could not notify %s using %s", person_id, notify_action
                )
        return delivered

    async def _resolve_occurrence(
        self,
        occurrence_id: str,
        occurrence: dict[str, Any],
        *,
        completed_by: str | None = None,
        award_point: bool = True,
        resolution_reason: str = "completed",
    ) -> None:
        if occurrence.get("resolved"):
            return
        resolved_at = dt_util.utcnow()
        occurrence["resolved"] = True
        occurrence["resolved_at"] = resolved_at.isoformat()
        occurrence["resolution_reason"] = resolution_reason
        if award_point:
            person_id = (
                completed_by
                if completed_by in self.people
                else occurrence.get("assignee")
            )
            if person_id in self.people:
                occurrence["completed_by"] = person_id
                scores = self.state.setdefault("scores", {})
                scores[person_id] = int(scores.get(person_id, 0)) + 1
        await self._clear_notifications(occurrence_id, occurrence)
        if resolution_reason == "completed":
            await self._create_completion_followups(occurrence, resolved_at)

    async def _create_completion_followups(
        self, occurrence: dict[str, Any], resolved_at: datetime
    ) -> None:
        """Create dependent and completion-relative task occurrences."""
        source_task = occurrence.get("task", {})
        source_task_id = occurrence.get("task_id")
        current_template = self.tasks.get(source_task_id)
        if (
            current_template
            and current_template.get("enabled", True)
            and current_template.get("schedule", {}).get("type") == "after_completion"
        ):
            interval = self._parse_duration(current_template["schedule"]["interval"])
            await self._create_occurrence(
                source_task_id,
                current_template,
                completion_due(dt_util.as_local(resolved_at), interval),
                manual=True,
            )
        for follow_up in source_task.get("follow_ups", []):
            target_id = follow_up.get("task_id")
            target = self.tasks.get(target_id)
            if not target or not target.get("enabled", True):
                continue
            delay = self._parse_duration(follow_up.get("delay", "00:00:00"))
            await self._create_occurrence(
                target_id,
                target,
                dt_util.as_local(resolved_at) + delay,
                manual=True,
            )

    def _person_for_context(self, context: Context | None) -> str | None:
        """Map a Home Assistant user context to a configured person."""
        if context is None or not context.user_id:
            return None
        return next(
            (
                person_id
                for person_id, person in self.people.items()
                if person.get("user_id") == context.user_id
            ),
            None,
        )

    async def _clear_notifications(
        self, occurrence_id: str, occurrence: dict[str, Any]
    ) -> None:
        """Clear every push notification associated with an occurrence."""
        for person_id in list(occurrence.get("notified_people", [])):
            await self._clear_notification_for(occurrence_id, person_id)
        occurrence["notified_people"] = []

    async def _clear_notification_for(self, occurrence_id: str, person_id: str) -> None:
        if person_id not in self.people:
            return
        service = self.people[person_id]["notify"].removeprefix("notify.")
        try:
            await self.hass.services.async_call(
                "notify",
                service,
                {
                    "message": "clear_notification",
                    "data": {"tag": f"household_task_{occurrence_id}"},
                },
                blocking=True,
            )
        except Exception:
            _LOGGER.debug(
                "Could not clear notification for %s",
                person_id,
                exc_info=True,
            )

    def _task_value(self, task: dict[str, Any], name: str, fallback: Any) -> Any:
        if name in task:
            return task[name]
        if name in self.config:
            return self.config[name]
        return fallback

    def _prune_state(self, now: datetime) -> None:
        cutoff = now - timedelta(days=90)
        self.state["occurrences"] = {
            occurrence_id: occurrence
            for occurrence_id, occurrence in self.state["occurrences"].items()
            if not occurrence.get("resolved")
            or dt_util.parse_datetime(occurrence.get("resolved_at", "")) is None
            or dt_util.parse_datetime(occurrence["resolved_at"]) >= cutoff
        }
        cutoff_day = cutoff.date().isoformat()
        self.state["daily_triggers"] = {
            task_id: {
                source_day: target_day
                for source_day, target_day in source_days.items()
                if source_day >= cutoff_day
            }
            for task_id, source_days in self.state["daily_triggers"].items()
        }

    def _prune_handovers(self, now: datetime) -> None:
        """Remove expired handovers while retaining occurrence audit history."""
        active: dict[str, dict[str, Any]] = {}
        for person_id, handover in self.state.get("handovers", {}).items():
            if not isinstance(handover, dict):
                continue
            until = dt_util.parse_datetime(str(handover.get("until", "")))
            if until is not None and dt_util.as_local(until) <= now:
                continue
            if person_id in self.people and handover.get("to") in self.people:
                active[person_id] = handover
        self.state["handovers"] = active

    async def _save(self) -> None:
        await self.store.async_save(self.state)

    @staticmethod
    def _parse_time(value: str) -> time:
        return parse_time(value)

    @staticmethod
    def _parse_duration(value: str | int | float) -> timedelta:
        return parse_duration(value)

    @staticmethod
    def _month_day(year: int, month: int, value: int | str) -> int:
        return month_day(year, month, value)

    def _parse_calendar_datetime(self, value: Any) -> datetime | None:
        if not value:
            return None
        parsed = dt_util.parse_datetime(str(value))
        if parsed:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=ZoneInfo(self.hass.config.time_zone))
            return dt_util.as_local(parsed)
        try:
            parsed_date = date.fromisoformat(str(value))
        except ValueError:
            return None
        return datetime.combine(
            parsed_date,
            time.min,
            ZoneInfo(self.hass.config.time_zone),
        )
