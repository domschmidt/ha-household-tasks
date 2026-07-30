"""Pure forecast evaluation and seasonal execution helpers."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, tzinfo
from typing import Any

from .resources import resource_condition_matches


def _forecast_datetime(value: Any, zone: tzinfo | None) -> datetime | None:
    """Normalize Home Assistant daily and hourly forecast timestamps."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min)
    else:
        text = str(value or "").strip().replace("Z", "+00:00")
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None and zone is not None:
        parsed = parsed.replace(tzinfo=zone)
    return parsed


def season_key(reference: datetime, months: list[int]) -> str:
    """Return a stable key for a possibly year-spanning month season."""
    normalized = sorted({int(month) for month in months if 1 <= int(month) <= 12})
    if not normalized:
        return str(reference.year)
    if len(normalized) == 12:
        return str(reference.year)

    # A season starts after the largest inactive gap. This turns
    # [10, 11, 12, 1, 2, 3] into an October-to-March season.
    gaps = []
    for index, month in enumerate(normalized):
        following = normalized[(index + 1) % len(normalized)]
        distance = (following - month) % 12
        gaps.append((distance, following))
    _, start_month = max(gaps)
    start_year = reference.year - 1 if reference.month < start_month else reference.year
    crosses_year = any(month < start_month for month in normalized)
    return f"{start_year}-{start_year + 1}" if crosses_year else str(start_year)


def _period_key(
    timestamp: datetime,
    *,
    now: datetime,
    horizon: datetime,
    forecast_type: str,
) -> Any | None:
    """Return an aligned period key when the forecast lies in the horizon."""
    if forecast_type == "hourly":
        if not now <= timestamp <= horizon:
            return None
        return timestamp.replace(minute=0, second=0, microsecond=0)
    if not now.date() <= timestamp.date() <= horizon.date():
        return None
    return timestamp.date()


def _index_forecasts(
    forecasts: dict[str, list[dict[str, Any]]],
    *,
    now: datetime,
    horizon: datetime,
    forecast_type: str,
) -> tuple[
    dict[str, dict[Any, tuple[datetime, dict[str, Any]]]],
    set[Any],
]:
    """Index forecast payloads by entity and aligned period."""
    indexed: dict[str, dict[Any, tuple[datetime, dict[str, Any]]]] = {}
    candidate_periods: set[Any] = set()
    for entity_id, entries in forecasts.items():
        entity_periods: dict[Any, tuple[datetime, dict[str, Any]]] = {}
        for entry in entries:
            timestamp = _forecast_datetime(
                entry.get("datetime", entry.get("date")), now.tzinfo
            )
            if timestamp is None:
                continue
            local_timestamp = (
                timestamp.astimezone(now.tzinfo) if now.tzinfo else timestamp
            )
            period_key = _period_key(
                local_timestamp,
                now=now,
                horizon=horizon,
                forecast_type=forecast_type,
            )
            if period_key is None:
                continue
            entity_periods[period_key] = (local_timestamp, entry)
            candidate_periods.add(period_key)
        indexed[entity_id] = entity_periods
    return indexed, candidate_periods


def _condition_result(
    condition: dict[str, Any],
    period: tuple[datetime, dict[str, Any]] | None,
) -> tuple[dict[str, Any], datetime | None]:
    """Evaluate one forecast condition for one aligned period."""
    entity_id = str(condition.get("entity_id", ""))
    attribute = str(condition.get("attribute", "")).strip()
    timestamp, entry = period if period else (None, {})
    raw_value = entry.get(attribute) if attribute else None
    available = raw_value is not None and str(raw_value) not in {
        "unknown",
        "unavailable",
    }
    matches = available and resource_condition_matches(
        raw_value,
        condition.get("condition", "below"),
        condition.get("threshold"),
    )
    return (
        {
            "entity_id": entity_id,
            "attribute": attribute or None,
            "current": raw_value,
            "threshold": condition.get("threshold"),
            "condition": condition.get("condition", "below"),
            "available": available,
            "matches": bool(matches),
        },
        timestamp,
    )


