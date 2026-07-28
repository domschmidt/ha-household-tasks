"""Tests for completion, state and weekly workflow decisions."""

import importlib.util
import unittest
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

MODULE_PATH = (
    Path(__file__).parents[1] / "custom_components" / "household_tasks" / "workflows.py"
)
SPEC = importlib.util.spec_from_file_location("household_tasks_workflows", MODULE_PATH)
workflows = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(workflows)


class WorkflowTests(unittest.TestCase):
    def test_completion_due_uses_actual_completion(self):
        completed = datetime(2026, 7, 27, 20, tzinfo=UTC)
        self.assertEqual(
            workflows.completion_due(completed, timedelta(days=7)),
            datetime(2026, 8, 3, 20, tzinfo=UTC),
        )

    def test_state_trigger_respects_open_task_and_cooldown(self):
        now = datetime(2026, 7, 27, 20, tzinfo=UTC)
        self.assertFalse(
            workflows.state_trigger_allowed(
                happened_at=now,
                previous=None,
                cooldown=timedelta(0),
                has_open_occurrence=True,
                skip_if_open=True,
            )
        )
        self.assertFalse(
            workflows.state_trigger_allowed(
                happened_at=now,
                previous=now - timedelta(minutes=5),
                cooldown=timedelta(minutes=10),
                has_open_occurrence=False,
                skip_if_open=True,
            )
        )
        self.assertTrue(
            workflows.state_trigger_allowed(
                happened_at=now,
                previous=now - timedelta(minutes=11),
                cooldown=timedelta(minutes=10),
                has_open_occurrence=False,
                skip_if_open=True,
            )
        )

    def test_weekly_summary_only_fires_once_after_time(self):
        before = datetime(2026, 8, 2, 17, tzinfo=UTC)
        after = datetime(2026, 8, 2, 18, tzinfo=UTC)
        self.assertIsNone(
            workflows.weekly_summary_due(
                now=before,
                weekday=6,
                scheduled_time=time(18),
                last_summary_id=None,
            )
        )
        summary_id = workflows.weekly_summary_due(
            now=after,
            weekday=6,
            scheduled_time=time(18),
            last_summary_id=None,
        )
        self.assertEqual(summary_id, "2026-W31")
        self.assertIsNone(
            workflows.weekly_summary_due(
                now=after,
                weekday=6,
                scheduled_time=time(18),
                last_summary_id=summary_id,
            )
        )
