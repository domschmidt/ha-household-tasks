"""Tests for NFC task matching."""

import unittest

from custom_components.household_tasks import nfc


class NfcTests(unittest.TestCase):
    def test_task_for_tag_ignores_disabled_tasks(self):
        tasks = {
            "disabled": {
                "enabled": False,
                "nfc": {"tag_id": "same"},
            },
            "active": {
                "enabled": True,
                "nfc": {"tag_id": "target"},
            },
        }
        self.assertEqual(nfc.task_for_tag(tasks, " target ")[0], "active")
        self.assertIsNone(nfc.task_for_tag(tasks, "same"))

    def test_task_for_tag_ignores_temporarily_paused_tasks(self):
        tasks = {
            "paused": {
                "enabled": True,
                "paused_until": "2999-01-01T00:00:00+00:00",
                "nfc": {"tag_id": "target"},
            }
        }

        self.assertIsNone(nfc.task_for_tag(tasks, "target"))

    def test_open_occurrence_returns_oldest_due(self):
        occurrences = {
            "later": {
                "task_id": "laundry",
                "due": "2026-07-28T10:00:00+00:00",
                "resolved": False,
            },
            "done": {
                "task_id": "laundry",
                "due": "2026-07-26T10:00:00+00:00",
                "resolved": True,
            },
            "earlier": {
                "task_id": "laundry",
                "due": "2026-07-27T10:00:00+00:00",
                "resolved": False,
            },
        }
        self.assertEqual(
            nfc.open_occurrence_for_task(occurrences, "laundry")[0],
            "earlier",
        )

    def test_open_occurrence_returns_none_without_match(self):
        self.assertIsNone(nfc.open_occurrence_for_task({}, "unknown"))

    def test_feedback_prefers_user_and_deduplicates_assignee(self):
        people = {
            "a": {
                "user_id": "user-a",
                "nfc_device_id": "device-a",
            },
            "b": {"nfc_device_id": "device-b"},
        }
        self.assertEqual(
            nfc.feedback_recipients(
                people,
                user_id="user-a",
                device_id="device-b",
                assignee="a",
                recipient_mode="both",
            ),
            ["a"],
        )
        self.assertEqual(
            nfc.feedback_recipients(
                people,
                user_id=None,
                device_id="device-b",
                assignee="a",
                recipient_mode="both",
            ),
            ["b", "a"],
        )