def _period_result(
    period_key: Any,
    conditions: list[dict[str, Any]],
    indexed: dict[str, dict[Any, tuple[datetime, dict[str, Any]]]],
    *,
    logic: str,
    zone: tzinfo | None,
) -> dict[str, Any]:
    """Build one explainable aligned forecast result."""
    results = []
    timestamps = []
    for condition in conditions:
        entity_id = str(condition.get("entity_id", ""))
        result, timestamp = _condition_result(
            condition,
            indexed.get(entity_id, {}).get(period_key),
        )
        results.append(result)
        if timestamp is not None:
            timestamps.append(timestamp)
    matcher = all if logic == "all" else any
    allowed = bool(results) and matcher(item["matches"] for item in results)
    fallback_date = (
        period_key.date() if isinstance(period_key, datetime) else period_key
    )
    if timestamps:
        timestamp = min(timestamps)
    else:
        timestamp = datetime.combine(fallback_date, time.min, tzinfo=zone)
    return {
        "date": fallback_date.isoformat(),
        "datetime": timestamp.isoformat(),
        "allowed": allowed,
        "conditions": results,
    }


def _forecast_status(
    matched: dict[str, Any] | None,
    unavailable: bool,
) -> tuple[str, str]:
    """Return a stable status code and explanation."""
    if matched:
        return "forecast_condition_met", "Die Vorhersagebedingung ist erfüllt."
    if unavailable:
        return (
            "forecast_unavailable",
            "Es sind keine verwendbaren Vorhersagedaten verfügbar.",
        )
    return (
        "forecast_condition_not_met",
        "Die Vorhersagebedingung ist im Prüfzeitraum nicht erfüllt.",
    )


def forecast_decision(
    weather: dict[str, Any] | None,
    forecasts: dict[str, list[dict[str, Any]]],
    *,
    now: datetime,
    horizon_hours: int,
    forecast_type: str = "daily",
) -> dict[str, Any]:
    """Evaluate weather conditions against aligned forecast periods."""
    conditions = weather.get("conditions", []) if weather else []
    logic = weather.get("logic", "all") if weather else "all"
    if not conditions:
        return {
            "allowed": False,
            "code": "forecast_conditions_missing",
            "message": "Es sind keine Vorhersagebedingungen konfiguriert.",
            "periods": [],
        }

    horizon = now + timedelta(hours=max(1, horizon_hours))
    indexed, candidate_periods = _index_forecasts(
        forecasts,
        now=now,
        horizon=horizon,
        forecast_type=forecast_type,
    )

    periods = []
    matched = None
    for period_key in sorted(candidate_periods):
        period_result = _period_result(
            period_key,
            conditions,
            indexed,
            logic=logic,
            zone=now.tzinfo,
        )
        periods.append(period_result)
        if period_result["allowed"] and matched is None:
            matched = period_result

    unavailable = not periods or all(
        not any(item["available"] for item in period["conditions"])
        for period in periods
    )
    code, message = _forecast_status(matched, unavailable)
    return {
        "allowed": matched is not None,
        "code": code,
        "message": message,
        "logic": logic,
        "horizon_hours": horizon_hours,
        "matched_period": matched,
        "periods": periods[:14],
    }


def forecast_activation(
    matched_datetime: str,
    *,
    lead_days: int,
    activation_time: time,
    zone: tzinfo,
) -> datetime:
    """Return the configured local activation time before a forecast period."""
    matched = _forecast_datetime(matched_datetime, zone)
    if matched is None:
        raise ValueError("Forecast period has no valid timestamp")
    local = matched.astimezone(zone)
    return datetime.combine(
        local.date() - timedelta(days=max(0, lead_days)),
        activation_time,
        zone,
    )
