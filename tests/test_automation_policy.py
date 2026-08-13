"""Tests for advanced execution, privacy, and notification policies."""

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from custom_components.household_tasks.automation_policy import (
    energy_decision,
    evaluate_conditions,
    notification_policy_decision,
    redact_private_occurrence,
    replacement_candidates,
    visible_to,
    wait_state_decision,
)

BERLIN = ZoneInfo("Europe/Berlin")


def test_condition_groups_support_numeric_attributes_and_any_logic():
    states = {
        "sensor.energy": {
            "state": "0.31",
            "attributes": {"surplus": 1800},
        },
        "sensor.mode": {"state": "eco", "attributes": {}},
    }
    result = evaluate_conditions(
        [
            {"entity_id": "sensor.energy", "condition": "below", "value": 0.25},
            {
                "entity_id": "sensor.energy",
                "attribute": "surplus",
                "condition": "at_least",
                "value": 1500,
            },
        ],
        states,
        match="any",
    )
    assert result["allowed"] is True
    assert [item["matched"] for item in result["conditions"]] == [False, True]


def test_wait_state_timeout_can_release_or_cancel():
    now = datetime(2026, 8, 13, 12, tzinfo=BERLIN)
    base = {
        "enabled": True,
        "conditions": [
            {
                "entity_id": "sensor.delivery",
                "condition": "equals",
                "value": "delivered",
            }
        ],
        "timeout_hours": 2,
    }
    states = {"sensor.delivery": {"state": "shipping", "attributes": {}}}
    release = wait_state_decision(
        {**base, "timeout_action": "release"},
        states,
        now=now,
        waiting_since=datetime(2026, 8, 13, 9, tzinfo=BERLIN),
    )
    cancel = wait_state_decision(
        {**base, "timeout_action": "cancel"},
        states,
        now=now,
        waiting_since=datetime(2026, 8, 13, 9, tzinfo=BERLIN),
    )
    assert release["waiting"] is False
    assert release["reason"] == "timeout_release"
    assert cancel["waiting"] is True
    assert cancel["reason"] == "timeout_cancel"


def test_energy_policy_combines_tariff_surplus_and_overnight_window():
    policy = {
        "enabled": True,
        "tariff_entity": "sensor.price",
        "max_price": 0.25,
        "surplus_entity": "sensor.surplus",
        "min_surplus": 1000,
        "preferred_start": "22:00",
        "preferred_end": "06:00",
    }
    states = {
        "sensor.price": {"state": "0.18", "attributes": {}},
        "sensor.surplus": {"state": "1500", "attributes": {}},
    }
    assert energy_decision(
        policy, states, now=datetime(2026, 8, 13, 23, tzinfo=BERLIN)
    )["allowed"]
    assert not energy_decision(
        policy, states, now=datetime(2026, 8, 13, 12, tzinfo=BERLIN)
    )["allowed"]


def test_quiet_hours_budget_and_critical_bypass_are_explainable():
    policy = {
        "quiet_start": "22:00",
        "quiet_end": "07:00",
        "daily_budget": 2,
        "quiet_behavior": "defer",
        "critical_bypass": True,
    }
    quiet = notification_policy_decision(
        policy,
        now=datetime(2026, 8, 13, 23, tzinfo=BERLIN),
        sent_today=0,
        priority="normal",
    )
    exhausted = notification_policy_decision(
        policy,
        now=datetime(2026, 8, 13, 12, tzinfo=BERLIN),
        sent_today=2,
        priority="normal",
    )
    critical = notification_policy_decision(
        policy,
        now=datetime(2026, 8, 13, 23, tzinfo=BERLIN),
        sent_today=2,
        priority="critical",
    )
    assert quiet["reason"] == "quiet_hours" and not quiet["allowed"]
    assert exhausted["reason"] == "budget_exhausted" and not exhausted["allowed"]
    assert critical["reason"] == "critical_bypass" and critical["allowed"]


def test_private_visibility_and_detail_redaction():
    task = {
        "visibility": {
            "level": "people",
            "people": ["alex"],
            "hide_details": True,
        }
    }
    occurrence = {
        "assignee": "sam",
        "task": task,
        "title": "Medication",
        "description": "Sensitive",
        "checklist": [{"title": "Dose"}],
    }
    assert visible_to(task, occurrence, viewer_person="alex", is_admin=False)
    assert not visible_to(task, occurrence, viewer_person="kim", is_admin=False)
    redacted = redact_private_occurrence(
        occurrence, viewer_person="kim", is_admin=False
    )
    assert redacted["title"] == "Private Aufgabe"
    assert redacted["description"] is None
    assert redacted["checklist"] == []


def test_replacement_candidates_stay_in_domain_and_rank_similar_names():
    states = {
        "sensor.outdoor_temperature": SimpleNamespace(
            attributes={"friendly_name": "Outdoor temperature"}
        ),
        "sensor.living_room": SimpleNamespace(
            attributes={"friendly_name": "Living room"}
        ),
        "binary_sensor.outdoor_temperature": SimpleNamespace(attributes={}),
    }
    candidates = replacement_candidates("sensor.outside_temperature", states)
    assert candidates[0]["entity_id"] == "sensor.outdoor_temperature"
    assert all(item["entity_id"].startswith("sensor.") for item in candidates)
