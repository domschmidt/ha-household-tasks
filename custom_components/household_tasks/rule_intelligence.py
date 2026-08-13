"""Pure rule diagnostics, observation learning, and graph projections."""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any


def observation_suggestions(
    occurrences: dict[str, dict[str, Any]],
    *,
    minimum_samples: int = 3,
) -> list[dict[str, Any]]:
    """Suggest state rules repeatedly observed before manual task creation."""
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    task_samples: Counter[str] = Counter()
    task_names: dict[str, str] = {}
    for occurrence in occurrences.values():
        if not occurrence.get("manual_creation"):
            continue
        task_id = str(occurrence.get("task_id", ""))
        task_samples[task_id] += 1
        task_names[task_id] = str(occurrence.get("task", {}).get("name", task_id))
        for context in occurrence.get("observation_context", []):
            key = (
                task_id,
                str(context.get("entity_id", "")),
                str(context.get("from", "")),
                str(context.get("to", "")),
            )
            if all(key):
                groups[key].append(context)

    suggestions = []
    for (task_id, entity_id, from_state, to_state), samples in groups.items():
        support = len(samples) / max(1, task_samples[task_id])
        if len(samples) < minimum_samples or support < 0.6:
            continue
        ages = sorted(max(0, int(item.get("age_seconds", 0))) for item in samples)
        median_age = ages[len(ages) // 2]
        suggestions.append(
            {
                "id": f"{task_id}:{entity_id}:{from_state}:{to_state}",
                "task_id": task_id,
                "task_name": task_names[task_id],
                "entity_id": entity_id,
                "from": from_state,
                "to": to_state,
                "samples": len(samples),
                "confidence": round(support, 2),
                "typical_delay_seconds": median_age,
                "proposed_schedule": {
                    "type": "state_trigger",
                    "triggers": [
                        {
                            "entity_id": entity_id,
                            "from": from_state,
                            "to": to_state,
                            "for": "00:00:00",
                        }
                    ],
                    "due_after": _duration(median_age),
                    "cooldown": "12:00:00",
                    "skip_if_open": True,
                },
            }
        )
    return sorted(
        suggestions,
        key=lambda item: (-int(item["samples"]), str(item["task_name"])),
    )[:20]


def _duration(seconds: int) -> str:
    hours, remainder = divmod(max(0, seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def dependency_graph(tasks: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Return a UI-safe graph with rule, output, entity, and person nodes."""
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    signatures: dict[str, list[str]] = defaultdict(list)

    def add_node(node_id: str, kind: str, label: str, **metadata: Any) -> None:
        nodes[node_id] = {"id": node_id, "kind": kind, "label": label, **metadata}

    def add_edge(source: str, target: str, kind: str, label: str) -> None:
        edges.append(
            {
                "id": f"{kind}:{source}:{target}",
                "source": source,
                "target": target,
                "kind": kind,
                "label": label,
            }
        )

    for task_id, task in tasks.items():
        rule_id = f"rule:{task_id}"
        output_id = f"task:{task_id}"
        add_node(rule_id, "rule", str(task.get("name", task_id)), task_id=task_id)
        add_node(output_id, "task", str(task.get("name", task_id)), task_id=task_id)
        add_edge(rule_id, output_id, "creates", "erzeugt")
        schedule = task.get("schedule", {})
        signature = repr((task.get("name"), schedule, task.get("weather")))
        signatures[signature].append(task_id)
        for entity_id in _task_entities(task):
            entity_node = f"entity:{entity_id}"
            add_node(entity_node, "entity", entity_id)
            add_edge(entity_node, rule_id, "triggers", "steuert")
        for dependency in task.get("depends_on", []):
            if dependency in tasks:
                add_edge(f"task:{dependency}", output_id, "blocks", "blockiert")
        for follow_up in task.get("follow_ups", []):
            target = follow_up.get("task_id") if isinstance(follow_up, dict) else None
            if target in tasks:
                add_edge(output_id, f"rule:{target}", "follows", "startet danach")
        for person_id in _task_people(task):
            person_node = f"person:{person_id}"
            add_node(person_node, "person", person_id)
            add_edge(output_id, person_node, "assigns", "weist zu")
        if contradiction := _weather_contradiction(task):
            issues.append(
                {
                    "type": "contradiction",
                    "severity": "critical",
                    "task_ids": [task_id],
                    "message": contradiction,
                }
            )

    for duplicate_ids in signatures.values():
        if len(duplicate_ids) > 1:
            issues.append(
                {
                    "type": "duplicate",
                    "severity": "warning",
                    "task_ids": duplicate_ids,
                    "message": "Mehrere Regeln erzeugen voraussichtlich dieselbe Aufgabe.",
                }
            )
    cycles = _cycles(tasks)
    issues.extend(
        {
            "type": "cycle",
            "severity": "critical",
            "task_ids": cycle,
            "message": "Zyklische Regelabhängigkeit erkannt.",
        }
        for cycle in cycles
    )
    return {"nodes": list(nodes.values()), "edges": edges, "issues": issues}


def _task_entities(task: dict[str, Any]) -> list[str]:
    values = []
    schedule = task.get("schedule", {})
    if schedule.get("entity_id"):
        values.append(str(schedule["entity_id"]))
    values.extend(
        str(item.get("entity_id"))
        for item in schedule.get("triggers", [])
        if isinstance(item, dict) and item.get("entity_id")
    )
    values.extend(
        str(item.get("entity_id"))
        for item in task.get("weather", {}).get("conditions", [])
        if isinstance(item, dict) and item.get("entity_id")
    )
    if task.get("season", {}).get("entity_id"):
        values.append(str(task["season"]["entity_id"]))
    return list(dict.fromkeys(values))


def _task_people(task: dict[str, Any]) -> list[str]:
    result = []
    if task.get("assignee"):
        result.append(str(task["assignee"]))
    assignment = task.get("assignment", {})
    result.extend(str(item) for item in assignment.get("people", []))
    result.extend(str(item) for item in assignment.get("fallback_people", []))
    return list(dict.fromkeys(result))


def _weather_contradiction(task: dict[str, Any]) -> str | None:
    """Detect impossible numeric bounds used together with AND logic."""
    weather = task.get("weather", {})
    if weather.get("logic", "all") != "all":
        return None
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for condition in weather.get("conditions", []):
        if not isinstance(condition, dict):
            continue
        key = (str(condition.get("entity_id", "")), str(condition.get("attribute", "")))
        if key[0]:
            grouped[key].append(condition)
    for (entity_id, attribute), conditions in grouped.items():
        lower: tuple[float, bool] | None = None
        upper: tuple[float, bool] | None = None
        for condition in conditions:
            operator = condition.get("condition")
            if operator not in {"above", "at_least", "below", "at_most"}:
                continue
            try:
                threshold = float(condition.get("threshold"))
            except (TypeError, ValueError):
                continue
            if operator in {"above", "at_least"}:
                candidate = (threshold, operator == "above")
                if (
                    lower is None
                    or candidate[0] > lower[0]
                    or (candidate[0] == lower[0] and candidate[1])
                ):
                    lower = candidate
            else:
                candidate = (threshold, operator == "below")
                if (
                    upper is None
                    or candidate[0] < upper[0]
                    or (candidate[0] == upper[0] and candidate[1])
                ):
                    upper = candidate
        if (
            lower
            and upper
            and (
                lower[0] > upper[0] or (lower[0] == upper[0] and (lower[1] or upper[1]))
            )
        ):
            target = f"{entity_id}.{attribute}" if attribute else entity_id
            return f"Widersprüchliche UND-Bedingungen für {target}: der Wertebereich ist leer."
    return None


def _cycles(tasks: dict[str, dict[str, Any]]) -> list[list[str]]:
    graph = {
        task_id: [
            *[str(item) for item in task.get("depends_on", []) if item in tasks],
            *[
                str(item.get("task_id"))
                for item in task.get("follow_ups", [])
                if isinstance(item, dict) and item.get("task_id") in tasks
            ],
        ]
        for task_id, task in tasks.items()
    }
    found: list[list[str]] = []
    visiting: list[str] = []
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            cycle = [*visiting[visiting.index(node) :], node]
            if cycle not in found:
                found.append(cycle)
            return
        if node in visited:
            return
        visiting.append(node)
        for target in graph[node]:
            visit(target)
        visiting.pop()
        visited.add(node)

    for node in graph:
        visit(node)
    return found


def decision_dossier(
    occurrence_id: str,
    occurrence: dict[str, Any],
    events: list[dict[str, Any]],
    people: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build an ordered, human-readable decision record for an occurrence."""
    task = occurrence.get("task", {})
    schedule = task.get("schedule", {})
    steps: list[dict[str, Any]] = []

    def step(kind: str, title: str, detail: str, passed: bool = True) -> None:
        steps.append({"kind": kind, "title": title, "detail": detail, "passed": passed})

    schedule_type = str(schedule.get("type", "manual"))
    if schedule_type == "calendar":
        step(
            "trigger",
            "Kalenderereignis erkannt",
            occurrence.get("calendar_summary", "Passender Termin"),
        )
        if schedule.get("match"):
            step("condition", "Kalenderfilter passte", str(schedule["match"]))
        step("offset", "Zeitversatz berechnet", str(schedule.get("offset", "00:00:00")))
    elif schedule_type in {"state_trigger", "daily_after_state"}:
        trace = occurrence.get("creation_trace") or {}
        trigger = trace.get("trigger") or (schedule.get("triggers") or [{}])[0]
        step(
            "trigger",
            "Zustandswechsel erkannt",
            f"{trigger.get('entity_id', 'Entität')}: {trigger.get('from', '*')} → {trigger.get('to', '*')}",
        )
    elif schedule_type in {"weather_trigger", "forecast_trigger"}:
        trace = occurrence.get("creation_trace") or {}
        for item in trace.get("trace", trace if isinstance(trace, list) else []):
            if isinstance(item, dict):
                step(
                    "condition",
                    str(item.get("step", "Wetterregel")),
                    str(item.get("message", "geprüft")),
                    bool(item.get("passed", True)),
                )
    else:
        step("trigger", "Regel ausgelöst", schedule_type)

    reason = occurrence.get("assignment_reason", {})
    original = reason.get("original")
    if (
        reason.get("type")
        in {"absence_assigned", "absence_open", "absence_fallback", "presence"}
        and original
    ):
        step(
            "assignment",
            "Fest zugeordnete Person nicht anwesend",
            people.get(original, {}).get("name", original),
            False,
        )
    configured = reason.get("configured_candidates", [])
    candidates = reason.get("candidates", [])
    for person_id in configured:
        if person_id not in candidates:
            step(
                "assignment",
                "Fallback-Person nicht berücksichtigt",
                f"{people.get(person_id, {}).get('name', person_id)} · nicht anwesend oder nicht verfügbar",
                False,
            )
    for excluded in reason.get("excluded", []):
        person_id = (
            excluded.get("person_id") if isinstance(excluded, dict) else excluded
        )
        detail = (
            excluded.get("reason", "nicht verfügbar")
            if isinstance(excluded, dict)
            else "nicht verfügbar"
        )
        step(
            "assignment",
            "Person nicht berücksichtigt",
            f"{people.get(person_id, {}).get('name', person_id)} · {detail}",
            False,
        )
    selected = occurrence.get("assignee")
    selected_name = people.get(selected, {}).get("name", selected or "Offene Aufgabe")
    strategy = {
        "absence_fallback": "aus Fallback-Gruppe gewählt",
        "absence_open": "zur freiwilligen Übernahme geöffnet",
        "fair": "nach aktueller Belastung gewählt",
        "rotation": "nach Rotation gewählt",
        "handover": "durch Haushaltsübergabe gewählt",
        "fixed": "fest zugeordnet",
        "presence": "wartet auf Anwesenheit",
        "open": "offen zur Übernahme",
    }.get(reason.get("type"), str(reason.get("type", "offen")))
    step("assignment", "Zuständigkeit entschieden", f"{selected_name} · {strategy}")
    step("creation", "Aufgabe erzeugt", str(occurrence.get("due", "")))
    for person_id in occurrence.get("notified_people", []):
        step(
            "notification",
            "Benachrichtigung zugestellt",
            people.get(person_id, {}).get("name", person_id),
        )
    for event in events:
        if event.get("type") == "task_created":
            continue
        step(
            "event",
            str(event.get("type", "Änderung")),
            str(event.get("occurred_at", "")),
        )
    return {
        "occurrence_id": occurrence_id,
        "task_id": occurrence.get("task_id"),
        "title": occurrence.get("title"),
        "steps": steps,
        "source": deepcopy(occurrence.get("creation_trace")),
    }


def recent_observation_context(
    observations: list[dict[str, Any]],
    now: datetime,
    *,
    window: timedelta = timedelta(minutes=30),
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Return the nearest recent state changes without exposing attributes."""
    result = []
    for item in reversed(observations):
        happened = datetime.fromisoformat(str(item["at"]).replace("Z", "+00:00"))
        age = now - happened.astimezone(now.tzinfo)
        if age > window:
            break
        copy = deepcopy(item)
        copy["age_seconds"] = max(0, int(age.total_seconds()))
        result.append(copy)
        if len(result) >= limit:
            break
    return result
