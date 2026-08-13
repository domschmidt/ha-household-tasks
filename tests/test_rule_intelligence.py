"""Tests for explainable rules, observation learning, and community packs."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
import voluptuous as vol
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from custom_components.household_tasks.community_templates import (
    is_newer,
    substitute_entities,
    validate_pack,
    validate_resolved_host,
    validate_source_url,
)
from custom_components.household_tasks.rule_intelligence import (
    decision_dossier,
    dependency_graph,
    observation_suggestions,
    recent_observation_context,
)


def test_observation_suggestions_require_repeated_high_support_patterns():
    """A coincidental state change must not become an automation suggestion."""
    occurrences = {}
    for index in range(4):
        contexts = [
            {
                "entity_id": "binary_sensor.washer",
                "from": "on",
                "to": "off",
                "at": f"2026-08-0{index + 1}T12:00:00+00:00",
                "age_seconds": 300,
            }
        ]
        if index == 0:
            contexts.append(
                {
                    "entity_id": "switch.coffee",
                    "from": "off",
                    "to": "on",
                    "at": "2026-08-01T11:59:00+00:00",
                    "age_seconds": 60,
                }
            )
        occurrences[str(index)] = {
            "task_id": "laundry",
            "manual_creation": True,
            "task": {"name": "Wäsche ausräumen"},
            "observation_context": contexts,
        }
    suggestions = observation_suggestions(occurrences)
    assert len(suggestions) == 1
    assert suggestions[0]["entity_id"] == "binary_sensor.washer"
    assert suggestions[0]["samples"] == 4
    assert suggestions[0]["confidence"] == 1
    assert suggestions[0]["proposed_schedule"]["due_after"] == "00:05:00"


def test_recent_observation_context_is_bounded_and_attribute_free():
    now = datetime(2026, 8, 11, 12, tzinfo=UTC)
    result = recent_observation_context(
        [
            {
                "entity_id": "switch.old",
                "from": "off",
                "to": "on",
                "at": (now - timedelta(hours=1)).isoformat(),
            },
            {
                "entity_id": "switch.recent",
                "from": "off",
                "to": "on",
                "at": (now - timedelta(minutes=5)).isoformat(),
            },
        ],
        now,
    )
    assert result == [
        {
            "entity_id": "switch.recent",
            "from": "off",
            "to": "on",
            "at": "2026-08-11T11:55:00+00:00",
            "age_seconds": 300,
        }
    ]


def test_dependency_graph_exposes_entities_outputs_cycles_and_duplicates():
    tasks = {
        "wash": {
            "name": "Laundry",
            "assignee": "alex",
            "schedule": {
                "type": "state_trigger",
                "triggers": [{"entity_id": "binary_sensor.washer", "to": "off"}],
            },
            "follow_ups": [{"task_id": "fold"}],
        },
        "fold": {
            "name": "Fold",
            "schedule": {"type": "manual"},
            "depends_on": ["wash"],
            "follow_ups": [{"task_id": "wash"}],
        },
        "duplicate": {
            "name": "Laundry",
            "schedule": {
                "type": "state_trigger",
                "triggers": [{"entity_id": "binary_sensor.washer", "to": "off"}],
            },
        },
    }
    graph = dependency_graph(tasks)
    assert any(node["id"] == "entity:binary_sensor.washer" for node in graph["nodes"])
    assert any(edge["kind"] == "creates" for edge in graph["edges"])
    assert {issue["type"] for issue in graph["issues"]} == {"cycle", "duplicate"}


def test_dependency_graph_detects_impossible_and_weather_bounds():
    graph = dependency_graph(
        {
            "impossible": {
                "name": "Impossible",
                "schedule": {"type": "weather_trigger"},
                "weather": {
                    "logic": "all",
                    "conditions": [
                        {
                            "entity_id": "sensor.temperature",
                            "condition": "above",
                            "threshold": 10,
                        },
                        {
                            "entity_id": "sensor.temperature",
                            "condition": "below",
                            "threshold": 5,
                        },
                    ],
                },
            }
        }
    )
    assert graph["issues"] == [
        {
            "type": "contradiction",
            "severity": "critical",
            "task_ids": ["impossible"],
            "message": "Widersprüchliche UND-Bedingungen für sensor.temperature: der Wertebereich ist leer.",
        }
    ]


def test_decision_dossier_orders_trigger_assignment_creation_and_delivery():
    dossier = decision_dossier(
        "one",
        {
            "task_id": "waste",
            "title": "Waste",
            "due": "2026-08-11T18:00:00+00:00",
            "calendar_summary": "Restmüll",
            "task": {
                "schedule": {
                    "type": "calendar",
                    "match": "rest.*",
                    "offset": "-12:00:00",
                }
            },
            "assignment_reason": {"type": "fixed"},
            "assignee": "alex",
            "notified_people": ["alex"],
        },
        [],
        {"alex": {"name": "Alex"}},
    )
    assert [item["kind"] for item in dossier["steps"]] == [
        "trigger",
        "condition",
        "offset",
        "assignment",
        "creation",
        "notification",
    ]


def test_decision_dossier_explains_presence_fallback_selection():
    dossier = decision_dossier(
        "one",
        {
            "task_id": "bins",
            "title": "Bins",
            "due": "2026-08-11T18:00:00+00:00",
            "task": {"schedule": {"type": "weekly"}},
            "assignment_reason": {
                "type": "absence_fallback",
                "original": "alina",
                "configured_candidates": ["dominik", "valentina"],
                "candidates": ["dominik"],
            },
            "assignee": "dominik",
        },
        [],
        {
            "alina": {"name": "Alina"},
            "dominik": {"name": "Dominik"},
            "valentina": {"name": "Valentina"},
        },
    )
    assignment_steps = [
        item for item in dossier["steps"] if item["kind"] == "assignment"
    ]
    assert assignment_steps[0] == {
        "kind": "assignment",
        "title": "Fest zugeordnete Person nicht anwesend",
        "detail": "Alina",
        "passed": False,
    }
    assert "Valentina" in assignment_steps[1]["detail"]
    assert assignment_steps[-1]["detail"] == "Dominik · aus Fallback-Gruppe gewählt"


def test_signed_community_pack_is_verified_and_placeholders_are_substituted():
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    payload = {
        "kind": "household_tasks_template_pack",
        "schema_version": 1,
        "id": "example.pack",
        "name": "Example Pack",
        "version": "1.2.3",
        "publisher": {
            "id": "example.publisher",
            "name": "Example Publisher",
            "public_key": base64.b64encode(public_key).decode(),
        },
        "templates": [
            {
                "id": "washer",
                "name": "Washer",
                "required_entities": [
                    {"key": "washer", "name": "Washer", "domains": ["binary_sensor"]}
                ],
                "task": {
                    "name": "Empty washer",
                    "schedule": {
                        "type": "state_trigger",
                        "triggers": [{"entity_id": "{{washer}}", "to": "off"}],
                    },
                },
            }
        ],
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    payload["signature"] = {
        "algorithm": "ed25519",
        "value": base64.b64encode(private_key.sign(canonical)).decode(),
    }
    preview = validate_pack(payload, {})
    assert preview["version"] == "1.2.3"
    assert preview["publisher"]["trusted"] is False
    task = substitute_entities(
        preview["templates"][0]["task"], {"washer": "binary_sensor.washer"}
    )
    assert task["schedule"]["triggers"][0]["entity_id"] == "binary_sensor.washer"
    payload["templates"][0]["name"] = "Tampered"
    with pytest.raises(vol.Invalid, match="Signatur"):
        validate_pack(payload, {})


def test_community_source_and_version_boundaries():
    assert is_newer("2.0.0", "1.9.9")
    assert not is_newer("1.0.0", "1.0.0")
    assert validate_source_url("https://example.com/pack.json")[1] == "example.com"
    with pytest.raises(vol.Invalid):
        validate_source_url("http://example.com/pack.json")
    with pytest.raises(vol.Invalid):
        validate_source_url("https://127.0.0.1/pack.json")


@pytest.mark.asyncio
async def test_community_dns_validation_rejects_empty_private_and_failed_results():
    loop = __import__("asyncio").get_running_loop()
    with (
        patch.object(loop, "getaddrinfo", AsyncMock(return_value=[])),
        pytest.raises(vol.Invalid, match="keine Adresse"),
    ):
        await validate_resolved_host("example.com")
    private_record = [(None, None, None, None, ("127.0.0.1", 443))]
    with (
        patch.object(loop, "getaddrinfo", AsyncMock(return_value=private_record)),
        pytest.raises(vol.Invalid, match="Lokale oder private"),
    ):
        await validate_resolved_host("example.com")
    with (
        patch.object(loop, "getaddrinfo", AsyncMock(side_effect=OSError)),
        pytest.raises(vol.Invalid, match="aufgelöst"),
    ):
        await validate_resolved_host("example.com")


def test_community_validation_rejects_malformed_sources_and_placeholders():
    invalid_urls = [
        "https://user:secret@example.com/pack.json",
        "https://example.com:444/pack.json",
        "not-a-url",
    ]
    for url in invalid_urls:
        with pytest.raises(vol.Invalid):
            validate_source_url(url)
    with pytest.raises(vol.Invalid, match="erforderliche Entität"):
        substitute_entities({"entity_id": "{{missing}}"}, {})
    assert substitute_entities(
        {"items": ["plain", {"entity_id": "{{sensor}}"}]},
        {"sensor": "sensor.valid"},
    ) == {"items": ["plain", {"entity_id": "sensor.valid"}]}
    assert not is_newer("invalid", "1.0.0")
