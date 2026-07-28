"""Tests for fair task assignment."""

import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "household_tasks"
    / "assignment.py"
)
SPEC = importlib.util.spec_from_file_location("household_tasks_assignment", MODULE_PATH)
assignment = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(assignment)


class AssignmentTests(unittest.TestCase):
    def test_count_then_open_load_then_order_break_ties(self):
        candidates = ["a", "b", "c"]
        self.assertEqual(
            assignment.select_fair_candidate(
                candidates,
                {"a": 2, "b": 1, "c": 1},
                {"a": 0, "b": 2, "c": 1},
            ),
            "c",
        )
        self.assertEqual(
            assignment.select_fair_candidate(
                candidates,
                {"a": 1, "b": 1, "c": 1},
                {"a": 0, "b": 0, "c": 0},
            ),
            "a",
        )

    def test_empty_candidates_return_none(self):
        self.assertIsNone(assignment.select_fair_candidate([], {}, {}))
