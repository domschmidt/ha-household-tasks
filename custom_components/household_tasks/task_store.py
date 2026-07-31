"""Native task-domain persistence helpers.

The integration owns its task lifecycle. Home Assistant provides the runtime,
automations, entities, notifications, and storage API, but no external to-do
entity is used as a source of truth.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any
from uuid import uuid4

TASK_SCHEMA_VERSION = 2
VALID_TASK_STATUSES = {
    "open",
    "in_progress",
    "waiting",
    "blocked",
    "completed",
    "cancelled",
}
TERMINAL_TASK_STATUSES = {"completed", "cancelled"}
MAX_JOURNAL_ENTRIES = 2_000


def _checklist_item(item: Any, index: int) -> dict[str, Any]:
    """Normalize one checklist definition or persisted item."""
    if isinstance(item, str):
        return {"id": f"step_{index + 1}", "title": item, "completed": False}
    source = dict(item) if isinstance(item, dict) else {}
    return {
        "id": str(source.get("id") or f"step_{index + 1}"),
        "title": str(
            source.get("title") or source.get("name") or f"Schritt {index + 1}"
        ),
        "completed": bool(source.get("completed", False)),
        **(
            {"completed_at": source["completed_at"]}
            if source.get("completed_at")
            else {}
        ),
        **(
            {"completed_by": source["completed_by"]}
            if source.get("completed_by")
            else {}
        ),
    }


def normalize_occurrence(
    occurrence_id: str,
    occurrence: dict[str, Any],
    *,
    migrated_at: str,
) -> dict[str, Any]:
    """Return one occurrence in the native schema."""
    normalized = deepcopy(occurrence)
    normalized.pop("uid", None)
    status = str(normalized.get("status") or "")
    if status not in VALID_TASK_STATUSES:
        status = "completed" if normalized.get("resolved") else "open"
    normalized["status"] = status
    normalized["resolved"] = status in {"completed", "cancelled"}
    normalized.setdefault("created_at", migrated_at)
    normalized["updated_at"] = str(normalized.get("updated_at") or migrated_at)
    normalized["revision"] = max(1, int(normalized.get("revision", 1)))
    checklist = normalized.get("checklist")
    if checklist is None:
        checklist = normalized.get("task", {}).get("checklist", [])
    normalized["checklist"] = [
        _checklist_item(item, index) for index, item in enumerate(checklist or [])
    ]
    normalized["dependencies"] = [
        str(item)
        for item in normalized.get("dependencies", [])
        if str(item) != occurrence_id
    ]
    return normalized


def migrate_state(stored: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    """Migrate legacy mirrored tasks into the native task schema."""
    state = deepcopy(stored)
    migrated_at = now.isoformat()
    occurrences = state.setdefault("occurrences", {})
    state["occurrences"] = {
        occurrence_id: normalize_occurrence(
            occurrence_id,
            occurrence,
            migrated_at=migrated_at,
        )
        for occurrence_id, occurrence in occurrences.items()
        if isinstance(occurrence, dict)
    }
    state.setdefault("task_events", [])
    state.setdefault("store_recovery", [])
    previous_version = int(state.get("task_schema_version", 1))
    state["task_schema_version"] = TASK_SCHEMA_VERSION
    if previous_version < TASK_SCHEMA_VERSION:
        append_event(
            state,
            event_type="store_migrated",
            occurred_at=migrated_at,
            details={"from": previous_version, "to": TASK_SCHEMA_VERSION},
        )
    return state


def append_event(
    state: dict[str, Any],
    *,
    event_type: str,
    occurred_at: str,
    occurrence_id: str | None = None,
    actor: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append an immutable, bounded audit event."""
    event = {
        "id": uuid4().hex,
        "type": event_type,
        "occurred_at": occurred_at,
        **({"occurrence_id": occurrence_id} if occurrence_id else {}),
        **({"actor": actor} if actor else {}),
        **({"details": deepcopy(details)} if details else {}),
    }
    journal = state.setdefault("task_events", [])
    journal.append(event)
    del journal[:-MAX_JOURNAL_ENTRIES]
    return event


def task_store_health(state: dict[str, Any]) -> dict[str, Any]:
    """Return integrity findings for the native task store."""
    findings: list[dict[str, Any]] = []
    occurrences = state.get("occurrences", {})
    for occurrence_id, occurrence in occurrences.items():
        status = occurrence.get("status")
        if status not in VALID_TASK_STATUSES:
            findings.append(
                {
                    "severity": "critical",
                    "code": "invalid_task_status",
                    "message": f"Aufgabe {occurrence_id} hat den ungültigen Status {status}.",
                }
            )
        missing = [
            dependency
            for dependency in occurrence.get("dependencies", [])
            if dependency not in occurrences
        ]
        if missing:
            findings.append(
                {
                    "severity": "warning",
                    "code": "orphan_dependencies",
                    "message": f"Aufgabe {occurrence_id} verweist auf fehlende Abhängigkeiten.",
                    "details": {"missing": missing},
                }
            )
        checklist_ids = [item.get("id") for item in occurrence.get("checklist", [])]
        if len(checklist_ids) != len(set(checklist_ids)):
            findings.append(
                {
                    "severity": "critical",
                    "code": "duplicate_checklist_ids",
                    "message": f"Aufgabe {occurrence_id} enthält doppelte Checklisten-IDs.",
                }
            )
    return {
        "schema_version": int(state.get("task_schema_version", 1)),
        "occurrence_count": len(occurrences),
        "journal_entries": len(state.get("task_events", [])),
        "findings": findings,
    }


def has_dependency_cycle(
    occurrences: dict[str, dict[str, Any]],
    occurrence_id: str,
    dependencies: list[str],
) -> bool:
    """Return whether replacing one dependency list would create a cycle."""
    graph = {
        item_id: list(item.get("dependencies", []))
        for item_id, item in occurrences.items()
    }
    graph[occurrence_id] = dependencies
    visiting: set[str] = set()
    visited: set[str] = set()

    def _visit(item_id: str) -> bool:
        if item_id in visiting:
            return True
        if item_id in visited:
            return False
        visiting.add(item_id)
        for dependency in graph.get(item_id, []):
            if dependency in graph and _visit(dependency):
                return True
        visiting.remove(item_id)
        visited.add(item_id)
        return False

    return any(_visit(item_id) for item_id in graph)
