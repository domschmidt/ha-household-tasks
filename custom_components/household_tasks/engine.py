"""Runtime engine and integration lifecycle for Household Tasks."""

from __future__ import annotations

import asyncio
import base64
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
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Context, Event, HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
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
from .comfort import (
    discovery_suggestions,
    notification_digest_due,
    parse_smart_task,
)
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
from .features import (
    HOUSEHOLD_MODES,
    MODE_POLICIES,
    PRIORITIES,
    SEASON_CONDITIONS,
    default_household_mode,
    dependency_cycles,
    mode_decision,
    season_decision,
    template_gallery,
)
from .forecast_rules import (
    forecast_activation,
    forecast_decision,
    season_key,
)
from .intents import async_register_intents, async_unregister_intents
from .nfc import (
    NFC_ACTIONS,
    NFC_FEEDBACK_MODES,
    NFC_FEEDBACK_RECIPIENTS,
    feedback_recipients,
    open_occurrence_for_task,
    task_for_tag,
)
from .productivity import (
    configuration_conflicts,
    contextual_home,
    habit_suggestions,
    parse_natural_move,
    parse_task_batch,
    schedule_projection,
)
from .resources import (
    RESOURCE_CONDITIONS,
    render_resource_text,
    resource_condition_matches,
)
from .scheduling import month_day, parse_duration, parse_time
from .task_store import (
    TASK_SCHEMA_VERSION,
    TERMINAL_TASK_STATUSES,
    append_event,
    has_dependency_cycle,
    migrate_state,
    task_store_health,
)
from .weather_rules import WEATHER_LOGIC, WEATHER_OPERATORS, weather_decision
from .workflows import (
    completion_due,
    state_trigger_allowed,
    weekly_summary_due,
)

