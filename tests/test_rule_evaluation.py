"""Tests for rule effectiveness, noise detection, and generated boundaries."""

from datetime import UTC, datetime, timedelta

from custom_components.household_tasks.rule_evaluation import (
    build_rule_insights,
    counterexample_scenarios,
)


def test_rule_insights_measure_effect_and_propose_noise_reduction():
    now = datetime(2026, 8, 11, 12, tzinfo=UTC)
    tasks = {
        "laundry": {
            "name": "Laundry",
            "schedule": {"type": "state_trigger", "cooldown": "01:00:00"},
        }
    }
    occurrences = {}
    for index in range(10):
        occurrence = {
            "task_id": "laundry",
            "task": {"name": "Laundry"},
            "created_at": (now - timedelta(days=index)).isoformat(),
            "due": (now - timedelta(days=index, hours=2)).isoformat(),
            "resolved": index < 3,
        }
        if index < 3:
            occurrence.update(
                {
                    "resolved_at": (now - timedelta(days=index, hours=1)).isoformat(),
                    "resolution_reason": "completed",
                }
            )
        occurrences[str(index)] = occurrence
    events = [
        {
            "occurrence_id": str(index),
            "type": "task_snoozed",
            "occurred_at": (now - timedelta(days=index)).isoformat(),
        }
        for index in range(3)
    ]
    result = build_rule_insights(tasks, occurrences, events, [], [], now=now)
    effect = result["effects"]["laundry"]
    assert effect["generated"] == 10
    assert effect["completed"] == 3
    assert effect["completion_rate"] == 30
    assert {item["type"] for item in result["noise_findings"]} == {
        "low_completion",
        "overdue_backlog",
    }
    assert {item["type"] for item in result["improvement_suggestions"]} == {
        "increase_cooldown",
        "adjust_time",
    }


def test_counterexamples_cover_weather_presence_season_and_vacation():
    task = {
        "assignment": {
            "type": "fair",
            "people": ["alex"],
            "presence_required": True,
        },
        "weather": {
            "logic": "all",
            "conditions": [
                {
                    "entity_id": "sensor.temperature",
                    "condition": "below",
                    "threshold": 5,
                }
            ],
        },
        "season": {"months": [10, 11, 12, 1, 2, 3]},
    }
    examples = counterexample_scenarios(task, datetime(2026, 11, 1, 12, tzinfo=UTC))
    assert {item["id"] for item in examples} == {
        "weather_boundary_0",
        "nobody_home",
        "outside_season",
        "vacation_mode",
    }
