"""Protocol and domain tests for the embedded CalDAV VTODO server."""

from __future__ import annotations

import base64
import re
from datetime import UTC, datetime, timedelta
from xml.etree import ElementTree as ET

import pytest
import voluptuous as vol
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.household_tasks.caldav import (
    CALDAV,
    CALDAV_BASE,
    DAV,
    _render_vtodo,
    get_caldav_service,
    parse_vtodo,
)
from custom_components.household_tasks.const import DOMAIN

pytestmark = pytest.mark.usefixtures("mock_frontend_loaded")


def _auth(username: str, password: str) -> dict[str, str]:
    value = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {value}"}


def _vtodo(
    *,
    uid: str = "client-created@example.test",
    summary: str = "Frostschutz prüfen",
    status: str = "NEEDS-ACTION",
    due: str = "20261201T180000Z",
) -> bytes:
    return (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//Tests//EN\r\n"
        "BEGIN:VTODO\r\n"
        f"UID:{uid}\r\n"
        f"SUMMARY:{summary}\r\n"
        f"STATUS:{status}\r\n"
        f"DUE:{due}\r\n"
        "PRIORITY:3\r\n"
        "CATEGORIES:Auto,Winter\r\n"
        "X-APPLE-SORT-ORDER:42\r\n"
        "BEGIN:VALARM\r\n"
        "ACTION:DISPLAY\r\n"
        "TRIGGER:-PT30M\r\n"
        "DESCRIPTION:Frostschutz\r\n"
        "END:VALARM\r\n"
        "END:VTODO\r\n"
        "END:VCALENDAR\r\n"
    ).encode()


def _sync_body(token: str | None = None) -> str:
    """Build an RFC 6578 sync-collection request."""
    token_value = token or ""
    return (
        f'<D:sync-collection xmlns:D="{DAV}">'
        f"<D:sync-token>{token_value}</D:sync-token>"
        "<D:sync-level>1</D:sync-level>"
        "<D:prop><D:getetag/></D:prop></D:sync-collection>"
    )


async def _sync(client, calendar: str, headers: dict[str, str], token=None):
    """Run one collection sync and return response, token, and resource statuses."""
    response = await client.request(
        "REPORT", calendar, headers=headers, data=_sync_body(token)
    )
    if response.status != 207:
        return response, None, {}
    root = ET.fromstring(await response.read())
    next_token = root.findtext(f"{{{DAV}}}sync-token")
    resources = {}
    for item in root.findall(f"{{{DAV}}}response"):
        href = item.findtext(f"{{{DAV}}}href")
        status = item.findtext(f"{{{DAV}}}status")
        if status is None:
            status = item.findtext(f".//{{{DAV}}}status")
        resources[href] = status
    return response, next_token, resources


async def _parent_resource(client, calendar: str, headers: dict[str, str]):
    """Return the projected parent task URL, ETag, and representation."""
    response = await client.request(
        "PROPFIND", calendar, headers={**headers, "Depth": "1"}
    )
    assert response.status == 207
    root = ET.fromstring(await response.read())
    href = next(
        item.text
        for item in root.findall(f".//{{{DAV}}}href")
        if item.text and item.text.endswith(".ics") and "/check-" not in item.text
    )
    response = await client.get(href, headers=headers)
    assert response.status == 200
    return href, response.headers["ETag"], await response.read()


