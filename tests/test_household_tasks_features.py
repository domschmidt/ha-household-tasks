"""Tests for household modes, seasonal rules, and curated templates."""

from datetime import UTC, datetime

from custom_components.household_tasks.features import (
    dependency_cycles,
    mode_decision,
    season_decision,
    task_activation_decision,
    template_gallery,
)


def test_task_activation_supports_temporary_and_permanent_pauses():
    """A pause blocks only references before its configured end."""
    before = datetime(2026, 8, 3, 8, tzinfo=UTC)
    after = datetime(2026, 8, 5, 8, tzinfo=UTC)
    task = {"enabled": True, "paused_until": "2026-08-04T12:00:00+00:00"}

    assert task_activation_decision(task, before)["code"] == "template_paused"
    assert task_activation_decision(task, after)["allowed"]
    assert not task_activation_decision({"enabled": False}, after)["allowed"]
    assert not task_activation_decision({"paused_until": "invalid"}, after)["allowed"]


def test_vacation_modes_pause_reduce_and_delegate():
    """Vacation policies deliberately pause, filter, or delegate tasks."""
    task = {"market": {"priority": "normal"}}
    paused = mode_decision({"mode": "vacation", "policy": "pause"}, task)
    assert not paused["allowed"]
    assert paused["code"] == "vacation_paused"

    reduced = mode_decision({"mode": "vacation", "policy": "reduce"}, task)
    essential = mode_decision(
        {"mode": "vacation", "policy": "reduce"},
        {"market": {"priority": "critical"}},
    )
    assert not reduced["allowed"]
    assert essential["allowed"]

    delegated = mode_decision(
        {"mode": "vacation", "policy": "delegate", "delegate_to": "sam"},
        task,
    )
    assert delegated["allowed"]
    assert delegated["delegate_to"] == "sam"


def test_guest_only_and_guest_skip_are_explicit():
    """Guest-only and guest-suppressed templates behave predictably."""
    assert not mode_decision({"mode": "normal"}, {"modes": {"guest_only": True}})[
        "allowed"
    ]
    assert mode_decision({"mode": "guest"}, {"modes": {"guest_only": True}})["allowed"]
    assert not mode_decision({"mode": "guest"}, {"modes": {"skip_in_guest": True}})[
        "allowed"
    ]


def test_seasonal_month_and_entity_thresholds():
    """Seasonal rules combine a month window with an optional sensor condition."""
    winter = datetime(2026, 1, 15, tzinfo=UTC)
    summer = datetime(2026, 7, 15, tzinfo=UTC)
    task = {
        "season": {
            "months": [11, 12, 1, 2],
            "entity_id": "sensor.outside",
            "condition": "below",
            "threshold": 2,
        }
    }
    assert season_decision(task, winter, "-1")["allowed"]
    assert not season_decision(task, winter, "8")["allowed"]
    assert season_decision(task, summer, "-1")["code"] == "outside_season"


def test_dependency_cycles_and_gallery_contract():
    """Cycles are discoverable and gallery entries are safe isolated copies."""
    tasks = {
        "a": {"follow_ups": [{"task_id": "b"}]},
        "b": {"follow_ups": [{"task_id": "a"}]},
    }
    assert dependency_cycles(tasks) == [["a", "b", "a"]]

    gallery = template_gallery()
    assert {"frostschutz", "pollenfilter", "reifenwechsel", "gaestezimmer"} <= {
        item["id"] for item in gallery
    }
    gallery[0]["task"]["name"] = "changed"
    assert template_gallery()[0]["task"]["name"] != "changed"


def test_deep_dependency_graphs_distinguish_diamonds_and_cycles():
    """Shared downstream tasks are valid while deep back edges remain detectable."""
    diamond = {
        "start": {"follow_ups": [{"task_id": "left"}, {"task_id": "right"}]},
        "left": {"follow_ups": [{"task_id": "finish"}]},
        "right": {"follow_ups": [{"task_id": "finish"}]},
        "finish": {"follow_ups": []},
    }
    assert dependency_cycles(diamond) == []

    diamond["finish"]["follow_ups"] = [{"task_id": "start"}]
    cycles = dependency_cycles(diamond)
    assert any(cycle[0] == "start" and cycle[-1] == "start" for cycle in cycles)


def test_gallery_contains_weather_and_complex_household_presets():
    """The curated gallery covers common weather and climate routines."""
    gallery = {entry["id"]: entry for entry in template_gallery()}

    expected = {
        "frostschutz",
        "hitzeschutz_garten",
        "sturm_sichern",
        "regen_fenster",
        "frost_und_niederschlag",
        "luften_bei_feuchte",
        "uv_schutz",
        "schnee_raeumen",
        "haustiere_hitze",
        "first_frost_personal_vehicle",
    }
    assert expected <= set(gallery)
    assert len(gallery["frost_und_niederschlag"]["task"]["weather"]["conditions"]) == 2
    assert gallery["schnee_raeumen"]["task"]["weather"]["logic"] == "any"
    frost = gallery["first_frost_personal_vehicle"]["task"]
    assert frost["schedule"]["type"] == "forecast_trigger"
    assert frost["assignment"]["type"] == "per_person"
    assert frost["repeat"]["mode"] == "once_per_season"
