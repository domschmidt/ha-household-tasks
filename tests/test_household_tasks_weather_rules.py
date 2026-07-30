"""Tests for combined weather and climate conditions."""

from custom_components.household_tasks.weather_rules import weather_decision


def test_temperature_sensor_thresholds_support_below_and_above():
    """Plain sensor states can trigger low- and high-temperature rules."""
    states = {
        "sensor.outside_temperature": {
            "state": "-2.5",
            "attributes": {"unit_of_measurement": "°C"},
        }
    }
    frost = {
        "logic": "all",
        "conditions": [
            {
                "entity_id": "sensor.outside_temperature",
                "condition": "below",
                "threshold": 2,
            }
        ],
    }
    heat = {
        "logic": "all",
        "conditions": [
            {
                "entity_id": "sensor.outside_temperature",
                "condition": "above",
                "threshold": 28,
            }
        ],
    }

    assert weather_decision(frost, states)["allowed"]
    assert not weather_decision(heat, states)["allowed"]


def test_weather_attributes_combine_with_all_logic():
    """A complex frost-and-rain rule requires every weather attribute."""
    rule = {
        "logic": "all",
        "conditions": [
            {
                "entity_id": "weather.home",
                "attribute": "temperature",
                "condition": "below",
                "threshold": 1,
            },
            {
                "entity_id": "weather.home",
                "attribute": "precipitation_probability",
                "condition": "above",
                "threshold": 30,
            },
            {
                "entity_id": "weather.home",
                "attribute": "wind_speed",
                "condition": "at_most",
                "threshold": 80,
            },
        ],
    }
    matching = {
        "weather.home": {
            "state": "rainy",
            "attributes": {
                "temperature": 0,
                "precipitation_probability": 75,
                "wind_speed": 35,
            },
        }
    }
    dry = {
        "weather.home": {
            "state": "cloudy",
            "attributes": {
                "temperature": 0,
                "precipitation_probability": 10,
                "wind_speed": 35,
            },
        }
    }

    assert weather_decision(rule, matching)["allowed"]
    decision = weather_decision(rule, dry)
    assert not decision["allowed"]
    assert [item["matches"] for item in decision["conditions"]] == [
        True,
        False,
        True,
    ]


def test_any_logic_accepts_multiple_weather_state_spellings():
    """Text conditions can model alternative Home Assistant weather states."""
    rule = {
        "logic": "any",
        "conditions": [
            {
                "entity_id": "weather.home",
                "condition": "equals",
                "threshold": "snow",
            },
            {
                "entity_id": "weather.home",
                "condition": "equals",
                "threshold": "snowy",
            },
        ],
    }

    decision = weather_decision(
        rule,
        {"weather.home": {"state": "snowy", "attributes": {}}},
    )

    assert decision["allowed"]
    assert decision["logic"] == "any"


def test_missing_attribute_is_explicitly_unavailable():
    """Missing forecast attributes never accidentally satisfy a rule."""
    rule = {
        "logic": "all",
        "conditions": [
            {
                "entity_id": "weather.home",
                "attribute": "wind_speed",
                "condition": "above",
                "threshold": 60,
            }
        ],
    }

    decision = weather_decision(
        rule,
        {"weather.home": {"state": "windy", "attributes": {}}},
    )

    assert not decision["allowed"]
    assert decision["code"] == "weather_entity_unavailable"
    assert not decision["conditions"][0]["available"]


def test_no_weather_rule_keeps_non_weather_tasks_eligible():
    """Adding weather support does not change ordinary task behavior."""
    decision = weather_decision(None, {})

    assert decision["allowed"]
    assert decision["code"] == "no_weather_rule"