def test_vtodo_parser_rejects_unsafe_or_ambiguous_data_and_keeps_extensions():
    """The parser accepts useful VTODO fields but rejects unsafe input."""
    parsed = parse_vtodo(_vtodo(), UTC)

    assert parsed.summary == "Frostschutz prüfen"
    assert parsed.status == "NEEDS-ACTION"
    assert parsed.priority == 3
    assert parsed.categories == ["Auto", "Winter"]
    assert parsed.alarms == [
        {
            "ACTION": "DISPLAY",
            "TRIGGER": "-PT30M",
            "DESCRIPTION": "Frostschutz",
        }
    ]
    assert parsed.preserved_lines == ["X-APPLE-SORT-ORDER:42"]

    with pytest.raises(Exception, match=r"Unsafe XML|VCALENDAR"):
        parse_vtodo(b"<!DOCTYPE x><x/>", UTC)
    with pytest.raises(Exception, match="Exactly one VTODO"):
        parse_vtodo(
            b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
            b"BEGIN:VTODO\r\nUID:one\r\nSUMMARY:One\r\nEND:VTODO\r\n"
            b"BEGIN:VTODO\r\nUID:two\r\nSUMMARY:Two\r\nEND:VTODO\r\n"
            b"END:VCALENDAR\r\n",
            UTC,
        )


def test_vtodo_rendering_is_stable_folded_and_contains_checklist_relations():
    """Projected tasks use deterministic UIDs and RFC-style folded lines."""
    occurrence = {
        "title": "[Alex] Eine sehr lange Aufgabe " + "ä" * 90,
        "description": "Zeile 1\nZeile 2",
        "assignee": "alex",
        "due": datetime(2026, 12, 1, 18, tzinfo=UTC).isoformat(),
        "created_at": datetime(2026, 8, 1, tzinfo=UTC).isoformat(),
        "updated_at": datetime(2026, 8, 2, tzinfo=UTC).isoformat(),
        "revision": 4,
        "status": "open",
        "task": {"market": {"priority": "high", "points": 3}},
        "dependencies": ["first"],
    }
    parent = _render_vtodo("task:1", occurrence)
    child = _render_vtodo(
        "task:1",
        occurrence,
        checklist={"id": "photo", "title": "Foto", "completed": True},
    )

    assert b"\r\n " in parent
    assert b"DESCRIPTION:Zeile 1\\nZeile 2" in parent
    assert b"X-HOUSEHOLD-REVISION:4" in parent
    assert b"RELTYPE=DEPENDS-ON" in parent
    assert b"RELATED-TO;RELTYPE=PARENT" in child
    assert b"STATUS:COMPLETED" in child


async def _configured_runtime(hass, unused_tcp_port):
    hass.services.async_register("notify", "mobile_app_alex", lambda call: None)
    hass.services.async_register("notify", "mobile_app_sam", lambda call: None)
    assert await async_setup_component(
        hass,
        DOMAIN,
        {"http": {"server_host": "127.0.0.1", "server_port": unused_tcp_port}},
    )
    entry = MockConfigEntry(domain=DOMAIN, data={}, version=2)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    engine = entry.runtime_data
    await engine.async_save_person(
        "alex", {"name": "Alex", "notify": "notify.mobile_app_alex"}
    )
    await engine.async_save_person(
        "sam", {"name": "Sam", "notify": "notify.mobile_app_sam"}
    )
    await engine.async_save_task(
        "filter",
        {
            "enabled": True,
            "name": "Filter prüfen",
            "description": "Filter und Foto kontrollieren",
            "assignee": "alex",
            "assignment": {"type": "fixed"},
            "schedule": {"type": "manual"},
            "checklist": [{"id": "photo", "title": "Foto aufnehmen"}],
            "market": {"priority": "high", "points": 3},
        },
    )
    await engine.async_create_manual("filter")
    service = get_caldav_service(hass)
    assert service is not None
    await service.async_save_settings(
        {**service.state["settings"], "enabled": True, "require_tls": False}
    )
    created = await service.async_create_credential(
        person_id="alex",
        label="Alex iPhone",
        permission="read_write",
        scope="personal",
        include_claimable=True,
        complete_checklist_on_parent=True,
        expires_at=None,
    )
    credential = created["created_credential"]
    return entry, engine, service, credential


