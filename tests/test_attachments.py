"""Tests for bounded evidence storage and retention cleanup."""

import base64
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import voluptuous as vol

from custom_components.household_tasks.bootstrap import initial_config
from custom_components.household_tasks.engine import (
    ATTACHMENT_CHUNK_BYTES,
    ATTACHMENT_MAX_BYTES,
    ATTACHMENT_MAX_COUNT,
    ATTACHMENT_MAX_PENDING_UPLOADS,
    ATTACHMENT_TOTAL_MAX_BYTES,
    HISTORY_RETENTION_DAYS,
    HouseholdTaskEngine,
)


def _engine(hass) -> HouseholdTaskEngine:
    engine = HouseholdTaskEngine(hass, initial_config())
    engine.state["occurrences"] = {"task-1": {"status": "open", "resolved": False}}
    engine._save = AsyncMock()
    return engine


async def test_attachment_limits_allow_larger_evidence_safely(hass):
    """Files larger than the legacy limit fit below transport-safe bounds."""
    engine = _engine(hass)
    content = base64.b64encode(b"x" * 1_000_000).decode()

    attachment = await engine.async_add_attachment(
        "task-1", "photo.jpg", "image/jpeg", content
    )

    assert attachment["size"] == 1_000_000
    assert "content" not in attachment
    assert engine._save.await_count == 1

    oversized = base64.b64encode(b"x" * (ATTACHMENT_MAX_BYTES + 1)).decode()
    with pytest.raises(vol.Invalid, match="20 MB"):
        await engine.async_add_attachment(
            "task-1", "too-large.jpg", "image/jpeg", oversized
        )


async def test_attachment_count_and_total_limits_are_independent(hass):
    """Count and aggregate size prevent an unbounded Home Assistant store."""
    engine = _engine(hass)
    attachments = engine.state["attachments"].setdefault("task-1", [])
    attachments.extend(
        {"id": str(index), "size": 1, "content": "eA=="}
        for index in range(ATTACHMENT_MAX_COUNT)
    )
    with pytest.raises(vol.Invalid, match="zehn Anhänge"):
        await engine.async_add_attachment("task-1", "extra.png", "image/png", "eA==")

    attachments[:] = [
        {
            "id": "large-existing",
            "size": ATTACHMENT_TOTAL_MAX_BYTES,
            "content": "eA==",
        }
    ]
    with pytest.raises(vol.Invalid, match="100 MB"):
        await engine.async_add_attachment("task-1", "extra.png", "image/png", "eA==")


async def test_large_attachment_upload_and_download_are_chunked(hass):
    """Large files cross the WebSocket boundary as small ordered blocks."""
    engine = _engine(hass)
    first = base64.b64encode(b"first-").decode()
    second = base64.b64encode(b"second").decode()

    progress = await engine.async_add_attachment_chunk(
        "task-1", "upload-1", "photo.jpg", "image/jpeg", 0, 2, first
    )
    result = await engine.async_add_attachment_chunk(
        "task-1", "upload-1", "photo.jpg", "image/jpeg", 1, 2, second
    )

    assert progress == {"complete": False, "next_chunk": 1}
    assert result["complete"] is True
    attachment_id = result["attachment"]["id"]
    first_download = engine.attachment_content_chunk(
        "task-1", attachment_id, 0, chunk_chars=4
    )
    second_download = engine.attachment_content_chunk(
        "task-1", attachment_id, first_download["next_offset"], chunk_chars=100
    )
    assert first_download["complete"] is False
    encoded = first_download["content"] + second_download["content"]
    assert base64.b64decode(encoded) == b"first-second"
    assert second_download["complete"] is True

    oversized_chunk = base64.b64encode(b"x" * (ATTACHMENT_CHUNK_BYTES + 1)).decode()
    with pytest.raises(vol.Invalid, match="Upload-Block"):
        await engine.async_add_attachment_chunk(
            "task-1", "upload-2", "photo.jpg", "image/jpeg", 0, 1, oversized_chunk
        )

    with pytest.raises(vol.Invalid, match="Download-Offset"):
        engine.attachment_content_chunk("task-1", attachment_id, 99_999)


async def test_five_megabyte_phone_photo_crosses_chunk_transport(hass):
    """A typical phone photo larger than the HA message limit uploads intact."""
    engine = _engine(hass)
    photo = b"p" * 5_000_000
    chunks = [
        photo[offset : offset + ATTACHMENT_CHUNK_BYTES]
        for offset in range(0, len(photo), ATTACHMENT_CHUNK_BYTES)
    ]
    result = None
    for index, chunk in enumerate(chunks):
        result = await engine.async_add_attachment_chunk(
            "task-1",
            "phone-photo",
            "iphone.jpg",
            "image/jpeg",
            index,
            len(chunks),
            base64.b64encode(chunk).decode(),
        )

    assert result is not None
    assert result["complete"] is True
    assert result["attachment"]["size"] == 5_000_000


