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

    def test_complex_configuration_round_trip_preserves_nested_rules(self):
        """Weather, chains, flexible schedules, and monitors survive backup."""
        source = {
            "people": {
                "alex": {
                    "name": "Alex",
                    "notify": "notify.mobile_app_alex",
                    "presence": "person.alex",
                }
            },
            "tasks": {
                "ice": {
                    "enabled": True,
                    "paused_until": "2026-08-15T12:00:00+00:00",
                    "name": "Prevent ice",
                    "schedule": {
                        "type": "weather_trigger",
                        "due_after": "00:30:00",
                        "cooldown": "12:00:00",
                    },
                    "weather": {
                        "logic": "all",
                        "conditions": [
                            {
                                "entity_id": "weather.home",
                                "attribute": "temperature",
                                "condition": "below",
                                "threshold": 1,
                            },
                            {
                                "entity_id": "weather.home",
                                "attribute": "precipitation_probability",
                                "condition": "above",
                                "threshold": 30,
                            },
                        ],
                    },
                    "follow_ups": [{"task_id": "inspect", "delay": "12:00:00"}],
                },
                "first_frost": {
                    "name": "Check antifreeze",
                    "assignment": {
                        "type": "per_person",
                        "people": ["alex"],
                    },
                    "schedule": {
                        "type": "forecast_trigger",
                        "forecast_type": "daily",
                        "horizon_hours": 48,
                        "lead_days": 1,
                        "time": "18:00:00",
                    },
                    "season": {"months": [10, 11, 12, 1, 2, 3]},
                    "repeat": {"mode": "once_per_season"},
                },
                "inspect": {
                    "name": "Inspect paths",
                    "schedule": {
                        "type": "flexible_after_completion",
                        "start": "2026-11-01T08:00:00+00:00",
                        "earliest_interval": "120:00:00",
                        "preferred_interval": "168:00:00",
                        "latest_interval": "240:00:00",
                    },
                },
            },
            "defaults": {
                "notification_digest": {
                    "enabled": True,
                    "time": "17:30:00",
                    "minimum_tasks": 2,
                }
            },
            "monitors": {
                "resources": {
                    "salt": {
                        "entity_id": "sensor.salt",
                        "condition": "below",
                        "threshold": 20,
                    }
                }
            },
        }

        exported = config_io.build_export(
            source,
            exported_at=datetime(2026, 7, 30, tzinfo=UTC),
        )
        imported = config_io.parse_import(exported)

        self.assertEqual(imported, source)
        self.assertEqual(
            imported["tasks"]["ice"]["weather"]["conditions"][1]["threshold"],
            30,
        )
        self.assertEqual(
            imported["tasks"]["inspect"]["schedule"]["latest_interval"],
            "240:00:00",
        )
        self.assertEqual(
            imported["tasks"]["first_frost"]["assignment"]["type"],
            "per_person",
        )
        self.assertEqual(
            imported["tasks"]["ice"]["paused_until"],
            "2026-08-15T12:00:00+00:00",
        )


if __name__ == "__main__":
    unittest.main()