async def test_caldav_discovery_query_get_put_sync_and_conflict_round_trip(
    hass, hass_client, unused_tcp_port
):
    """A real HTTP client can discover, sync, and complete personal tasks."""
    _, engine, service, credential = await _configured_runtime(hass, unused_tcp_port)
    client = await hass_client()
    headers = _auth(credential["username"], credential["password"])
    username = credential["username"]
    calendar = f"{CALDAV_BASE}/calendars/{username}/tasks/"

    response = await client.request("OPTIONS", CALDAV_BASE + "/", headers=headers)
    assert response.status == 200
    assert "calendar-access" in response.headers["DAV"]

    response = await client.request(
        "PROPFIND",
        f"{CALDAV_BASE}/principals/{username}/",
        headers={**headers, "Depth": "0"},
        data=(
            f'<D:propfind xmlns:D="{DAV}" xmlns:C="{CALDAV}">'
            "<D:prop><C:calendar-home-set/><D:current-user-principal/></D:prop>"
            "</D:propfind>"
        ),
    )
    assert response.status == 207
    body = await response.text()
    assert f"/calendars/{username}/" in body

    response = await client.request(
        "PROPFIND",
        calendar,
        headers={**headers, "Depth": "1"},
        data=f'<D:propfind xmlns:D="{DAV}"><D:prop><D:getetag/></D:prop></D:propfind>',
    )
    assert response.status == 207
    multistatus = ET.fromstring(await response.read())
    hrefs = [item.text for item in multistatus.findall(f".//{{{DAV}}}href")]
    resource_hrefs = [href for href in hrefs if href and href.endswith(".ics")]
    assert len(resource_hrefs) == 2  # parent plus checklist item
    parent_href = next(href for href in resource_hrefs if "/check-" not in href)

    response = await client.get(parent_href, headers=headers)
    assert response.status == 200
    initial_etag = response.headers["ETag"]
    task_data = await response.read()
    assert b"SUMMARY:Filter" in task_data
    completed_data = task_data.replace(
        b"STATUS:NEEDS-ACTION", b"STATUS:COMPLETED"
    ).replace(b"PERCENT-COMPLETE:0", b"PERCENT-COMPLETE:100")
    response = await client.put(
        parent_href,
        headers={**headers, "If-Match": initial_etag},
        data=completed_data,
    )
    assert response.status == 204
    occurrence = next(iter(engine.state["occurrences"].values()))
    assert occurrence["status"] == "completed"
    assert occurrence["completed_by"] == "alex"
    assert occurrence["checklist"][0]["completed"] is True

    response = await client.put(
        parent_href,
        headers={**headers, "If-Match": initial_etag},
        data=completed_data,
    )
    assert response.status == 412

    sync_body = (
        f'<D:sync-collection xmlns:D="{DAV}">'
        "<D:sync-token/><D:sync-level>1</D:sync-level>"
        "<D:prop><D:getetag/></D:prop></D:sync-collection>"
    )
    response = await client.request("REPORT", calendar, headers=headers, data=sync_body)
    assert response.status == 207
    sync_xml = ET.fromstring(await response.read())
    token = sync_xml.find(f"{{{DAV}}}sync-token")
    assert token is not None and token.text.startswith("urn:uuid:")

    created_href = calendar + "from-iphone.ics"
    response = await client.put(
        created_href,
        headers={**headers, "If-None-Match": "*"},
        data=_vtodo(summary="Mülltonne rausstellen"),
    )
    assert response.status == 201
    assert any(
        "Mülltonne" in item["title"] for item in engine.state["occurrences"].values()
    )
    assert "password_hash" not in str(service.public_status())
    assert credential["password"] not in str(service.state)


