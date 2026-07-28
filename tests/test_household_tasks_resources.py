"""Tests for generic resource and consumption monitors."""

from custom_components.household_tasks.resources import (
    render_resource_text,
    resource_condition_matches,
)


def test_numeric_resource_conditions():
    assert resource_condition_matches("14.5", "below", 20)
    assert resource_condition_matches(20, "at_most", 20)
    assert resource_condition_matches(81, "above", 80)
    assert resource_condition_matches(80, "at_least", 80)
    assert not resource_condition_matches("unknown", "below", 20)


def test_text_resource_conditions_are_case_insensitive():
    assert resource_condition_matches("Low", "equals", "low")
    assert resource_condition_matches("ready", "not_equals", "empty")


def test_resource_text_uses_state_and_unit():
    assert (
        render_resource_text("Füllstand: {state} {unit}", 12, "%") == "Füllstand: 12 %"
    )
