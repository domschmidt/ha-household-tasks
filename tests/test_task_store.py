"""Tests for the native, versioned task store."""

from datetime import UTC, datetime

from custom_components.household_tasks.task_store import (
    MAX_JOURNAL_ENTRIES,
    TASK_SCHEMA_VERSION,
    append_event,
    has_dependency_cycle,
    migrate_state,
    normalize_occurrence,
    task_store_health,
)

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def test_legacy_occurrence_migrates_without_external_identifier():
    """Legacy mirror metadata becomes a complete native task occurrence."""
    state = {
        "occurrences": {
            "task-1": {
                "uid": "external-item-42",
                "title": "Frostschutz prüfen",
                "resolved": False,
                "task": {"checklist": ["Kühlmittel", {"name": "Scheibenwasser"}]},
            }
        }
    }

    migrated = migrate_state(state, now=NOW)
    occurrence = migrated["occurrences"]["task-1"]

    assert migrated["task_schema_version"] == TASK_SCHEMA_VERSION
    assert "uid" not in occurrence
    assert occurrence["status"] == "open"
    assert occurrence["revision"] == 1
    assert occurrence["created_at"] == NOW.isoformat()
    assert occurrence["checklist"] == [
        {"id": "step_1", "title": "Kühlmittel", "completed": False},
        {"id": "step_2", "title": "Scheibenwasser", "completed": False},
    ]
    assert migrated["task_events"][0]["type"] == "store_migrated"
    assert state["occurrences"]["task-1"]["uid"] == "external-item-42"


def test_normalization_preserves_native_audit_fields_and_terminal_state():
    """Native data survives an idempotent migration."""
    occurrence = normalize_occurrence(
        "done",
        {
            "status": "completed",
            "revision": 4,
            "updated_at": "2026-09-30T10:00:00+00:00",
            "checklist": [
                {
                    "id": "proof",
                    "title": "Foto anhängen",
                    "completed": True,
                    "completed_at": "2026-09-30T09:00:00+00:00",
                    "completed_by": "alex",
                }
            ],
            "dependencies": ["done", "prerequisite"],
        },
        migrated_at=NOW.isoformat(),
    )

    assert occurrence["resolved"] is True
    assert occurrence["revision"] == 4
    assert occurrence["dependencies"] == ["prerequisite"]
    assert occurrence["checklist"][0]["completed_by"] == "alex"


def test_journal_is_bounded_and_copies_details():
    """The audit trail has a deterministic storage bound and immutable input."""
    state = {}
    details = {"status": "open"}
    for index in range(MAX_JOURNAL_ENTRIES + 3):
        append_event(
            state,
            event_type="task_status_changed",
            occurred_at=f"2026-10-01T12:00:{index:04d}+00:00",
            occurrence_id="task-1",
            actor="alex",
            details=details,
        )
    details["status"] = "mutated"

    assert len(state["task_events"]) == MAX_JOURNAL_ENTRIES
    assert state["task_events"][0]["occurred_at"].endswith("0003+00:00")
    assert state["task_events"][-1]["details"]["status"] == "open"


def test_health_reports_all_native_integrity_failures():
    """Corrupt statuses, references, and checklist IDs are actionable."""
    state = {
        "task_schema_version": TASK_SCHEMA_VERSION,
        "task_events": [{"id": "event"}],
        "occurrences": {
            "broken": {
                "status": "mystery",
                "dependencies": ["missing"],
                "checklist": [
                    {"id": "same", "title": "One"},
                    {"id": "same", "title": "Two"},
                ],
            }
        },
    }

    health = task_store_health(state)

    assert health["occurrence_count"] == 1
    assert health["journal_entries"] == 1
    assert {finding["code"] for finding in health["findings"]} == {
        "invalid_task_status",
        "orphan_dependencies",
        "duplicate_checklist_ids",
    }


def test_invalid_legacy_shapes_receive_safe_defaults():
    """Unexpected old values do not prevent startup migration."""
    migrated = migrate_state(
        {
            "task_schema_version": TASK_SCHEMA_VERSION,
            "occurrences": {
                "odd": {
                    "status": "invalid",
                    "resolved": True,
                    "revision": 0,
                    "checklist": [None, {"id": "", "title": ""}],
                },
                "ignored": "not-a-mapping",
            },
        },
        now=NOW,
    )

    assert set(migrated["occurrences"]) == {"odd"}
    assert migrated["occurrences"]["odd"]["status"] == "completed"
    assert migrated["occurrences"]["odd"]["revision"] == 1
    assert migrated["occurrences"]["odd"]["checklist"][0]["title"] == "Schritt 1"
    assert migrated["task_events"] == []


def test_dependency_cycle_detection_covers_direct_and_transitive_cycles():
    """Runtime dependency edits cannot introduce circular task chains."""
    occurrences = {
        "one": {"dependencies": ["two"]},
        "two": {"dependencies": ["three"]},
        "three": {"dependencies": []},
    }

    assert has_dependency_cycle(occurrences, "three", ["one"]) is True
    assert has_dependency_cycle(occurrences, "three", ["two"]) is True
    assert has_dependency_cycle(occurrences, "three", []) is False
    assert has_dependency_cycle(occurrences, "one", ["missing"]) is False