async def test_caldav_scope_read_only_delete_and_revocation(
    hass, hass_client, unused_tcp_port
):
    """Scopes, deletion policy, read-only access, and revocation fail closed."""
    _, engine, service, credential = await _configured_runtime(hass, unused_tcp_port)
    read_only = await service.async_create_credential(
        person_id="sam",
        label="Sam read only",
        permission="read_only",
        scope="personal",
        include_claimable=False,
        complete_checklist_on_parent=False,
        expires_at=(datetime.now(UTC) + timedelta(days=30)).isoformat(),
    )
    client = await hass_client()
    ro = read_only["created_credential"]
    ro_headers = _auth(ro["username"], ro["password"])
    ro_calendar = f"{CALDAV_BASE}/calendars/{ro['username']}/tasks/"
    response = await client.request(
        "PROPFIND", ro_calendar, headers={**ro_headers, "Depth": "1"}
    )
    assert response.status == 207
    assert ".ics" not in await response.text()
    response = await client.put(
        ro_calendar + "forbidden.ics", headers=ro_headers, data=_vtodo()
    )
    assert response.status == 403

    headers = _auth(credential["username"], credential["password"])
    calendar = f"{CALDAV_BASE}/calendars/{credential['username']}/tasks/"
    response = await client.request(
        "PROPFIND", calendar, headers={**headers, "Depth": "1"}
    )
    xml = ET.fromstring(await response.read())
    href = next(
        item.text
        for item in xml.findall(f".//{{{DAV}}}href")
        if item.text and item.text.endswith(".ics") and "/check-" not in item.text
    )
    response = await client.delete(href, headers=headers)
    assert response.status == 204
    assert next(iter(engine.state["occurrences"].values()))["status"] == "cancelled"

    await service.async_revoke_credential(credential["id"])
    response = await client.request("OPTIONS", CALDAV_BASE + "/", headers=headers)
    assert response.status == 401


async def test_caldav_settings_health_and_credential_validation(
    hass,
):
    """Unsafe settings and invalid scopes fail before credentials are changed."""
    _, _, service, credential = await _configured_runtime(hass, 8123)

    for update, message in (
        ({"unknown": True}, "Unknown CalDAV settings"),
        ({**service.state["settings"], "delete_mode": "hard"}, "delete mode"),
        ({**service.state["settings"], "expose_completed_days": 4000}, "retention"),
        (
            {**service.state["settings"], "default_reminder_minutes": 600_000},
            "reminder",
        ),
        ({**service.state["settings"], "calendar_color": "blue"}, "color"),
    ):
        with pytest.raises(vol.Invalid, match=message):
            await service.async_save_settings(update)

    for options, message in (
        ({"scope": "invalid"}, "scope"),
        ({"permission": "owner"}, "permission"),
        ({"person_id": "missing"}, "person"),
        (
            {"expires_at": (datetime.now(UTC) - timedelta(days=1)).isoformat()},
            "future",
        ),
    ):
        values = {
            "person_id": "alex",
            "label": "Invalid",
            "permission": "read_write",
            "scope": "personal",
            "include_claimable": True,
            "complete_checklist_on_parent": True,
            "expires_at": None,
            **options,
        }
        with pytest.raises(vol.Invalid, match=message):
            await service.async_create_credential(**values)

    service.state["settings"]["require_tls"] = True
    assert any(
        item["code"] == "caldav_https_missing" for item in service.health_findings()
    )
    await service.async_revoke_credential(credential["id"])
    assert any(
        item["code"] == "caldav_no_credentials" for item in service.health_findings()
    )
    service.state["settings"]["enabled"] = False
    assert service.health_findings() == []


