"""Tests for Household Tasks configuration import and export."""

import importlib.util
import unittest
from datetime import UTC, datetime
from pathlib import Path

MODULE_PATH = (
    Path(__file__).parents[1] / "custom_components" / "household_tasks" / "config_io.py"
)
SPEC = importlib.util.spec_from_file_location("household_tasks_config_io", MODULE_PATH)
config_io = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(config_io)


class ConfigIoTests(unittest.TestCase):
    def test_export_round_trip_is_versioned_and_isolated(self):
        source = {
            "people": {"a": {"name": "A"}},
            "tasks": {},
            "defaults": {"escalation": []},
            "monitors": {},
        }
        exported = config_io.build_export(
            source, exported_at=datetime(2026, 7, 27, tzinfo=UTC)
        )
        imported = config_io.parse_import(exported)

        self.assertEqual(exported["format"], "household_tasks_config")
        self.assertEqual(exported["version"], 1)
        self.assertEqual(imported, source)
        imported["people"]["a"]["name"] = "Changed"
        self.assertEqual(source["people"]["a"]["name"], "A")

    def test_import_rejects_invalid_documents(self):
        payloads = [
            None,
            {},
            {"format": "other", "version": 1, "config": {}},
            {"format": "household_tasks_config", "version": 99, "config": {}},
            {
                "format": "household_tasks_config",
                "version": 1,
                "config": {"people": {}, "tasks": {}, "defaults": {}},
            },
        ]
        for payload in payloads:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                config_io.parse_import(payload)


if __name__ == "__main__":
    unittest.main()
