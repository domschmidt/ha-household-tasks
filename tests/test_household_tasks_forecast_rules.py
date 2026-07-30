"""Tests for forecast planning and seasonal execution keys."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from custom_components.household_tasks.forecast_rules import (
    forecast_activation,
    forecast_decision,
    season_key,
)


def test_first_matching_daily_forecast_is_selected_with_a_full_trace():
    """The earliest matching frost day wins and all examined days stay visible."""
    rule = {
        "logic": "all",
        "conditions": [
            {
                "entity_id": "weather.home",
                "attribute": "templow",
                "condition": "below",
                "threshold": 0,
            }
        ],
    }
    forecasts = {
        "weather.home": [
            {"datetime": "2026-10-10T00:00:00+02:00", "templow": 4},
            {"datetime": "2026-10-11T00:00:00+02:00", "templow": -2},
            {"datetime": "2026-10-12T00:00:00+02:00", "templow": -4},
        ]
    }

    result = forecast_decision(
        rule,
        forecasts,
        now=datetime(2026, 10, 9, 17, 0, tzinfo=ZoneInfo("Europe/Berlin")),
        horizon_hours=96,
    )

    assert result["allowed"]
    assert result["matched_period"]["date"] == "2026-10-11"
    assert [period["allowed"] for period in result["periods"]] == [
        False,
        True,
        True,
    ]
    assert result["matched_period"]["conditions"][0]["current"] == -2


def test_forecast_conditions_align_multiple_entities_by_local_day():
    """Complex AND rules never combine values from different forecast days."""
    rule = {
        "logic": "all",
        "conditions": [
            {
                "entity_id": "weather.home",
                "attribute": "templow",
                "condition": "below",
                "threshold": 0,
            },
            {
                "entity_id": "weather.warning",
                "attribute": "precipitation_probability",
                "condition": "above",
                "threshold": 40,
            },
        ],
    }
    forecasts = {
        "weather.home": [
            {"datetime": "2026-11-01T00:00:00+01:00", "templow": -1},
            {"datetime": "2026-11-02T00:00:00+01:00", "templow": 3},
        ],
        "weather.warning": [
            {
                "datetime": "2026-11-01T00:00:00+01:00",
                "precipitation_probability": 10,
            },
            {
                "datetime": "2026-11-02T00:00:00+01:00",
                "precipitation_probability": 80,
            },
        ],
    }

    result = forecast_decision(
        rule,
        forecasts,
        now=datetime(2026, 10, 31, 12, 0, tzinfo=UTC),
        horizon_hours=72,
    )

    assert not result["allowed"]
    assert all(not period["allowed"] for period in result["periods"])


def test_missing_forecast_values_are_explicitly_unavailable():
    """Missing attributes cannot accidentally satisfy a numeric condition."""
    result = forecast_decision(
        {
            "conditions": [
                {
                    "entity_id": "weather.home",
                    "attribute": "templow",
                    "condition": "below",
                    "threshold": 0,
                }
            ]
        },
        {"weather.home": [{"datetime": "2026-10-11T00:00:00+02:00", "temperature": 8}]},
        now=datetime(2026, 10, 10, 12, 0, tzinfo=UTC),
        horizon_hours=48,
    )

    assert not result["allowed"]
    assert result["code"] == "forecast_unavailable"
    assert not result["periods"][0]["conditions"][0]["available"]


def test_hourly_forecasts_do_not_collapse_different_hours_of_one_day():
    """Hourly rules select the first matching hour instead of the day's last."""
    result = forecast_decision(
        {
            "conditions": [
                {
                    "entity_id": "weather.home",
                    "attribute": "temperature",
                    "condition": "below",
                    "threshold": 0,
                }
            ]
        },
        {
            "weather.home": [
                {"datetime": "2026-10-10T20:00:00+00:00", "temperature": 4},
                {"datetime": "2026-10-10T23:00:00+00:00", "temperature": -1},
                {"datetime": "2026-10-11T02:00:00+00:00", "temperature": 2},
            ]
        },
        now=datetime(2026, 10, 10, 18, 0, tzinfo=UTC),
        horizon_hours=12,
        forecast_type="hourly",
    )

    assert result["matched_period"]["datetime"] == "2026-10-10T23:00:00+00:00"
    assert len(result["periods"]) == 3


def test_winter_season_key_crosses_year_and_resets_next_autumn():
    """October through March shares one key and resets in the next October."""
    months = [10, 11, 12, 1, 2, 3]

    assert season_key(datetime(2026, 10, 1, tzinfo=UTC), months) == "2026-2027"
    assert season_key(datetime(2027, 2, 1, tzinfo=UTC), months) == "2026-2027"
    assert season_key(datetime(2027, 10, 1, tzinfo=UTC), months) == "2027-2028"


def test_activation_uses_local_previous_day_across_dst():
    """The previous-evening plan is based on calendar days, not 24-hour math."""
    activation = forecast_activation(
        "2026-10-26T00:00:00+01:00",
        lead_days=1,
        activation_time=datetime.strptime("18:00:00", "%H:%M:%S").time(),
        zone=ZoneInfo("Europe/Berlin"),
    )

    assert activation.isoformat() == "2026-10-25T18:00:00+01:00"