async def test_caldav_reports_properties_reopen_and_uid_protection(
    hass, hass_client, unused_tcp_port
):
    """Queries, presentation properties, reopening, and UID checks round-trip."""
    _, engine, service, credential = await _configured_runtime(hass, unused_tcp_port)
    client = await hass_client()
    headers = _auth(credential["username"], credential["password"])
    username = credential["username"]
    calendar = f"{CALDAV_BASE}/calendars/{username}/tasks/"

    response = await client.request(
        "PROPPATCH",
        calendar,
        headers=headers,
        data=(
            f'<D:propertyupdate xmlns:D="{DAV}" xmlns:A="http://apple.com/ns/ical/">'
            "<D:set><D:prop><D:displayname>Alex Aufgaben</D:displayname>"
            "<A:calendar-color>#FF0000FF</A:calendar-color></D:prop></D:set>"
            "</D:propertyupdate>"
        ),
    )
    assert response.status == 207
    assert (
        service.state["credentials"][credential["id"]]["calendar_name"]
        == "Alex Aufgaben"
    )

    query = (
        f'<C:calendar-query xmlns:D="{DAV}" xmlns:C="{CALDAV}">'
        "<D:prop><D:getetag/><C:calendar-data/></D:prop>"
        '<C:filter><C:comp-filter name="VCALENDAR"><C:comp-filter name="VTODO"/>'
        "</C:comp-filter></C:filter></C:calendar-query>"
    )
    response = await client.request("REPORT", calendar, headers=headers, data=query)
    assert response.status == 207
    query_xml = ET.fromstring(await response.read())
    hrefs = [
        item.text
        for item in query_xml.findall(f".//{{{DAV}}}href")
        if item.text and item.text.endswith(".ics")
    ]
    parent_href = next(item for item in hrefs if "/check-" not in item)
    response = await client.head(parent_href, headers=headers)
    assert response.status == 200
    etag = response.headers["ETag"]
    response = await client.get(parent_href, headers={**headers, "If-None-Match": etag})
    assert response.status == 304

    response = await client.get(parent_href, headers=headers)
    data = await response.read()
    etag = response.headers["ETag"]
    completed = data.replace(b"STATUS:NEEDS-ACTION", b"STATUS:COMPLETED")
    response = await client.put(
        parent_href, headers={**headers, "If-Match": etag}, data=completed
    )
    assert response.status == 204
    occurrence = next(iter(engine.state["occurrences"].values()))
    assert occurrence["status"] == "completed"
    assert engine.state["scores"]["alex"] == 3

    response = await client.get(parent_href, headers=headers)
    completed = await response.read()
    etag = response.headers["ETag"]
    reopened = completed.replace(b"STATUS:COMPLETED", b"STATUS:NEEDS-ACTION")
    response = await client.put(
        parent_href, headers={**headers, "If-Match": etag}, data=reopened
    )
    assert response.status == 204
    assert occurrence["status"] == "open"
    assert engine.state["scores"]["alex"] == 0

    response = await client.get(parent_href, headers=headers)
    current = await response.read()
    etag = response.headers["ETag"]
    conflicting = re.sub(
        rb"UID:[^\r\n]+", b"UID:different@example.test", current, count=1
    )
    response = await client.put(
        parent_href, headers={**headers, "If-Match": etag}, data=conflicting
    )
    assert response.status == 403

    multiget = (
        f'<C:calendar-multiget xmlns:D="{DAV}" xmlns:C="{CALDAV}">'
        f"<D:prop><D:getetag/></D:prop><D:href>{parent_href}</D:href>"
        "<D:href>/missing.ics</D:href></C:calendar-multiget>"
    )
    response = await client.request("REPORT", calendar, headers=headers, data=multiget)
    assert response.status == 207
    body = await response.text()
    assert "404 Not Found" in body

    response = await client.request(
        "PROPFIND",
        calendar,
        headers={**headers, "Depth": "infinity"},
    )
    assert response.status == 403