async def test_chunk_upload_rejects_invalid_or_unbounded_sessions(hass):
    """Malformed, unordered, and excessive upload sessions fail closed."""
    engine = _engine(hass)
    valid = base64.b64encode(b"x").decode()
    with pytest.raises(vol.Invalid, match="Anhang ist ungültig"):
        await engine.async_add_attachment_chunk(
            "task-1", "bad", "proof.png", "image/png", 0, 1, "%%%"
        )
    with pytest.raises(vol.Invalid, match="Upload-Reihenfolge"):
        await engine.async_add_attachment_chunk(
            "task-1", "bad-order", "proof.png", "image/png", 0, 0, valid
        )
    with pytest.raises(vol.Invalid, match="erste Upload-Block"):
        await engine.async_add_attachment_chunk(
            "task-1", "missing", "proof.png", "image/png", 1, 2, valid
        )

    now = datetime.now(UTC)
    engine.pending_attachment_uploads = {
        ("task-1", f"pending-{index}"): {"updated_at": now}
        for index in range(ATTACHMENT_MAX_PENDING_UPLOADS)
    }
    with pytest.raises(vol.Invalid, match="parallele"):
        await engine.async_add_attachment_chunk(
            "task-1", "one-more", "proof.png", "image/png", 0, 1, valid
        )


async def test_chunk_upload_rejects_changed_metadata_and_size(hass):
    """An upload cannot switch metadata or exceed its declared file bound."""
    engine = _engine(hass)
    valid = base64.b64encode(b"x").decode()
    await engine.async_add_attachment_chunk(
        "task-1", "changed", "proof.png", "image/png", 0, 2, valid
    )
    with pytest.raises(vol.Invalid, match="inkonsistent"):
        await engine.async_add_attachment_chunk(
            "task-1", "changed", "other.png", "image/png", 1, 2, valid
        )

    engine.pending_attachment_uploads[("task-1", "too-large")] = {
        "name": "proof.png",
        "mime_type": "image/png",
        "total_chunks": 2,
        "chunks": [b"x"],
        "size": ATTACHMENT_MAX_BYTES,
        "updated_at": datetime.now(UTC),
    }
    with pytest.raises(vol.Invalid, match="20 MB"):
        await engine.async_add_attachment_chunk(
            "task-1", "too-large", "proof.png", "image/png", 1, 2, valid
        )


async def test_decoded_attachment_validation_is_defensive(hass):
    """The final persistence boundary repeats identity, type, and size checks."""
    engine = _engine(hass)
    with pytest.raises(vol.Invalid, match="Unbekannte Aufgabe"):
        await engine._async_store_attachment("missing", "x", "image/png", b"x")
    with pytest.raises(vol.Invalid, match="JPG"):
        await engine._async_store_attachment("task-1", "x", "text/plain", b"x")
    with pytest.raises(vol.Invalid, match="20 MB"):
        await engine._async_store_attachment("task-1", "x", "image/png", b"")


def test_prune_state_removes_old_tasks_evidence_and_task_events(hass):
    """The 90-day cleanup removes one complete audit bundle atomically."""
    engine = _engine(hass)
    now = datetime(2026, 8, 1, 12, tzinfo=UTC)
    old = now - timedelta(days=HISTORY_RETENTION_DAYS + 1)
    recent = now - timedelta(days=HISTORY_RETENTION_DAYS - 1)
    engine.state["occurrences"] = {
        "old": {
            "status": "completed",
            "resolved": True,
            "resolved_at": old.isoformat(),
        },
        "recent": {
            "status": "completed",
            "resolved": True,
            "resolved_at": recent.isoformat(),
        },
        "open": {"status": "open", "resolved": False},
    }
    engine.state["attachments"] = {
        "old": [{"id": "old-proof"}],
        "recent": [{"id": "recent-proof"}],
    }
    engine.state["task_events"] = [
        {"id": "old-event", "occurrence_id": "old"},
        {"id": "recent-event", "occurrence_id": "recent"},
        {"id": "global-event", "type": "store_migrated"},
    ]

    engine._prune_state(now)

    assert set(engine.state["occurrences"]) == {"recent", "open"}
    assert set(engine.state["attachments"]) == {"recent"}
    assert [event["id"] for event in engine.state["task_events"]] == [
        "recent-event",
        "global-event",
    ]
