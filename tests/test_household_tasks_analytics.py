"""Tests for pure Household Tasks analytics."""

import importlib.util
import unittest
from datetime import UTC, datetime
from pathlib import Path

MODULE_PATH = (
    Path(__file__).parents[1] / "custom_components" / "household_tasks" / "analytics.py"
)
SPEC = importlib.util.spec_from_file_location("household_tasks_analytics", MODULE_PATH)
analytics = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(analytics)


class AnalyticsTests(unittest.TestCase):
    def test_counts_open_overdue_and_completion_quality(self):
        now = datetime(2026, 7, 27, 12, tzinfo=UTC)
        occurrences = {
            "open": {
                "due": "2026-07-27T14:00:00+00:00",
                "assignee": "alex",
                "resolved": False,
            },
            "overdue": {
                "due": "2026-07-26T12:00:00+00:00",
                "assignee": "alex",
                "resolved": False,
            },
            "on_time": {
                "task_id": "kitchen",
                "task": {"name": "Kitchen"},
                "due": "2026-07-25T18:00:00+00:00",
                "resolved_at": "2026-07-25T17:30:00+00:00",
                "resolved": True,
                "completed_by": "sam",
            },
            "late": {
                "task_id": "kitchen",
                "task": {"name": "Kitchen"},
                "due": "2026-07-24T18:00:00+00:00",
                "resolved_at": "2026-07-24T19:00:00+00:00",
                "resolved": True,
                "completed_by": "alex",
            },
        }
        result = analytics.build_analytics(
            occurrences,
            {"alex": {"name": "Alex"}, "sam": {"name": "Sam"}},
            now=now,
        )

        self.assertEqual(result["open"], 2)
        self.assertEqual(result["overdue"], 1)
        self.assertEqual(result["completed"], 2)
        self.assertEqual(result["on_time_rate"], 50.0)
        self.assertEqual(result["average_delay_minutes"], 30.0)
        self.assertEqual(result["per_person"]["alex"]["overdue"], 1)
        self.assertEqual(
            result["per_task"]["kitchen"],
            {
                "name": "Kitchen",
                "completed": 2,
                "late": 1,
                "open": 0,
                "overdue": 0,
            },
        )
        self.assertEqual(result["retrospective"][0]["type"], "recurring_late")

    def test_ignores_old_and_automatic_resolutions(self):
        now = datetime(2026, 7, 27, 12, tzinfo=UTC)
        occurrences = [
            {
                "due": "2026-05-01T10:00:00+00:00",
                "resolved_at": "2026-05-01T11:00:00+00:00",
                "resolved": True,
            },
            {
                "due": "2026-07-26T10:00:00+00:00",
                "resolved_at": "2026-07-26T11:00:00+00:00",
                "resolved": True,
                "resolution_reason": "printer_recovered",
            },
        ]

        result = analytics.build_analytics(occurrences, {}, now=now)

        self.assertEqual(result["completed"], 0)
        self.assertIsNone(result["on_time_rate"])

    def test_retrospective_detects_backlog_and_workload_imbalance(self):
        now = datetime(2026, 7, 27, 12, tzinfo=UTC)
        occurrences = {
            f"open-{index}": {
                "task_id": "laundry",
                "task": {"name": "Laundry"},
                "due": "2026-07-26T12:00:00+00:00",
                "assignee": "alex",
                "resolved": False,
            }
            for index in range(3)
        }

        result = analytics.build_analytics(
            occurrences,
            {"alex": {"name": "Alex"}, "sam": {"name": "Sam"}},
            now=now,
        )

        insight_types = {item["type"] for item in result["retrospective"]}
        self.assertIn("task_backlog", insight_types)
        self.assertIn("workload_imbalance", insight_types)


if __name__ == "__main__":
    unittest.main()
