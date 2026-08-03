"""Runtime tests for chained tasks and combined weather configurations."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
import voluptuous as vol
from homeassistant.core import SupportsResponse

from custom_components.household_tasks.bootstrap import initial_config
from custom_components.household_tasks.engine import HouseholdTaskEngine


async def _native_engine(hass, tasks, people=None):
    """Build a real engine backed only by the native task store."""
    config = initial_config()
    config["people"] = people or {
        "alex": {
            "name": "Alex",
            "notify": "notify.mobile_app_alex",
        }
    }
    config["tasks"] = tasks
    engine = HouseholdTaskEngine(hass, config)
    engine._validate_config()
    return engine, []


@pytest.mark.asyncio
async def test_temporary_pause_blocks_manual_creation_until_it_expires(hass):
    """Paused templates reject new work without altering open occurrences."""
    task = {
        "enabled": True,
        "paused_until": "2999-01-01T00:00:00+00:00",
        "name": "Paused routine",
        "assignee": "alex",
        "assignment": {"type": "fixed"},
        "schedule": {"type": "manual"},
    }
    engine, _ = await _native_engine(hass, {"paused": task})
    engine.state["occurrences"]["existing"] = {
        "id": "existing",
        "task_id": "paused",
        "resolved": False,
    }

    with pytest.raises(vol.Invalid, match="disabled"):
        await engine.async_create_manual("paused")

    assert list(engine.state["occurrences"]) == ["existing"]
    engine.tasks["paused"]["paused_until"] = "2020-01-01T00:00:00+00:00"
    await engine.async_create_manual("paused")
    assert len(engine.state["occurrences"]) == 2


async def test_temporary_pause_is_enforced_by_the_creation_boundary(hass):
    """Internal callers cannot bypass a pause and receive an audit explanation."""
    task = {
        "enabled": True,
        "paused_until": "2999-01-01T00:00:00+00:00",
        "name": "Paused routine",
        "assignee": "alex",
        "assignment": {"type": "fixed"},
        "schedule": {"type": "manual"},
    }
    engine, _ = await _native_engine(hass, {"paused": task})

    created = await engine._create_occurrence(
        "paused",
        task,
        datetime(2026, 8, 3, 12, tzinfo=UTC),
    )

    assert created is None
    assert engine.state["occurrences"] == {}
    assert engine.state["decision_log"][-1]["code"] == "template_paused"


async def test_temporary_pause_blocks_schedules_and_initial_completion_work(hass):
    """Every planning path resumes automatically after the pause has elapsed."""
    tasks = {
        "weekly": {
            "enabled": True,
            "paused_until": "2999-01-01T00:00:00+00:00",
            "name": "Weekly routine",
            "assignee": "alex",
            "assignment": {"type": "fixed"},
            "schedule": {
                "type": "weekly",
                "weekdays": ["mon"],
                "time": "11:00:00",
            },
        },
        "completion": {
            "enabled": True,
            "paused_until": "2999-01-01T00:00:00+00:00",
            "name": "Completion routine",
            "assignee": "alex",
            "assignment": {"type": "fixed"},
            "schedule": {
                "type": "after_completion",
                "interval": "24:00:00",
                "start": "2026-08-03T10:00:00+00:00",
            },
        },
    }
    engine, _ = await _native_engine(hass, tasks)
    start = datetime(2026, 8, 3, 0, tzinfo=UTC)
    end = datetime(2026, 8, 3, 23, tzinfo=UTC)

    await engine._create_scheduled_occurrences(start, end)
    await engine._ensure_after_completion_starts(end)
    assert engine.state["occurrences"] == {}

    for task in engine.tasks.values():
        task["paused_until"] = "2020-01-01T00:00:00+00:00"
    await engine._create_scheduled_occurrences(start, end)
    await engine._ensure_after_completion_starts(end)

    assert {
        occurrence["task_id"] for occurrence in engine.state["occurrences"].values()
    } == {"weekly", "completion"}


def _first_frost_task():
    """Return a realistic forecast/fan-out rule used by runtime tests."""
    return {
        "enabled": True,
        "name": "Check antifreeze in your own car",
        "assignment": {"type": "per_person", "people": ["alex", "sam"]},
        "schedule": {
            "type": "forecast_trigger",
            "forecast_type": "daily",
            "horizon_hours": 48,
            "lead_days": 1,
            "time": "18:00:00",
            "cooldown": "24:00:00",
            "skip_if_open": True,
        },
        "weather": {
            "logic": "all",
            "conditions": [
                {
                    "entity_id": "weather.home",
                    "attribute": "templow",
                    "condition": "below",
                    "threshold": 0,
                }
            ],
        },
        "season": {"months": [10, 11, 12, 1, 2, 3]},
        "repeat": {"mode": "once_per_season"},
    }


def _manual(name, follow_ups=None):
    return {
        "enabled": True,
        "name": name,
        "assignee": "alex",
        "assignment": {"type": "fixed"},
        "schedule": {"type": "manual"},
        "follow_ups": follow_ups or [],
    }


async def test_diamond_follow_up_chain_creates_shared_target_once(hass):
    """Two completed branches converging on one template are idempotent."""
    tasks = {
        "root": _manual(
            "Root",
            [
                {"task_id": "left", "delay": "00:00:00"},
                {"task_id": "right", "delay": "00:00:00"},
            ],
        ),
        "left": _manual(
            "Left",
            [{"task_id": "finish", "delay": "00:00:00"}],
        ),
        "right": _manual(
            "Right",
            [{"task_id": "finish", "delay": "00:00:00"}],
        ),
        "finish": _manual("Finish"),
    }
    engine, _items = await _native_engine(hass, tasks)

    await engine.async_create_manual("root")
    root_id, root = next(iter(engine.state["occurrences"].items()))
    await engine._resolve_occurrence(root_id, root, completed_by="alex")
    branches = [
        (occurrence_id, occurrence)
        for occurrence_id, occurrence in engine.state["occurrences"].items()
        if occurrence["task_id"] in {"left", "right"}
    ]
    assert len(branches) == 2

    for occurrence_id, occurrence in branches:
        await engine._resolve_occurrence(
            occurrence_id,
            occurrence,
            completed_by="alex",
        )

    finish = [
        occurrence
        for occurrence in engine.state["occurrences"].values()
        if occurrence["task_id"] == "finish"
    ]
    assert len(finish) == 1
    assert not finish[0]["resolved"]


async def test_temporary_pause_suppresses_follow_up_creation(hass):
    """Completing a source does not bypass a target template pause."""
    tasks = {
        "source": _manual(
            "Source",
            [{"task_id": "target", "delay": "00:00:00"}],
        ),
        "target": {
            **_manual("Target"),
            "paused_until": "2999-01-01T00:00:00+00:00",
        },
    }
    engine, _ = await _native_engine(hass, tasks)

    await engine.async_create_manual("source")
    source_id, source = next(iter(engine.state["occurrences"].items()))
    await engine._resolve_occurrence(source_id, source, completed_by="alex")

    assert not any(
        occurrence["task_id"] == "target"
        for occurrence in engine.state["occurrences"].values()
    )


async def test_weather_trigger_combines_attributes_edges_and_open_deduplication(hass):
    """A complex weather rule creates only on valid edges and respects open work."""
    task = {
        "enabled": True,
        "name": "Prevent ice",
        "assignee": "alex",
        "assignment": {"type": "fixed"},
        "schedule": {
            "type": "weather_trigger",
            "due_after": "00:30:00",
            "cooldown": "01:00:00",
            "skip_if_open": True,
        },
        "weather": {
            "logic": "all",
            "conditions": [
                {
                    "entity_id": "weather.home",
                    "attribute": "temperature",
                    "condition": "below",
                    "threshold": 1,
                },
                {
                    "entity_id": "weather.home",
                    "attribute": "precipitation_probability",
                    "condition": "above",
                    "threshold": 30,
                },
            ],
        },
    }
    engine, _items = await _native_engine(hass, {"ice": task})
    now = datetime(2026, 1, 10, 6, 0, tzinfo=UTC)
    hass.states.async_set(
        "weather.home",
        "rainy",
        {"temperature": 0, "precipitation_probability": 80},
    )

    await engine._scan_weather_tasks(now)
    await engine._scan_weather_tasks(now + timedelta(minutes=5))

    occurrences = list(engine.state["occurrences"].values())
    assert len(occurrences) == 1
    assert datetime.fromisoformat(occurrences[0]["due"]) == now + timedelta(minutes=30)

    hass.states.async_set(
        "weather.home",
        "cloudy",
        {"temperature": 0, "precipitation_probability": 10},
    )
    await engine._scan_weather_tasks(now + timedelta(minutes=10))
    hass.states.async_set(
        "weather.home",
        "rainy",
        {"temperature": 0, "precipitation_probability": 90},
    )
    await engine._scan_weather_tasks(now + timedelta(minutes=20))

    assert len(engine.state["occurrences"]) == 1


async def test_weather_season_and_vacation_policies_compose_safely(hass):
    """Weather matching cannot bypass season or household-mode restrictions."""
    task = {
        "enabled": True,
        "name": "Protect plants",
        "assignee": "alex",
        "assignment": {"type": "fixed"},
        "schedule": {
            "type": "weather_trigger",
            "due_after": "00:00:00",
            "cooldown": "24:00:00",
            "skip_if_open": True,
        },
        "weather": {
            "logic": "all",
            "conditions": [
                {
                    "entity_id": "sensor.outside_temperature",
                    "condition": "below",
                    "threshold": 2,
                }
            ],
        },
        "season": {"months": [11, 12, 1, 2]},
        "modes": {"vacation": "pause"},
    }
    engine, _items = await _native_engine(hass, {"plants": task})
    hass.states.async_set("sensor.outside_temperature", "-3")
    engine.state["household_mode"] = {
        "mode": "vacation",
        "policy": "pause",
        "until": None,
    }

    await engine._scan_weather_tasks(datetime(2026, 1, 10, 6, 0, tzinfo=UTC))

    assert engine.state["occurrences"] == {}
    assert engine.state["weather_matches"]["plants"] is False


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda task: task["weather"].update({"logic": "neither"}),
            "weather logic",
        ),
        (
            lambda task: task["weather"]["conditions"][0].update({"entity_id": ""}),
            "needs entity_id",
        ),
        (
            lambda task: task["weather"]["conditions"][0].update(
                {"condition": "roughly"}
            ),
            "invalid operator",
        ),
        (
            lambda task: task["schedule"].update({"cooldown": "-1:00:00"}),
            "invalid cooldown",
        ),
    ],
)
async def test_complex_weather_configuration_validation_is_atomic(
    hass,
    mutation,
    message,
):
    """Every invalid nested weather field produces an actionable error."""
    task = {
        "enabled": True,
        "name": "Weather task",
        "assignee": "alex",
        "assignment": {"type": "fixed"},
        "schedule": {
            "type": "weather_trigger",
            "due_after": "00:00:00",
            "cooldown": "24:00:00",
            "skip_if_open": True,
        },
        "weather": {
            "logic": "all",
            "conditions": [
                {
                    "entity_id": "sensor.temperature",
                    "condition": "below",
                    "threshold": 2,
                }
            ],
        },
    }
    mutation(task)
    config = initial_config()
    config["people"] = {"alex": {"name": "Alex", "notify": "notify.mobile_app_alex"}}
    config["tasks"] = {"weather": task}
    engine = HouseholdTaskEngine(hass, config)

    with pytest.raises(vol.Invalid, match=message):
        engine._validate_config()


async def test_first_frost_fans_out_once_per_person_and_winter(hass):
    """Forecast changes cannot duplicate a person's seasonal responsibility."""
    forecasts = [
        {"datetime": "2026-10-11T00:00:00+00:00", "templow": -2},
        {"datetime": "2026-10-12T00:00:00+00:00", "templow": -4},
    ]

    async def get_forecasts(_call):
        return {"weather.home": {"forecast": list(forecasts)}}

    hass.services.async_register(
        "weather",
        "get_forecasts",
        get_forecasts,
        supports_response=SupportsResponse.ONLY,
    )
    hass.states.async_set("weather.home", "cloudy")
    people = {
        "alex": {"name": "Alex", "notify": "notify.mobile_app_alex"},
        "sam": {"name": "Sam", "notify": "notify.mobile_app_sam"},
    }
    engine, _items = await _native_engine(
        hass, {"first_frost": _first_frost_task()}, people
    )

    await engine._scan_forecast_tasks(datetime(2026, 10, 10, 18, 0, tzinfo=UTC))
    await engine._scan_forecast_tasks(datetime(2026, 10, 10, 18, 5, tzinfo=UTC))

    occurrences = list(engine.state["occurrences"].values())
    assert len(occurrences) == 2
    assert {item["target_person"] for item in occurrences} == {"alex", "sam"}
    assert {item["assignee"] for item in occurrences} == {"alex", "sam"}
    assert {item["season_key"] for item in occurrences} == {"2026-2027"}
    assert {item["campaign_id"] for item in occurrences} == {"first_frost:2026-2027"}
    assert all(item["creation_trace"]["matched_period"] for item in occurrences)
    assert len(engine.state["seasonal_executions"]) == 2

    for item in occurrences:
        item["resolved"] = True
    forecasts[:] = [{"datetime": "2026-11-02T00:00:00+00:00", "templow": -6}]
    await engine._scan_forecast_tasks(datetime(2026, 11, 1, 18, 0, tzinfo=UTC))
    assert len(engine.state["occurrences"]) == 2

    forecasts[:] = [{"datetime": "2027-10-11T00:00:00+00:00", "templow": -1}]
    await engine._scan_forecast_tasks(datetime(2027, 10, 10, 18, 0, tzinfo=UTC))
    assert len(engine.state["occurrences"]) == 4
    assert {item["season_key"] for item in engine.state["occurrences"].values()} == {
        "2026-2027",
        "2027-2028",
    }


