"""Pure policy helpers for advanced household task automation."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, time, timedelta
from difflib import SequenceMatcher
from typing import Any

INVALID_STATES = {"", "unknown", "unavailable", "none", "null"}
COMPARATORS = {"below", "at_most", "above", "at_least", "equals", "not_equals"}
VISIBILITY_LEVELS = {"household", "assignee", "people"}
WAIT_TIMEOUT_ACTIONS = {"keep_waiting", "release", "cancel"}
QUIET_BEHAVIORS = {"defer", "digest", "allow"}


def compare_value(actual: Any, comparator: str, expected: Any) -> bool:
    """Compare numeric values when possible and strings otherwise."""
    if comparator not in COMPARATORS:
        return False
    try:
        left: Any = float(actual)
        right: Any = float(expected)
    except (TypeError, ValueError):
        left = str(actual).strip().casefold()
        right = str(expected).strip().casefold()
    return {
        "below": left < right,
        "at_most": left <= right,
        "above": left > right,
        "at_least": left >= right,
        "equals": left == right,
        "not_equals": left != right,
    }[comparator]


def condition_value(condition: Mapping[str, Any], states: Mapping[str, Any]) -> Any:
    """Read one state or attribute from a serializable state mapping."""
    state = states.get(str(condition.get("entity_id", "")))
    if state is None:
        return None
    if not isinstance(state, Mapping):
        return state
    attribute = str(condition.get("attribute", "")).strip()
    if attribute:
        attributes = state.get("attributes", {})
        return attributes.get(attribute) if isinstance(attributes, Mapping) else None
    return state.get("state")


def evaluate_conditions(
    conditions: Iterable[Mapping[str, Any]],
    states: Mapping[str, Any],
    *,
    match: str = "all",
) -> dict[str, Any]:
    """Evaluate a bounded condition group and retain an explainable trace."""
    results = []
    for condition in conditions:
        actual = condition_value(condition, states)
        expected = condition.get("value", condition.get("threshold"))
        comparator = str(condition.get("condition", "equals"))
        available = actual is not None and str(actual).casefold() not in INVALID_STATES
        matched = available and compare_value(actual, comparator, expected)
        results.append(
            {
                "entity_id": str(condition.get("entity_id", "")),
                "attribute": str(condition.get("attribute", "")),
                "condition": comparator,
                "expected": expected,
                "actual": actual,
                "available": available,
                "matched": matched,
            }
        )
    allowed = bool(results) and (
        any(item["matched"] for item in results)
        if match == "any"
        else all(item["matched"] for item in results)
    )
    return {"allowed": allowed, "match": match, "conditions": results}


def wait_state_decision(
    policy: Mapping[str, Any] | None,
    states: Mapping[str, Any],
    *,
    now: datetime,
    waiting_since: datetime | None = None,
) -> dict[str, Any]:
    """Return whether a waiting occurrence may be released or timed out."""
    if not policy or not policy.get("enabled", False):
        return {"waiting": False, "reason": "disabled"}
    evaluation = evaluate_conditions(
        policy.get("conditions", []), states, match=str(policy.get("match", "all"))
    )
    if evaluation["allowed"]:
        return {**evaluation, "waiting": False, "reason": "condition_matched"}
    timeout_hours = max(0.0, float(policy.get("timeout_hours", 0) or 0))
    timed_out = bool(
        timeout_hours
        and waiting_since is not None
        and now >= waiting_since + timedelta(hours=timeout_hours)
    )
    timeout_action = str(policy.get("timeout_action", "keep_waiting"))
    if timed_out and timeout_action == "release":
        return {**evaluation, "waiting": False, "reason": "timeout_release"}
    return {
        **evaluation,
        "waiting": True,
        "timed_out": timed_out,
        "timeout_action": timeout_action,
        "reason": "timeout_cancel"
        if timed_out and timeout_action == "cancel"
        else "condition_pending",
    }


def energy_decision(
    policy: Mapping[str, Any] | None,
    states: Mapping[str, Any],
    *,
    now: datetime,
) -> dict[str, Any]:
    """Evaluate tariff, surplus, and optional preferred-hour constraints."""
    if not policy or not policy.get("enabled", False):
        return {"allowed": True, "reason": "disabled", "conditions": []}
    conditions = list(policy.get("conditions", []))
    tariff_entity = str(policy.get("tariff_entity", "")).strip()
    if tariff_entity and policy.get("max_price") not in {None, ""}:
        conditions.append(
            {
                "entity_id": tariff_entity,
                "condition": "at_most",
                "value": policy["max_price"],
            }
        )
    surplus_entity = str(policy.get("surplus_entity", "")).strip()
    if surplus_entity and policy.get("min_surplus") not in {None, ""}:
        conditions.append(
            {
                "entity_id": surplus_entity,
                "condition": "at_least",
                "value": policy["min_surplus"],
            }
        )
    evaluation = evaluate_conditions(
        conditions, states, match=str(policy.get("match", "all"))
    )
    if not conditions:
        evaluation["allowed"] = True
    start = _parse_clock(policy.get("preferred_start"))
    end = _parse_clock(policy.get("preferred_end"))
    within_hours = (
        True
        if start is None or end is None
        else _in_clock_window(now.time(), start, end)
    )
    allowed = evaluation["allowed"] and within_hours
    return {
        **evaluation,
        "allowed": allowed,
        "within_preferred_hours": within_hours,
        "reason": "energy_window_open" if allowed else "energy_window_pending",
    }


def notification_policy_decision(
    policy: Mapping[str, Any] | None,
    *,
    now: datetime,
    sent_today: int,
    priority: str,
) -> dict[str, Any]:
    """Apply quiet hours and a per-person daily interruption budget."""
    if not policy:
        return {"allowed": True, "reason": "no_policy"}
    critical = priority == "critical"
    bypass = bool(policy.get("critical_bypass", True)) and critical
    quiet = False
    start = _parse_clock(policy.get("quiet_start"))
    end = _parse_clock(policy.get("quiet_end"))
    if start is not None and end is not None:
        quiet = _in_clock_window(now.time(), start, end)
    budget = max(0, int(policy.get("daily_budget", 0) or 0))
    exhausted = bool(budget and sent_today >= budget)
    behavior = str(policy.get("quiet_behavior", "defer"))
    allowed = bypass or behavior == "allow" or not (quiet or exhausted)
    reason = "allowed"
    if exhausted:
        reason = "budget_exhausted"
    if quiet:
        reason = "quiet_hours"
    if bypass:
        reason = "critical_bypass"
    return {
        "allowed": allowed,
        "reason": reason,
        "quiet": quiet,
        "budget": budget,
        "sent_today": sent_today,
        "behavior": behavior,
    }


def visible_to(
    task: Mapping[str, Any],
    occurrence: Mapping[str, Any],
    *,
    viewer_person: str | None,
    is_admin: bool,
) -> bool:
    """Enforce task-level privacy without weakening administrator access."""
    visibility = task.get("visibility", {})
    if not isinstance(visibility, Mapping):
        return True
    if is_admin and visibility.get("admin_access", True):
        return True
    level = str(visibility.get("level", "household"))
    if level == "household":
        return True
    if viewer_person is None:
        return False
    if viewer_person == occurrence.get("assignee") or viewer_person == occurrence.get(
        "target_person"
    ):
        return True
    return level == "people" and viewer_person in visibility.get("people", [])


def redact_private_occurrence(
    occurrence: Mapping[str, Any], *, viewer_person: str | None, is_admin: bool
) -> dict[str, Any]:
    """Return a safe copy and optionally hide sensitive descriptive content."""
    result = dict(occurrence)
    task = result.get("task", {})
    visibility = task.get("visibility", {}) if isinstance(task, Mapping) else {}
    privileged = is_admin or viewer_person in {
        result.get("assignee"),
        result.get("target_person"),
        *visibility.get("people", []),
    }
    if visibility.get("hide_details", False) and not privileged:
        result["title"] = "Private Aufgabe"
        result["description"] = None
        result["checklist"] = []
        result.pop("creation_trace", None)
    return result


def replacement_candidates(
    missing_entity_id: str,
    states: Mapping[str, Any],
    *,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Rank same-domain replacement entities using IDs and friendly names."""
    domain, _, object_id = missing_entity_id.partition(".")
    if not domain or not object_id:
        return []
    needle = object_id.replace("_", " ").casefold()
    candidates = []
    for entity_id, state in states.items():
        if not str(entity_id).startswith(f"{domain}."):
            continue
        attributes = getattr(state, "attributes", None)
        if attributes is None and isinstance(state, Mapping):
            attributes = state.get("attributes", {})
        friendly = str((attributes or {}).get("friendly_name", ""))
        haystacks = [
            str(entity_id).split(".", 1)[1].replace("_", " ").casefold(),
            friendly.casefold(),
        ]
        score = max(
            SequenceMatcher(None, needle, haystack).ratio()
            for haystack in haystacks
            if haystack
        )
        if score >= 0.35:
            candidates.append(
                {
                    "entity_id": str(entity_id),
                    "friendly_name": friendly,
                    "score": round(score, 3),
                }
            )
    return sorted(candidates, key=lambda item: (-item["score"], item["entity_id"]))[
        :limit
    ]


def _parse_clock(value: Any) -> time | None:
    if not value:
        return None
    try:
        return time.fromisoformat(str(value))
    except ValueError:
        return None


def _in_clock_window(value: time, start: time, end: time) -> bool:
    if start == end:
        return True
    return start <= value < end if start < end else value >= start or value < end
