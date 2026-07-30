"""Pure evaluation helpers for combined Home Assistant weather rules."""

from __future__ import annotations

from typing import Any

from .resources import resource_condition_matches

WEATHER_OPERATORS = {
    "below",
    "at_most",
    "above",
    "at_least",
    "equals",
    "not_equals",
}
WEATHER_LOGIC = {"all", "any"}


def _state_value(
    state: dict[str, Any] | None,
    attribute: str,
) -> Any:
    """Read either one state attribute or the native state value."""
    if state is None:
        return None
    if attribute:
        return state.get("attributes", {}).get(attribute)
    return state.get("state")


def _condition_result(
    condition: dict[str, Any],
    states: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Evaluate one condition without coupling it to aggregation logic."""
    entity_id = str(condition.get("entity_id", ""))
    attribute = str(condition.get("attribute", "")).strip()
    raw_value = _state_value(states.get(entity_id), attribute)
    available = raw_value is not None and str(raw_value) not in {
        "unknown",
        "unavailable",
    }
    matches = available and resource_condition_matches(
        raw_value,
        condition.get("condition", "below"),
        condition.get("threshold"),
    )
    return {
        "entity_id": entity_id,
        "attribute": attribute or None,
        "current": raw_value,
        "threshold": condition.get("threshold"),
        "condition": condition.get("condition", "below"),
        "available": available,
        "matches": bool(matches),
    }


def _decision_text(allowed: bool, unavailable: bool) -> tuple[str, str]:
    """Return the stable status code and localized explanation."""
    if allowed:
        return "weather_condition_met", "Die kombinierte Wetterbedingung ist erfüllt."
    if unavailable:
        return (
            "weather_entity_unavailable",
            "Mindestens eine Wetterentität ist nicht verfügbar.",
        )
    return (
        "weather_condition_not_met",
        "Die kombinierte Wetterbedingung ist derzeit nicht erfüllt.",
    )


def weather_decision(
    weather: dict[str, Any] | None,
    states: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Evaluate a weather rule and retain one result per condition."""
    if not weather:
        return {
            "allowed": True,
            "code": "no_weather_rule",
            "message": "Für die Aufgabe gilt keine Wetterbedingung.",
            "conditions": [],
        }
    logic = weather.get("logic", "all")
    results = [
        _condition_result(condition, states)
        for condition in weather.get("conditions", [])
    ]
    matcher = all if logic == "all" else any
    allowed = bool(results) and matcher(item["matches"] for item in results)
    unavailable = [item for item in results if not item["available"]]
    code, message = _decision_text(allowed, bool(unavailable))
    return {
        "allowed": allowed,
        "code": code,
        "message": message,
        "logic": logic,
        "conditions": results,
    }