async def test_forecast_lead_time_may_start_before_target_season(hass):
    """A September evening can prepare for an in-season frost on October 1."""

    async def get_forecasts(_call):
        return {
            "weather.home": {
                "forecast": [{"datetime": "2026-10-01T00:00:00+00:00", "templow": -1}]
            }
        }

    hass.services.async_register(
        "weather",
        "get_forecasts",
        get_forecasts,
        supports_response=SupportsResponse.ONLY,
    )
    hass.states.async_set("weather.home", "cloudy")
    people = {
        "alex": {"name": "Alex", "notify": "notify.mobile_app_alex"},
        "sam": {"name": "Sam", "notify": "notify.mobile_app_sam"},
    }
    engine, _items = await _native_engine(
        hass, {"first_frost": _first_frost_task()}, people
    )

    await engine._scan_forecast_tasks(datetime(2026, 9, 30, 18, 0, tzinfo=UTC))

    assert len(engine.state["occurrences"]) == 2
    assert all(
        item["rule_reference"].startswith("2026-10-01")
        for item in engine.state["occurrences"].values()
    )


async def test_forecast_scan_reuses_provider_data_across_rules(hass):
    """One scan asks each provider/type pair once even with multiple rules."""
    calls = 0

    async def get_forecasts(_call):
        nonlocal calls
        calls += 1
        return {
            "weather.home": {
                "forecast": [{"datetime": "2026-10-11T00:00:00+00:00", "templow": -2}]
            }
        }

    hass.services.async_register(
        "weather",
        "get_forecasts",
        get_forecasts,
        supports_response=SupportsResponse.ONLY,
    )
    hass.states.async_set("weather.home", "cloudy")
    people = {
        "alex": {"name": "Alex", "notify": "notify.mobile_app_alex"},
        "sam": {"name": "Sam", "notify": "notify.mobile_app_sam"},
    }
    engine, _items = await _native_engine(
        hass,
        {
            "cars": _first_frost_task(),
            "garden": _first_frost_task(),
        },
        people,
    )

    await engine._scan_forecast_tasks(datetime(2026, 10, 10, 18, 0, tzinfo=UTC))

    assert calls == 1
    assert len(engine.state["occurrences"]) == 4