_LOGGER = logging.getLogger(__name__)
_PANEL_PATH = f"/{PANEL_URL}"
_TASK_NOT_OPEN = "Die Aufgabe ist nicht mehr offen."
_HELP_NEEDED = "Hilfe benötigt"
_OPEN_TASK = "Aufgabe öffnen"

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

    async def _async_set_status(call: ServiceCall) -> None:
        engine = get_loaded_engine(hass)
        if engine is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="not_loaded",
            )
        await engine.async_set_occurrence_status(
            call.data["occurrence_id"],
            call.data["status"],
            expected_revision=call.data.get("expected_revision"),
            context=call.context,
        )

    async def _async_set_checklist_item(call: ServiceCall) -> None:
        engine = get_loaded_engine(hass)
        if engine is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="not_loaded",
            )
        await engine.async_set_checklist_item(
            call.data["occurrence_id"],
            call.data["item_id"],
            call.data["completed"],
            expected_revision=call.data.get("expected_revision"),
            context=call.context,
        )

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
    hass.services.async_register(
        DOMAIN,
        "set_status",
        _async_set_status,
        schema=vol.Schema(
            {
                vol.Required("occurrence_id"): str,
                vol.Required("status"): vol.In(
                    [
                        "open",
                        "in_progress",
                        "waiting",
                        "blocked",
                        "completed",
                        "cancelled",
                    ]
                ),
                vol.Optional("expected_revision"): int,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        "set_checklist_item",
        _async_set_checklist_item,
        schema=vol.Schema(
            {
                vol.Required("occurrence_id"): str,
                vol.Required("item_id"): str,
                vol.Required("completed"): bool,
                vol.Optional("expected_revision"): int,
            }
        ),
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Household Tasks from a UI config entry."""
    engine = HouseholdTaskEngine(hass, initial_config())
    await engine.async_setup()
    async_register_intents(hass)
    entry.runtime_data = engine
    await hass.config_entries.async_forward_entry_setups(entry, [Platform.SENSOR])
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
    if not await hass.config_entries.async_unload_platforms(entry, [Platform.SENSOR]):
        return False
    engine = getattr(entry, "runtime_data", None)
    if isinstance(engine, HouseholdTaskEngine):
        await engine.async_shutdown()
    async_unregister_intents(hass)
    entry.runtime_data = None
    async_remove_panel(hass, PANEL_URL)
    return True


class HouseholdTaskEngine:
    """Create, track, notify, and escalate household tasks."""

    def __init__(self, hass: HomeAssistant, config: dict[str, Any]) -> None:
        self.hass = hass
        self.config = config
        self.initial_config = deepcopy(config)
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
            "household_mode": default_household_mode(),
            "decision_log": [],
            "undo_stack": [],
            "favorites": {},
            "notification_queue": {},
            "last_notification_digest": {},
            "task_stacks": {},
            "attachments": {},
            "weather_matches": {},
            "weather_last_created": {},
            "seasonal_executions": {},
            "forecast_traces": {},
            "forecast_last_created": {},
            "ui_config": None,
            "task_schema_version": TASK_SCHEMA_VERSION,
            "task_events": [],
            "store_recovery": [],
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
        migrated_store = False
        if stored:
            migrated = migrate_state(stored, now=dt_util.utcnow())
            migrated_store = migrated != stored
            self.state.update(migrated)
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
        self.state.setdefault("household_mode", default_household_mode())
        self.state.setdefault("decision_log", [])
        self.state.setdefault("undo_stack", [])
        self.state.setdefault("favorites", {})
        self.state.setdefault("notification_queue", {})
        self.state.setdefault("last_notification_digest", {})
        self.state.setdefault("task_stacks", {})
        self.state.setdefault("attachments", {})
        self.state.setdefault("weather_matches", {})
        self.state.setdefault("weather_last_created", {})
        self.state.setdefault("seasonal_executions", {})
        self.state.setdefault("forecast_traces", {})
        self.state.setdefault("forecast_last_created", {})
        self.state.setdefault("ui_config", None)
        self.state.setdefault("task_schema_version", TASK_SCHEMA_VERSION)
        self.state.setdefault("task_events", [])
        self.state.setdefault("store_recovery", [])
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
        elif migrated_store:
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
            if assignment_type not in {
                "fixed",
                "rotation",
                "fair",
                "open",
                "per_person",
            }:
                errors.append(f"task '{task_id}' has an invalid assignment type")
            if assignment_type == "fixed" and task.get("assignee") not in self.people:
                errors.append(f"task '{task_id}' has an unknown assignee")
            if assignment_type in {"rotation", "fair", "per_person"}:
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
                "flexible_after_completion",
                "weather_trigger",
                "forecast_trigger",
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
            if schedule_type == "weather_trigger":
                for field in ("due_after", "cooldown"):
                    try:
                        duration = self._parse_duration(schedule.get(field, "00:00:00"))
                        if duration < timedelta(0):
                            raise ValueError
                    except (TypeError, ValueError, vol.Invalid):
                        errors.append(
                            f"task '{task_id}' weather trigger has invalid {field}"
                        )
            if schedule_type == "forecast_trigger":
                try:
                    self._parse_time(schedule.get("time", "18:00:00"))
                except (TypeError, ValueError, vol.Invalid):
                    errors.append(
                        f"task '{task_id}' forecast trigger needs a valid time"
                    )
                try:
                    if not 1 <= int(schedule.get("horizon_hours", 48)) <= 240:
                        raise ValueError
                except (TypeError, ValueError):
                    errors.append(
                        f"task '{task_id}' forecast horizon must be 1..240 hours"
                    )
                try:
                    if not 0 <= int(schedule.get("lead_days", 1)) <= 7:
                        raise ValueError
                except (TypeError, ValueError):
                    errors.append(f"task '{task_id}' forecast lead_days must be 0..7")
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
            if schedule_type == "flexible_after_completion":
                try:
                    earliest = self._parse_duration(
                        schedule.get("earliest_interval", "")
                    )
                    preferred = self._parse_duration(
                        schedule.get("preferred_interval", "")
                    )
                    latest = self._parse_duration(schedule.get("latest_interval", ""))
                    if not timedelta(0) < earliest <= preferred <= latest:
                        raise ValueError
                except (TypeError, ValueError, vol.Invalid):
                    errors.append(
                        f"task '{task_id}' flexible intervals must be positive and ordered"
                    )
                if not dt_util.parse_datetime(str(schedule.get("start", ""))):
                    errors.append(
                        f"task '{task_id}' needs a valid first flexible due date"
                    )

            checklist = task.get("checklist", [])
            if not isinstance(checklist, list):
                errors.append(f"task '{task_id}' checklist must be a list")
            else:
                checklist_ids = [
                    str(item.get("id") or f"step_{index + 1}")
                    if isinstance(item, dict)
                    else f"step_{index + 1}"
                    for index, item in enumerate(checklist)
                ]
                if len(checklist_ids) != len(set(checklist_ids)):
                    errors.append(f"task '{task_id}' checklist IDs must be unique")
                if any(
                    not str(item.get("title") or item.get("name") or "").strip()
                    if isinstance(item, dict)
                    else not str(item).strip()
                    for item in checklist
                ):
                    errors.append(f"task '{task_id}' checklist items need a title")

            depends_on = task.get("depends_on", [])
            if not isinstance(depends_on, list):
                errors.append(f"task '{task_id}' dependencies must be a list")
            elif any(
                dependency not in self.tasks or dependency == task_id
                for dependency in depends_on
            ):
                errors.append(f"task '{task_id}' has an invalid dependency")

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

            market = task.get("market", {})
            if not isinstance(market, dict):
                errors.append(f"task '{task_id}' market settings must be a mapping")
            else:
                if market.get("priority", "normal") not in PRIORITIES:
                    errors.append(f"task '{task_id}' has an invalid market priority")
                try:
                    if int(market.get("points", 1)) < 0:
                        raise ValueError
                except (TypeError, ValueError):
                    errors.append(
                        f"task '{task_id}' market points must be non-negative"
                    )

            modes = task.get("modes", {})
            if not isinstance(modes, dict):
                errors.append(f"task '{task_id}' mode settings must be a mapping")
            elif modes.get("vacation", "pause") not in {
                "pause",
                "reduce",
                "delegate",
                "always",
            }:
                errors.append(f"task '{task_id}' has invalid vacation behavior")

            season = task.get("season")
            if season is not None:
                if not isinstance(season, dict):
                    errors.append(f"task '{task_id}' season must be a mapping")
                else:
                    try:
                        invalid_month = any(
                            not 1 <= int(month) <= 12
                            for month in season.get("months", [])
                        )
                    except (TypeError, ValueError):
                        invalid_month = True
                    if invalid_month:
                        errors.append(f"task '{task_id}' has invalid season months")
                    if season.get("condition") not in {None, *SEASON_CONDITIONS}:
                        errors.append(f"task '{task_id}' has invalid season condition")
                    if season.get("condition") and not season.get("entity_id"):
                        errors.append(
                            f"task '{task_id}' season condition needs entity_id"
                        )

            device = task.get("device")
            if device is not None:
                if not isinstance(device, dict):
                    errors.append(f"task '{task_id}' device file must be a mapping")
                else:
                    entity_id = str(device.get("entity_id", ""))
                    if entity_id and "." not in entity_id:
                        errors.append(f"task '{task_id}' device entity_id is invalid")
                    manual_url = str(device.get("manual_url", ""))
                    if manual_url and not manual_url.startswith("https://"):
                        errors.append(
                            f"task '{task_id}' device manual_url must use HTTPS"
                        )

            weather = task.get("weather")
            if weather is not None:
                if not isinstance(weather, dict):
                    errors.append(f"task '{task_id}' weather rule must be a mapping")
                else:
                    if weather.get("logic", "all") not in WEATHER_LOGIC:
                        errors.append(f"task '{task_id}' weather logic is invalid")
                    conditions = weather.get("conditions", [])
                    if not isinstance(conditions, list) or not conditions:
                        errors.append(f"task '{task_id}' weather rule needs conditions")
                    else:
                        for index, condition in enumerate(conditions):
                            if not isinstance(condition, dict):
                                errors.append(
                                    f"task '{task_id}' weather condition {index + 1} "
                                    "must be a mapping"
                                )
                                continue
                            if "." not in str(condition.get("entity_id", "")):
                                errors.append(
                                    f"task '{task_id}' weather condition {index + 1} "
                                    "needs entity_id"
                                )
                            if (
                                condition.get("condition", "below")
                                not in WEATHER_OPERATORS
                            ):
                                errors.append(
                                    f"task '{task_id}' weather condition {index + 1} "
                                    "has invalid operator"
                                )
                            if condition.get("threshold") in {None, ""}:
                                errors.append(
                                    f"task '{task_id}' weather condition {index + 1} "
                                    "needs threshold"
                                )
                            if schedule_type == "forecast_trigger" and not str(
                                condition.get("entity_id", "")
                            ).startswith("weather."):
                                errors.append(
                                    f"task '{task_id}' forecast conditions need a "
                                    "weather.* entity"
                                )
                            if (
                                schedule_type == "forecast_trigger"
                                and not str(condition.get("attribute", "")).strip()
                            ):
                                errors.append(
                                    f"task '{task_id}' forecast conditions need an "
                                    "attribute"
                                )

            repeat = task.get("repeat", {})
            if repeat and repeat.get("mode") != "once_per_season":
                errors.append(f"task '{task_id}' has an invalid repeat mode")
            if repeat.get("mode") == "once_per_season" and not task.get(
                "season", {}
            ).get("months"):
                errors.append(
                    f"task '{task_id}' once-per-season repeat needs season months"
                )

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

        for cycle in dependency_cycles(self.tasks):
            errors.append(f"task dependency cycle: {' -> '.join(cycle)}")

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

        notification_digest = self.defaults.get("notification_digest", {})
        if not isinstance(notification_digest, dict):
            errors.append("Notification digest settings must be a mapping")
        else:
            try:
                self._parse_time(notification_digest.get("time", "17:30:00"))
                if int(notification_digest.get("minimum_tasks", 2)) < 1:
                    raise ValueError
            except (TypeError, ValueError):
                errors.append("Notification digest settings are invalid")

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
            if any(
                task_id in stack.get("task_ids", [])
                for stack in self.state.get("task_stacks", {}).values()
                if isinstance(stack, dict)
            ):
                raise vol.Invalid(
                    "Die Vorlage wird noch in einem Aufgabenstapel verwendet."
                )
            self._push_undo(
                "config",
                f"Vorlage „{self.tasks[task_id].get('name', task_id)}“ löschen",
                {"config": self._editable_config()},
            )
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
            self._push_undo(
                "handovers",
                "Haushaltsübergabe ändern",
                {"handovers": previous},
            )

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
            handovers = self.state.setdefault("handovers", {})
            self._push_undo(
                "handovers",
                "Haushaltsübergabe beenden",
                {"handovers": handovers},
            )
            handovers.pop(from_person, None)
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
            self._push_undo(
                "config",
                f"Person „{self.people[person_id].get('name', person_id)}“ löschen",
                {"config": self._editable_config()},
            )
            self.people.pop(person_id)
            self.config["people"] = self.people
            self.state["ui_config"] = self._editable_config()
            await self._save()

    async def async_reset_ui_config(self) -> None:
        """Discard UI overrides and restore the initial values."""
        async with self.lock:
            self._push_undo(
                "config",
                "Konfiguration zurücksetzen",
                {"config": self._editable_config()},
            )
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
            self._push_undo(
                "config",
                "Konfiguration importieren",
                {"config": previous},
            )
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
        attachment_metadata = {
            occurrence_id: [
                {key: value for key, value in attachment.items() if key != "content"}
                for attachment in attachments
            ]
            for occurrence_id, attachments in self.state.get("attachments", {}).items()
        }
        return {
            "people": deepcopy(self.people),
            "tasks": deepcopy(self.tasks),
            "defaults": deepcopy(self.defaults),
            "monitors": deepcopy(self.monitors),
            "detected_printers": self._printer_entities(),
            "scores": deepcopy(self.state.get("scores", {})),
            "handovers": deepcopy(self.state.get("handovers", {})),
            "occurrences": occurrences,
            "task_store": {
                "schema_version": self.state.get("task_schema_version"),
                "journal_entries": len(self.state.get("task_events", [])),
                "backend": "home_assistant_store",
            },
            "task_events": deepcopy(self.state.get("task_events", [])[-200:]),
            "using_ui_config": self.state.get("ui_config") is not None,
            "last_check": self.state.get("last_check"),
            "household_mode": deepcopy(self._current_household_mode()),
            "decision_log": deepcopy(self.state.get("decision_log", [])[-50:]),
            "forecast_traces": deepcopy(self.state.get("forecast_traces", {})),
            "undo": deepcopy(self.state.get("undo_stack", [])[-1:]) or [],
            "favorites": deepcopy(self.state.get("favorites", {})),
            "task_stacks": deepcopy(self.state.get("task_stacks", {})),
            "attachments": attachment_metadata,
            "habits": habit_suggestions(
                self.state["occurrences"],
                self.people,
            ),
            "home_context": contextual_home(dt_util.now(), occurrences),
            "template_gallery": template_gallery(),
            "discovery_suggestions": self.discovery_suggestions(),
            "configuration_health": self.configuration_health(),
            "analytics": build_analytics(
                self.state["occurrences"],
                self.people,
                now=dt_util.now(),
            ),
        }

    def _current_household_mode(self) -> dict[str, Any]:
        """Return the active mode and expire temporary modes when necessary."""
        mode = self.state.setdefault("household_mode", default_household_mode())
        until = dt_util.parse_datetime(str(mode.get("until") or ""))
        if until is not None and dt_util.as_utc(until) <= dt_util.utcnow():
            mode = default_household_mode()
            mode["changed_at"] = dt_util.utcnow().isoformat()
            self.state["household_mode"] = mode
        return mode

    def configuration_health(self) -> dict[str, Any]:
        """Return actionable configuration findings without exposing secrets."""
        findings: list[dict[str, Any]] = []
        store_health = task_store_health(self.state)
        findings.extend(store_health["findings"])
        for person_id, person in self.people.items():
            findings.extend(self._person_health_findings(person_id, person))
        for task_id, task in self.tasks.items():
            findings.extend(self._task_health_findings(task_id, task))
        findings.extend(self._dependency_health_findings())
        return {
            "status": self._health_status(findings),
            "findings": findings,
            "checked_at": dt_util.utcnow().isoformat(),
            "task_store": store_health,
        }

    def _person_health_findings(
        self,
        person_id: str,
        person: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Return actionable findings for one configured person."""
        findings = []
        presence = person.get("presence")
        if presence and self.hass.states.get(presence) is None:
            findings.append(
                {
                    "severity": "warning",
                    "code": "presence_missing",
                    "person_id": person_id,
                    "message": f"Anwesenheits-Entität {presence} fehlt.",
                    "action": {"type": "edit_person", "person_id": person_id},
                }
            )
        notify = str(person.get("notify", "")).removeprefix("notify.")
        if notify and not self.hass.services.has_service("notify", notify):
            findings.append(
                {
                    "severity": "critical",
                    "code": "notify_missing",
                    "person_id": person_id,
                    "message": f"Benachrichtigungsdienst notify.{notify} fehlt.",
                    "action": {"type": "edit_person", "person_id": person_id},
                }
            )
        return findings

    def _task_health_findings(
        self,
        task_id: str,
        task: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Return actionable findings for one task template."""
        findings = []
        edit_action = {"type": "edit_task", "task_id": task_id}
        if task.get("schedule", {}).get(
            "type"
        ) == "forecast_trigger" and not self.hass.services.has_service(
            "weather", "get_forecasts"
        ):
            findings.append(
                {
                    "severity": "critical",
                    "code": "forecast_service_missing",
                    "task_id": task_id,
                    "message": (
                        "Der Home-Assistant-Dienst weather.get_forecasts "
                        "ist nicht verfügbar."
                    ),
                    "action": edit_action,
                }
            )
        for condition in task.get("weather", {}).get("conditions", []):
            weather_entity = condition.get("entity_id")
            if weather_entity and self.hass.states.get(weather_entity) is None:
                findings.append(
                    {
                        "severity": "warning",
                        "code": "weather_entity_missing",
                        "task_id": task_id,
                        "message": f"Wetter-Entität {weather_entity} fehlt.",
                        "action": edit_action,
                    }
                )
        for entity_type, entity_id in (
            ("device", task.get("device", {}).get("entity_id")),
            ("season", task.get("season", {}).get("entity_id")),
        ):
            if entity_id and self.hass.states.get(entity_id) is None:
                label = "Geräteakten" if entity_type == "device" else "Saisonale"
                findings.append(
                    {
                        "severity": "warning",
                        "code": f"{entity_type}_entity_missing",
                        "task_id": task_id,
                        "message": f"{label} Entität {entity_id} fehlt.",
                        "action": edit_action,
                    }
                )
        tag_id = task.get("nfc", {}).get("tag_id")
        if tag_id:
            findings.append(
                {
                    "severity": "info",
                    "code": "nfc_verify",
                    "task_id": task_id,
                    "tag_id": tag_id,
                    "message": "NFC-Tag im Panel gegen die Home-Assistant-Registry prüfen.",
                    "action": edit_action,
                }
            )
        return findings

    def _dependency_health_findings(self) -> list[dict[str, Any]]:
        """Return cycle and conflict findings with direct edit actions."""
        findings = [
            {
                "severity": "critical",
                "code": "dependency_cycle",
                "task_ids": cycle,
                "message": f"Zyklische Folgeaufgaben: {' → '.join(cycle)}",
                "action": {"type": "edit_task", "task_id": cycle[0]},
            }
            for cycle in dependency_cycles(self.tasks)
        ]
        for source in configuration_conflicts(self.tasks):
            finding = deepcopy(source)
            task_ids = finding.get("task_ids", [])
            if task_ids:
                finding["action"] = {"type": "edit_task", "task_id": task_ids[-1]}
            findings.append(finding)
        return findings

    @staticmethod
    def _health_status(findings: list[dict[str, Any]]) -> str:
        """Return the highest health severity without nested conditions."""
        severities = {finding["severity"] for finding in findings}
        if "critical" in severities:
            return "critical"
        if "warning" in severities:
            return "warning"
        return "ok"

    def discovery_suggestions(self) -> list[dict[str, Any]]:
        """Return installable suggestions derived from local HA entity metadata."""
        configured = set(self._watched_state_entities())
        configured.update(
            monitor.get("entity_id")
            for monitor in self.monitors.get("resources", {}).values()
            if isinstance(monitor, dict) and monitor.get("entity_id")
        )
        states = (
            {
                "entity_id": state.entity_id,
                "state": state.state,
                "attributes": dict(state.attributes),
            }
            for state in self.hass.states.async_all()
        )
        return discovery_suggestions(states, configured)

    def preview_smart_task(self, text: str) -> dict[str, Any]:
        """Parse smart capture input without changing state."""
        return parse_smart_task(text, self.people, dt_util.now())

    def preview_task_batch(self, text: str) -> list[dict[str, Any]]:
        """Parse multiple smart tasks without changing state."""
        return parse_task_batch(text, self.people, dt_util.now())

    def task_projection(self, task: dict[str, Any]) -> dict[str, Any]:
        """Return an explainable task volume estimate."""
        return schedule_projection(task)

    async def async_toggle_favorite(self, person_id: str, task_id: str) -> bool:
        """Toggle one reusable task in a person's quick favorites."""
        if person_id not in self.people:
            raise vol.Invalid("Unknown person")
        if task_id not in self.tasks:
            raise vol.Invalid("Unknown task")
        async with self.lock:
            favorites = self.state.setdefault("favorites", {}).setdefault(person_id, [])
            if task_id in favorites:
                favorites.remove(task_id)
                enabled = False
            else:
                favorites.append(task_id)
                enabled = True
            await self._save()
            return enabled

    async def async_install_discovery_suggestion(
        self,
        suggestion_id: str,
        task_id: str,
        assignee: str,
    ) -> None:
        """Install one currently valid local entity suggestion."""
        if assignee not in self.people:
            raise vol.Invalid("Unknown assignee")
        suggestion = next(
            (
                item
                for item in self.discovery_suggestions()
                if item["id"] == suggestion_id
            ),
            None,
        )
        if suggestion is None:
            raise vol.Invalid("Suggestion is no longer available")
        if suggestion["kind"] == "resource":
            monitor = deepcopy(suggestion["monitor"])
            monitor["assignee"] = assignee
            async with self.lock:
                previous = deepcopy(self.monitors)
                self.monitors.setdefault("resources", {})[task_id] = monitor
                try:
                    self._validate_config()
                except vol.Invalid:
                    self.monitors = previous
                    self.config["monitors"] = self.monitors
                    raise
                self.config["monitors"] = self.monitors
                self.state["ui_config"] = self._editable_config()
                self._refresh_state_listener()
                await self._save()
            return
        task = deepcopy(suggestion["task"])
        if task.get("assignment", {}).get("type") == "fixed":
            task["assignee"] = assignee
        else:
            task.setdefault("assignment", {})["people"] = [assignee]
        await self.async_save_task(task_id, task)

    async def async_bulk_occurrences(
        self,
        occurrence_ids: list[str],
        action: str,
        context: Context | None = None,
    ) -> dict[str, Any]:
        """Apply a supported reversible action to multiple open occurrences."""
        unique_ids = list(dict.fromkeys(occurrence_ids))[:100]
        results = {"completed": [], "failed": {}}
        for occurrence_id in unique_ids:
            try:
                if action == "complete":
                    await self.async_complete_occurrence(occurrence_id, context)
                elif action == "tomorrow":
                    await self.async_snooze_occurrence(occurrence_id, "tomorrow")
                elif action == "help":
                    await self.async_request_help(occurrence_id)
                elif action == "decline":
                    await self.async_decline_occurrence(occurrence_id)
                else:
                    raise vol.Invalid("Unknown bulk action")
                results["completed"].append(occurrence_id)
            except Exception as err:
                results["failed"][occurrence_id] = str(err)
        return results

    async def async_move_occurrence(
        self,
        occurrence_id: str,
        instruction: str,
    ) -> dict[str, Any]:
        """Move an occurrence using a natural-language time or presence condition."""
        async with self.lock:
            occurrence = self.state["occurrences"].get(occurrence_id)
            if not occurrence or occurrence.get("resolved"):
                raise vol.Invalid(_TASK_NOT_OPEN)
            try:
                move = parse_natural_move(instruction, dt_util.now(), self.people)
            except ValueError as err:
                raise vol.Invalid(str(err)) from err
            if move["kind"] == "presence":
                occurrence["waiting_for"] = {
                    "type": "presence",
                    "person_id": move["person_id"],
                    "created_at": dt_util.utcnow().isoformat(),
                }
            else:
                due = dt_util.parse_datetime(move["due"])
                if due is None:
                    raise vol.Invalid("Die neue Fälligkeit ist ungültig.")
                await self._update_native_occurrence(occurrence, due=due)
                occurrence["due"] = due.isoformat()
                occurrence.pop("waiting_for", None)
            self._touch_occurrence(
                occurrence_id,
                occurrence,
                "task_moved",
                details={"instruction": instruction, "kind": move["kind"]},
            )
            await self._save()
            return move

    async def async_create_batch(
        self,
        text: str,
        context: Context | None = None,
    ) -> dict[str, Any]:
        """Create several reviewed smart tasks from one text block."""
        previews = self.preview_task_batch(text)
        created = []
        failed = []
        for index, preview in enumerate(previews):
            if preview.get("missing"):
                failed.append({"index": index, "reason": ", ".join(preview["missing"])})
                continue
            try:
                await self.async_create_ad_hoc(
                    preview["name"],
                    preview["assignee"],
                    preview["due"],
                    priority=preview["priority"],
                    points=preview["points"],
                    context=context,
                )
                created.append(index)
            except vol.Invalid as err:
                failed.append({"index": index, "reason": str(err)})
        return {"created": created, "failed": failed, "previews": previews}

    async def async_save_task_stack(
        self,
        stack_id: str,
        stack: dict[str, Any] | None,
    ) -> None:
        """Create, update, or delete a reusable ordered task stack."""
        async with self.lock:
            stacks = self.state.setdefault("task_stacks", {})
            if stack is None:
                stacks.pop(stack_id, None)
            else:
                task_ids = list(dict.fromkeys(stack.get("task_ids", [])))
                if not str(stack.get("name", "")).strip() or not task_ids:
                    raise vol.Invalid("Ein Aufgabenstapel braucht Name und Vorlagen.")
                unknown = set(task_ids) - set(self.tasks)
                if unknown:
                    raise vol.Invalid(
                        f"Unbekannte Vorlagen: {', '.join(sorted(unknown))}"
                    )
                stacks[stack_id] = {
                    "name": str(stack["name"]).strip(),
                    "task_ids": task_ids,
                }
            await self._save()

    async def async_launch_task_stack(
        self,
        stack_id: str,
        context: Context | None = None,
    ) -> list[str]:
        """Create all enabled templates in a reusable task stack."""
        stack = self.state.setdefault("task_stacks", {}).get(stack_id)
        if not stack:
            raise vol.Invalid("Unbekannter Aufgabenstapel.")
        created = []
        for task_id in stack["task_ids"]:
            before = set(self.state["occurrences"])
            await self.async_create_manual(task_id, context)
            created.extend(sorted(set(self.state["occurrences"]) - before))
        return created

    async def async_add_attachment(
        self,
        occurrence_id: str,
        name: str,
        mime_type: str,
        content: str,
    ) -> dict[str, Any]:
        """Store one bounded local image or document attachment."""
        occurrence = self.state["occurrences"].get(occurrence_id)
        if occurrence is None:
            raise vol.Invalid("Unbekannte Aufgabe.")
        if mime_type not in {
            "image/jpeg",
            "image/png",
            "image/webp",
            "application/pdf",
        }:
            raise vol.Invalid("Nur JPG, PNG, WebP und PDF werden unterstützt.")
        try:
            decoded = base64.b64decode(content, validate=True)
        except (ValueError, TypeError) as err:
            raise vol.Invalid("Der Anhang ist ungültig.") from err
        if not decoded or len(decoded) > 750_000:
            raise vol.Invalid("Anhänge dürfen höchstens 750 KB groß sein.")
        attachment_id = hashlib.sha256(
            occurrence_id.encode() + decoded + dt_util.utcnow().isoformat().encode()
        ).hexdigest()[:20]
        attachment = {
            "id": attachment_id,
            "name": str(name).strip()[:120] or "Anhang",
            "mime_type": mime_type,
            "size": len(decoded),
            "created_at": dt_util.utcnow().isoformat(),
            "content": content,
        }
        async with self.lock:
            attachments = self.state.setdefault("attachments", {}).setdefault(
                occurrence_id, []
            )
            if len(attachments) >= 10:
                raise vol.Invalid("Pro Aufgabe sind höchstens zehn Anhänge möglich.")
            attachments.append(attachment)
            await self._save()
        return {key: value for key, value in attachment.items() if key != "content"}

    def attachment_content(
        self,
        occurrence_id: str,
        attachment_id: str,
    ) -> dict[str, Any]:
        """Return one attachment payload on explicit request."""
        attachment = next(
            (
                item
                for item in self.state.get("attachments", {}).get(occurrence_id, [])
                if item.get("id") == attachment_id
            ),
            None,
        )
        if attachment is None:
            raise vol.Invalid("Anhang nicht gefunden.")
        return deepcopy(attachment)

    async def async_delete_attachment(
        self,
        occurrence_id: str,
        attachment_id: str,
    ) -> None:
        """Delete one attachment."""
        async with self.lock:
            items = self.state.setdefault("attachments", {}).get(occurrence_id, [])
            remaining = [item for item in items if item.get("id") != attachment_id]
            if len(remaining) == len(items):
                raise vol.Invalid("Anhang nicht gefunden.")
            self.state["attachments"][occurrence_id] = remaining
            await self._save()

    def _push_undo(self, kind: str, label: str, payload: dict[str, Any]) -> None:
        """Append a bounded, local-only undo record."""
        stack = self.state.setdefault("undo_stack", [])
        stack.append(
            {
                "kind": kind,
                "label": label,
                "payload": deepcopy(payload),
                "created_at": dt_util.utcnow().isoformat(),
            }
        )
        del stack[:-20]

    def _record_creation_decision(
        self,
        task_id: str,
        task: dict[str, Any],
        due: datetime,
        decision: dict[str, Any],
    ) -> None:
        """Persist a bounded explanation for a task that was not created."""
        log = self.state.setdefault("decision_log", [])
        log.append(
            {
                "task_id": task_id,
                "task_name": task.get("name", task_id),
                "due": due.isoformat(),
                "checked_at": dt_util.utcnow().isoformat(),
                **deepcopy(decision),
            }
        )
        del log[:-100]

    def explain_task(self, task_id: str) -> dict[str, Any]:
        """Explain current eligibility and candidate filtering for one template."""
        task = self.tasks.get(task_id)
        if task is None:
            raise vol.Invalid(f"Unknown task_id: {task_id}")
        now = dt_util.now()
        mode = mode_decision(self._current_household_mode(), task)
        season = self._season_decision(task, now)
        weather = self._weather_decision(task)
        configured = task.get("assignment", {}).get("people", []) or list(self.people)
        candidates = self._assignment_candidates(task)
        excluded = []
        for person_id in configured:
            if person_id not in self.people:
                excluded.append({"person_id": person_id, "reason": "unknown"})
            elif person_id not in candidates:
                excluded.append(
                    {
                        "person_id": person_id,
                        "reason": (
                            "not_home"
                            if task.get("assignment", {}).get("presence_required")
                            else "handover_or_policy"
                        ),
                    }
                )
        return {
            "task_id": task_id,
            "mode": mode,
            "season": season,
            "weather": weather,
            "forecast_trace": deepcopy(
                self.state.get("forecast_traces", {}).get(task_id)
            ),
            "configured_candidates": configured,
            "eligible_candidates": candidates,
            "excluded_candidates": excluded,
            "recent_decisions": [
                item
                for item in self.state.get("decision_log", [])
                if item.get("task_id") == task_id
            ][-10:],
        }

    async def async_set_household_mode(
        self,
        mode: str,
        *,
        policy: str = "pause",
        delegate_to: str | None = None,
        until: str | None = None,
        note: str | None = None,
    ) -> None:
        """Activate normal, vacation, or guest behavior."""
        if mode not in HOUSEHOLD_MODES:
            raise vol.Invalid("Unknown household mode")
        if policy not in MODE_POLICIES:
            raise vol.Invalid("Unknown household mode policy")
        if delegate_to is not None and delegate_to not in self.people:
            raise vol.Invalid("Unknown delegate person")
        parsed_until = dt_util.parse_datetime(until) if until else None
        if until and parsed_until is None:
            raise vol.Invalid("Mode until must be an ISO date-time")
        if parsed_until and dt_util.as_utc(parsed_until) <= dt_util.utcnow():
            raise vol.Invalid("Mode until must be in the future")
        async with self.lock:
            self._push_undo(
                "household_mode",
                "Haushaltsmodus ändern",
                {"mode": self._current_household_mode()},
            )
            self.state["household_mode"] = {
                "mode": mode,
                "policy": policy,
                "delegate_to": delegate_to,
                "until": parsed_until.isoformat() if parsed_until else None,
                "note": str(note or "").strip() or None,
                "changed_at": dt_util.utcnow().isoformat(),
            }
            await self._save()

    def _apply_gallery_assignment(
        self,
        task: dict[str, Any],
        assignee: str | None,
        people: list[str] | None,
    ) -> None:
        """Apply a validated household-specific gallery assignment."""
        assignment_type = task.get("assignment", {}).get("type", "fixed")
        if assignment_type == "fixed":
            if assignee not in self.people:
                raise vol.Invalid("Template needs a configured assignee")
            task["assignee"] = assignee
            return
        if assignment_type == "per_person":
            candidates = people or ([assignee] if assignee else [])
            selected = list(
                dict.fromkeys(person for person in candidates if person in self.people)
            )
            if not selected:
                raise vol.Invalid(
                    "Template needs at least one configured target person"
                )
            task["assignment"]["people"] = selected
            return
        if assignee in self.people:
            task["assignment"]["people"] = [assignee]

    def _apply_gallery_entities(
        self,
        task: dict[str, Any],
        entity_id: str | None,
    ) -> None:
        """Fill required trigger and weather entities in a gallery copy."""
        entity_available = bool(
            entity_id and self.hass.states.get(entity_id) is not None
        )
        triggers = task.get("schedule", {}).get("triggers", [])
        if triggers and not triggers[0].get("entity_id"):
            if not entity_available:
                raise vol.Invalid("Template needs an available trigger entity")
            triggers[0]["entity_id"] = entity_id
        missing_weather = [
            condition
            for condition in task.get("weather", {}).get("conditions", [])
            if not condition.get("entity_id")
        ]
        if not missing_weather:
            return
        if not entity_available:
            raise vol.Invalid("Template needs an available weather entity")
        for condition in missing_weather:
            condition["entity_id"] = entity_id

    async def async_install_gallery_template(
        self,
        template_id: str,
        task_id: str,
        assignee: str | None,
        entity_id: str | None = None,
        people: list[str] | None = None,
    ) -> None:
        """Install one curated template after applying household-specific values."""
        entry = next(
            (item for item in template_gallery() if item["id"] == template_id),
            None,
        )
        if entry is None:
            raise vol.Invalid("Unknown gallery template")
        task = deepcopy(entry["task"])
        self._apply_gallery_assignment(task, assignee, people)
        self._apply_gallery_entities(task, entity_id)
        await self.async_save_task(task_id, task)

    async def async_undo_last(self) -> str:
        """Undo the latest reversible panel action."""
        async with self.lock:
            stack = self.state.setdefault("undo_stack", [])
            if not stack:
                raise vol.Invalid("Keine Aktion zum Rückgängigmachen vorhanden.")
            entry = stack.pop()
            await self._apply_undo_entry(entry)
            await self._save()
            return str(entry["label"])

    async def _apply_undo_entry(self, entry: dict[str, Any]) -> None:
        """Apply one validated undo record."""
        kind = entry["kind"]
        payload = entry["payload"]
        if kind == "config":
            self._apply_editable_config(payload["config"])
            self.state["ui_config"] = self._editable_config()
            self._validate_config()
            self._refresh_state_listener()
            return
        if kind == "handovers":
            self.state["handovers"] = deepcopy(payload["handovers"])
            return
        if kind == "household_mode":
            self.state["household_mode"] = deepcopy(payload["mode"])
            return
        if kind == "complete":
            await self._undo_completion(payload)
            return
        if kind == "seasonal_executions":
            self.state.setdefault("seasonal_executions", {}).update(
                deepcopy(payload["entries"])
            )
            return
        raise vol.Invalid("Diese Aktion kann nicht rückgängig gemacht werden.")

    async def _undo_completion(self, payload: dict[str, Any]) -> None:
        """Restore a completed occurrence and its awarded score."""
        occurrence_id = payload["occurrence_id"]
        occurrence = deepcopy(payload["occurrence"])
        self.state["occurrences"][occurrence_id] = occurrence
        person_id = payload.get("completed_by")
        points = int(payload.get("points", 0))
        if person_id in self.people and points:
            scores = self.state.setdefault("scores", {})
            scores[person_id] = max(0, int(scores.get(person_id, 0)) - points)
        occurrence["status"] = "open"
        occurrence["resolved"] = False
        occurrence.pop("resolved_at", None)
        occurrence.pop("resolution_reason", None)
        self._touch_occurrence(occurrence_id, occurrence, "task_reopened")
        for created_id in payload.get("created_occurrences", []):
            if self.state["occurrences"].pop(created_id, None):
                self._record_task_event("task_removed_by_undo", created_id)

    async def async_reset_seasonal_executions(self, task_id: str) -> int:
        """Reset all persisted season targets for one configured rule."""
        if task_id not in self.tasks:
            raise vol.Invalid(f"Unknown task_id: {task_id}")
        async with self.lock:
            ledger = self.state.setdefault("seasonal_executions", {})
            prefix = f"{task_id}|"
            removed = {
                key: value for key, value in ledger.items() if key.startswith(prefix)
            }
            if removed:
                self._push_undo(
                    "seasonal_executions",
                    f"Saisonsperren für „{self.tasks[task_id]['name']}“ zurücksetzen",
                    {"entries": removed},
                )
                for key in removed:
                    ledger.pop(key, None)
                await self._save()
            return len(removed)

    async def async_preview_task(
        self,
        task: dict[str, Any],
        task_id: str | None = None,
        scenario: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Preview a task without creating an occurrence or changing state."""
        schedule = task.get("schedule", {})
        schedule_type = schedule.get("type", "manual")
        now = dt_util.now().replace(microsecond=0)
        result: dict[str, Any] = {
            "schedule_type": schedule_type,
            "next_due": None,
            "calendar_events": [],
            "state_triggers": [],
            "mode": mode_decision(self._current_household_mode(), task),
            "season": self._season_decision(task, now),
            "weather": self._weather_decision(task),
            "forecast": None,
            "planned_occurrences": [],
            "trace": [],
        }

        if schedule_type in {
            "weekly",
            "monthly",
            "yearly",
            "interval_months",
        }:
            result["next_due"] = self._preview_scheduled_due(schedule, now)
        elif schedule_type in {"after_completion", "flexible_after_completion"}:
            result["next_due"] = self._preview_after_completion_due(schedule, now)
        elif schedule_type == "calendar":
            result["calendar_events"] = await self._preview_calendar_events(
                schedule, now
            )
            if result["calendar_events"]:
                result["next_due"] = result["calendar_events"][0]["due"]
        elif schedule_type == "forecast_trigger":
            await self._apply_forecast_preview(
                result,
                task,
                task_id or "__preview__",
                now,
                scenario,
            )

        if schedule_type in {"state_trigger", "daily_after_state"}:
            result["state_triggers"] = self._preview_state_triggers(schedule)
        fanout_allowed = True
        if schedule_type == "forecast_trigger":
            fanout_allowed = any(
                item["would_create"] for item in result["planned_occurrences"]
            )
        result["would_create"] = bool(
            result["mode"]["allowed"]
            and result["season"]["allowed"]
            and result["weather"]["allowed"]
            and fanout_allowed
        )
        return result

    async def _apply_forecast_preview(
        self,
        result: dict[str, Any],
        task: dict[str, Any],
        task_id: str,
        now: datetime,
        scenario: dict[str, Any] | None,
    ) -> None:
        """Add forecast, activation, fan-out, and trace details to a preview."""
        schedule = task.get("schedule", {})
        forecast = await self._forecast_decision(task, now, scenario=scenario)
        result["forecast"] = forecast
        result["weather"] = forecast
        matched = forecast.get("matched_period")
        matched_reference = self._preview_matched_reference(result, task, matched)
        if matched:
            activation = forecast_activation(
                matched["datetime"],
                lead_days=int(schedule.get("lead_days", 1)),
                activation_time=self._parse_time(schedule.get("time", "18:00:00")),
                zone=ZoneInfo(self.hass.config.time_zone),
            )
            result["next_due"] = activation.isoformat()
        result["planned_occurrences"] = [
            self._planned_forecast_occurrence(
                task_id,
                task,
                target,
                matched_reference or now,
                forecast,
                result,
            )
            for target in self._fanout_targets(task)
        ]
        result["trace"] = self._forecast_trace(task, result)

    def _preview_matched_reference(
        self,
        result: dict[str, Any],
        task: dict[str, Any],
        matched: dict[str, Any] | None,
    ) -> datetime | None:
        """Update preview season context from the matched forecast period."""
        if not matched:
            return None
        reference = dt_util.parse_datetime(matched["datetime"])
        if reference is None:
            return None
        reference = dt_util.as_local(reference)
        result["season"] = self._season_decision(task, reference)
        return reference

    def _planned_forecast_occurrence(
        self,
        task_id: str,
        task: dict[str, Any],
        target: str,
        reference: datetime,
        forecast: dict[str, Any],
        preview: dict[str, Any],
    ) -> dict[str, Any]:
        """Return one explainable per-person forecast plan."""
        repetition = self._seasonal_execution_decision(
            task_id,
            task,
            reference,
            target,
        )
        effective = self._active_handover_target(target)
        would_create = all(
            (
                forecast["allowed"],
                preview["mode"]["allowed"],
                preview["season"]["allowed"],
                repetition["allowed"],
            )
        )
        return {
            "target_person": target,
            "target_name": self.people.get(target, {}).get("name", target),
            "assignee": effective,
            "assignee_name": self.people.get(effective, {}).get("name", effective),
            "would_create": would_create,
            "repetition": repetition,
        }

    async def _forecast_decision(
        self,
        task: dict[str, Any],
        now: datetime,
        *,
        scenario: dict[str, Any] | None = None,
        cache: dict[tuple[str, str], list[dict[str, Any]]] | None = None,
    ) -> dict[str, Any]:
        """Fetch Home Assistant forecasts and evaluate the configured horizon."""
        weather = task.get("weather", {})
        entity_ids = list(
            dict.fromkeys(
                str(condition.get("entity_id", ""))
                for condition in weather.get("conditions", [])
                if condition.get("entity_id")
            )
        )
        if scenario:
            forecasts = self._scenario_forecasts(weather, scenario, now)
        else:
            forecast_type = str(task.get("schedule", {}).get("forecast_type", "daily"))
            forecasts = await self._load_forecasts(
                entity_ids,
                forecast_type,
                cache,
            )
        result = forecast_decision(
            weather,
            forecasts,
            now=now,
            horizon_hours=int(task.get("schedule", {}).get("horizon_hours", 48)),
            forecast_type=str(task.get("schedule", {}).get("forecast_type", "daily")),
        )
        if scenario:
            result["scenario"] = True
        return result

    @staticmethod
    def _scenario_forecasts(
        weather: dict[str, Any],
        scenario: dict[str, Any],
        now: datetime,
    ) -> dict[str, list[dict[str, Any]]]:
        """Translate simulator values into Home Assistant-shaped forecasts."""
        scenario_date = str(
            scenario.get("date") or (now + timedelta(days=1)).date().isoformat()
        )
        values = list(scenario.get("values", []))
        by_entity: dict[str, dict[str, Any]] = {}
        for index, condition in enumerate(weather.get("conditions", [])):
            entity_id = str(condition.get("entity_id", ""))
            entry = by_entity.setdefault(entity_id, {"datetime": scenario_date})
            attribute = str(condition.get("attribute", "")).strip()
            if attribute and index < len(values):
                entry[attribute] = values[index]
        return {entity_id: [entry] for entity_id, entry in by_entity.items()}

    async def _load_forecasts(
        self,
        entity_ids: list[str],
        forecast_type: str,
        cache: dict[tuple[str, str], list[dict[str, Any]]] | None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Load forecasts once and reuse an optional scan-local cache."""
        if not entity_ids:
            return {}
        missing = entity_ids
        if cache is not None:
            missing = [
                entity_id
                for entity_id in entity_ids
                if (forecast_type, entity_id) not in cache
            ]
        response = {}
        if missing:
            response = (
                await self.hass.services.async_call(
                    "weather",
                    "get_forecasts",
                    {"entity_id": missing, "type": forecast_type},
                    blocking=True,
                    return_response=True,
                )
                or {}
            )
        if cache is not None:
            for entity_id in missing:
                cache[(forecast_type, entity_id)] = self._forecast_payload(
                    response.get(entity_id)
                )
            return {
                entity_id: cache.get((forecast_type, entity_id), [])
                for entity_id in entity_ids
            }
        return {
            entity_id: self._forecast_payload(response.get(entity_id))
            for entity_id in entity_ids
        }

    @staticmethod
    def _forecast_payload(payload: Any) -> list[dict[str, Any]]:
        """Return a forecast array from one HA service payload."""
        return payload.get("forecast", []) if isinstance(payload, dict) else []

    def _forecast_trace(
        self, task: dict[str, Any], preview: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Build a compact, ordered explanation shared by UI and runtime."""
        forecast = preview.get("forecast") or {}
        planned = preview.get("planned_occurrences", [])
        return [
            {
                "step": "forecast",
                "passed": bool(forecast.get("allowed")),
                "message": forecast.get("message"),
                "matched_period": forecast.get("matched_period"),
            },
            {
                "step": "household_mode",
                "passed": bool(preview["mode"]["allowed"]),
                "message": preview["mode"]["message"],
            },
            {
                "step": "season",
                "passed": bool(preview["season"]["allowed"]),
                "message": preview["season"]["message"],
            },
            {
                "step": "distribution",
                "passed": bool(planned),
                "message": (
                    f"{len(planned)} personenbezogene Aufgabe(n) geplant."
                    if task.get("assignment", {}).get("type") == "per_person"
                    else "Eine Aufgabe geplant."
                ),
            },
            {
                "step": "seasonal_deduplication",
                "passed": any(item["would_create"] for item in planned)
                if planned
                else True,
                "message": (
                    f"{sum(item['would_create'] for item in planned)} von "
                    f"{len(planned)} Zielpersonen sind in dieser Saison offen."
                    if planned
                    else "Keine personenbezogene Saisonsperre erforderlich."
                ),
            },
        ]

    def _preview_scheduled_due(
        self, schedule: dict[str, Any], now: datetime
    ) -> str | None:
        """Return the next generated due time for a calendar-independent schedule."""
        end = now + timedelta(days=400)
        return next(
            (
                due.isoformat()
                for due in self._scheduled_times(
                    schedule, now - timedelta(microseconds=1), end
                )
            ),
            None,
        )

    def _preview_after_completion_due(
        self, schedule: dict[str, Any], now: datetime
    ) -> str | None:
        """Return the initial due time for an after-completion schedule."""
        start = dt_util.parse_datetime(str(schedule.get("start", "")))
        if start is not None and start.tzinfo is None:
            start = start.replace(tzinfo=ZoneInfo(self.hass.config.time_zone))
        return start.isoformat() if start is not None and start > now else None

    def _preview_state_triggers(self, schedule: dict[str, Any]) -> list[dict[str, Any]]:
        """Return current match information for each configured state trigger."""
        result = []
        for trigger in schedule.get("triggers", []):
            entity_id = str(trigger.get("entity_id", ""))
            state = self.hass.states.get(entity_id)
            current = state.state if state is not None else None
            wanted = str(trigger.get("to", ""))
            result.append(
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
                "data": {"tag": "household_tasks_test", "url": _PANEL_PATH},
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
        priority: str = "normal",
        points: int = 1,
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
            if priority not in PRIORITIES:
                raise vol.Invalid("Die Priorität ist ungültig.")
            if not 0 <= int(points) <= 100:
                raise vol.Invalid("Punkte müssen zwischen 0 und 100 liegen.")
            task: dict[str, Any] = {
                "enabled": True,
                "name": clean_name,
                "assignee": assignee,
                "schedule": {"type": "manual"},
                "market": {"priority": priority, "points": int(points)},
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
        self,
        occurrence_id: str,
        context: Context | None = None,
        *,
        expected_revision: int | None = None,
    ) -> None:
        """Complete a task owned by the native Household Tasks store."""
        async with self.lock:
            occurrence = self._occurrence_for_update(occurrence_id, expected_revision)
            if occurrence.get("resolved"):
                raise vol.Invalid(_TASK_NOT_OPEN)
            if occurrence.get("status") == "blocked":
                raise vol.Invalid("Offene Abhängigkeiten blockieren diese Aufgabe.")
            incomplete = [
                item
                for item in occurrence.get("checklist", [])
                if not item.get("completed")
            ]
            if incomplete and occurrence.get("task", {}).get(
                "require_checklist_completion", True
            ):
                raise vol.Invalid(f"Noch {len(incomplete)} Checklistenpunkt(e) offen.")
            snapshot = deepcopy(occurrence)
            previous_occurrences = set(self.state["occurrences"])
            await self._resolve_occurrence(
                occurrence_id,
                occurrence,
                completed_by=self._person_for_context(context),
            )
            created_occurrences = sorted(
                set(self.state["occurrences"]) - previous_occurrences
            )
            self._push_undo(
                "complete",
                f"Aufgabe „{snapshot['title']}“ erledigen",
                {
                    "occurrence_id": occurrence_id,
                    "occurrence": snapshot,
                    "created_occurrences": created_occurrences,
                    "completed_by": occurrence.get("completed_by"),
                    "points": int(
                        occurrence.get("task", {}).get("market", {}).get("points", 1)
                    ),
                },
            )
            await self._save()

    async def async_set_occurrence_status(
        self,
        occurrence_id: str,
        status: str,
        *,
        expected_revision: int | None = None,
        context: Context | None = None,
    ) -> None:
        """Transition one task with optimistic concurrency protection."""
        if status not in {
            "open",
            "in_progress",
            "waiting",
            "blocked",
            "completed",
            "cancelled",
        }:
            raise vol.Invalid(f"Unknown task status: {status}")
        if status == "blocked":
            raise vol.Invalid(
                "Der Status Blockiert wird ausschließlich aus Abhängigkeiten berechnet."
            )
        if status == "completed":
            await self.async_complete_occurrence(
                occurrence_id,
                context,
                expected_revision=expected_revision,
            )
            return
        async with self.lock:
            occurrence = self._occurrence_for_update(occurrence_id, expected_revision)
            unresolved = [
                dependency
                for dependency in occurrence.get("dependencies", [])
                if self.state["occurrences"].get(dependency, {}).get("status")
                not in TERMINAL_TASK_STATUSES
            ]
            if unresolved:
                raise vol.Invalid(
                    "Offene Abhängigkeiten blockieren diesen Statuswechsel."
                )
            if status == "cancelled":
                await self._resolve_occurrence(
                    occurrence_id,
                    occurrence,
                    completed_by=self._person_for_context(context),
                    award_point=False,
                    resolution_reason="cancelled",
                )
                await self._save()
                return
            occurrence["status"] = status
            occurrence["resolved"] = False
            occurrence.pop("resolved_at", None)
            occurrence.pop("resolution_reason", None)
            self._touch_occurrence(
                occurrence_id,
                occurrence,
                "task_status_changed",
                actor=self._person_for_context(context),
                details={"status": status},
            )
            await self._save()

    async def async_set_checklist_item(
        self,
        occurrence_id: str,
        item_id: str,
        completed: bool,
        *,
        expected_revision: int | None = None,
        context: Context | None = None,
    ) -> None:
        """Update one structured checklist item."""
        async with self.lock:
            occurrence = self._occurrence_for_update(occurrence_id, expected_revision)
            item = next(
                (
                    entry
                    for entry in occurrence.get("checklist", [])
                    if entry.get("id") == item_id
                ),
                None,
            )
            if item is None:
                raise vol.Invalid(f"Unknown checklist item: {item_id}")
            actor = self._person_for_context(context)
            item["completed"] = completed
            if completed:
                item["completed_at"] = dt_util.utcnow().isoformat()
                if actor:
                    item["completed_by"] = actor
            else:
                item.pop("completed_at", None)
                item.pop("completed_by", None)
            self._touch_occurrence(
                occurrence_id,
                occurrence,
                "checklist_item_completed" if completed else "checklist_item_reopened",
                actor=actor,
                details={"item_id": item_id},
            )
            await self._save()

    async def async_set_occurrence_dependencies(
        self,
        occurrence_id: str,
        dependencies: list[str],
        *,
        expected_revision: int | None = None,
    ) -> None:
        """Replace occurrence dependencies and recompute its blocked state."""
        async with self.lock:
            occurrence = self._occurrence_for_update(occurrence_id, expected_revision)
            normalized = list(dict.fromkeys(str(item) for item in dependencies))
            if occurrence_id in normalized:
                raise vol.Invalid("Eine Aufgabe kann nicht von sich selbst abhängen.")
            missing = [
                item for item in normalized if item not in self.state["occurrences"]
            ]
            if missing:
                raise vol.Invalid(f"Unknown dependencies: {', '.join(missing)}")
            if has_dependency_cycle(
                self.state["occurrences"], occurrence_id, normalized
            ):
                raise vol.Invalid("Abhängigkeiten dürfen keinen Zyklus bilden.")
            occurrence["dependencies"] = normalized
            blocked = any(
                self.state["occurrences"][item].get("status")
                not in TERMINAL_TASK_STATUSES
                for item in normalized
            )
            if occurrence.get("status") not in TERMINAL_TASK_STATUSES:
                occurrence["status"] = "blocked" if blocked else "open"
            self._touch_occurrence(
                occurrence_id,
                occurrence,
                "task_dependencies_changed",
                details={"dependencies": normalized},
            )
            await self._save()

    def task_history(self, occurrence_id: str) -> list[dict[str, Any]]:
        """Return the immutable event history for one task."""
        if occurrence_id not in self.state["occurrences"]:
            raise vol.Invalid("Unknown task occurrence")
        return [
            deepcopy(event)
            for event in self.state.get("task_events", [])
            if event.get("occurrence_id") == occurrence_id
        ]

    def _occurrence_for_update(
        self, occurrence_id: str, expected_revision: int | None
    ) -> dict[str, Any]:
        occurrence = self.state["occurrences"].get(occurrence_id)
        if occurrence is None:
            raise vol.Invalid("Unknown task occurrence")
        if occurrence.get("status") in TERMINAL_TASK_STATUSES:
            raise vol.Invalid(
                "Abgeschlossene oder abgebrochene Aufgaben können nicht verändert werden."
            )
        if (
            expected_revision is not None
            and int(occurrence.get("revision", 0)) != expected_revision
        ):
            raise vol.Invalid(
                "Die Aufgabe wurde zwischenzeitlich geändert. Bitte neu laden."
            )
        return occurrence

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
            entities.update(
                str(condition.get("entity_id"))
                for condition in task.get("weather", {}).get("conditions", [])
                if condition.get("entity_id")
            )
            season_entity = task.get("season", {}).get("entity_id")
            if season_entity:
                entities.add(season_entity)
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
                            "url": _PANEL_PATH,
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
            await self._create_occurrence(task_id, task, due)
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
                await self._request_help(occurrence_id, occurrence)
                await self._save()
                return
            if prefix == CLAIM_HELP_PREFIX and helper_id is not None:
                helpers = occurrence.setdefault("helpers", [])
                if helper_id not in helpers:
                    helpers.append(helper_id)
                occurrence["help_status"] = "accepted"
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
            snapshot = deepcopy(occurrence)
            previous_occurrences = set(self.state["occurrences"])
            await self._resolve_occurrence(
                occurrence_id,
                occurrence,
                completed_by=completed_by,
            )
            self._push_undo(
                "complete",
                f"Aufgabe „{snapshot['title']}“ erledigen",
                {
                    "occurrence_id": occurrence_id,
                    "occurrence": snapshot,
                    "created_occurrences": sorted(
                        set(self.state["occurrences"]) - previous_occurrences
                    ),
                    "completed_by": occurrence.get("completed_by"),
                    "points": int(
                        occurrence.get("task", {}).get("market", {}).get("points", 1)
                    ),
                },
            )
            await self._save()

    async def async_claim_occurrence(
        self, occurrence_id: str, context: Context | None = None
    ) -> None:
        """Claim an open task from the panel using the current HA user."""
        async with self.lock:
            occurrence = self.state["occurrences"].get(occurrence_id)
            if not occurrence or occurrence.get("resolved"):
                raise vol.Invalid(_TASK_NOT_OPEN)
            person_id = self._person_for_context(context)
            if person_id is None:
                raise vol.Invalid(
                    "Dem Home-Assistant-Benutzer ist keine Person zugeordnet."
                )
            await self._claim_occurrence(
                occurrence_id, occurrence, person_id, context=context
            )
            await self._save()

    async def async_snooze_occurrence(self, occurrence_id: str, choice: str) -> None:
        """Snooze one occurrence to this evening or tomorrow morning."""
        async with self.lock:
            occurrence = self.state["occurrences"].get(occurrence_id)
            if not occurrence or occurrence.get("resolved"):
                raise vol.Invalid(_TASK_NOT_OPEN)
            now = dt_util.now()
            if choice == "evening":
                target = datetime.combine(
                    now.date(),
                    time(18, 0),
                    ZoneInfo(self.hass.config.time_zone),
                )
                if target <= now:
                    target = now + timedelta(hours=2)
            elif choice == "tomorrow":
                target = datetime.combine(
                    now.date() + timedelta(days=1),
                    time(9, 0),
                    ZoneInfo(self.hass.config.time_zone),
                )
            else:
                raise vol.Invalid("Unknown snooze choice")
            await self._snooze_occurrence(occurrence_id, occurrence, target)
            await self._save()

    async def async_request_help(self, occurrence_id: str) -> None:
        """Ask the rest of the household for voluntary assistance."""
        async with self.lock:
            occurrence = self.state["occurrences"].get(occurrence_id)
            if not occurrence or occurrence.get("resolved"):
                raise vol.Invalid(_TASK_NOT_OPEN)
            await self._request_help(occurrence_id, occurrence)
            await self._save()

    async def async_decline_occurrence(self, occurrence_id: str) -> None:
        """Try to delegate an occurrence before its escalation chain runs."""
        async with self.lock:
            occurrence = self.state["occurrences"].get(occurrence_id)
            if not occurrence or occurrence.get("resolved"):
                raise vol.Invalid(_TASK_NOT_OPEN)
            if not await self._delegate_occurrence(occurrence_id, occurrence):
                await self._open_occurrence_for_claim(
                    occurrence_id,
                    occurrence,
                    reason_type="declined_open",
                )
            await self._save()

    async def _request_help(
        self, occurrence_id: str, occurrence: dict[str, Any]
    ) -> None:
        """Notify eligible helpers and expose a one-tap assistance action."""
        helpers = [
            person for person in self.people if person != occurrence.get("assignee")
        ]
        occurrence["help_requested_at"] = dt_util.utcnow().isoformat()
        occurrence["help_status"] = "requested"
        for helper in helpers:
            await self._notify(
                occurrence_id,
                occurrence,
                [helper],
                _HELP_NEEDED,
                f"{occurrence['title']}: Kannst du unterstützen?",
                claim_help_for=helper,
            )

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
        self._touch_occurrence(
            occurrence_id,
            occurrence,
            "task_claimed",
            actor=person_id,
        )
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
        self._touch_occurrence(
            occurrence_id,
            occurrence,
            "task_reassigned",
            actor=person_id,
            details={"from": previous, "to": person_id, "reason": reason_type},
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
        """Move a task and restart its escalation clock."""
        due = dt_util.as_local(due).replace(second=0, microsecond=0)
        await self._update_native_occurrence(occurrence, due=due)
        await self._clear_notifications(occurrence_id, occurrence)
        occurrence["due"] = due.isoformat()
        occurrence["snoozed_until"] = due.isoformat()
        occurrence["sent_steps"] = []
        occurrence["first_notification_at"] = None
        self._touch_occurrence(
            occurrence_id,
            occurrence,
            "task_snoozed",
            details={"due": due.isoformat()},
        )

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
        self._touch_occurrence(
            occurrence_id,
            occurrence,
            "task_delegated",
            actor=assignee,
            details={"from": current, "to": assignee},
        )
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
        self._touch_occurrence(
            occurrence_id,
            occurrence,
            "task_opened_for_claim",
            details={"from": previous, "reason": reason_type},
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
        """Update native occurrence fields without an external mirror."""
        if due is not None:
            occurrence["due"] = due.isoformat()
        if title is not None:
            occurrence["title"] = title

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
            await self._scan_weather_tasks(current)
            await self._scan_forecast_tasks(current)
            await self._process_waiting_occurrences(current)
            await self._process_escalations(current)
            await self._process_weekly_summary(current)
            await self._process_notification_digest(current)

            self.state["last_check"] = current.isoformat()
            self._prune_handovers(current)
            self._prune_state(current)
            await self._save()

    async def _scan_weather_tasks(self, now: datetime) -> None:
        """Create weather-triggered tasks on a matching edge or cooldown."""
        matches = self.state.setdefault("weather_matches", {})
        last_created = self.state.setdefault("weather_last_created", {})
        for task_id, task in self.tasks.items():
            await self._scan_weather_task(
                task_id,
                task,
                now,
                matches,
                last_created,
            )

    async def _scan_weather_task(
        self,
        task_id: str,
        task: dict[str, Any],
        now: datetime,
        matches: dict[str, Any],
        last_created: dict[str, Any],
    ) -> None:
        """Evaluate and potentially create one weather-triggered task."""
        schedule = task.get("schedule", {})
        if not task.get("enabled", True) or schedule.get("type") != "weather_trigger":
            return
        decision = self._weather_decision(task)
        previous = bool(matches.get(task_id, False))
        matches[task_id] = decision["allowed"]
        if not decision["allowed"]:
            return
        cooldown = self._parse_duration(schedule.get("cooldown", "24:00:00"))
        last = dt_util.parse_datetime(str(last_created.get(task_id, "")))
        cooldown_due = last is None or dt_util.as_local(last) + cooldown <= now
        skip_if_open = schedule.get("skip_if_open", True)
        if skip_if_open and self._open_occurrence_exists(task_id):
            return
        if previous and (skip_if_open or not cooldown_due):
            return
        due_after = self._parse_duration(schedule.get("due_after", "00:00:00"))
        occurrence_id = await self._create_occurrence(task_id, task, now + due_after)
        if occurrence_id:
            last_created[task_id] = now.isoformat()
            return
        # A matching edge blocked by another policy must remain retryable.
        matches[task_id] = False

    def _open_occurrence_exists(
        self,
        task_id: str,
        target_person: str | None = None,
    ) -> bool:
        """Return whether one matching unresolved occurrence already exists."""
        return any(
            not occurrence.get("resolved")
            and occurrence.get("task_id") == task_id
            and (
                target_person is None
                or occurrence.get("target_person") == target_person
            )
            for occurrence in self.state["occurrences"].values()
        )

    async def _scan_forecast_tasks(self, now: datetime) -> None:
        """Plan and create tasks from Home Assistant weather forecasts."""
        last_created = self.state.setdefault("forecast_last_created", {})
        forecast_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for task_id, task in self.tasks.items():
            schedule = task.get("schedule", {})
            if (
                not task.get("enabled", True)
                or schedule.get("type") != "forecast_trigger"
            ):
                continue
            try:
                decision = await self._forecast_decision(
                    task, now, cache=forecast_cache
                )
            except Exception as err:  # Home Assistant service errors stay traceable.
                _LOGGER.warning("Could not evaluate forecast task %s: %s", task_id, err)
                self.state.setdefault("forecast_traces", {})[task_id] = {
                    "checked_at": now.isoformat(),
                    "allowed": False,
                    "code": "forecast_service_error",
                    "message": str(err),
                }
                continue
            trace = {
                "checked_at": now.isoformat(),
                **deepcopy(decision),
            }
            self.state.setdefault("forecast_traces", {})[task_id] = trace
            matched = decision.get("matched_period")
            if not matched:
                continue
            activation = forecast_activation(
                matched["datetime"],
                lead_days=int(schedule.get("lead_days", 1)),
                activation_time=self._parse_time(schedule.get("time", "18:00:00")),
                zone=ZoneInfo(self.hass.config.time_zone),
            )
            trace["activation_at"] = activation.isoformat()
            if now < activation:
                trace["code"] = "forecast_match_waiting_for_lead_time"
                trace["message"] = (
                    "Die Vorhersage passt; die konfigurierte Vorlaufzeit ist "
                    "noch nicht erreicht."
                )
                continue
            cooldown = self._parse_duration(schedule.get("cooldown", "24:00:00"))
            last = dt_util.parse_datetime(str(last_created.get(task_id, "")))
            if (
                task.get("repeat", {}).get("mode") != "once_per_season"
                and last is not None
                and dt_util.as_local(last) + cooldown > now
            ):
                trace["code"] = "forecast_cooldown"
                trace["message"] = "Der Vorhersage-Cooldown ist noch aktiv."
                continue
            occurrence_id = await self._create_occurrence(
                task_id,
                task,
                max(now, activation),
                prechecked_weather=decision,
                creation_trace=trace,
                rule_reference=dt_util.parse_datetime(matched["datetime"]),
            )
            if occurrence_id:
                last_created[task_id] = now.isoformat()

    async def _process_waiting_occurrences(self, now: datetime) -> None:
        """Release naturally deferred tasks when their local condition is met."""
        for occurrence in self.state["occurrences"].values():
            waiting = occurrence.get("waiting_for")
            if (
                occurrence.get("resolved")
                or not isinstance(waiting, dict)
                or waiting.get("type") != "presence"
                or not self._is_home(waiting.get("person_id"))
            ):
                continue
            due = now.replace(second=0, microsecond=0)
            await self._update_native_occurrence(occurrence, due=due)
            occurrence["due"] = due.isoformat()
            occurrence["released_at"] = dt_util.utcnow().isoformat()
            occurrence.pop("waiting_for", None)

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
                    occurrence["task"]["description"] = detail
                    occurrence["description"] = detail
                    self._touch_occurrence(
                        active["occurrence_id"],
                        occurrence,
                        "task_description_updated",
                    )
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
                "flexible_after_completion",
                "weather_trigger",
                "forecast_trigger",
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
                or schedule.get("type")
                not in {"after_completion", "flexible_after_completion"}
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
                            "url": _PANEL_PATH,
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

    async def _process_notification_digest(self, now: datetime) -> None:
        """Deliver queued routine reminders as one actionable message per person."""
        settings = self.defaults.get("notification_digest", {})
        if not settings.get("enabled", False):
            return
        queue = self.state.setdefault("notification_queue", {})
        last_sent = self.state.setdefault("last_notification_digest", {})
        if not notification_digest_due(
            now,
            last_sent.get("date"),
            settings.get("time", "17:30:00"),
        ):
            return
        delivered = False
        for person_id, pending in queue.items():
            if person_id not in self.people or not pending:
                continue
            entries = list(pending.values())
            minimum = max(1, int(settings.get("minimum_tasks", 2)))
            payloads = self._notification_digest_payloads(
                person_id,
                entries,
                minimum,
            )
            if await self._send_notification_digest(person_id, payloads):
                queue[person_id] = {}
                delivered = True
        if delivered:
            last_sent["date"] = now.date().isoformat()

    @staticmethod
    def _notification_digest_payloads(
        person_id: str,
        entries: list[dict[str, Any]],
        minimum: int,
    ) -> list[dict[str, Any]]:
        """Build individual or bundled digest payloads."""
        if len(entries) < minimum:
            return [
                {
                    "title": entry["short_title"],
                    "message": entry["title"],
                    "data": {
                        "tag": f"household_task_{entry['occurrence_id']}",
                        "url": _PANEL_PATH,
                        "actions": [
                            {
                                "action": (
                                    f"{ACTION_PREFIX}"
                                    f"{entry['occurrence_id']}_{person_id}"
                                ),
                                "title": "Erledigt",
                                "activationMode": "background",
                            },
                            {
                                "action": "URI",
                                "title": _OPEN_TASK,
                                "uri": _PANEL_PATH,
                            },
                        ],
                    },
                }
                for entry in entries
            ]
        task_count = len(entries)
        message = " · ".join(entry["title"] for entry in entries[:4])
        if task_count > 4:
            message += f" · +{task_count - 4} weitere"
        actions = [
            {
                "action": f"{ACTION_PREFIX}{entry['occurrence_id']}_{person_id}",
                "title": f"✓ {entry['short_title'][:24]}",
                "activationMode": "background",
            }
            for entry in entries[:3]
        ]
        actions.append(
            {
                "action": "URI",
                "title": "Meine Aufgaben öffnen",
                "uri": _PANEL_PATH,
            }
        )
        return [
            {
                "title": f"{task_count} Haushaltsaufgaben",
                "message": message,
                "data": {
                    "tag": f"household_tasks_digest_{person_id}",
                    "url": _PANEL_PATH,
                    "actions": actions,
                },
            }
        ]

    async def _send_notification_digest(
        self,
        person_id: str,
        payloads: list[dict[str, Any]],
    ) -> bool:
        """Send all payloads for one person as one transactional digest."""
        notify_action = self.people[person_id]["notify"]
        service = notify_action.removeprefix("notify.")
        try:
            for payload in payloads:
                await self.hass.services.async_call(
                    "notify", service, payload, blocking=True
                )
        except Exception:
            _LOGGER.exception(
                "Could not send notification digest using %s",
                notify_action,
            )
            return False
        return True

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

    def _fanout_targets(self, task: dict[str, Any]) -> list[str]:
        """Return stable responsibility targets without applying handovers."""
        assignment = task.get("assignment", {})
        if assignment.get("type") == "per_person":
            return list(
                dict.fromkeys(
                    person_id
                    for person_id in assignment.get("people", [])
                    if person_id in self.people
                )
            )
        assignee = task.get("assignee")
        return [assignee] if assignee in self.people else []

    def _seasonal_execution_decision(
        self,
        task_id: str,
        task: dict[str, Any],
        reference: datetime,
        target_person: str | None,
    ) -> dict[str, Any]:
        """Explain whether a seasonal execution target is still available."""
        if task.get("repeat", {}).get("mode") != "once_per_season":
            return {
                "allowed": True,
                "code": "no_seasonal_deduplication",
                "message": "Keine einmalige Saisonsperre konfiguriert.",
            }
        key = season_key(reference, task.get("season", {}).get("months", []))
        target = target_person or "__task__"
        ledger_key = f"{task_id}|{key}|{target}"
        executed_at = self.state.setdefault("seasonal_executions", {}).get(ledger_key)
        return {
            "allowed": executed_at is None,
            "code": (
                "seasonal_target_available"
                if executed_at is None
                else "already_executed_this_season"
            ),
            "message": (
                f"Für die Saison {key} wurde noch keine Aufgabe erzeugt."
                if executed_at is None
                else f"Die Aufgabe wurde für die Saison {key} bereits erzeugt."
            ),
            "season_key": key,
            "target_person": target_person,
            "executed_at": executed_at,
            "ledger_key": ledger_key,
        }

    def _mark_seasonal_execution(self, decision: dict[str, Any]) -> None:
        """Persist a successful once-per-season execution."""
        ledger_key = decision.get("ledger_key")
        if ledger_key:
            self.state.setdefault("seasonal_executions", {})[ledger_key] = (
                dt_util.utcnow().isoformat()
            )

    def _season_decision(
        self, task: dict[str, Any], reference: datetime
    ) -> dict[str, Any]:
        season = task.get("season", {})
        entity_id = season.get("entity_id")
        state = self.hass.states.get(entity_id) if entity_id else None
        return season_decision(
            task,
            reference,
            state.state if state is not None else None,
        )

    def _weather_decision(self, task: dict[str, Any]) -> dict[str, Any]:
        """Evaluate all configured weather entities and attributes locally."""
        weather = task.get("weather")
        states = {}
        for condition in weather.get("conditions", []) if weather else []:
            entity_id = str(condition.get("entity_id", ""))
            state = self.hass.states.get(entity_id)
            if state is not None:
                states[entity_id] = {
                    "state": state.state,
                    "attributes": dict(state.attributes),
                }
        return weather_decision(weather, states)

    def _automatic_creation_decision(
        self,
        task: dict[str, Any],
        due: datetime,
        *,
        prechecked_weather: dict[str, Any] | None = None,
        rule_reference: datetime | None = None,
    ) -> dict[str, Any]:
        mode = mode_decision(self._current_household_mode(), task)
        if not mode["allowed"]:
            return mode
        season = self._season_decision(task, rule_reference or due)
        if not season["allowed"]:
            return season
        weather = prechecked_weather or self._weather_decision(task)
        if not weather["allowed"]:
            return weather
        return {
            "allowed": True,
            "code": "eligible",
            "message": "Alle Erzeugungsbedingungen sind erfüllt.",
            "delegate_to": mode.get("delegate_to"),
        }

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
        prechecked_weather: dict[str, Any] | None = None,
        creation_trace: dict[str, Any] | None = None,
        rule_reference: datetime | None = None,
        _target_person: str | None = None,
    ) -> str | None:
        if (
            task.get("assignment", {}).get("type") == "per_person"
            and _target_person is None
        ):
            return await self._create_fanout_occurrences(
                task_id,
                task,
                due,
                event_summary=event_summary,
                manual=manual,
                context=context,
                prechecked_weather=prechecked_weather,
                creation_trace=creation_trace,
                rule_reference=rule_reference,
            )

        due = dt_util.as_local(due).replace(microsecond=0)
        occurrence_id = self._occurrence_id(
            task_id,
            due,
            manual=manual,
            target_person=_target_person,
        )
        if occurrence_id in self.state["occurrences"]:
            return occurrence_id

        prepared = self._prepare_occurrence_task(
            task_id,
            task,
            due,
            manual=manual,
            prechecked_weather=prechecked_weather,
            rule_reference=rule_reference,
            target_person=_target_person,
        )
        if prepared is None:
            return None
        task, repetition = prepared

        assignee, assignment_reason = self._select_assignee_with_reason(task_id, task)
        assignee_name = self.people.get(assignee, {}).get("name", "Offen")
        title = f"[{assignee_name}] {task['name']}"
        description = self._occurrence_description(task, event_summary)
        dependencies = self._dependency_occurrence_ids(task)
        occurrence = self._occurrence_record(
            task_id,
            task,
            title,
            assignee,
            assignment_reason,
            due,
            repetition,
            description=description,
            dependencies=dependencies,
            target_person=_target_person,
            creation_trace=creation_trace,
            rule_reference=rule_reference,
        )
        self.state["occurrences"][occurrence_id] = occurrence
        self._record_task_event(
            "task_created",
            occurrence_id,
            actor=self._person_for_context(context),
            details={"task_id": task_id, "due": due.isoformat()},
        )
        if not manual:
            self._mark_seasonal_execution(repetition)
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

    async def _create_fanout_occurrences(
        self,
        task_id: str,
        task: dict[str, Any],
        due: datetime,
        **options: Any,
    ) -> str | None:
        """Create one independent occurrence per configured target person."""
        first_created = None
        skip_if_open = task.get("schedule", {}).get("skip_if_open", True)
        for target_person in self._fanout_targets(task):
            if skip_if_open and self._open_occurrence_exists(task_id, target_person):
                continue
            occurrence_id = await self._create_occurrence(
                task_id,
                self._targeted_task(task, target_person),
                due,
                **options,
                _target_person=target_person,
            )
            first_created = first_created or occurrence_id
        return first_created

    @staticmethod
    def _targeted_task(
        task: dict[str, Any],
        target_person: str,
    ) -> dict[str, Any]:
        """Convert one per-person rule into a fixed targeted copy."""
        targeted_task = deepcopy(task)
        targeted_task["assignee"] = target_person
        assignment = {"type": "fixed", "people": [target_person]}
        if task.get("assignment", {}).get("presence_required"):
            assignment["presence_required"] = True
        targeted_task["assignment"] = assignment
        return targeted_task

    @staticmethod
    def _occurrence_id(
        task_id: str,
        due: datetime,
        *,
        manual: bool,
        target_person: str | None,
    ) -> str:
        """Build the deterministic occurrence identity."""
        identity = f"{task_id}|{due.astimezone(dt_util.UTC).isoformat()}"
        if target_person:
            identity += f"|person:{target_person}"
        if manual:
            identity += f"|{dt_util.utcnow().isoformat()}"
        return hashlib.sha256(identity.encode()).hexdigest()[:20]

    def _prepare_occurrence_task(
        self,
        task_id: str,
        task: dict[str, Any],
        due: datetime,
        *,
        manual: bool,
        prechecked_weather: dict[str, Any] | None,
        rule_reference: datetime | None,
        target_person: str | None,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        """Apply automatic policy decisions before native task creation."""
        repetition = self._seasonal_execution_decision(
            task_id,
            task,
            rule_reference or due,
            target_person,
        )
        if manual:
            return task, repetition
        if not repetition["allowed"]:
            self._record_creation_decision(
                task_id,
                task,
                due,
                {**repetition, "target_person": target_person},
            )
            return None
        decision = self._automatic_creation_decision(
            task,
            due,
            prechecked_weather=prechecked_weather,
            rule_reference=rule_reference,
        )
        if not decision["allowed"]:
            self._record_creation_decision(
                task_id,
                task,
                due,
                {**decision, "target_person": target_person},
            )
            return None
        delegate_to = decision.get("delegate_to")
        if delegate_to not in self.people:
            return task, repetition
        delegated = deepcopy(task)
        delegated["assignee"] = delegate_to
        delegated["assignment"] = {"type": "fixed", "people": [delegate_to]}
        return delegated, repetition

    @staticmethod
    def _occurrence_description(
        task: dict[str, Any],
        event_summary: str | None,
    ) -> str | None:
        """Combine task and calendar descriptions."""
        description = task.get("description")
        if event_summary and description:
            return f"{description}\nKalender: {event_summary}"
        if event_summary:
            return f"Kalender: {event_summary}"
        return description

    def _occurrence_record(
        self,
        task_id: str,
        task: dict[str, Any],
        title: str,
        assignee: str | None,
        assignment_reason: dict[str, Any],
        due: datetime,
        repetition: dict[str, Any],
        *,
        description: str | None,
        dependencies: list[str],
        target_person: str | None,
        creation_trace: dict[str, Any] | None,
        rule_reference: datetime | None,
    ) -> dict[str, Any]:
        """Build persisted occurrence metadata."""
        occurrence = {
            "task_id": task_id,
            "task": deepcopy(task),
            "title": title,
            "assignee": assignee,
            "assignment_reason": assignment_reason,
            "due": due.isoformat(),
            "description": description,
            "status": self._initial_occurrence_status(assignment_reason, dependencies),
            "created_at": dt_util.utcnow().isoformat(),
            "updated_at": dt_util.utcnow().isoformat(),
            "revision": 1,
            "checklist": [
                {
                    "id": str(item.get("id") or f"step_{index + 1}"),
                    "title": str(item.get("title") or item.get("name") or "Schritt"),
                    "completed": False,
                }
                if isinstance(item, dict)
                else {
                    "id": f"step_{index + 1}",
                    "title": str(item),
                    "completed": False,
                }
                for index, item in enumerate(task.get("checklist", []))
            ],
            "dependencies": dependencies,
            "sent_steps": [],
            "notified_people": [],
            "first_notification_at": None,
            "resolved": False,
        }
        optional = {
            "target_person": target_person,
            "creation_trace": deepcopy(creation_trace) if creation_trace else None,
            "rule_reference": rule_reference.isoformat() if rule_reference else None,
        }
        occurrence.update({key: value for key, value in optional.items() if value})
        if repetition.get("season_key"):
            occurrence["season_key"] = repetition["season_key"]
            occurrence["campaign_id"] = f"{task_id}:{repetition['season_key']}"
        self._add_due_window(occurrence, task, due)
        return occurrence

    def _dependency_occurrence_ids(self, task: dict[str, Any]) -> list[str]:
        """Resolve template dependencies to current unresolved occurrences."""
        template_ids = {str(item) for item in task.get("depends_on", [])}
        if not template_ids:
            return []
        return [
            occurrence_id
            for occurrence_id, occurrence in self.state["occurrences"].items()
            if occurrence.get("task_id") in template_ids
            and occurrence.get("status") not in {"completed", "cancelled"}
        ]

    @staticmethod
    def _initial_occurrence_status(
        assignment_reason: dict[str, Any], dependencies: list[str]
    ) -> str:
        if dependencies:
            return "blocked"
        if assignment_reason.get("waiting"):
            return "waiting"
        return "open"

    def _add_due_window(
        self,
        occurrence: dict[str, Any],
        task: dict[str, Any],
        due: datetime,
    ) -> None:
        """Add a flexible due window when configured."""
        schedule = task.get("schedule", {})
        if schedule.get("type") != "flexible_after_completion":
            return
        anchor = due - self._parse_duration(schedule["preferred_interval"])
        occurrence["due_window"] = {
            "earliest": (
                anchor + self._parse_duration(schedule["earliest_interval"])
            ).isoformat(),
            "preferred": due.isoformat(),
            "latest": (
                anchor + self._parse_duration(schedule["latest_interval"])
            ).isoformat(),
        }

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
            if self._should_queue_notification(occurrence, title, claim_help_for):
                self._queue_notification(
                    occurrence_id,
                    occurrence,
                    person_id,
                    message,
                )
                delivered = True
                continue
            actions = self._notification_actions(
                occurrence_id,
                person_id,
                occurrence.get("assignee"),
                claim_help_for,
            )
            if await self._send_notification(
                service,
                notify_action,
                title,
                message,
                tag,
                actions,
            ):
                if person_id not in occurrence["notified_people"]:
                    occurrence["notified_people"].append(person_id)
                delivered = True
        return delivered

    def _should_queue_notification(
        self,
        occurrence: dict[str, Any],
        title: str,
        claim_help_for: str | None,
    ) -> bool:
        """Return whether a routine reminder belongs in the digest."""
        digest = self.defaults.get("notification_digest", {})
        priority = (
            occurrence.get("task", {}).get("market", {}).get("priority", "normal")
        )
        return bool(
            digest.get("enabled", False)
            and claim_help_for is None
            and occurrence.get("assignee") is not None
            and priority != "critical"
            and title not in {_HELP_NEEDED, "Hilfe zugesagt"}
        )

    def _queue_notification(
        self,
        occurrence_id: str,
        occurrence: dict[str, Any],
        person_id: str,
        message: str,
    ) -> None:
        """Queue one routine reminder and record its intended recipient."""
        person_queue = self.state.setdefault("notification_queue", {}).setdefault(
            person_id, {}
        )
        person_queue[occurrence_id] = {
            "occurrence_id": occurrence_id,
            "title": message,
            "short_title": self._plain_occurrence_title(occurrence),
            "queued_at": dt_util.utcnow().isoformat(),
        }
        if person_id not in occurrence["notified_people"]:
            occurrence["notified_people"].append(person_id)

    @staticmethod
    def _notification_actions(
        occurrence_id: str,
        person_id: str,
        assignee: str | None,
        claim_help_for: str | None,
    ) -> list[dict[str, Any]]:
        """Build mobile actions for help, market, or assigned tasks."""
        open_action = {"action": "URI", "title": _OPEN_TASK, "uri": _PANEL_PATH}
        if claim_help_for:
            return [
                {
                    "action": f"{CLAIM_HELP_PREFIX}{occurrence_id}_{claim_help_for}",
                    "title": "Ich helfe",
                    "activationMode": "background",
                },
                open_action,
            ]
        if assignee is None:
            return [
                {
                    "action": f"{CLAIM_TASK_PREFIX}{occurrence_id}_{person_id}",
                    "title": "Übernehmen",
                    "activationMode": "background",
                },
                open_action,
            ]
        return [
            {
                "action": f"{ACTION_PREFIX}{occurrence_id}_{person_id}",
                "title": "Erledigt",
                "activationMode": "background",
            },
            {
                "action": f"{SNOOZE_EVENING_PREFIX}{occurrence_id}",
                "title": "Heute Abend",
                "activationMode": "background",
            },
            {
                "action": f"{SNOOZE_TOMORROW_PREFIX}{occurrence_id}",
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
                "title": _HELP_NEEDED,
                "activationMode": "background",
            },
        ]

    async def _send_notification(
        self,
        service: str,
        notify_action: str,
        title: str,
        message: str,
        tag: str,
        actions: list[dict[str, Any]],
    ) -> bool:
        """Send one actionable notification with isolated error handling."""
        try:
            await self.hass.services.async_call(
                "notify",
                service,
                {
                    "title": title,
                    "message": message,
                    "data": {"tag": tag, "url": _PANEL_PATH, "actions": actions},
                },
                blocking=True,
            )
        except Exception:
            _LOGGER.exception("Could not call notification action %s", notify_action)
            return False
        return True

    @staticmethod
    def _plain_occurrence_title(occurrence: dict[str, Any]) -> str:
        """Return an assignee-free title for compact notification actions."""
        return re.sub(r"^\[[^\]]+\]\s*", "", str(occurrence.get("title", "Aufgabe")))

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
        occurrence["status"] = (
            "cancelled" if resolution_reason == "cancelled" else "completed"
        )
        occurrence["resolved_at"] = resolved_at.isoformat()
        occurrence["resolution_reason"] = resolution_reason
        for pending in self.state.setdefault("notification_queue", {}).values():
            if isinstance(pending, dict):
                pending.pop(occurrence_id, None)
        if award_point:
            person_id = (
                completed_by
                if completed_by in self.people
                else occurrence.get("assignee")
            )
            if person_id in self.people:
                occurrence["completed_by"] = person_id
                scores = self.state.setdefault("scores", {})
                points = max(
                    0,
                    int(occurrence.get("task", {}).get("market", {}).get("points", 1)),
                )
                occurrence["awarded_points"] = points
                scores[person_id] = int(scores.get(person_id, 0)) + points
        await self._clear_notifications(occurrence_id, occurrence)
        self._touch_occurrence(
            occurrence_id,
            occurrence,
            "task_completed"
            if occurrence["status"] == "completed"
            else "task_cancelled",
            actor=completed_by,
            details={"reason": resolution_reason},
        )
        if resolution_reason == "completed":
            await self._create_completion_followups(occurrence, resolved_at)
        self._release_dependents(occurrence_id)

    def _release_dependents(self, completed_id: str) -> None:
        """Unblock tasks once all of their native dependencies are resolved."""
        terminal = {"completed", "cancelled"}
        for occurrence_id, candidate in self.state["occurrences"].items():
            dependencies = candidate.get("dependencies", [])
            if completed_id not in dependencies or candidate.get("status") != "blocked":
                continue
            if all(
                self.state["occurrences"].get(dependency, {}).get("status") in terminal
                for dependency in dependencies
            ):
                candidate["status"] = "open"
                self._touch_occurrence(
                    occurrence_id,
                    candidate,
                    "task_unblocked",
                    details={"completed_dependency": completed_id},
                )

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
            and current_template.get("schedule", {}).get("type")
            in {"after_completion", "flexible_after_completion"}
        ):
            schedule = current_template["schedule"]
            interval = self._parse_duration(
                schedule.get("interval", schedule.get("preferred_interval"))
            )
            await self._create_occurrence(
                source_task_id,
                current_template,
                completion_due(dt_util.as_local(resolved_at), interval),
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
        retained_ids = set(self.state["occurrences"])
        self.state["attachments"] = {
            occurrence_id: attachments
            for occurrence_id, attachments in self.state.get("attachments", {}).items()
            if occurrence_id in retained_ids
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
        open_count = sum(
            occurrence.get("status") not in {"completed", "cancelled"}
            for occurrence in self.state.get("occurrences", {}).values()
        )
        self.hass.bus.async_fire(
            f"{DOMAIN}_updated",
            {
                "schema_version": self.state.get("task_schema_version"),
                "open_count": open_count,
                "occurrence_count": len(self.state.get("occurrences", {})),
            },
        )

    def _record_task_event(
        self,
        event_type: str,
        occurrence_id: str | None = None,
        *,
        actor: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append one immutable native task journal entry."""
        return append_event(
            self.state,
            event_type=event_type,
            occurred_at=dt_util.utcnow().isoformat(),
            occurrence_id=occurrence_id,
            actor=actor,
            details=details,
        )

    def _touch_occurrence(
        self,
        occurrence_id: str,
        occurrence: dict[str, Any],
        event_type: str,
        *,
        actor: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Advance optimistic revision metadata and append an event."""
        occurrence["revision"] = int(occurrence.get("revision", 0)) + 1
        occurrence["updated_at"] = dt_util.utcnow().isoformat()
        self._record_task_event(
            event_type,
            occurrence_id,
            actor=actor,
            details=details,
        )

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
