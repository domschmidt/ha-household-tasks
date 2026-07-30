"""Runtime tests for chained tasks and combined weather configurations."""

from datetime import UTC, datetime, timedelta

import pytest
import voluptuous as vol
from homeassistant.core import SupportsResponse

from custom_components.household_tasks.bootstrap import initial_config
from custom_components.household_tasks.engine import HouseholdTaskEngine


async def _engine_with_todo(hass, tasks, people=None):
    """Build a real engine against an in-memory Home Assistant to-do service."""
    items = []

    async def get_items(_call):
        return {
            "todo.household": {
                "items": [item for item in items if item["status"] == "needs_action"]
            }
        }

    async def add_item(call):
        items.append(
            {
                "uid": f"item-{len(items) + 1}",
                "summary": call.data["item"],
                "due": call.data["due_datetime"],
                "status": "needs_action",
            }
        )

    hass.services.async_register(
        "todo",
        "get_items",
        get_items,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register("todo", "add_item", add_item)
    hass.states.async_set("todo.household", "0")
    config = initial_config("todo.household")
    config["people"] = people or {
        "alex": {
            "name": "Alex",
            "notify": "notify.mobile_app_alex",
        }
    }
    config["tasks"] = tasks
    engine = HouseholdTaskEngine(hass, config)
    engine._validate_config()
    return engine, items


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
    engine, _items = await _engine_with_todo(hass, tasks)

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
    engine, _items = await _engine_with_todo(hass, {"ice": task})
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
    engine, _items = await _engine_with_todo(hass, {"plants": task})
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
    config = initial_config("todo.household")
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
    engine, _items = await _engine_with_todo(
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
    engine, _items = await _engine_with_todo(
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
    engine, _items = await _engine_with_todo(
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
    engine, _items = await _engine_with_todo(hass, {"first_frost": task}, people)
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
    engine, _items = await _engine_with_todo(
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
    config = initial_config("todo.household")
    config["people"] = {
        "alex": {"name": "Alex", "notify": "notify.mobile_app_alex"},
        "sam": {"name": "Sam", "notify": "notify.mobile_app_sam"},
    }
    config["tasks"] = {"forecast": task}

    with pytest.raises(vol.Invalid, match=message):
        HouseholdTaskEngine(hass, config)._validate_config()