async def test_forecast_preview_is_side_effect_free_and_explains_each_target(hass):
    """Planning shows recipients and seasonal suppression without writing state."""

    async def get_forecasts(_call):
        return {
            "weather.home": {
                "forecast": [{"datetime": "2026-10-11T00:00:00+00:00", "templow": -2}]
            }
        }

    hass.services.async_register(
        "weather",
        "get_forecasts",
        get_forecasts,
        supports_response=SupportsResponse.ONLY,
    )
    hass.states.async_set("weather.home", "cloudy")
    people = {
        "alex": {"name": "Alex", "notify": "notify.mobile_app_alex"},
        "sam": {"name": "Sam", "notify": "notify.mobile_app_sam"},
    }
    task = _first_frost_task()
    engine, _items = await _native_engine(hass, {"first_frost": task}, people)
    engine.state["seasonal_executions"]["first_frost|2026-2027|alex"] = (
        "2026-10-01T18:00:00+00:00"
    )

    before = dict(engine.state["seasonal_executions"])
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "custom_components.household_tasks.engine.dt_util.now",
            lambda: datetime(2026, 10, 10, 18, 0, tzinfo=UTC),
        )
        preview = await engine.async_preview_task(task, "first_frost")

    assert engine.state["seasonal_executions"] == before
    assert preview["would_create"]
    assert preview["forecast"]["matched_period"]["date"] == "2026-10-11"
    assert [
        (item["target_person"], item["would_create"])
        for item in preview["planned_occurrences"]
    ] == [("alex", False), ("sam", True)]
    assert preview["trace"][-1]["message"].startswith("1 von 2")

    scenario = await engine.async_preview_task(
        task,
        "first_frost",
        {
            "date": "2026-10-12",
            "values": ["5"],
        },
    )
    assert scenario["forecast"]["scenario"]
    assert not scenario["forecast"]["allowed"]
    assert engine.state["seasonal_executions"] == before


