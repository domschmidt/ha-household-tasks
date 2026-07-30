"""Tests for planning, learning, conflict, and safety helpers."""

from datetime import UTC, datetime

from custom_components.household_tasks.productivity import (
    configuration_conflicts,
    contextual_home,
    habit_suggestions,
    parse_natural_move,
    parse_task_batch,
    schedule_projection,
)

PEOPLE = {
    "alex": {"name": "Alex"},
    "sam": {"name": "Sam"},
}


def test_natural_move_understands_relative_and_presence_phrases():
    """Natural moves support concrete due dates and a local presence condition."""
    now = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)

    tomorrow = parse_natural_move("morgen nach dem Essen", now, PEOPLE)
    present = parse_natural_move("wenn Alex zuhause ist", now, PEOPLE)
    exact = parse_natural_move("2026-08-03 17:30", now, PEOPLE)

    assert tomorrow["due"] == "2026-07-31T19:00:00+00:00"
    assert present == {
        "kind": "presence",
        "person_id": "alex",
        "label": "Wenn Alex zuhause ist",
    }
    assert exact["due"] == "2026-08-03T17:30:00+00:00"


def test_batch_capture_parses_separate_smart_tasks():
    """Newlines and semicolons produce independent editable previews."""
    previews = parse_task_batch(
        "Müll morgen 18 Uhr an Alex; Pflanzen heute Abend an Sam",
        PEOPLE,
        datetime(2026, 7, 30, 10, 0, tzinfo=UTC),
    )

    assert [item["assignee"] for item in previews] == ["alex", "sam"]
    assert [item["name"] for item in previews] == ["Müll", "Pflanzen"]


def test_habits_are_transparent_and_need_two_samples():
    """Learned recommendations expose their sample count and remain local."""
    occurrences = {
        "one": {
            "task_id": "waste",
            "resolved": True,
            "resolution_reason": "completed",
            "resolved_at": "2026-07-01T18:00:00+00:00",
            "completed_by": "alex",
        },
        "two": {
            "task_id": "waste",
            "resolved": True,
            "resolution_reason": "completed",
            "resolved_at": "2026-07-08T20:00:00+00:00",
            "completed_by": "alex",
        },
        "single": {
            "task_id": "bath",
            "resolved": True,
            "resolution_reason": "completed",
            "resolved_at": "2026-07-02T10:00:00+00:00",
            "completed_by": "sam",
        },
    }

    habits = habit_suggestions(occurrences, PEOPLE)

    assert habits["waste"]["assignee"] == "alex"
    assert habits["waste"]["hour"] == 19
    assert habits["waste"]["samples"] == 2
    assert "bath" not in habits


def test_conflicts_find_similar_names_and_duplicate_nfc_tags():
    """Configuration diagnostics catch common accidental duplicates."""
    tasks = {
        "waste_a": {"name": "Müll rausbringen", "nfc": {"tag_id": "tag-1"}},
        "waste_b": {"name": "Müll-rausbringen", "nfc": {"tag_id": "tag-1"}},
    }

    findings = configuration_conflicts(tasks)

    assert {item["code"] for item in findings} == {
        "duplicate_name",
        "duplicate_nfc",
    }


def test_projection_warns_about_high_frequency_rules():
    """Pre-save volume estimates flag schedules producing many tasks."""
    projection = schedule_projection(
        {
            "schedule": {
                "type": "after_completion",
                "interval": "08:00:00",
            }
        }
    )

    assert projection["per_week"] == 21
    assert projection["risk"] == "high"


def test_contextual_home_changes_with_time_of_day():
    """The start page explains its morning, daytime, and evening focus."""
    occurrences = [{"id": "one", "due": "2026-07-30T18:00:00+00:00"}]

    morning = contextual_home(datetime(2026, 7, 30, 8, tzinfo=UTC), occurrences)
    evening = contextual_home(datetime(2026, 7, 30, 20, tzinfo=UTC), occurrences)

    assert morning["mode"] == "plan"
    assert evening["mode"] == "evening"
    assert morning["occurrence_ids"] == ["one"]
