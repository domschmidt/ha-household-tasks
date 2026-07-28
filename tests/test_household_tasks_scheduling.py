"""Tests for pure scheduling helpers."""

import importlib.util
import unittest
from datetime import timedelta
from pathlib import Path

MODULE_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "household_tasks"
    / "scheduling.py"
)
SPEC = importlib.util.spec_from_file_location("household_tasks_scheduling", MODULE_PATH)
scheduling = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(scheduling)


class SchedulingTests(unittest.TestCase):
    def test_signed_duration_and_numeric_seconds(self):
        self.assertEqual(
            scheduling.parse_duration("-12:30:00"),
            -timedelta(hours=12, minutes=30),
        )
        self.assertEqual(scheduling.parse_duration(90), timedelta(seconds=90))

    def test_invalid_duration_is_rejected(self):
        for value in ("1:00", "01:99:00", "nope"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                scheduling.parse_duration(value)

    def test_month_day_handles_leap_year_and_clamping(self):
        self.assertEqual(scheduling.month_day(2024, 2, "last"), 29)
        self.assertEqual(scheduling.month_day(2025, 2, 31), 28)