async def test_seasonal_reset_is_scoped_persistent_and_undoable(hass):
    """An explicit reset touches one rule only and participates in Undo."""
    people = {
        "alex": {"name": "Alex", "notify": "notify.mobile_app_alex"},
        "sam": {"name": "Sam", "notify": "notify.mobile_app_sam"},
    }
    engine, _items = await _native_engine(
        hass,
        {
            "first_frost": _first_frost_task(),
            "other": _manual("Other"),
        },
        people,
    )
    engine.state["seasonal_executions"] = {
        "first_frost|2026-2027|alex": "one",
        "first_frost|2026-2027|sam": "two",
        "other|2026-2027|alex": "three",
    }

    assert await engine.async_reset_seasonal_executions("first_frost") == 2
    assert engine.state["seasonal_executions"] == {"other|2026-2027|alex": "three"}

    await engine.async_undo_last()
    assert set(engine.state["seasonal_executions"]) == {
        "first_frost|2026-2027|alex",
        "first_frost|2026-2027|sam",
        "other|2026-2027|alex",
    }


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda task: task["schedule"].update({"horizon_hours": 0}),
            "forecast horizon",
        ),
        (
            lambda task: task["weather"]["conditions"][0].update(
                {"entity_id": "sensor.outside"}
            ),
            r"weather\.\* entity",
        ),
        (
            lambda task: task["weather"]["conditions"][0].update({"attribute": ""}),
            "forecast conditions need an attribute",
        ),
        (
            lambda task: task.update({"season": {"months": []}}),
            "once-per-season repeat needs season months",
        ),
    ],
)
async def test_forecast_configuration_validation_is_actionable(hass, mutation, message):
    """Invalid forecast rules fail before they can reach the runtime scanner."""
    task = _first_frost_task()
    mutation(task)
    config = initial_config()
    config["people"] = {
        "alex": {"name": "Alex", "notify": "notify.mobile_app_alex"},
        "sam": {"name": "Sam", "notify": "notify.mobile_app_sam"},
    }
    config["tasks"] = {"forecast": task}
    engine = HouseholdTaskEngine(hass, config)

    with pytest.raises(vol.Invalid, match=message):
        engine._validate_config()


