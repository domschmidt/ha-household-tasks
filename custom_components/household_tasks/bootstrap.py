"""Privacy-safe initial configuration for a new installation."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

DEFAULT_CONFIG: dict[str, Any] = {
    "todo_entity": "todo.haushalt",
    "scan_interval_seconds": 60,
    "catch_up_days": 7,
    "notify_on_create": False,
    "people": {},
    "defaults": {
        "nfc_feedback": {
            "mode": "always",
            "recipients": "scanner",
        },
        "weekly_summary": {
            "enabled": False,
            "weekday": "sun",
            "time": "18:00:00",
        },
        "notification_digest": {
            "enabled": False,
            "time": "17:30:00",
            "minimum_tasks": 2,
        },
        "escalation": [
            {
                "after": "00:00:00",
                "recipients": "assignee",
                "presence_required": True,
            },
            {
                "after": "02:00:00",
                "recipients": "assignee",
                "relative_to": "first_notification",
                "action": "delegate",
            },
            {"after": "24:00:00", "recipients": "all"},
        ],
    },
    "monitors": {
        "printers": {
            "enabled": False,
            "assignee": None,
        },
        "resources": {},
    },
    "tasks": {},
}


def initial_config(todo_entity: str) -> dict[str, Any]:
    """Return an isolated, person-free initial configuration."""
    config = deepcopy(DEFAULT_CONFIG)
    config["todo_entity"] = todo_entity
    return config
