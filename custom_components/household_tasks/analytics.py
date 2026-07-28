"""Pure analytics helpers for Household Tasks."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _average(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 1)


def build_analytics(
    occurrences: dict[str, dict[str, Any]] | list[dict[str, Any]],
    people: dict[str, dict[str, Any]],
    *,
    now: datetime,
    period_days: int = 30,
) -> dict[str, Any]:
    """Build stable, JSON-serializable household performance metrics."""
    items = (
        list(occurrences.values())
        if isinstance(occurrences, dict)
        else list(occurrences)
    )
    period_start = now - timedelta(days=period_days)
    open_items = [item for item in items if not item.get("resolved")]
    overdue_items = [
        item
        for item in open_items
        if (due := _parse_datetime(item.get("due"))) is not None and due < now
    ]
    completed = [
        item
        for item in items
        if item.get("resolved")
        and (resolved := _parse_datetime(item.get("resolved_at"))) is not None
        and resolved >= period_start
        and item.get("resolution_reason", "completed") == "completed"
    ]

    delays: list[float] = []
    on_time = 0
    for item in completed:
        due = _parse_datetime(item.get("due"))
        resolved = _parse_datetime(item.get("resolved_at"))
        if due is None or resolved is None:
            continue
        delay = (resolved - due).total_seconds() / 60
        if delay <= 0:
            on_time += 1
        delays.append(max(0, delay))

    per_person: dict[str, dict[str, Any]] = {}
    for person_id, person in people.items():
        person_completed = [
            item for item in completed if item.get("completed_by") == person_id
        ]
        person_open = [item for item in open_items if item.get("assignee") == person_id]
        person_overdue = [
            item for item in overdue_items if item.get("assignee") == person_id
        ]
        person_delays = []
        for item in person_completed:
            due = _parse_datetime(item.get("due"))
            resolved = _parse_datetime(item.get("resolved_at"))
            if due is not None and resolved is not None:
                person_delays.append(max(0, (resolved - due).total_seconds() / 60))
        per_person[person_id] = {
            "name": person.get("name", person_id),
            "completed": len(person_completed),
            "open": len(person_open),
            "overdue": len(person_overdue),
            "average_delay_minutes": _average(person_delays),
        }

    per_task: dict[str, dict[str, Any]] = {}
    for item in completed + open_items:
        task_id = str(item.get("task_id", "unknown"))
        row = per_task.setdefault(
            task_id,
            {
                "name": (item.get("task") or {}).get("name", task_id),
                "completed": 0,
                "late": 0,
                "open": 0,
                "overdue": 0,
            },
        )
        if not item.get("resolved"):
            row["open"] += 1
            if item in overdue_items:
                row["overdue"] += 1
            continue
        row["completed"] += 1
        due = _parse_datetime(item.get("due"))
        resolved = _parse_datetime(item.get("resolved_at"))
        if due is not None and resolved is not None and resolved > due:
            row["late"] += 1

    retrospective: list[dict[str, Any]] = []
    for task_id, row in per_task.items():
        if row["completed"] >= 2:
            late_rate = round(row["late"] * 100 / row["completed"], 1)
            if late_rate >= 50:
                retrospective.append(
                    {
                        "type": "recurring_late",
                        "severity": "warning",
                        "task_id": task_id,
                        "name": row["name"],
                        "late_rate": late_rate,
                    }
                )
        if row["overdue"] >= 2:
            retrospective.append(
                {
                    "type": "task_backlog",
                    "severity": "critical",
                    "task_id": task_id,
                    "name": row["name"],
                    "overdue": row["overdue"],
                }
            )

    if len(per_person) >= 2:
        ordered_load = sorted(
            per_person.items(),
            key=lambda item: (item[1]["open"], item[1]["overdue"]),
        )
        least_id, least = ordered_load[0]
        most_id, most = ordered_load[-1]
        if most["open"] - least["open"] >= 3:
            retrospective.append(
                {
                    "type": "workload_imbalance",
                    "severity": "warning",
                    "most_person_id": most_id,
                    "most_name": most["name"],
                    "most_open": most["open"],
                    "least_person_id": least_id,
                    "least_name": least["name"],
                    "least_open": least["open"],
                }
            )

    reassigned = sum(
        len(item.get("assignment_history", []))
        for item in items
        if (_parse_datetime(item.get("due")) or now) >= period_start
    )
    if reassigned >= 3:
        retrospective.append(
            {
                "type": "frequent_reassignment",
                "severity": "info",
                "count": reassigned,
            }
        )
    retrospective.sort(
        key=lambda item: {"critical": 0, "warning": 1, "info": 2}[item["severity"]]
    )

    return {
        "period_days": period_days,
        "open": len(open_items),
        "overdue": len(overdue_items),
        "completed": len(completed),
        "on_time_rate": (round(on_time * 100 / len(delays), 1) if delays else None),
        "average_delay_minutes": _average(delays),
        "per_person": per_person,
        "per_task": per_task,
        "retrospective": retrospective,
    }