async def test_advanced_task_operations_cover_success_and_failure_paths(hass):
    """Favorites, batches, stacks, moves, and attachments remain transactional."""
    engine, _items = await _native_engine(
        hass,
        {"laundry": _manual("Laundry"), "dishes": _manual("Dishes")},
    )

    assert await engine.async_toggle_favorite("alex", "laundry")
    assert not await engine.async_toggle_favorite("alex", "laundry")
    with pytest.raises(vol.Invalid, match="Unknown person"):
        await engine.async_toggle_favorite("nobody", "laundry")
    with pytest.raises(vol.Invalid, match="Unknown task"):
        await engine.async_toggle_favorite("alex", "missing")

    previews = [
        {
            "name": "Laundry",
            "assignee": "alex",
            "due": "2026-08-01T18:00:00+00:00",
            "priority": "normal",
            "points": 1,
            "missing": [],
        },
        {
            "name": "Incomplete",
            "assignee": None,
            "due": "2026-08-01T18:00:00+00:00",
            "priority": "normal",
            "points": 1,
            "missing": ["assignee"],
        },
        {
            "name": "Rejected",
            "assignee": "alex",
            "due": "2026-08-01T18:00:00+00:00",
            "priority": "normal",
            "points": 1,
            "missing": [],
        },
    ]
    create_ad_hoc = AsyncMock(side_effect=[None, vol.Invalid("creation rejected")])
    with (
        patch.object(engine, "preview_task_batch", return_value=previews),
        patch.object(engine, "async_create_ad_hoc", create_ad_hoc),
    ):
        batch = await engine.async_create_batch("Laundry; Incomplete; Rejected")
    assert batch["created"] == [0]
    assert [item["index"] for item in batch["failed"]] == [1, 2]

    await engine.async_create_manual("laundry")
    occurrence_id = next(iter(engine.state["occurrences"]))
    move = await engine.async_move_occurrence(occurrence_id, "wenn Alex zuhause ist")
    assert move["kind"] == "presence"
    assert engine.state["occurrences"][occurrence_id]["waiting_for"]["person_id"] == (
        "alex"
    )
    with pytest.raises(vol.Invalid):
        await engine.async_move_occurrence(occurrence_id, "nonsense")

    attachment = await engine.async_add_attachment(
        occurrence_id, "", "image/png", "aGVsbG8="
    )
    assert attachment["name"] == "Anhang"
    assert engine.attachment_content(occurrence_id, attachment["id"])["size"] == 5
    with pytest.raises(vol.Invalid, match="Nur JPG"):
        await engine.async_add_attachment(
            occurrence_id, "bad.exe", "application/octet-stream", "aGVsbG8="
        )
    with pytest.raises(vol.Invalid, match="ungültig"):
        await engine.async_add_attachment(
            occurrence_id, "bad.png", "image/png", "not-base64"
        )
    with pytest.raises(vol.Invalid, match="Anhang nicht gefunden"):
        engine.attachment_content(occurrence_id, "missing")
    await engine.async_delete_attachment(occurrence_id, attachment["id"])
    with pytest.raises(vol.Invalid, match="Anhang nicht gefunden"):
        await engine.async_delete_attachment(occurrence_id, attachment["id"])

    with pytest.raises(vol.Invalid, match="Name und Vorlagen"):
        await engine.async_save_task_stack("empty", {"name": "", "task_ids": []})
    with pytest.raises(vol.Invalid, match="Unbekannte Vorlagen"):
        await engine.async_save_task_stack(
            "broken", {"name": "Broken", "task_ids": ["missing"]}
        )
    await engine.async_save_task_stack(
        "evening",
        {"name": " Evening ", "task_ids": ["laundry", "dishes", "laundry"]},
    )
    created = await engine.async_launch_task_stack("evening")
    assert len(created) == 2
    assert len(engine.state["occurrences"]) == 3
    await engine.async_save_task_stack("evening", None)
    with pytest.raises(vol.Invalid, match="Unbekannter Aufgabenstapel"):
        await engine.async_launch_task_stack("evening")

    with (
        patch.object(engine, "async_complete_occurrence", new=AsyncMock()),
        patch.object(engine, "async_snooze_occurrence", new=AsyncMock()),
        patch.object(engine, "async_request_help", new=AsyncMock()),
        patch.object(engine, "async_decline_occurrence", new=AsyncMock()),
    ):
        for action in ("complete", "tomorrow", "help", "decline"):
            result = await engine.async_bulk_occurrences(["one", "one"], action)
            assert result["completed"] == ["one"]
        rejected = await engine.async_bulk_occurrences(["one"], "unsupported")
    assert rejected["failed"]["one"] == "Unknown bulk action"


