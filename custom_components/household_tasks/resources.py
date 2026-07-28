"""Pure resource-monitoring decisions."""

from __future__ import annotations

from typing import Any

NUMERIC_CONDITIONS = {"above", "at_least", "below", "at_most"}
TEXT_CONDITIONS = {"equals", "not_equals"}
RESOURCE_CONDITIONS = NUMERIC_CONDITIONS | TEXT_CONDITIONS


def resource_condition_matches(
    state: Any,
    condition: str,
    threshold: Any,
) -> bool:
    """Return whether a resource state requires household attention."""
    if condition not in RESOURCE_CONDITIONS:
        raise ValueError(f"Unsupported resource condition: {condition}")
    if condition in NUMERIC_CONDITIONS:
        try:
            current = float(state)
            expected = float(threshold)
        except (TypeError, ValueError):
            return False
        return {
            "above": current > expected,
            "at_least": current >= expected,
            "below": current < expected,
            "at_most": current <= expected,
        }[condition]
    current_text = str(state).strip().casefold()
    expected_text = str(threshold).strip().casefold()
    return (
        current_text == expected_text
        if condition == "equals"
        else current_text != expected_text
    )


def render_resource_text(template: str, state: Any, unit: str | None) -> str:
    """Substitute the deliberately small resource template vocabulary."""
    return (
        str(template)
        .replace("{state}", str(state))
        .replace("{unit}", str(unit or ""))
        .strip()
    )
