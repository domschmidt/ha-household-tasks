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
    results = []
    for condition in weather.get("conditions", []):
        entity_id = str(condition.get("entity_id", ""))
        state = states.get(entity_id)
        attribute = str(condition.get("attribute", "")).strip()
        raw_value = (
            state.get("attributes", {}).get(attribute)
            if state is not None and attribute
            else state.get("state")
            if state is not None
            else None
        )
        available = raw_value is not None and str(raw_value) not in {
            "unknown",
            "unavailable",
        }
        matches = available and resource_condition_matches(
            raw_value,
            condition.get("condition", "below"),
            condition.get("threshold"),
        )
        results.append(
            {
                "entity_id": entity_id,
                "attribute": attribute or None,
                "current": raw_value,
                "threshold": condition.get("threshold"),
                "condition": condition.get("condition", "below"),
                "available": available,
                "matches": bool(matches),
            }
        )
    allowed = bool(results) and (
        all(item["matches"] for item in results)
        if logic == "all"
        else any(item["matches"] for item in results)
    )
    unavailable = [item for item in results if not item["available"]]
    return {
        "allowed": allowed,
        "code": (
            "weather_condition_met"
            if allowed
            else "weather_entity_unavailable"
            if unavailable
            else "weather_condition_not_met"
        ),
        "message": (
            "Die kombinierte Wetterbedingung ist erfüllt."
            if allowed
            else "Mindestens eine Wetterentität ist nicht verfügbar."
            if unavailable
            else "Die kombinierte Wetterbedingung ist derzeit nicht erfüllt."
        ),
        "logic": logic,
        "conditions": results,
    }