async def test_modes_gallery_discovery_and_undo_are_explainable(hass):
    """Advanced configuration helpers validate local context and support undo."""
    people = {
        "alex": {"name": "Alex", "notify": "notify.mobile_app_alex"},
        "sam": {"name": "Sam", "notify": "notify.mobile_app_sam"},
    }
    engine, _items = await _native_engine(hass, {"laundry": _manual("Laundry")}, people)
    hass.states.async_set(
        "sensor.phone_battery",
        "18",
        {"friendly_name": "Phone", "device_class": "battery"},
    )
    hass.states.async_set(
        "binary_sensor.washing_machine",
        "on",
        {"friendly_name": "Washing machine"},
    )
    hass.states.async_set("weather.home", "sunny", {"temperature": 20})

    suggestions = engine.discovery_suggestions()
    resource = next(item for item in suggestions if item["kind"] == "resource")
    appliance = next(item for item in suggestions if item["kind"] == "appliance")
    await engine.async_install_discovery_suggestion(
        resource["id"], "phone_battery", "alex"
    )
    await engine.async_install_discovery_suggestion(
        appliance["id"], "washer_done", "sam"
    )
    assert engine.monitors["resources"]["phone_battery"]["assignee"] == "alex"
    assert engine.tasks["washer_done"]["assignment"]["people"] == ["sam"]
    with pytest.raises(vol.Invalid, match="Unknown assignee"):
        await engine.async_install_discovery_suggestion(
            appliance["id"], "other", "nobody"
        )
    with pytest.raises(vol.Invalid, match="no longer available"):
        await engine.async_install_discovery_suggestion("missing", "other", "alex")

    with pytest.raises(vol.Invalid, match="Unknown household mode"):
        await engine.async_set_household_mode("invalid")
    with pytest.raises(vol.Invalid, match="policy"):
        await engine.async_set_household_mode("vacation", policy="invalid")
    with pytest.raises(vol.Invalid, match="delegate"):
        await engine.async_set_household_mode(
            "vacation", policy="delegate", delegate_to="nobody"
        )
    with pytest.raises(vol.Invalid, match="ISO"):
        await engine.async_set_household_mode("vacation", until="not-a-date")
    await engine.async_set_household_mode(
        "vacation",
        policy="delegate",
        delegate_to="sam",
        until=(datetime.now(UTC) + timedelta(days=2)).isoformat(),
        note=" Holiday ",
    )
    assert engine.state["household_mode"]["note"] == "Holiday"
    assert await engine.async_undo_last() == "Haushaltsmodus ändern"
    assert engine.state["household_mode"]["mode"] == "normal"

    await engine.async_install_gallery_template(
        "first_frost_personal_vehicle",
        "car_frost",
        None,
        "weather.home",
        people=["alex", "sam", "alex", "nobody"],
    )
    assert engine.tasks["car_frost"]["assignment"]["people"] == ["alex", "sam"]
    await engine.async_install_gallery_template(
        "frostschutz", "garden_frost", "alex", "weather.home"
    )
    assert engine.tasks["garden_frost"]["assignee"] == "alex"
    assert (
        engine.tasks["garden_frost"]["weather"]["conditions"][0]["entity_id"]
        == "weather.home"
    )
    with pytest.raises(vol.Invalid, match="Unknown gallery"):
        await engine.async_install_gallery_template("missing", "missing", "alex")
    with pytest.raises(vol.Invalid, match="target person"):
        await engine.async_install_gallery_template(
            "first_frost_personal_vehicle", "missing_people", None, people=[]
        )
    with pytest.raises(vol.Invalid, match="weather entity"):
        await engine.async_install_gallery_template(
            "frostschutz", "missing_weather", "alex", "weather.missing"
        )

    explanation = engine.explain_task("car_frost")
    assert explanation["configured_candidates"] == ["alex", "sam"]
    with pytest.raises(vol.Invalid, match="Unknown task_id"):
        engine.explain_task("missing")
    engine.state["undo_stack"] = []
    with pytest.raises(vol.Invalid, match="Keine Aktion"):
        await engine.async_undo_last()