async def test_offline_completion_conflicts_with_native_household_tasks_change(
    hass, hass_client, unused_tcp_port
):
    """An offline client cannot overwrite a task changed natively in HA."""
    _, engine, _, credential = await _configured_runtime(hass, unused_tcp_port)
    client = await hass_client()
    headers = _auth(credential["username"], credential["password"])
    calendar = f"{CALDAV_BASE}/calendars/{credential['username']}/tasks/"
    occurrence_id = next(iter(engine.state["occurrences"]))

    _, offline_token, initial = await _sync(client, calendar, headers)
    assert offline_token
    assert len(initial) == 2
    parent_href, offline_etag, offline_copy = await _parent_resource(
        client, calendar, headers
    )

    # The phone is now offline. Household Tasks changes the due date in HA.
    original_due = engine.state["occurrences"][occurrence_id]["due"]
    await engine.async_move_occurrence(occurrence_id, "morgen")
    native_due = engine.state["occurrences"][occurrence_id]["due"]
    assert native_due != original_due

    queued_completion = offline_copy.replace(
        b"STATUS:NEEDS-ACTION", b"STATUS:COMPLETED"
    ).replace(b"PERCENT-COMPLETE:0", b"PERCENT-COMPLETE:100")
    response = await client.put(
        parent_href,
        headers={**headers, "If-Match": offline_etag},
        data=queued_completion,
    )
    assert response.status == 412
    response = await client.delete(
        parent_href, headers={**headers, "If-Match": offline_etag}
    )
    assert response.status == 412
    assert engine.state["occurrences"][occurrence_id]["status"] == "open"
    assert engine.state["occurrences"][occurrence_id]["due"] == native_due

    # Reconnection exposes the HA update through incremental sync.
    _, refreshed_token, changes = await _sync(client, calendar, headers, offline_token)
    assert refreshed_token != offline_token
    assert parent_href in changes
    assert changes[parent_href].endswith("200 OK")
    _, current_etag, current_copy = await _parent_resource(client, calendar, headers)
    assert current_etag != offline_etag
    assert parse_vtodo(current_copy, UTC).due == datetime.fromisoformat(
        native_due
    ).astimezone(UTC)

    # After refreshing, the same intended completion is safe to apply.
    completion = current_copy.replace(
        b"STATUS:NEEDS-ACTION", b"STATUS:COMPLETED"
    ).replace(b"PERCENT-COMPLETE:0", b"PERCENT-COMPLETE:100")
    response = await client.put(
        parent_href,
        headers={**headers, "If-Match": current_etag},
        data=completion,
    )
    assert response.status == 204
    assert engine.state["occurrences"][occurrence_id]["status"] == "completed"
    assert engine.state["occurrences"][occurrence_id]["completed_by"] == "alex"


async def test_two_clients_detect_conflicts_and_sync_without_lost_updates(
    hass, hass_client, unused_tcp_port
):
    """Two devices cannot silently overwrite each other's queued changes."""
    _, engine, service, first = await _configured_runtime(hass, unused_tcp_port)
    second = (
        await service.async_create_credential(
            person_id="alex",
            label="Alex iPad",
            permission="read_write",
            scope="personal",
            include_claimable=True,
            complete_checklist_on_parent=True,
            expires_at=None,
        )
    )["created_credential"]
    client = await hass_client()
    first_headers = _auth(first["username"], first["password"])
    second_headers = _auth(second["username"], second["password"])
    first_calendar = f"{CALDAV_BASE}/calendars/{first['username']}/tasks/"
    second_calendar = f"{CALDAV_BASE}/calendars/{second['username']}/tasks/"

    _, first_token, _ = await _sync(client, first_calendar, first_headers)
    _, second_token, _ = await _sync(client, second_calendar, second_headers)
    first_href, first_etag, first_copy = await _parent_resource(
        client, first_calendar, first_headers
    )
    second_href, second_etag, second_copy = await _parent_resource(
        client, second_calendar, second_headers
    )
    assert first_etag == second_etag

    first_update = first_copy.replace(
        b"SUMMARY:Filter pr\xc3\xbcfen", b"SUMMARY:Filter am Samstag pr\xc3\xbcfen"
    )
    response = await client.put(
        first_href,
        headers={**first_headers, "If-Match": first_etag},
        data=first_update,
    )
    assert response.status == 204

    stale_second_update = second_copy.replace(
        b"SUMMARY:Filter pr\xc3\xbcfen", b"SUMMARY:Filter sofort pr\xc3\xbcfen"
    )
    response = await client.put(
        second_href,
        headers={**second_headers, "If-Match": second_etag},
        data=stale_second_update,
    )
    assert response.status == 412
    occurrence = next(iter(engine.state["occurrences"].values()))
    assert "Samstag" in occurrence["title"]
    assert "sofort" not in occurrence["title"]

    _, next_second_token, second_changes = await _sync(
        client, second_calendar, second_headers, second_token
    )
    assert next_second_token != second_token
    assert second_href in second_changes

    # Sync tokens are credential-scoped and cannot cross device boundaries.
    response, _, _ = await _sync(client, second_calendar, second_headers, first_token)
    assert response.status == 409
    assert "valid-sync-token" in await response.text()


