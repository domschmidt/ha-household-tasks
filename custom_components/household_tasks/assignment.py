"""Pure assignment helpers for Household Tasks."""

from __future__ import annotations

from collections.abc import Iterable


def select_fair_candidate(
    candidates: Iterable[str],
    assignment_counts: dict[str, int],
    open_load: dict[str, int],
) -> str | None:
    """Select the least-assigned candidate with stable workload tie-breaking."""
    ordered = list(candidates)
    if not ordered:
        return None
    return min(
        ordered,
        key=lambda person: (
            int(assignment_counts.get(person, 0)),
            int(open_load.get(person, 0)),
            ordered.index(person),
        ),
    )