def test_device_manual_urls_require_https(hass):
    """Device records never create downgrade links to insecure manuals."""
    config = initial_config()
    config["people"] = {"alex": {"name": "Alex", "notify": "notify.mobile_app_alex"}}
    config["tasks"] = {
        "valid": {
            **_manual("Valid"),
            "device": {
                "entity_id": "sensor.appliance",
                "manual_url": "https://example.com/manual.pdf",
            },
        }
    }
    hass.states.async_set("sensor.appliance", "ok")
    HouseholdTaskEngine(hass, config)._validate_config()

    config["tasks"]["valid"]["device"]["manual_url"] = "http://example.com/manual.pdf"
    engine = HouseholdTaskEngine(hass, config)
    with pytest.raises(vol.Invalid, match="must use HTTPS"):
        engine._validate_config()


def test_configuration_health_reports_actionable_combined_failures(hass):
    """The health report explains independent failures in one deterministic pass."""
    config = initial_config()
    config["people"] = {
        "alex": {
            "name": "Alex",
            "notify": "notify.mobile_app_missing",
            "presence": "person.missing",
        }
    }
    config["tasks"] = {
        "cycle_a": {
            **_manual("Duplicate"),
            "nfc": {"tag_id": "tag-a"},
            "follow_ups": [{"task_id": "cycle_b", "delay": "00:00:00"}],
        },
        "cycle_b": {
            **_manual("Duplicate"),
            "follow_ups": [{"task_id": "cycle_a", "delay": "00:00:00"}],
        },
        "forecast": {
            **_manual("Forecast"),
            "schedule": {"type": "forecast_trigger"},
            "weather": {
                "conditions": [
                    {
                        "entity_id": "weather.missing",
                        "attribute": "templow",
                        "condition": "below",
                        "threshold": 0,
                    }
                ]
            },
            "device": {"entity_id": "sensor.missing_device"},
            "season": {"entity_id": "sensor.missing_season"},
        },
    }
    engine = HouseholdTaskEngine(hass, config)

    health = engine.configuration_health()
    codes = {finding["code"] for finding in health["findings"]}

    assert health["status"] == "critical"
    assert {
        "presence_missing",
        "notify_missing",
        "forecast_service_missing",
        "weather_entity_missing",
        "device_entity_missing",
        "season_entity_missing",
        "nfc_verify",
        "dependency_cycle",
        "duplicate_name",
    } <= codes
    assert all("action" in finding for finding in health["findings"])

    engine.state["household_mode"] = {
        "mode": "vacation",
        "until": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
    }
    assert engine._current_household_mode()["mode"] == "normal"


def test_configuration_validation_aggregates_complex_rule_errors(hass):
    """One validation pass reports independent expert-rule mistakes together."""
    config = initial_config()
    config["people"] = {"alex": {"name": "Alex", "notify": "notify.mobile_app_alex"}}
    config["tasks"] = {
        "forecast": {
            **_manual("Forecast"),
            "schedule": {
                "type": "forecast_trigger",
                "time": "invalid",
                "horizon_hours": 0,
                "lead_days": 8,
            },
            "weather": {
                "logic": "xor",
                "conditions": [
                    "invalid",
                    {
                        "entity_id": "invalid",
                        "condition": "invalid",
                        "threshold": None,
                    },
                ],
            },
        },
        "completion": {
            **_manual("Completion"),
            "schedule": {
                "type": "after_completion",
                "interval": "00:00:00",
                "start": "invalid",
            },
        },
        "flexible": {
            **_manual("Flexible"),
            "schedule": {
                "type": "flexible_after_completion",
                "earliest_interval": "24:00:00",
                "preferred_interval": "12:00:00",
                "latest_interval": "bad",
                "start": "invalid",
            },
        },
        "metadata": {
            **_manual("Metadata"),
            "paused_until": "not-a-date",
            "follow_ups": [
                "invalid",
                {"task_id": "metadata", "delay": "-01:00:00"},
            ],
            "market": {"priority": "urgent", "points": -1},
            "modes": {"vacation": "invalid"},
            "season": {
                "months": ["invalid"],
                "condition": "invalid",
            },
            "device": "invalid",
            "repeat": {"mode": "invalid"},
            "escalation": [
                {
                    "after": "invalid",
                    "relative_to": "invalid",
                    "action": "invalid",
                }
            ],
        },
        "invalid_sections": {
            **_manual("Invalid sections"),
            "follow_ups": "invalid",
            "market": "invalid",
            "modes": "invalid",
            "season": "invalid",
            "weather": "invalid",
        },
    }
    config["monitors"] = {
        "printers": {"enabled": True, "assignee": "missing"},
        "resources": "invalid",
    }
    config["defaults"]["nfc_feedback"] = "invalid"
    config["defaults"]["weekly_summary"] = "invalid"
    config["defaults"]["notification_digest"] = "invalid"
    engine = HouseholdTaskEngine(hass, config)

    with pytest.raises(vol.Invalid) as error:
        engine._validate_config()

    message = str(error.value)
    assert "forecast trigger needs a valid time" in message
    assert "completion interval must be positive" in message
    assert "flexible intervals must be positive and ordered" in message
    assert "follow-up delay is invalid" in message
    assert "paused_until must be an ISO date-time" in message
    assert "device file must be a mapping" in message
    assert "weather rule must be a mapping" in message
    assert "resource monitors must be a mapping" in message
    assert "Notification digest settings must be a mapping" in message