async def test_native_create_status_and_visibility_changes_produce_incremental_sync(
    hass, hass_client, unused_tcp_port
):
    """Native HA changes appear as additions, updates, and removals per person."""
    _, engine, service, alex = await _configured_runtime(hass, unused_tcp_port)
    sam = (
        await service.async_create_credential(
            person_id="sam",
            label="Sam iPhone",
            permission="read_write",
            scope="personal",
            include_claimable=False,
            complete_checklist_on_parent=True,
            expires_at=None,
        )
    )["created_credential"]
    client = await hass_client()
    alex_headers = _auth(alex["username"], alex["password"])
    sam_headers = _auth(sam["username"], sam["password"])
    alex_calendar = f"{CALDAV_BASE}/calendars/{alex['username']}/tasks/"
    sam_calendar = f"{CALDAV_BASE}/calendars/{sam['username']}/tasks/"
    _, alex_token, alex_initial = await _sync(client, alex_calendar, alex_headers)
    _, sam_token, sam_initial = await _sync(client, sam_calendar, sam_headers)
    assert len(alex_initial) == 2
    assert sam_initial == {}

    created_id = await engine.async_create_ad_hoc(
        "Direkt in Household Tasks erstellt",
        "alex",
        (datetime.now(UTC) + timedelta(hours=4)).isoformat(),
        description="Muss ohne CalDAV-Schreibvorgang synchronisiert werden",
    )
    _, alex_after_create, created_changes = await _sync(
        client, alex_calendar, alex_headers, alex_token
    )
    assert alex_after_create != alex_token
    additions = [href for href, status in created_changes.items() if "200 OK" in status]
    assert len(additions) == 1
    response = await client.get(additions[0], headers=alex_headers)
    assert b"Direkt in Household Tasks erstellt" in await response.read()

    revision = engine.state["occurrences"][created_id]["revision"]
    await engine.async_set_occurrence_status(
        created_id, "in_progress", expected_revision=revision
    )
    _, alex_after_status, status_changes = await _sync(
        client, alex_calendar, alex_headers, alex_after_create
    )
    assert additions[0] in status_changes
    response = await client.get(additions[0], headers=alex_headers)
    assert b"STATUS:IN-PROCESS" in await response.read()

    # A native handover removes Alex's resources and adds them to Sam's list.
    await engine.async_set_handover("alex", "sam", reason="Urlaubsvertretung")
    _, _, alex_removals = await _sync(
        client, alex_calendar, alex_headers, alex_after_status
    )
    assert len(alex_removals) == 3
    assert all(status.endswith("404 Not Found") for status in alex_removals.values())
    _, _, sam_additions = await _sync(client, sam_calendar, sam_headers, sam_token)
    assert len(sam_additions) == 3
    assert all(status.endswith("200 OK") for status in sam_additions.values())


