"""Pure comfort helpers for smart capture, discovery, and notification digests."""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime, time, timedelta
from typing import Any

_TIME_PATTERN = re.compile(
    r"\b(?:um\s+)?(\d{1,2}):(\d{2})\b|\b(?:um\s+)?(\d{1,2})\s*uhr\b",
    re.I,
)
_POINTS_PATTERN = re.compile(r"\b(\d{1,3})\s*punkte?\b", re.I)
_PRIORITY_WORDS = {
    "kritisch": "critical",
    "dringend": "critical",
    "hoch": "high",
    "normal": "normal",
    "niedrig": "low",
}


def parse_smart_task(
    text: str,
    people: dict[str, dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    """Parse a short German task phrase into an editable preview."""
    source = " ".join(str(text).strip().split())
    lowered = source.casefold()
    due_day = now.date()
    matched_tokens: list[str] = []
    if "übermorgen" in lowered:
        due_day += timedelta(days=2)
        matched_tokens.append("übermorgen")
    elif "morgen" in lowered:
        due_day += timedelta(days=1)
        matched_tokens.append("morgen")
    elif "heute" in lowered:
        matched_tokens.append("heute")

    time_match = _TIME_PATTERN.search(source)
    due_time = time(18, 0)
    if time_match:
        hour = int(time_match.group(1) or time_match.group(3))
        minute = int(time_match.group(2) or 0)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            due_time = time(hour, minute)
            matched_tokens.append(time_match.group(0))
    elif "abend" in lowered:
        due_time = time(19, 0)
        matched_tokens.append("Abend")

    assignee = None
    person_label = None
    for person_id, person in people.items():
        name = str(person.get("name", person_id))
        match = re.search(rf"\b(?:an|für)\s+{re.escape(name)}\b", source, re.I)
        if match:
            assignee = person_id
            person_label = name
            matched_tokens.append(match.group(0))
            break

    points_match = _POINTS_PATTERN.search(source)
    points = int(points_match.group(1)) if points_match else 1
    if points_match:
        matched_tokens.append(points_match.group(0))

    priority = "normal"
    for word, value in _PRIORITY_WORDS.items():
        match = re.search(rf"\b{word}\b", source, re.I)
        if match:
            priority = value
            matched_tokens.append(match.group(0))
            break

    title = source
    for token in sorted(matched_tokens, key=len, reverse=True):
        title = re.sub(re.escape(token), " ", title, flags=re.I)
    title = re.sub(r"\s+", " ", title).strip(" ,.-")
    due = datetime.combine(due_day, due_time, tzinfo=now.tzinfo)
    if due <= now and due_day == now.date() and not time_match:
        due = now + timedelta(hours=1)

    missing = []
    if not title:
        missing.append("name")
    if assignee is None:
        missing.append("assignee")
    return {
        "source": source,
        "name": title,
        "assignee": assignee,
        "assignee_name": person_label,
        "due": due.replace(microsecond=0).isoformat(),
        "points": points,
        "priority": priority,
        "missing": missing,
        "confidence": round(max(0.25, 1 - 0.2 * len(missing)), 2),
    }


def discovery_suggestions(
    states: Iterable[dict[str, Any]],
    existing_entity_ids: set[str],
) -> list[dict[str, Any]]:
    """Suggest privacy-safe task templates from Home Assistant entity metadata."""
    suggestions: list[dict[str, Any]] = []
    for entity in states:
        entity_id = str(entity.get("entity_id", ""))
        if not entity_id or entity_id in existing_entity_ids:
            continue
        domain = entity_id.partition(".")[0]
        attributes = entity.get("attributes", {})
        name = str(attributes.get("friendly_name") or entity_id)
        haystack = f"{entity_id} {name} {attributes.get('device_class', '')}".casefold()
        suggestion: dict[str, Any] | None = None
        if domain == "sensor" and (
            attributes.get("device_class") == "battery" or "battery" in haystack
        ):
            suggestion = _state_suggestion(
                entity_id,
                f"Batterie prüfen: {name}",
                "below",
                "20",
                "high",
            )
        elif domain == "calendar" and any(
            word in haystack for word in ("müll", "abfall", "waste", "trash")
        ):
            suggestion = {
                "kind": "calendar",
                "entity_id": entity_id,
                "name": f"{name} bereitstellen",
                "reason": "Ein Abfallkalender wurde erkannt.",
                "task": {
                    "enabled": True,
                    "name": f"{name} bereitstellen",
                    "assignment": {"type": "open"},
                    "schedule": {
                        "type": "calendar",
                        "entity_id": entity_id,
                        "match": "",
                        "offset": "-12:00:00",
                    },
                    "market": {"priority": "normal", "points": 1},
                },
            }
        elif any(
            word in haystack
            for word in ("washer", "washing_machine", "waschmaschine", "dishwasher")
        ):
            suggestion = {
                "kind": "appliance",
                "entity_id": entity_id,
                "name": f"{name} ausräumen",
                "reason": "Ein Haushaltsgerät mit Status wurde erkannt.",
                "task": {
                    "enabled": True,
                    "name": f"{name} ausräumen",
                    "assignment": {"type": "open"},
                    "schedule": {
                        "type": "state_trigger",
                        "triggers": [{"entity_id": entity_id, "to": "on"}],
                        "skip_if_open": True,
                        "due_after": "00:00:00",
                    },
                    "market": {"priority": "normal", "points": 1},
                },
            }
        elif domain == "sensor" and any(
            word in haystack for word in ("filter", "salt", "toner", "füllstand")
        ):
            suggestion = _state_suggestion(
                entity_id,
                f"{name} prüfen",
                "below",
                "20",
                "normal",
            )
        if suggestion is not None:
            suggestion["id"] = f"{suggestion['kind']}:{entity_id}"
            suggestions.append(suggestion)
    return suggestions[:30]


def _state_suggestion(
    entity_id: str,
    name: str,
    condition: str,
    threshold: str,
    priority: str,
) -> dict[str, Any]:
    """Build one threshold-driven discovery suggestion."""
    return {
        "kind": "resource",
        "entity_id": entity_id,
        "name": name,
        "reason": "Ein Wartungs- oder Verbrauchssensor wurde erkannt.",
        "monitor": {
            "enabled": True,
            "entity_id": entity_id,
            "condition": condition,
            "threshold": threshold,
            "task_name": name,
            "due_after": "00:00:00",
            "cooldown": "24:00:00",
            "auto_resolve": True,
        },
        "task": {
            "enabled": True,
            "name": name,
            "assignment": {"type": "open"},
            "schedule": {"type": "manual"},
            "season": {
                "entity_id": entity_id,
                "condition": condition,
                "threshold": threshold,
            },
            "market": {"priority": priority, "points": 1},
        },
    }


def notification_digest_due(
    now: datetime,
    last_sent: str | None,
    configured_time: str,
) -> bool:
    """Return whether a queued notification digest should be sent now."""
    try:
        hour, minute, *seconds = (int(part) for part in configured_time.split(":"))
        target = time(hour, minute, seconds[0] if seconds else 0)
    except (TypeError, ValueError):
        return False
    return (
        now.time().replace(tzinfo=None) >= target
        and last_sent != now.date().isoformat()
    )
