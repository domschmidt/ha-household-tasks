"""Pure helpers for household modes, seasonal tasks, and configuration health."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

HOUSEHOLD_MODES = {"normal", "vacation", "guest"}
MODE_POLICIES = {"pause", "reduce", "delegate"}
PRIORITIES = {"low", "normal", "high", "critical"}
SEASON_CONDITIONS = {"below", "at_most", "above", "at_least", "equals", "not_equals"}


def task_activation_decision(
    task: dict[str, Any], reference: datetime | None = None
) -> dict[str, Any]:
    """Explain whether a template is enabled at a point in time."""
    if task.get("enabled", True) is False:
        return {
            "allowed": False,
            "code": "template_disabled",
            "message": "Die Aufgabenvorlage ist dauerhaft deaktiviert.",
            "paused_until": None,
        }

    raw_until = task.get("paused_until")
    if not raw_until:
        return {
            "allowed": True,
            "code": "template_active",
            "message": "Die Aufgabenvorlage ist aktiv.",
            "paused_until": None,
        }
    try:
        paused_until = datetime.fromisoformat(str(raw_until).replace("Z", "+00:00"))
    except ValueError:
        return {
            "allowed": False,
            "code": "template_pause_invalid",
            "message": "Der Pausenzeitpunkt ist ungültig.",
            "paused_until": str(raw_until),
        }

    if paused_until.tzinfo is None:
        paused_until = paused_until.replace(tzinfo=UTC)
    moment = reference or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    paused = moment.astimezone(UTC) < paused_until.astimezone(UTC)
    return {
        "allowed": not paused,
        "code": "template_paused" if paused else "template_pause_elapsed",
        "message": (
            f"Die Aufgabenvorlage ist bis {paused_until.isoformat()} pausiert."
            if paused
            else "Die zeitlich begrenzte Pause ist beendet."
        ),
        "paused_until": paused_until.isoformat(),
    }


def default_household_mode() -> dict[str, Any]:
    """Return an isolated default household mode."""
    return {
        "mode": "normal",
        "policy": "pause",
        "delegate_to": None,
        "until": None,
        "note": None,
        "changed_at": None,
    }


def _guest_decision(task_modes: dict[str, Any]) -> dict[str, Any]:
    """Return guest-mode eligibility."""
    allowed = not task_modes.get("skip_in_guest", False)
    return {
        "allowed": allowed,
        "code": "guest_allowed" if allowed else "guest_skipped",
        "message": (
            "Die Aufgabe ist im Gastmodus aktiv."
            if allowed
            else "Die Aufgabe ist im Gastmodus deaktiviert."
        ),
    }


def _vacation_decision(
    behavior: str,
    priority: str,
    mode: dict[str, Any],
) -> dict[str, Any]:
    """Return vacation-mode eligibility for one configured behavior."""
    if behavior == "always":
        return {
            "allowed": True,
            "code": "vacation_always",
            "message": "Die Aufgabe bleibt im Urlaubsmodus aktiv.",
        }
    if behavior == "reduce":
        allowed = priority in {"high", "critical"}
        return {
            "allowed": allowed,
            "code": "vacation_essential" if allowed else "vacation_reduced",
            "message": (
                "Die Aufgabe bleibt wegen ihrer hohen Priorität aktiv."
                if allowed
                else "Die Aufgabe wurde im reduzierten Urlaubsmodus ausgelassen."
            ),
        }
    if behavior == "delegate":
        delegate_to = mode.get("delegate_to")
        return {
            "allowed": bool(delegate_to),
            "code": (
                "vacation_delegated" if delegate_to else "vacation_delegate_missing"
            ),
            "message": (
                "Die Aufgabe wird im Urlaubsmodus delegiert."
                if delegate_to
                else "Für die Urlaubsdelegation ist keine Person ausgewählt."
            ),
            "delegate_to": delegate_to,
        }
    return {
        "allowed": False,
        "code": "vacation_paused",
        "message": "Die Aufgabe ist im Urlaubsmodus pausiert.",
    }


def mode_decision(
    mode: dict[str, Any],
    task: dict[str, Any],
) -> dict[str, Any]:
    """Explain whether a task may run in the active household mode."""
    current = mode.get("mode", "normal")
    task_modes = task.get("modes", {})
    priority = task.get("market", {}).get("priority", task.get("priority", "normal"))

    if task_modes.get("guest_only") and current != "guest":
        return {
            "allowed": False,
            "code": "guest_only",
            "message": "Die Aufgabe ist nur im Gastmodus aktiv.",
        }
    if current == "normal":
        return {
            "allowed": True,
            "code": "normal",
            "message": "Normalbetrieb ist aktiv.",
        }
    if current == "guest":
        return _guest_decision(task_modes)

    behavior = task_modes.get("vacation", mode.get("policy", "pause"))
    return _vacation_decision(behavior, priority, mode)


def season_decision(
    task: dict[str, Any],
    reference: datetime,
    state_value: str | None = None,
) -> dict[str, Any]:
    """Explain whether a seasonal task is currently applicable."""
    season = task.get("season")
    if not season:
        return {
            "allowed": True,
            "code": "no_season",
            "message": "Für die Aufgabe gilt keine saisonale Einschränkung.",
        }
    months = [int(month) for month in season.get("months", [])]
    if months and reference.month not in months:
        return {
            "allowed": False,
            "code": "outside_season",
            "message": "Der aktuelle Monat liegt außerhalb der konfigurierten Saison.",
        }
    condition = season.get("condition")
    if not condition or not season.get("entity_id"):
        return {
            "allowed": True,
            "code": "season_active",
            "message": "Die saisonale Zeitspanne ist aktiv.",
        }
    if state_value is None:
        return {
            "allowed": False,
            "code": "season_entity_unavailable",
            "message": "Die saisonale Entität ist nicht verfügbar.",
        }
    threshold = str(season.get("threshold", ""))
    numeric = condition in {"below", "at_most", "above", "at_least"}
    try:
        left: Any = float(state_value) if numeric else state_value.casefold()
        right: Any = float(threshold) if numeric else threshold.casefold()
    except (TypeError, ValueError):
        return {
            "allowed": False,
            "code": "season_value_invalid",
            "message": "Saisonwert und Grenzwert können nicht verglichen werden.",
        }
    matches = {
        "below": left < right,
        "at_most": left <= right,
        "above": left > right,
        "at_least": left >= right,
        "equals": left == right,
        "not_equals": left != right,
    }.get(condition, False)
    return {
        "allowed": matches,
        "code": "season_condition_met" if matches else "season_condition_not_met",
        "message": (
            "Die saisonale Bedingung ist erfüllt."
            if matches
            else "Die saisonale Bedingung ist derzeit nicht erfüllt."
        ),
        "current": state_value,
        "threshold": threshold,
    }


def dependency_cycles(tasks: dict[str, dict[str, Any]]) -> list[list[str]]:
    """Return cycles in the follow-up and prerequisite graph."""
    graph = {
        task_id: list(
            dict.fromkeys(
                [
                    str(follow_up.get("task_id"))
                    for follow_up in task.get("follow_ups", [])
                    if isinstance(follow_up, dict) and follow_up.get("task_id") in tasks
                ]
                + [
                    str(dependency)
                    for dependency in task.get("depends_on", [])
                    if dependency in tasks
                ]
            )
        )
        for task_id, task in tasks.items()
    }
    cycles: list[list[str]] = []
    visiting: list[str] = []
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            cycle = [*visiting[visiting.index(node) :], node]
            if cycle not in cycles:
                cycles.append(cycle)
            return
        if node in visited:
            return
        visiting.append(node)
        for target in graph[node]:
            visit(target)
        visiting.pop()
        visited.add(node)

    for task_id in graph:
        visit(task_id)
    return cycles


def _weather_template(
    template_id: str,
    name: str,
    task_name: str,
    description: str,
    conditions: list[dict[str, Any]],
    *,
    priority: str = "high",
    cooldown: str = "24:00:00",
    logic: str = "all",
    category: str = "Wetter",
) -> dict[str, Any]:
    """Build one isolated weather template with conservative defaults."""
    return {
        "id": template_id,
        "category": category,
        "name": name,
        "description": description,
        "task": {
            "enabled": True,
            "name": task_name,
            "assignment": {"type": "open"},
            "schedule": {
                "type": "weather_trigger",
                "due_after": "00:00:00",
                "cooldown": cooldown,
                "skip_if_open": True,
            },
            "weather": {"logic": logic, "conditions": conditions},
            "market": {"priority": priority, "points": 2},
        },
    }


def template_gallery() -> list[dict[str, Any]]:
    """Return privacy-safe templates users can copy into their configuration."""
    templates = [
        {
            "id": "first_frost_personal_vehicle",
            "category": "Vorhersage",
            "name": "Erster Frost: eigenes Auto prüfen",
            "description": (
                "Erzeugt am Vorabend des ersten vorhergesagten Frosttags für "
                "jede ausgewählte Person genau eine Aufgabe pro Wintersaison."
            ),
            "task": {
                "enabled": True,
                "name": "Frostschutz beim eigenen Auto prüfen",
                "description": (
                    "Frostschutz, Scheibenwaschanlage, Eiskratzer und "
                    "Winterausrüstung am eigenen Auto prüfen."
                ),
                "assignment": {"type": "per_person", "people": []},
                "schedule": {
                    "type": "forecast_trigger",
                    "forecast_type": "daily",
                    "horizon_hours": 48,
                    "lead_days": 1,
                    "time": "18:00:00",
                    "cooldown": "24:00:00",
                    "skip_if_open": True,
                },
                "weather": {
                    "logic": "all",
                    "conditions": [
                        {
                            "entity_id": "",
                            "attribute": "templow",
                            "condition": "below",
                            "threshold": 0,
                        }
                    ],
                },
                "season": {"months": [10, 11, 12, 1, 2, 3]},
                "repeat": {"mode": "once_per_season"},
                "market": {"priority": "high", "points": 2},
            },
        },
        {
            "id": "frostschutz",
            "category": "Saisonal",
            "name": "Außenwasser bei Frost sichern",
            "description": "Erzeugt im Winter eine Aufgabe, wenn eine Frostwarnung aktiv wird.",
            "task": {
                "enabled": True,
                "name": "Außenwasser abstellen",
                "assignment": {"type": "fixed"},
                "schedule": {
                    "type": "weather_trigger",
                    "due_after": "00:00:00",
                    "cooldown": "24:00:00",
                    "skip_if_open": True,
                },
                "weather": {
                    "logic": "all",
                    "conditions": [
                        {
                            "entity_id": "",
                            "attribute": "",
                            "condition": "below",
                            "threshold": 2,
                        }
                    ],
                },
                "season": {"months": [10, 11, 12, 1, 2, 3]},
                "market": {"priority": "critical", "points": 3},
            },
        },
        {
            "id": "pollenfilter",
            "category": "Saisonal",
            "name": "Pollenfilter prüfen",
            "description": "Reagiert auf eine Pollenwarnung während der typischen Pollensaison.",
            "task": {
                "enabled": True,
                "name": "Pollenfilter prüfen",
                "assignment": {"type": "fair"},
                "schedule": {
                    "type": "state_trigger",
                    "triggers": [{"entity_id": "", "to": "high"}],
                    "skip_if_open": True,
                },
                "season": {"months": [2, 3, 4, 5, 6, 7, 8, 9]},
                "market": {"priority": "high", "points": 2},
            },
        },
        {
            "id": "reifenwechsel",
            "category": "Saisonal",
            "name": "Winterreifen wechseln",
            "description": "Plant den Reifenwechsel jährlich zum Beginn der kalten Saison.",
            "task": {
                "enabled": True,
                "name": "Winterreifen wechseln",
                "assignment": {"type": "open"},
                "schedule": {
                    "type": "yearly",
                    "month": 10,
                    "day": 1,
                    "time": "18:00:00",
                },
                "season": {"months": [9, 10, 11]},
                "market": {"priority": "high", "points": 5, "reward": ""},
            },
        },
        {
            "id": "gaestezimmer",
            "category": "Gastmodus",
            "name": "Gästezimmer vorbereiten",
            "description": "Ist ausschließlich aktiv, solange der Gastmodus eingeschaltet ist.",
            "task": {
                "enabled": True,
                "name": "Gästezimmer vorbereiten",
                "assignment": {"type": "open"},
                "schedule": {"type": "manual"},
                "modes": {"guest_only": True},
                "market": {"priority": "normal", "points": 2},
            },
        },
        {
            "id": "wochenroutine",
            "category": "Routine",
            "name": "Wochenabschluss",
            "description": "Eine offene Wochenroutine mit einer kurzen, anpassbaren Checkliste.",
            "task": {
                "enabled": True,
                "name": "Haushalts-Wochenabschluss",
                "assignment": {"type": "open"},
                "schedule": {"type": "weekly", "weekdays": ["sun"], "time": "18:00:00"},
                "checklist": [
                    "Offene Aufgaben prüfen",
                    "Vorräte prüfen",
                    "Nächste Woche abstimmen",
                ],
                "market": {"priority": "normal", "points": 3},
            },
        },
    ]
    templates.extend(
        [
            _weather_template(
                "hitzeschutz_garten",
                "Garten bei Hitze versorgen",
                "Garten auf Hitze vorbereiten",
                "Erzeugt bei hoher Außentemperatur eine Gieß- und Schattenaufgabe.",
                [
                    {
                        "entity_id": "",
                        "attribute": "",
                        "condition": "above",
                        "threshold": 28,
                    }
                ],
            ),
            _weather_template(
                "sturm_sichern",
                "Außenbereich vor Sturm sichern",
                "Lose Gegenstände draußen sichern",
                "Reagiert auf hohe Windgeschwindigkeit einer Wetterentität.",
                [
                    {
                        "entity_id": "",
                        "attribute": "wind_speed",
                        "condition": "above",
                        "threshold": 60,
                    }
                ],
                priority="critical",
                cooldown="12:00:00",
            ),
            _weather_template(
                "regen_fenster",
                "Fenster vor Starkregen prüfen",
                "Offene Fenster und Dachfenster prüfen",
                "Nutzt die Niederschlagswahrscheinlichkeit der Wetterentität.",
                [
                    {
                        "entity_id": "",
                        "attribute": "precipitation_probability",
                        "condition": "at_least",
                        "threshold": 70,
                    }
                ],
                cooldown="06:00:00",
            ),
            _weather_template(
                "frost_und_niederschlag",
                "Glatteisvorsorge",
                "Wege auf Glätte prüfen und streuen",
                "Kombiniert Temperatur und Niederschlag mit UND.",
                [
                    {
                        "entity_id": "",
                        "attribute": "temperature",
                        "condition": "below",
                        "threshold": 1,
                    },
                    {
                        "entity_id": "",
                        "attribute": "precipitation_probability",
                        "condition": "above",
                        "threshold": 30,
                    },
                ],
                priority="critical",
                cooldown="12:00:00",
            ),
            _weather_template(
                "luften_bei_feuchte",
                "Bei hoher Luftfeuchte lüften",
                "Stoßlüften",
                "Erzeugt eine Lüftungsaufgabe oberhalb des Grenzwerts.",
                [
                    {
                        "entity_id": "",
                        "attribute": "",
                        "condition": "above",
                        "threshold": 65,
                    }
                ],
                priority="normal",
                cooldown="04:00:00",
                category="Klima",
            ),
            _weather_template(
                "uv_schutz",
                "Sonnenschutz bei hohem UV-Index",
                "Sonnenschutz vorbereiten",
                "Erinnert an Markisen, Beschattung oder Sonnenschutz.",
                [
                    {
                        "entity_id": "",
                        "attribute": "",
                        "condition": "at_least",
                        "threshold": 6,
                    }
                ],
                priority="normal",
            ),
            _weather_template(
                "schnee_raeumen",
                "Schnee räumen",
                "Eingang und Gehweg von Schnee räumen",
                "Reagiert auf die Wetterzustände snowy oder snow.",
                [
                    {
                        "entity_id": "",
                        "attribute": "",
                        "condition": "equals",
                        "threshold": "snowy",
                    },
                    {
                        "entity_id": "",
                        "attribute": "",
                        "condition": "equals",
                        "threshold": "snow",
                    },
                ],
                logic="any",
                cooldown="06:00:00",
            ),
            _weather_template(
                "haustiere_hitze",
                "Haustiere bei Hitze schützen",
                "Wasser und kühlen Rückzugsort für Haustiere prüfen",
                "Erinnert bei hohen Temperaturen an Wasser und Schatten.",
                [
                    {
                        "entity_id": "",
                        "attribute": "temperature",
                        "condition": "at_least",
                        "threshold": 27,
                    }
                ],
                priority="critical",
                cooldown="12:00:00",
            ),
        ]
    )
    return deepcopy(templates)
