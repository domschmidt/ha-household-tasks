"""Deterministic helpers for advanced household task comfort features."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any

from .comfort import parse_smart_task

_CLOCK = re.compile(r"\b(?:um\s*)?(\d{1,2})(?::(\d{2}))?\s*(?:uhr)?\b", re.I)


def parse_natural_move(
    text: str,
    now: datetime,
    people: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Turn a short German move instruction into a due date or wait condition."""
    source = " ".join(text.strip().split())
    lowered = source.casefold()
    iso_date = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", source)
    if iso_date:
        try:
            target = datetime.combine(
                datetime.fromisoformat(iso_date.group(1)).date(),
                now.timetz().replace(tzinfo=None),
                now.tzinfo,
            ).replace(second=0, microsecond=0)
        except ValueError as err:
            raise ValueError("Ungültiges Datum") from err
        clock = _CLOCK.search(lowered.replace(iso_date.group(1), ""))
        if clock:
            target = target.replace(
                hour=int(clock.group(1)),
                minute=int(clock.group(2) or 0),
            )
        return {"kind": "datetime", "due": target.isoformat(), "label": source}
    for person_id, person in people.items():
        name = str(person.get("name", person_id))
        if name.casefold() in lowered and any(
            token in lowered for token in ("zuhause", "da ist", "heimkommt")
        ):
            return {
                "kind": "presence",
                "person_id": person_id,
                "label": f"Wenn {name} zuhause ist",
            }

    target = now
    if "übermorgen" in lowered:
        target += timedelta(days=2)
    elif "morgen" in lowered:
        target += timedelta(days=1)
    elif "wochenende" in lowered:
        days = (5 - target.weekday()) % 7
        target += timedelta(days=days or 7)
    elif "nächste woche" in lowered or "naechste woche" in lowered:
        target += timedelta(days=7 - target.weekday())

    clock = _CLOCK.search(lowered)
    if clock:
        hour = int(clock.group(1))
        minute = int(clock.group(2) or 0)
        if hour > 23 or minute > 59:
            raise ValueError("Ungültige Uhrzeit")
        target = target.replace(hour=hour, minute=minute, second=0, microsecond=0)
    elif "abend" in lowered or "essen" in lowered:
        target = target.replace(hour=19, minute=0, second=0, microsecond=0)
    elif "morgen" in lowered or "wochenende" in lowered or "woche" in lowered:
        target = target.replace(hour=9, minute=0, second=0, microsecond=0)
    else:
        raise ValueError("Kein unterstützter Zeitpunkt erkannt")
    if target <= now and not any(
        token in lowered for token in ("morgen", "übermorgen", "wochenende", "woche")
    ):
        target += timedelta(days=1)
    return {"kind": "datetime", "due": target.isoformat(), "label": source}


def parse_task_batch(
    text: str,
    people: dict[str, dict[str, Any]],
    now: datetime,
) -> list[dict[str, Any]]:
    """Parse newline or semicolon separated smart tasks."""
    lines = [
        part.strip(" \t-•") for part in re.split(r"[\n;]+", text) if part.strip(" \t-•")
    ]
    return [parse_smart_task(line, people, now) for line in lines[:50]]


def habit_suggestions(
    occurrences: dict[str, dict[str, Any]],
    people: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Learn transparent local defaults from completed task history."""
    samples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for occurrence in occurrences.values():
        task_id = occurrence.get("task_id")
        if (
            occurrence.get("resolved")
            and occurrence.get("resolution_reason") == "completed"
            and task_id
            and task_id != "__adhoc__"
        ):
            samples[task_id].append(occurrence)
    result = {}
    for task_id, items in samples.items():
        if len(items) < 2:
            continue
        assignees = [
            item.get("completed_by") or item.get("assignee")
            for item in items
            if (item.get("completed_by") or item.get("assignee")) in people
        ]
        hours = []
        for item in items:
            completed = _datetime(item.get("resolved_at"))
            if completed:
                hours.append(completed.hour)
        result[task_id] = {
            "samples": len(items),
            "assignee": Counter(assignees).most_common(1)[0][0] if assignees else None,
            "hour": round(sum(hours) / len(hours)) if hours else None,
            "confidence": min(0.95, 0.45 + len(items) * 0.1),
            "explanation": "Lokal aus bisherigen Erledigungen abgeleitet.",
        }
    return result


def configuration_conflicts(tasks: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Find likely duplicates and conflicting schedule definitions."""
    findings = []
    normalized: dict[str, str] = {}
    tags: dict[str, str] = {}
    for task_id, task in tasks.items():
        name = re.sub(r"\W+", "", str(task.get("name", "")).casefold())
        if name and name in normalized:
            findings.append(
                {
                    "severity": "warning",
                    "code": "duplicate_name",
                    "task_ids": [normalized[name], task_id],
                    "message": f"Sehr ähnliche Vorlagen: {normalized[name]} und {task_id}.",
                }
            )
        elif name:
            normalized[name] = task_id
        tag = task.get("nfc", {}).get("tag_id")
        if tag and tag in tags:
            findings.append(
                {
                    "severity": "critical",
                    "code": "duplicate_nfc",
                    "task_ids": [tags[tag], task_id],
                    "message": f"NFC-Tag {tag} wird mehrfach verwendet.",
                }
            )
        elif tag:
            tags[tag] = task_id
    return findings


def schedule_projection(task: dict[str, Any]) -> dict[str, Any]:
    """Estimate creation volume and flag dangerous task schedules."""
    schedule = task.get("schedule", {})
    schedule_type = schedule.get("type", "manual")
    per_week = {
        "manual": 0,
        "weekly": len(schedule.get("weekdays", [])),
        "monthly": 0.23,
        "yearly": 0.02,
        "interval_months": 0.23 / max(1, int(schedule.get("months", 1) or 1)),
        "calendar": None,
        "state_trigger": None,
        "weather_trigger": None,
        "forecast_trigger": None,
        "daily_after_state": 7,
        "after_completion": _duration_rate(schedule.get("interval")),
        "flexible_after_completion": _duration_rate(schedule.get("preferred_interval")),
    }.get(schedule_type)
    risk = "unknown" if per_week is None else "high" if per_week > 14 else "normal"
    return {
        "per_week": round(per_week, 2) if per_week is not None else None,
        "risk": risk,
        "message": (
            "Ereignisabhängig; die tatsächliche Menge hängt von Home Assistant ab."
            if per_week is None
            else f"Voraussichtlich etwa {per_week:.1f} Erzeugungen pro Woche."
        ),
    }


def contextual_home(
    now: datetime,
    occurrences: list[dict[str, Any]],
) -> dict[str, Any]:
    """Choose a transparent landing-page focus for the current time."""
    hour = now.hour
    if hour < 10:
        mode, title = "plan", "Guten Morgen - heute planen"
    elif hour >= 18:
        mode, title = "evening", "Abendrunde"
    else:
        mode, title = "focus", "Als Nächstes sinnvoll"
    open_items = [item for item in occurrences if not item.get("resolved")]
    return {
        "mode": mode,
        "title": title,
        "occurrence_ids": [
            item["id"]
            for item in sorted(open_items, key=lambda item: item.get("due", ""))[:3]
            if item.get("id")
        ],
    }


def _duration_rate(value: Any) -> float | None:
    if not value:
        return None
    match = re.fullmatch(r"(-?\d+):([0-5]\d):([0-5]\d)", str(value))
    if not match:
        return None
    seconds = (
        int(match.group(1)) * 3600 + int(match.group(2)) * 60 + int(match.group(3))
    )
    return 604800 / seconds if seconds > 0 else None


def _datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None
