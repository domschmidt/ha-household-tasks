"""Pure NFC task matching helpers."""

from __future__ import annotations

from typing import Any

NFC_ACTIONS = {"create_or_complete", "create", "complete"}
NFC_FEEDBACK_MODES = {"off", "errors", "always"}
NFC_FEEDBACK_RECIPIENTS = {"scanner", "assignee", "both"}


def task_for_tag(
    tasks: dict[str, dict[str, Any]], tag_id: str
) -> tuple[str, dict[str, Any]] | None:
    """Return the enabled task configured for a scanned tag."""
    clean_tag_id = str(tag_id).strip()
    for task_id, task in tasks.items():
        nfc = task.get("nfc")
        if (
            task.get("enabled", True)
            and isinstance(nfc, dict)
            and str(nfc.get("tag_id", "")).strip() == clean_tag_id
        ):
            return task_id, task
    return None


def open_occurrence_for_task(
    occurrences: dict[str, dict[str, Any]], task_id: str
) -> tuple[str, dict[str, Any]] | None:
    """Return the oldest due unresolved occurrence for a task."""
    matches = [
        (occurrence_id, occurrence)
        for occurrence_id, occurrence in occurrences.items()
        if occurrence.get("task_id") == task_id and not occurrence.get("resolved")
    ]
    if not matches:
        return None
    return min(matches, key=lambda item: str(item[1].get("due", "")))


def feedback_recipients(
    people: dict[str, dict[str, Any]],
    *,
    user_id: str | None,
    device_id: str | None,
    assignee: str | None,
    recipient_mode: str,
) -> list[str]:
    """Resolve scanner and assignee feedback recipients without duplicates."""
    scanner = next(
        (
            person_id
            for person_id, person in people.items()
            if user_id and person.get("user_id") == user_id
        ),
        None,
    )
    if scanner is None:
        scanner = next(
            (
                person_id
                for person_id, person in people.items()
                if device_id and person.get("nfc_device_id") == device_id
            ),
            None,
        )
    recipients: list[str] = []
    if recipient_mode in {"scanner", "both"} and scanner in people:
        recipients.append(scanner)
    if recipient_mode in {"assignee", "both"} and assignee in people:
        recipients.append(assignee)
    return list(dict.fromkeys(recipients))
