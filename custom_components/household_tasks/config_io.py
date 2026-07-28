"""Versioned configuration import and export helpers."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

EXPORT_FORMAT = "household_tasks_config"
EXPORT_VERSION = 1
EDITABLE_KEYS = ("people", "tasks", "defaults", "monitors")


def build_export(
    editable_config: dict[str, Any], *, exported_at: datetime
) -> dict[str, Any]:
    """Create a versioned, portable configuration document."""
    return {
        "format": EXPORT_FORMAT,
        "version": EXPORT_VERSION,
        "exported_at": exported_at.isoformat(),
        "config": {
            key: deepcopy(editable_config.get(key, {})) for key in EDITABLE_KEYS
        },
    }


def parse_import(payload: Any) -> dict[str, Any]:
    """Validate an exported document and return its editable configuration."""
    if not isinstance(payload, dict):
        raise ValueError("The import must be a JSON object.")
    if payload.get("format") != EXPORT_FORMAT:
        raise ValueError("This is not a Household Tasks configuration export.")
    if payload.get("version") != EXPORT_VERSION:
        raise ValueError(f"Unsupported export version: {payload.get('version')!r}.")
    config = payload.get("config")
    if not isinstance(config, dict):
        raise ValueError("The export does not contain a configuration object.")
    missing = [key for key in EDITABLE_KEYS if key not in config]
    if missing:
        raise ValueError(
            f"The export is missing configuration sections: {', '.join(missing)}."
        )
    for key in EDITABLE_KEYS:
        if not isinstance(config[key], dict):
            raise ValueError(f"Configuration section '{key}' must be an object.")
    return {key: deepcopy(config[key]) for key in EDITABLE_KEYS}