async def test_delete_tombstone_is_reported_once_and_persisted_for_service_reload(
    hass, hass_client, unused_tcp_port
):
    """A deleted resource yields a durable tombstone without repeated changes."""
    _, engine, service, credential = await _configured_runtime(hass, unused_tcp_port)
    client = await hass_client()
    headers = _auth(credential["username"], credential["password"])
    calendar = f"{CALDAV_BASE}/calendars/{credential['username']}/tasks/"
    _, token, _ = await _sync(client, calendar, headers)
    parent_href, _, _ = await _parent_resource(client, calendar, headers)

    response = await client.delete(parent_href, headers=headers)
    assert response.status == 204
    occurrence = next(iter(engine.state["occurrences"].values()))
    assert occurrence["status"] == "cancelled"
    assert occurrence["caldav_deleted"] is True

    _, tombstone_token, changes = await _sync(client, calendar, headers, token)
    assert changes[parent_href].endswith("404 Not Found")
    assert any("check-" in href for href in changes)
    assert all(status.endswith("404 Not Found") for status in changes.values())

    _, unchanged_token, repeated = await _sync(
        client, calendar, headers, tombstone_token
    )
    assert unchanged_token == tombstone_token
    assert repeated == {}

    stored = await service.store.async_load()
    collection = stored["collections"][credential["id"]]
    assert collection["revision"] > 0
    assert any(item["deleted"] for item in collection["changes"])


async def test_sync_rejects_future_malformed_and_stale_tokens(
    hass, hass_client, unused_tcp_port
):
    """Invalid tokens fail closed instead of skipping unseen server changes."""
    _, _, service, credential = await _configured_runtime(hass, unused_tcp_port)
    client = await hass_client()
    headers = _auth(credential["username"], credential["password"])
    calendar = f"{CALDAV_BASE}/calendars/{credential['username']}/tasks/"
    _, token, _ = await _sync(client, calendar, headers)
    assert token
    prefix, revision = token.rsplit(":", 1)

    for invalid in (
        "not-a-sync-token",
        f"{prefix}:NaN",
        f"{prefix}:-1",
        f"{prefix}:{int(revision) + 100}",
    ):
        response, _, _ = await _sync(client, calendar, headers, invalid)
        assert response.status == 409
        assert "valid-sync-token" in await response.text()

    collection = service.state["collections"][credential["id"]]
    collection["revision"] = 20
    collection["changes"] = [{"revision": 10, "name": "later.ics", "deleted": False}]
    stale = f"{prefix}:1"
    response, _, _ = await _sync(client, calendar, headers, stale)
    assert response.status == 409
    assert "valid-sync-token" in await response.text()


async def test_offline_creates_enforce_href_and_uid_conflict_preconditions(
    hass, hass_client, unused_tcp_port
):
    """Queued creates cannot produce duplicate resources or duplicate UIDs."""
    _, engine, service, first = await _configured_runtime(hass, unused_tcp_port)
    second = (
        await service.async_create_credential(
            person_id="alex",
            label="Second offline client",
            permission="read_write",
            scope="personal",
            include_claimable=True,
            complete_checklist_on_parent=True,
            expires_at=None,
        )
    )["created_credential"]
    client = await hass_client()
    first_headers = _auth(first["username"], first["password"])
    second_headers = _auth(second["username"], second["password"])
    first_calendar = f"{CALDAV_BASE}/calendars/{first['username']}/tasks/"
    second_calendar = f"{CALDAV_BASE}/calendars/{second['username']}/tasks/"
    queued = _vtodo(uid="shared-offline-uid@example.test", summary="Offline neu")

    response = await client.put(
        first_calendar + "phone-copy.ics",
        headers={**first_headers, "If-None-Match": "*"},
        data=queued,
    )
    assert response.status == 201
    occurrence_count = len(engine.state["occurrences"])

    response = await client.put(
        first_calendar + "phone-copy.ics",
        headers={**first_headers, "If-None-Match": "*"},
        data=queued,
    )
    assert response.status == 412
    assert len(engine.state["occurrences"]) == occurrence_count

    # A second device has another collection URL but projects the same native list.
    response = await client.put(
        second_calendar + "tablet-copy.ics",
        headers={**second_headers, "If-None-Match": "*"},
        data=queued,
    )
    assert response.status == 403
    assert "no-uid-conflict" in await response.text()
    assert len(engine.state["occurrences"]) == occurrence_count
