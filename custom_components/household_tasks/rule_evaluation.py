"""Pure effectiveness, noise, and simulator suggestion helpers."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any


def _date(value: Any) -> datetime | None:
    try:
        parsed = (
            datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None
        )
        return (
            parsed.replace(tzinfo=UTC)
            if parsed is not None and parsed.tzinfo is None
            else parsed
        )
    except (TypeError, ValueError):
        return None


def build_rule_insights(
    tasks: dict[str, dict[str, Any]],
    occurrences: dict[str, dict[str, Any]],
    events: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    shadows: list[dict[str, Any]],
    *,
    now: datetime,
    period_days: int = 90,
) -> dict[str, Any]:
    """Measure rule outcomes and derive bounded actionable findings."""
    start = now - timedelta(days=period_days)
    by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for occurrence in occurrences.values():
        created = _date(occurrence.get("created_at") or occurrence.get("due"))
        if created is not None and created >= start:
            by_task[str(occurrence.get("task_id", ""))].append(occurrence)
    event_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for event in events:
        occurred = _date(event.get("occurred_at"))
        occurrence = occurrences.get(str(event.get("occurrence_id", "")), {})
        if occurred is not None and occurred >= start:
            event_counts[str(occurrence.get("task_id", ""))][
                str(event.get("type", ""))
            ] += 1

    effects: dict[str, dict[str, Any]] = {}
    noise: list[dict[str, Any]] = []
    improvements: list[dict[str, Any]] = []
    for task_id, task in tasks.items():
        rows = by_task.get(task_id, [])
        completed = [
            row
            for row in rows
            if row.get("resolved")
            and row.get("resolution_reason", "completed") == "completed"
        ]
        cancelled = [row for row in rows if row.get("resolution_reason") == "cancelled"]
        overdue = [
            row
            for row in rows
            if not row.get("resolved")
            and (due := _date(row.get("due"))) is not None
            and due < now
        ]
        delays = []
        completion_hours = []
        for row in completed:
            due = _date(row.get("due"))
            resolved = _date(row.get("resolved_at"))
            if due is not None and resolved is not None:
                delays.append(max(0, (resolved - due).total_seconds() / 60))
                completion_hours.append(resolved.hour + resolved.minute / 60)
        generated = len(rows)
        completion_rate = (
            round(len(completed) * 100 / generated, 1) if generated else None
        )
        counts = event_counts.get(task_id, Counter())
        virtual = sum(
            1
            for item in shadows
            if item.get("task_id") == task_id
            and (_date(item.get("evaluated_at")) or start) >= start
        )
        blocked = sum(
            1
            for item in decisions
            if item.get("task_id") == task_id
            and (_date(item.get("checked_at")) or start) >= start
        )
        effects[task_id] = {
            "task_id": task_id,
            "name": task.get("name", task_id),
            "period_days": period_days,
            "generated": generated,
            "completed": len(completed),
            "cancelled": len(cancelled),
            "overdue": len(overdue),
            "completion_rate": completion_rate,
            "median_delay_minutes": round(median(delays), 1) if delays else None,
            "snoozed": counts["task_snoozed"],
            "delegated": counts["task_delegated"],
            "virtual_triggers": virtual,
            "blocked_evaluations": blocked,
        }
        if generated >= 8 and (completion_rate or 0) < 50:
            noise.append(
                {
                    "task_id": task_id,
                    "severity": "warning",
                    "type": "low_completion",
                    "message": f"Nur {completion_rate or 0:g} % von {generated} erzeugten Aufgaben wurden erledigt.",
                }
            )
            improvements.append(
                {
                    "task_id": task_id,
                    "type": "increase_cooldown",
                    "title": "Auslösungen bündeln",
                    "message": "Cooldown erhöhen oder offene Duplikate konsequent überspringen.",
                    "patch": {
                        "schedule.cooldown": "24:00:00",
                        "schedule.skip_if_open": True,
                    },
                }
            )
        if len(overdue) >= 3:
            noise.append(
                {
                    "task_id": task_id,
                    "severity": "critical",
                    "type": "overdue_backlog",
                    "message": f"{len(overdue)} Aufgaben dieser Regel sind überfällig.",
                }
            )
        if counts["task_snoozed"] >= 3:
            preferred = (
                round(median(completion_hours)) % 24 if completion_hours else None
            )
            improvements.append(
                {
                    "task_id": task_id,
                    "type": "adjust_time",
                    "title": "Fälligkeit an Gewohnheit anpassen",
                    "message": (
                        f"Die Aufgabe wurde oft verschoben. Erledigungen liegen typischerweise gegen {preferred:02d}:00 Uhr."
                        if preferred is not None
                        else "Die Aufgabe wurde wiederholt verschoben; einen passenderen Zeitpunkt prüfen."
                    ),
                    "patch": {"schedule.time": f"{preferred:02d}:00:00"}
                    if preferred is not None
                    else {},
                }
            )
        fallback_count = sum(
            1
            for row in rows
            if row.get("assignment_reason", {}).get("type") == "absence_fallback"
        )
        if fallback_count >= 3:
            improvements.append(
                {
                    "task_id": task_id,
                    "type": "review_assignment",
                    "title": "Zuständigkeit prüfen",
                    "message": f"In {fallback_count} Fällen wurde eine Fallback-Person benötigt.",
                    "patch": {},
                }
            )
    return {
        "period_days": period_days,
        "effects": effects,
        "noise_findings": noise[:30],
        "improvement_suggestions": improvements[:30],
    }


def counterexample_scenarios(
    task: dict[str, Any], reference: datetime
) -> list[dict[str, Any]]:
    """Generate human-readable boundary cases from configured rule conditions."""
    examples: list[dict[str, Any]] = []
    for index, condition in enumerate(task.get("weather", {}).get("conditions", [])):
        operator = condition.get("condition")
        threshold = condition.get("threshold")
        if operator in {"below", "at_most", "above", "at_least"}:
            try:
                value = float(threshold)
            except (TypeError, ValueError):
                continue
            delta = max(abs(value) * 0.01, 0.1)
            outside = (
                value + delta if operator in {"below", "at_most"} else value - delta
            )
            examples.append(
                {
                    "id": f"weather_boundary_{index}",
                    "title": f"Grenzfall: {condition.get('entity_id', 'Wetterwert')}",
                    "reason": f"Wert {outside:g} liegt knapp außerhalb der Bedingung {operator} {threshold}.",
                    "scenario": {"condition_values": {str(index): outside}},
                }
            )
    assignment = task.get("assignment", {})
    if assignment.get("presence_required"):
        people = list(assignment.get("people", []))
        if assignment.get("type", "fixed") == "fixed" and task.get("assignee"):
            people.append(str(task["assignee"]))
        people.extend(str(item) for item in assignment.get("fallback_people", []))
        examples.append(
            {
                "id": "nobody_home",
                "title": "Niemand aus dem Personenkreis zuhause",
                "reason": "Prüft Warte-, Fallback- oder offene Zuweisung bei Abwesenheit.",
                "scenario": {"presence": dict.fromkeys(people, False)},
            }
        )
    months = task.get("season", {}).get("months", [])
    if months and len(set(months)) < 12:
        outside = next((month for month in range(1, 13) if month not in months), None)
        if outside:
            examples.append(
                {
                    "id": "outside_season",
                    "title": "Außerhalb der Saison",
                    "reason": f"Prüft die Regel in einem nicht freigegebenen Monat ({outside}).",
                    "scenario": {
                        "at": reference.replace(month=outside, day=1).isoformat()
                    },
                }
            )
    if task.get("modes", {}).get("vacation") != "always":
        examples.append(
            {
                "id": "vacation_mode",
                "title": "Urlaubsmodus aktiv",
                "reason": "Prüft, ob die Regel im Urlaub pausiert, reduziert oder delegiert wird.",
                "scenario": {"mode": "vacation"},
            }
        )
    return examples[:12]
