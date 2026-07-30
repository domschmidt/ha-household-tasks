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

    zone = now.tzinfo
    horizon = now + timedelta(hours=max(1, horizon_hours))
    indexed: dict[str, dict[Any, tuple[datetime, dict[str, Any]]]] = {}
    candidate_periods: set[Any] = set()
    for entity_id, entries in forecasts.items():
        indexed[entity_id] = {}
        for entry in entries:
            timestamp = _forecast_datetime(
                entry.get("datetime", entry.get("date")), zone
            )
            if timestamp is None:
                continue
            local_timestamp = timestamp.astimezone(zone) if zone else timestamp
            if forecast_type == "hourly":
                if not now <= local_timestamp <= horizon:
                    continue
                period_key: Any = local_timestamp.replace(
                    minute=0, second=0, microsecond=0
                )
            elif not now.date() <= local_timestamp.date() <= horizon.date():
                continue
            else:
                period_key = local_timestamp.date()
            indexed[entity_id][period_key] = (local_timestamp, entry)
            candidate_periods.add(period_key)

    periods = []
    matched = None
    for period_key in sorted(candidate_periods):
        results = []
        timestamps = []
        for condition in conditions:
            entity_id = str(condition.get("entity_id", ""))
            period = indexed.get(entity_id, {}).get(period_key)
            attribute = str(condition.get("attribute", "")).strip()
            timestamp, entry = period if period else (None, {})
            if timestamp is not None:
                timestamps.append(timestamp)
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
            results.append(
                {
                    "entity_id": entity_id,
                    "attribute": attribute or None,
                    "current": raw_value,
                    "threshold": condition.get("threshold"),
                    "condition": condition.get("condition", "below"),
                    "available": available,
                    "matches": bool(matches),
                }
            )
        allowed = bool(results) and (
            all(item["matches"] for item in results)
            if logic == "all"
            else any(item["matches"] for item in results)
        )
        fallback_date = (
            period_key.date() if isinstance(period_key, datetime) else period_key
        )
        timestamp = (
            min(timestamps)
            if timestamps
            else datetime.combine(fallback_date, time.min, tzinfo=zone)
        )
        period_result = {
            "date": fallback_date.isoformat(),
            "datetime": timestamp.isoformat(),
            "allowed": allowed,
            "conditions": results,
        }
        periods.append(period_result)
        if allowed and matched is None:
            matched = period_result

    unavailable = not periods or all(
        not any(item["available"] for item in period["conditions"])
        for period in periods
    )
    return {
        "allowed": matched is not None,
        "code": (
            "forecast_condition_met"
            if matched
            else "forecast_unavailable"
            if unavailable
            else "forecast_condition_not_met"
        ),
        "message": (
            "Die Vorhersagebedingung ist erfüllt."
            if matched
            else "Es sind keine verwendbaren Vorhersagedaten verfügbar."
            if unavailable
            else "Die Vorhersagebedingung ist im Prüfzeitraum nicht erfüllt."
        ),
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