async def test_native_checklist_dependency_history_and_revision_conflicts(hass):
    """Complex native task state remains consistent across chained writes."""
    tasks = {
        "prepare": {
            **_manual("Prepare workspace"),
            "checklist": [
                {"id": "tools", "title": "Get tools"},
                {"id": "cover", "title": "Cover floor"},
            ],
        },
        "paint": {
            **_manual("Paint room"),
            "depends_on": ["prepare"],
        },
        "optional_checklist": {
            **_manual("Optional checklist"),
            "checklist": [{"id": "optional", "title": "Optional step"}],
            "require_checklist_completion": False,
        },
    }
    engine, _ = await _native_engine(hass, tasks)

    await engine.async_create_manual("prepare")
    await engine.async_create_manual("paint")
    await engine.async_create_manual("optional_checklist")
    prepare_id = next(
        occurrence_id
        for occurrence_id, item in engine.state["occurrences"].items()
        if item["task_id"] == "prepare"
    )
    paint_id = next(
        occurrence_id
        for occurrence_id, item in engine.state["occurrences"].items()
        if item["task_id"] == "paint"
    )
    optional_id = next(
        occurrence_id
        for occurrence_id, item in engine.state["occurrences"].items()
        if item["task_id"] == "optional_checklist"
    )
    prepare = engine.state["occurrences"][prepare_id]
    paint = engine.state["occurrences"][paint_id]

    assert paint["status"] == "blocked"
    with pytest.raises(vol.Invalid, match="Abhängigkeiten"):
        await engine.async_complete_occurrence(paint_id)
    with pytest.raises(vol.Invalid, match="Checklistenpunkt"):
        await engine.async_complete_occurrence(prepare_id)

    first_revision = prepare["revision"]
    await engine.async_set_checklist_item(
        prepare_id,
        "tools",
        True,
        expected_revision=first_revision,
    )
    with pytest.raises(vol.Invalid, match="zwischenzeitlich"):
        await engine.async_set_checklist_item(
            prepare_id,
            "cover",
            True,
            expected_revision=first_revision,
        )
    await engine.async_set_checklist_item(
        prepare_id,
        "cover",
        True,
        expected_revision=prepare["revision"],
    )
    await engine.async_set_occurrence_status(
        prepare_id,
        "completed",
        expected_revision=prepare["revision"],
    )

    assert prepare["status"] == "completed"
    assert paint["status"] == "open"
    assert paint["revision"] == 2
    assert {event["type"] for event in engine.task_history(prepare_id)} >= {
        "task_created",
        "checklist_item_completed",
        "task_completed",
    }

    with pytest.raises(vol.Invalid, match="nicht verändert"):
        await engine.async_set_occurrence_status(prepare_id, "open")
    with pytest.raises(vol.Invalid, match="nicht verändert"):
        await engine.async_set_checklist_item(prepare_id, "tools", False)
    with pytest.raises(vol.Invalid, match="nicht verändert"):
        await engine.async_set_occurrence_dependencies(prepare_id, [])

    await engine.async_set_occurrence_status(
        paint_id,
        "cancelled",
        expected_revision=paint["revision"],
    )
    assert paint["status"] == "cancelled"
    assert paint["resolution_reason"] == "cancelled"
    with pytest.raises(vol.Invalid, match="nicht verändert"):
        await engine.async_set_occurrence_status(paint_id, "open")

    await engine.async_complete_occurrence(optional_id)
    assert engine.state["occurrences"][optional_id]["status"] == "completed"


async def test_native_runtime_dependencies_reject_cycles_and_manual_blocked_status(
    hass,
):
    """Only the dependency graph may determine blocked state and it stays acyclic."""
    tasks = {
        "one": _manual("One"),
        "two": _manual("Two"),
    }
    engine, _ = await _native_engine(hass, tasks)
    await engine.async_create_manual("one")
    await engine.async_create_manual("two")
    ids = {
        occurrence["task_id"]: occurrence_id
        for occurrence_id, occurrence in engine.state["occurrences"].items()
    }

    await engine.async_set_occurrence_dependencies(ids["one"], [ids["two"]])
    assert engine.state["occurrences"][ids["one"]]["status"] == "blocked"

    with pytest.raises(vol.Invalid, match="Zyklus"):
        await engine.async_set_occurrence_dependencies(ids["two"], [ids["one"]])
    with pytest.raises(vol.Invalid, match="ausschließlich"):
        await engine.async_set_occurrence_status(ids["two"], "blocked")
    with pytest.raises(vol.Invalid, match="blockieren"):
        await engine.async_set_occurrence_status(ids["one"], "in_progress")
