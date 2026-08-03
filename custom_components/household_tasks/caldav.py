"""Person-scoped CalDAV VTODO server for Household Tasks.

The native Household Tasks store remains authoritative.  This module exposes
an interoperable WebDAV/CalDAV projection with strong ETags, collection sync
tokens, scoped app passwords, and optimistic writes suitable for offline
clients such as Apple Reminders.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import re
import secrets
from collections import defaultdict, deque
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import Any
from urllib.parse import quote, unquote
from uuid import uuid4
from xml.etree import ElementTree as ET

import voluptuous as vol
from aiohttp import web
from defusedxml import ElementTree as DefusedET
from defusedxml.common import DefusedXmlException
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CALDAV_BASE = "/api/household_tasks/caldav"
CALDAV_STORAGE_KEY = "household_tasks.caldav"
CALDAV_STORAGE_VERSION = 1
CALDAV_STATE_VERSION = 1
MAX_REQUEST_BYTES = 1_000_000
MAX_REPORT_RESOURCES = 5_000
MAX_SYNC_CHANGES = 10_000
PBKDF2_ITERATIONS = 600_000
AUTH_CACHE_SECONDS = 300
FAILED_AUTH_WINDOW = timedelta(minutes=5)
FAILED_AUTH_LIMIT = 10
MAX_ACTIVE_CREDENTIALS = 100
MAX_AUTH_PEERS = 5_000
INVALID_SYNC_TOKEN = "Invalid sync token"

DAV = "DAV:"
CALDAV = "urn:ietf:params:xml:ns:caldav"
# These are standardized XML namespace identifiers and are never dereferenced.
CS = "http://calendarserver.org/ns/"  # NOSONAR
APPLE = "http://apple.com/ns/ical/"  # NOSONAR

ET.register_namespace("D", DAV)
ET.register_namespace("C", CALDAV)
ET.register_namespace("CS", CS)
ET.register_namespace("A", APPLE)

DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": False,
    "require_tls": True,
    "calendar_name": "Household Tasks",
    "calendar_description": "Persönliche Haushaltsaufgaben aus Home Assistant",
    "calendar_color": "#03A9F4FF",
    "allow_client_create": True,
    "allow_client_update": True,
    "allow_client_delete": True,
    "delete_mode": "cancel",
    "expose_completed_days": 90,
    "default_reminder_minutes": 0,
}


@dataclass(slots=True)
class CalDAVError(Exception):
    """Expected protocol failure."""

    status: int
    message: str
    precondition: str | None = None
    headers: dict[str, str] | None = None


@dataclass(slots=True)
class Principal:
    """Authenticated CalDAV app credential."""

    credential_id: str
    username: str
    person_id: str | None
    permission: str
    scope: str
    include_claimable: bool
    complete_checklist_on_parent: bool
    calendar_name: str
    calendar_color: str

    @property
    def can_write(self) -> bool:
        """Return whether the credential permits mutations."""
        return self.permission == "read_write"

    @property
    def collection_key(self) -> str:
        """Return a stable server-side collection identifier."""
        return self.credential_id


@dataclass(slots=True)
class CalendarResource:
    """One projected VTODO resource."""

    name: str
    occurrence_id: str
    checklist_id: str | None
    data: bytes
    etag: str
    updated_at: datetime

    @property
    def href_name(self) -> str:
        """Return URL-escaped resource filename."""
        return quote(self.name, safe="-._~")


@dataclass(slots=True)
class ParsedVTodo:
    """Normalized writable VTODO fields."""

    uid: str
    summary: str
    description: str
    status: str
    due: datetime | None
    priority: int
    percent_complete: int
    categories: list[str]
    url: str | None
    related_to: str | None
    alarms: list[dict[str, str]]
    raw_x_properties: dict[str, list[str]]
    preserved_lines: list[str]


@dataclass(slots=True)
class _VTodoParseState:
    """Bounded mutable state while reading one iCalendar object."""

    inside: bool = False
    alarm: dict[str, str] | None = None
    alarms: list[dict[str, str]] = field(default_factory=list)
    properties: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    preserved_lines: list[str] = field(default_factory=list)
    todo_count: int = 0


@dataclass(slots=True)
class _VTodoProjection:
    uid: str
    summary: str
    status: str
    percent: int
    description: str


def _default_state() -> dict[str, Any]:
    return {
        "version": CALDAV_STATE_VERSION,
        "server_id": uuid4().hex,
        "settings": deepcopy(DEFAULT_SETTINGS),
        "credentials": {},
        "collections": {},
    }


def _tag(namespace: str, name: str) -> str:
    return f"{{{namespace}}}{name}"


def _status_line(status: int) -> str:
    return f"HTTP/1.1 {status} {web.Response(status=status).reason}"


def _xml_response(root: ET.Element, *, status: int = 207) -> web.Response:
    return web.Response(
        body=ET.tostring(root, encoding="utf-8", xml_declaration=True),
        status=status,
        content_type="application/xml",
        charset="utf-8",
        headers={"Cache-Control": "no-store"},
    )


def _error_response(error: CalDAVError) -> web.Response:
    if error.precondition:
        root = ET.Element(_tag(DAV, "error"))
        ET.SubElement(root, _tag(CALDAV, error.precondition))
        response = _xml_response(root, status=error.status)
    else:
        response = web.Response(
            text=error.message,
            status=error.status,
            content_type="text/plain",
        )
    response.headers.update(error.headers or {})
    response.headers["Cache-Control"] = "no-store"
    return response


def _parse_xml(body: bytes) -> ET.Element | None:
    if not body:
        return None
    upper = body[:512].upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise CalDAVError(400, "Unsafe XML is not accepted")
    try:
        return DefusedET.fromstring(body)
    except (ET.ParseError, DefusedXmlException) as err:
        raise CalDAVError(400, "Malformed XML request") from err


def _unfold_ical(text: str) -> list[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    for line in normalized.split("\n"):
        if line.startswith((" ", "\t")) and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return lines


def _ical_unescape(value: str) -> str:
    return (
        value.replace("\\N", "\n")
        .replace("\\n", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def _ical_escape(value: Any) -> str:
    return (
        str(value or "")
        .replace("\\", "\\\\")
        .replace("\r\n", "\\n")
        .replace("\r", "\\n")
        .replace("\n", "\\n")
        .replace(";", "\\;")
        .replace(",", "\\,")
    )


def _fold_line(line: str) -> list[str]:
    """Fold one content line without splitting UTF-8 code points."""
    encoded = line.encode()
    if len(encoded) <= 75:
        return [line]
    chunks: list[str] = []
    current = bytearray()
    prefix = ""
    for character in line:
        value = character.encode()
        limit = 75 if not chunks else 74
        if current and len(current) + len(value) > limit:
            chunks.append(prefix + current.decode())
            current = bytearray()
            prefix = " "
        current.extend(value)
    chunks.append(prefix + current.decode())
    return chunks


def _calendar_bytes(lines: list[str]) -> bytes:
    folded = [part for line in lines for part in _fold_line(line)]
    return ("\r\n".join(folded) + "\r\n").encode()


def _format_ical_datetime(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _parse_ical_datetime(value: str, timezone: Any) -> datetime | None:
    clean = value.strip()
    formats = (
        ("%Y%m%dT%H%M%SZ", UTC),
        ("%Y%m%dT%H%M%S", timezone),
        ("%Y%m%d", timezone),
    )
    for pattern, tzinfo in formats:
        try:
            return datetime.strptime(clean, pattern).replace(tzinfo=tzinfo)
        except ValueError:
            continue
    return None


_PRESERVED_ICAL_PROPERTIES = {
    "ATTACH",
    "CLASS",
    "COMMENT",
    "CONTACT",
    "GEO",
    "LOCATION",
    "RESOURCES",
}


def _consume_vtodo_line(state: _VTodoParseState, line: str) -> None:
    """Consume one unfolded line without mixing parsing and validation."""
    upper = line.upper()
    if upper == "BEGIN:VTODO":
        state.todo_count += 1
        state.inside = True
        return
    if upper == "END:VTODO":
        state.inside = False
        return
    if not state.inside:
        return
    if upper == "BEGIN:VALARM":
        state.alarm = {}
        return
    if upper == "END:VALARM":
        if state.alarm is not None:
            state.alarms.append(state.alarm)
        state.alarm = None
        return
    if ":" not in line:
        return
    raw_name, value = line.split(":", 1)
    name = raw_name.split(";", 1)[0].upper()
    if state.alarm is not None:
        state.alarm[name] = _ical_unescape(value)
        return
    state.properties[name].append(_ical_unescape(value))
    is_client_extension = name.startswith("X-") and not name.startswith("X-HOUSEHOLD-")
    if name in _PRESERVED_ICAL_PROPERTIES or is_client_extension:
        state.preserved_lines.append(f"{raw_name}:{value}")


def _collect_vtodo(lines: list[str]) -> _VTodoParseState:
    state = _VTodoParseState()
    for line in lines:
        _consume_vtodo_line(state, line)
    if state.todo_count != 1:
        raise CalDAVError(
            403,
            "Exactly one VTODO is required",
            "valid-calendar-object-resource",
        )
    return state


def _first_property(
    properties: dict[str, list[str]], name: str, default: Any = ""
) -> Any:
    values = properties.get(name)
    return values[0] if values else default


def _bounded_vtodo_numbers(properties: dict[str, list[str]]) -> tuple[int, int]:
    try:
        priority = max(0, min(9, int(_first_property(properties, "PRIORITY", "0"))))
        percent = max(
            0,
            min(100, int(_first_property(properties, "PERCENT-COMPLETE", "0"))),
        )
    except ValueError as err:
        raise CalDAVError(
            403, "Invalid numeric VTODO property", "valid-calendar-data"
        ) from err
    return priority, percent


def _vtodo_status(properties: dict[str, list[str]]) -> str:
    status = str(_first_property(properties, "STATUS", "NEEDS-ACTION")).upper()
    if status in {"NEEDS-ACTION", "IN-PROCESS", "COMPLETED", "CANCELLED"}:
        return status
    return "NEEDS-ACTION"


def parse_vtodo(data: bytes, timezone: Any) -> ParsedVTodo:
    """Parse one bounded VCALENDAR containing exactly one VTODO."""
    if len(data) > MAX_REQUEST_BYTES:
        raise CalDAVError(413, "Calendar object is too large", "max-resource-size")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as err:
        raise CalDAVError(400, "Calendar object must be UTF-8") from err
    lines = _unfold_ical(text)
    if not any(line.upper() == "BEGIN:VCALENDAR" for line in lines):
        raise CalDAVError(403, "VCALENDAR is required", "valid-calendar-data")
    if any(line.upper().startswith("METHOD:") for line in lines):
        raise CalDAVError(
            403, "METHOD is not allowed", "valid-calendar-object-resource"
        )

    state = _collect_vtodo(lines)
    properties = state.properties
    uid = str(_first_property(properties, "UID")).strip()
    summary = str(_first_property(properties, "SUMMARY")).strip()
    if not uid or not summary:
        raise CalDAVError(403, "UID and SUMMARY are required", "valid-calendar-data")
    due = _parse_ical_datetime(str(_first_property(properties, "DUE")), timezone)
    priority, percent = _bounded_vtodo_numbers(properties)
    categories = [
        item.strip()
        for value in properties.get("CATEGORIES", [])
        for item in re.split(r"(?<!\\),", value)
        if item.strip()
    ]
    raw_x = {
        name: values for name, values in properties.items() if name.startswith("X-")
    }
    return ParsedVTodo(
        uid=uid,
        summary=summary[:255],
        description=str(_first_property(properties, "DESCRIPTION"))[:20_000],
        status=_vtodo_status(properties),
        due=due,
        priority=priority,
        percent_complete=percent,
        categories=categories[:50],
        url=_first_property(properties, "URL", None),
        related_to=_first_property(properties, "RELATED-TO", None),
        alarms=state.alarms[:10],
        raw_x_properties=raw_x,
        preserved_lines=state.preserved_lines[:200],
    )


def _plain_title(value: Any) -> str:
    return re.sub(r"^\[[^\]]+\]\s*", "", str(value or "Aufgabe"))


def _priority_to_ical(value: str) -> int:
    return {"critical": 1, "high": 3, "normal": 5, "low": 9}.get(value, 5)


def _priority_from_ical(value: int) -> str:
    if value <= 2 and value > 0:
        return "critical"
    if value <= 4 and value > 0:
        return "high"
    if value >= 7:
        return "low"
    return "normal"


def _occurrence_uid(occurrence_id: str) -> str:
    digest = hashlib.sha256(occurrence_id.encode()).hexdigest()[:32]
    return f"ht-{digest}@home-assistant.local"


def _resource_basename(occurrence_id: str) -> str:
    encoded = base64.urlsafe_b64encode(occurrence_id.encode()).decode().rstrip("=")
    return f"{encoded}.ics"


def _checklist_basename(occurrence_id: str, checklist_id: str) -> str:
    source = f"{occurrence_id}\0{checklist_id}".encode()
    encoded = base64.urlsafe_b64encode(source).decode().rstrip("=")
    return f"check-{encoded}.ics"


def _as_datetime(value: Any) -> datetime:
    parsed = dt_util.parse_datetime(str(value or ""))
    return parsed or dt_util.utcnow()


def _projection_fields(
    occurrence_id: str,
    occurrence: dict[str, Any],
    checklist: dict[str, Any] | None,
) -> _VTodoProjection:
    if checklist is not None:
        item_id = str(checklist.get("id", "step"))
        item_hash = hashlib.sha256(item_id.encode()).hexdigest()[:12]
        completed = bool(checklist.get("completed"))
        return _VTodoProjection(
            uid=f"{_occurrence_uid(occurrence_id)}-check-{item_hash}",
            summary=str(checklist.get("title") or "Schritt"),
            status="COMPLETED" if completed else "NEEDS-ACTION",
            percent=100 if completed else 0,
            description=f"Unteraufgabe von {_plain_title(occurrence.get('title'))}",
        )
    metadata = occurrence.get("caldav", {})
    status = occurrence.get("status", "open")
    todo_status = {
        "completed": "COMPLETED",
        "cancelled": "CANCELLED",
        "in_progress": "IN-PROCESS",
    }.get(status, "NEEDS-ACTION")
    return _VTodoProjection(
        uid=str(metadata.get("uid") or _occurrence_uid(occurrence_id)),
        summary=_plain_title(occurrence.get("title")),
        status=todo_status,
        percent=100 if status == "completed" else int(metadata.get("percent", 0)),
        description=str(
            occurrence.get("description")
            or occurrence.get("task", {}).get("description")
            or ""
        ),
    )


def _append_vtodo_optional_fields(
    lines: list[str],
    occurrence: dict[str, Any],
    projection: _VTodoProjection,
    metadata: dict[str, Any],
) -> None:
    if projection.description:
        lines.append(f"DESCRIPTION:{_ical_escape(projection.description)}")
    if not occurrence.get("caldav_no_due") and occurrence.get("due"):
        lines.append(f"DUE:{_format_ical_datetime(_as_datetime(occurrence['due']))}")
    if projection.status == "COMPLETED" and occurrence.get("resolved_at"):
        completed = _format_ical_datetime(_as_datetime(occurrence["resolved_at"]))
        lines.append(f"COMPLETED:{completed}")
    categories = metadata.get("categories") or ["Household Tasks"]
    if categories:
        lines.append(
            "CATEGORIES:" + ",".join(_ical_escape(item) for item in categories)
        )
    if metadata.get("url"):
        lines.append(f"URL:{metadata['url']}")


def _append_vtodo_relations(
    lines: list[str],
    occurrence_id: str,
    occurrence: dict[str, Any],
    checklist: dict[str, Any] | None,
) -> None:
    if checklist is not None:
        parent_uid = _ical_escape(_occurrence_uid(occurrence_id))
        lines.append(f"RELATED-TO;RELTYPE=PARENT:{parent_uid}")
        lines.append(f"X-HOUSEHOLD-CHECKLIST-ID:{_ical_escape(checklist.get('id'))}")
        return
    task = occurrence.get("task", {})
    for dependency in occurrence.get("dependencies", []):
        dependency_uid = _ical_escape(_occurrence_uid(dependency))
        lines.append(f"RELATED-TO;RELTYPE=DEPENDS-ON:{dependency_uid}")
    lines.extend(
        [
            f"X-HOUSEHOLD-OCCURRENCE-ID:{_ical_escape(occurrence_id)}",
            f"X-HOUSEHOLD-REVISION:{int(occurrence.get('revision', 1))}",
            f"X-HOUSEHOLD-ASSIGNEE:{_ical_escape(occurrence.get('assignee') or '')}",
            f"X-HOUSEHOLD-POINTS:{int(task.get('market', {}).get('points', 0))}",
        ]
    )
    metadata = occurrence.get("caldav", {})
    lines.extend(
        str(line)
        for line in metadata.get("preserved_lines", [])[:200]
        if "\r" not in str(line) and "\n" not in str(line)
    )


def _append_vtodo_alarms(
    lines: list[str],
    occurrence: dict[str, Any],
    summary: str,
    *,
    checklist: dict[str, Any] | None,
    default_reminder_minutes: int,
) -> None:
    alarms = occurrence.get("caldav", {}).get("alarms", []) if checklist is None else []
    if not alarms and default_reminder_minutes > 0 and occurrence.get("due"):
        alarms = [
            {
                "ACTION": "DISPLAY",
                "TRIGGER": f"-PT{default_reminder_minutes}M",
                "DESCRIPTION": summary,
            }
        ]
    for alarm in alarms[:10]:
        if not alarm.get("TRIGGER"):
            continue
        lines.extend(
            [
                "BEGIN:VALARM",
                f"ACTION:{alarm.get('ACTION', 'DISPLAY')}",
                f"TRIGGER:{alarm['TRIGGER']}",
                f"DESCRIPTION:{_ical_escape(alarm.get('DESCRIPTION') or summary)}",
                "END:VALARM",
            ]
        )


def _render_vtodo(
    occurrence_id: str,
    occurrence: dict[str, Any],
    *,
    checklist: dict[str, Any] | None = None,
    default_reminder_minutes: int = 0,
) -> bytes:
    task = occurrence.get("task", {})
    metadata = occurrence.get("caldav", {})
    projection = _projection_fields(occurrence_id, occurrence, checklist)
    created = _as_datetime(occurrence.get("created_at"))
    updated = _as_datetime(occurrence.get("updated_at"))
    lines = [
        "BEGIN:VCALENDAR",
        "PRODID:-//Household Tasks//CalDAV VTODO 1.0//EN",
        "VERSION:2.0",
        "CALSCALE:GREGORIAN",
        "BEGIN:VTODO",
        f"UID:{_ical_escape(projection.uid)}",
        f"DTSTAMP:{_format_ical_datetime(updated)}",
        f"CREATED:{_format_ical_datetime(created)}",
        f"LAST-MODIFIED:{_format_ical_datetime(updated)}",
        f"SUMMARY:{_ical_escape(projection.summary)}",
        f"STATUS:{projection.status}",
        f"PERCENT-COMPLETE:{projection.percent}",
        f"PRIORITY:{_priority_to_ical(task.get('market', {}).get('priority', 'normal'))}",
    ]
    _append_vtodo_optional_fields(lines, occurrence, projection, metadata)
    _append_vtodo_relations(lines, occurrence_id, occurrence, checklist)
    _append_vtodo_alarms(
        lines,
        occurrence,
        projection.summary,
        checklist=checklist,
        default_reminder_minutes=default_reminder_minutes,
    )
    lines.extend(["END:VTODO", "END:VCALENDAR"])
    return _calendar_bytes(lines)


def _etag(data: bytes) -> str:
    return f'"{hashlib.sha256(data).hexdigest()}"'


def _credential_expiry(expires_at: str | None) -> datetime | None:
    if not expires_at:
        return None
    expiry = dt_util.parse_datetime(expires_at)
    if expiry is None or expiry <= dt_util.utcnow():
        raise vol.Invalid("CalDAV credential expiry must be in the future")
    return expiry


def _normalize_credential_owner(
    person_id: str | None, scope: str, people: dict[str, Any]
) -> str | None:
    if scope not in {"personal", "household"}:
        raise vol.Invalid("Unknown CalDAV scope")
    if scope == "personal":
        if person_id not in people:
            raise vol.Invalid("A configured person is required for personal scope")
        return person_id
    return person_id if person_id in people else None


def _credential_record(
    *,
    username: str,
    label: str,
    person_id: str | None,
    permission: str,
    scope: str,
    include_claimable: bool,
    complete_checklist_on_parent: bool,
    salt: bytes,
    digest: bytes,
    expiry: datetime | None,
) -> dict[str, Any]:
    return {
        "username": username,
        "label": label.strip()[:100] or "CalDAV client",
        "person_id": person_id,
        "permission": permission,
        "scope": scope,
        "include_claimable": bool(include_claimable),
        "complete_checklist_on_parent": bool(complete_checklist_on_parent),
        "salt": base64.b64encode(salt).decode(),
        "password_hash": base64.b64encode(digest).decode(),
        "iterations": PBKDF2_ITERATIONS,
        "created_at": dt_util.utcnow().isoformat(),
        "expires_at": expiry.isoformat() if expiry else None,
    }


class CalDAVService:
    """Persist credentials and serve person-scoped task collections."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.store: Store[dict[str, Any]] = Store(
            hass, CALDAV_STORAGE_VERSION, CALDAV_STORAGE_KEY
        )
        self.state = _default_state()
        self.lock = asyncio.Lock()
        self._auth_cache: dict[str, tuple[datetime, Principal]] = {}
        self._failed_auth: dict[str, deque[datetime]] = defaultdict(deque)

    async def async_setup(self) -> None:
        """Load and normalize CalDAV state."""
        stored = await self.store.async_load()
        if isinstance(stored, dict):
            self.state.update(stored)
        self.state.setdefault("server_id", uuid4().hex)
        self.state.setdefault("credentials", {})
        self.state.setdefault("collections", {})
        settings = deepcopy(DEFAULT_SETTINGS)
        settings.update(self.state.get("settings", {}))
        self.state["settings"] = settings
        self.state["version"] = CALDAV_STATE_VERSION
        await self.store.async_save(self.state)

    def public_status(self) -> dict[str, Any]:
        """Return secret-free settings and credential metadata."""
        credentials = []
        now = dt_util.utcnow()
        for credential_id, item in self.state.get("credentials", {}).items():
            if item.get("revoked_at"):
                continue
            expires_at = dt_util.parse_datetime(str(item.get("expires_at") or ""))
            credentials.append(
                {
                    "id": credential_id,
                    "username": item.get("username"),
                    "label": item.get("label"),
                    "person_id": item.get("person_id"),
                    "permission": item.get("permission"),
                    "scope": item.get("scope"),
                    "include_claimable": bool(item.get("include_claimable", True)),
                    "complete_checklist_on_parent": bool(
                        item.get("complete_checklist_on_parent", True)
                    ),
                    "created_at": item.get("created_at"),
                    "last_used_at": item.get("last_used_at"),
                    "expires_at": item.get("expires_at"),
                    "expired": bool(expires_at and expires_at <= now),
                }
            )
        credentials.sort(key=lambda item: item.get("created_at") or "")
        return {
            "base_path": CALDAV_BASE,
            "server_url": self._absolute_url(CALDAV_BASE + "/"),
            "settings": deepcopy(self.state["settings"]),
            "credentials": credentials,
            "protocol": {
                "caldav": "RFC 4791",
                "icalendar": "RFC 5545 VTODO",
                "sync": "RFC 6578",
                "discovery": "RFC 6764",
                "components": ["VTODO", "VALARM"],
            },
        }

    def health_findings(self) -> list[dict[str, Any]]:
        """Return actionable, secret-free CalDAV configuration findings."""
        settings = self.state["settings"]
        if not settings.get("enabled"):
            return []
        findings: list[dict[str, Any]] = []
        active = [
            item
            for item in self.state.get("credentials", {}).values()
            if not item.get("revoked_at")
            and (
                not item.get("expires_at")
                or _as_datetime(item["expires_at"]) > dt_util.utcnow()
            )
        ]
        if not active:
            findings.append(
                {
                    "severity": "warning",
                    "code": "caldav_no_credentials",
                    "message": "CalDAV ist aktiv, aber es gibt kein gültiges App-Passwort.",
                }
            )
        engine = None
        with suppress(CalDAVError):
            engine = self._engine()
        if engine:
            for item in active:
                person_id = item.get("person_id")
                if item.get("scope") == "personal" and person_id not in engine.people:
                    findings.append(
                        {
                            "severity": "critical",
                            "code": "caldav_orphan_credential",
                            "message": "Ein persönlicher CalDAV-Zugang verweist auf eine fehlende Person.",
                        }
                    )
        server_url = self._absolute_url(CALDAV_BASE + "/")
        if settings.get("require_tls") and not server_url.startswith("https://"):
            findings.append(
                {
                    "severity": "critical",
                    "code": "caldav_https_missing",
                    "message": "CalDAV erzwingt HTTPS, aber Home Assistant hat keine HTTPS-URL konfiguriert.",
                }
            )
        return findings

    async def async_save_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        """Validate and persist operator-visible server settings."""
        allowed = set(DEFAULT_SETTINGS)
        unknown = set(settings) - allowed
        if unknown:
            raise vol.Invalid(f"Unknown CalDAV settings: {', '.join(sorted(unknown))}")
        updated = deepcopy(self.state["settings"])
        updated.update(settings)
        if updated["delete_mode"] != "cancel":
            raise vol.Invalid("CalDAV delete mode must be cancel")
        days = int(updated["expose_completed_days"])
        if not 0 <= days <= 3650:
            raise vol.Invalid("Completed retention must be between 0 and 3650 days")
        reminder = int(updated["default_reminder_minutes"])
        if not 0 <= reminder <= 525_600:
            raise vol.Invalid("Default reminder must be between 0 and 525600 minutes")
        color = str(updated["calendar_color"])
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?", color):
            raise vol.Invalid("Calendar color must be #RRGGBB or #RRGGBBAA")
        updated["expose_completed_days"] = days
        updated["default_reminder_minutes"] = reminder
        async with self.lock:
            self.state["settings"] = updated
            await self.store.async_save(self.state)
        return self.public_status()

    async def async_create_credential(
        self,
        *,
        person_id: str | None,
        label: str,
        permission: str,
        scope: str,
        include_claimable: bool,
        complete_checklist_on_parent: bool,
        expires_at: str | None,
    ) -> dict[str, Any]:
        """Create a one-time app password and persist only its verifier."""
        engine = self._engine()
        now = dt_util.utcnow()
        active_count = sum(
            not item.get("revoked_at")
            and (not item.get("expires_at") or _as_datetime(item["expires_at"]) > now)
            for item in self.state.get("credentials", {}).values()
        )
        if active_count >= MAX_ACTIVE_CREDENTIALS:
            raise vol.Invalid("Too many active CalDAV credentials")
        if permission not in {"read_only", "read_write"}:
            raise vol.Invalid("Unknown CalDAV permission")
        person_id = _normalize_credential_owner(person_id, scope, engine.people)
        expiry = _credential_expiry(expires_at)
        credential_id = uuid4().hex
        stem = re.sub(r"[^a-z0-9]+", "-", person_id or "household").strip("-")
        username = f"{stem}-{credential_id[:8]}"
        password = secrets.token_urlsafe(32)
        salt = secrets.token_bytes(16)
        digest = await self.hass.async_add_executor_job(
            hashlib.pbkdf2_hmac,
            "sha256",
            password.encode(),
            salt,
            PBKDF2_ITERATIONS,
        )
        record = _credential_record(
            username=username,
            label=label,
            person_id=person_id,
            permission=permission,
            scope=scope,
            include_claimable=include_claimable,
            complete_checklist_on_parent=complete_checklist_on_parent,
            salt=salt,
            digest=digest,
            expiry=expiry,
        )
        async with self.lock:
            self.state["credentials"][credential_id] = record
            await self.store.async_save(self.state)
        self.hass.bus.async_fire(
            f"{DOMAIN}_caldav_security_event",
            {"event": "credential_created", "credential_id": credential_id},
        )
        return {
            **self.public_status(),
            "created_credential": {
                "id": credential_id,
                "username": username,
                "password": password,
                "server_url": self._absolute_url(CALDAV_BASE + "/"),
            },
        }

    async def async_revoke_credential(self, credential_id: str) -> dict[str, Any]:
        """Revoke an app password without erasing its audit metadata."""
        record = self.state.get("credentials", {}).get(credential_id)
        if not record or record.get("revoked_at"):
            raise vol.Invalid("Unknown CalDAV credential")
        async with self.lock:
            record["revoked_at"] = dt_util.utcnow().isoformat()
            self._auth_cache.clear()
            await self.store.async_save(self.state)
        self.hass.bus.async_fire(
            f"{DOMAIN}_caldav_security_event",
            {"event": "credential_revoked", "credential_id": credential_id},
        )
        return self.public_status()

    async def handle(self, request: web.Request) -> web.StreamResponse:
        """Dispatch one WebDAV/CalDAV request."""
        try:
            if request.path == "/.well-known/caldav":
                raise web.HTTPPermanentRedirect(location=CALDAV_BASE + "/")
            if not self.state["settings"].get("enabled", True):
                raise CalDAVError(503, "CalDAV is disabled")
            if self.state["settings"].get("require_tls", True) and not request.secure:
                raise CalDAVError(403, "CalDAV requires HTTPS")
            principal = await self._authenticate(request)
            path = self._classify_path(request.path, principal)
            return await self._dispatch(request, principal, path)
        except web.HTTPException:
            raise
        except CalDAVError as error:
            return _error_response(error)
        except vol.Invalid as error:
            return _error_response(CalDAVError(409, str(error)))
        except Exception:
            _LOGGER.exception("Unexpected CalDAV request failure")
            return _error_response(CalDAVError(500, "Internal CalDAV error"))

    async def _dispatch(
        self,
        request: web.Request,
        principal: Principal,
        path: tuple[str, str | None],
    ) -> web.StreamResponse:
        method = request.method.upper()
        if method == "OPTIONS":
            return self._options(path)
        if method == "PROPFIND":
            return await self._propfind(request, principal, path)
        if method == "REPORT":
            return await self._report(request, principal, path)
        if method in {"GET", "HEAD"}:
            return self._get(request, principal, path)
        if method == "PUT":
            return await self._put(request, principal, path)
        if method == "DELETE":
            return await self._delete(request, principal, path)
        if method == "PROPPATCH":
            return await self._proppatch(request, principal, path)
        if method == "ACL":
            return web.Response(status=200, headers=self._dav_headers())
        raise CalDAVError(
            405, "Method not allowed", headers={"Allow": self._allow(path)}
        )

    def _engine(self) -> Any:
        from .engine import get_loaded_engine

        engine = get_loaded_engine(self.hass)
        if engine is None:
            raise CalDAVError(503, "Household Tasks is not loaded")
        return engine

    def _absolute_url(self, path: str) -> str:
        base = self.hass.config.external_url or self.hass.config.internal_url or ""
        return base.rstrip("/") + path

    def _authentication_attempts(
        self, request: web.Request, now: datetime
    ) -> deque[datetime]:
        peer = request.remote or "unknown"
        if peer not in self._failed_auth and len(self._failed_auth) >= MAX_AUTH_PEERS:
            self._failed_auth.clear()
        attempts = self._failed_auth[peer]
        while attempts and attempts[0] < now - FAILED_AUTH_WINDOW:
            attempts.popleft()
        if len(attempts) >= FAILED_AUTH_LIMIT:
            raise CalDAVError(
                429, "Too many authentication attempts", headers={"Retry-After": "300"}
            )
        return attempts

    def _decode_basic_credentials(
        self, header: str, attempts: deque[datetime], now: datetime
    ) -> tuple[str, str]:
        if not header.startswith("Basic "):
            raise self._unauthorized()
        try:
            decoded = base64.b64decode(header[6:], validate=True).decode()
            username, password = decoded.split(":", 1)
            return username, password
        except ValueError as err:
            attempts.append(now)
            raise self._unauthorized() from err

    async def _password_matches(self, item: dict[str, Any], password: str) -> bool:
        salt = base64.b64decode(item["salt"])
        expected = base64.b64decode(item["password_hash"])
        actual = await self.hass.async_add_executor_job(
            hashlib.pbkdf2_hmac,
            "sha256",
            password.encode(),
            salt,
            int(item.get("iterations", PBKDF2_ITERATIONS)),
        )
        return hmac.compare_digest(actual, expected)

    def _principal(self, credential_id: str, item: dict[str, Any]) -> Principal:
        return Principal(
            credential_id=credential_id,
            username=item["username"],
            person_id=item.get("person_id"),
            permission=item.get("permission", "read_only"),
            scope=item.get("scope", "personal"),
            include_claimable=bool(item.get("include_claimable", True)),
            complete_checklist_on_parent=bool(
                item.get("complete_checklist_on_parent", True)
            ),
            calendar_name=item.get("calendar_name")
            or self.state["settings"]["calendar_name"],
            calendar_color=item.get("calendar_color")
            or self.state["settings"]["calendar_color"],
        )

    async def _record_credential_use(self, item: dict[str, Any], now: datetime) -> None:
        last_used = (
            _as_datetime(item["last_used_at"]) if item.get("last_used_at") else None
        )
        if last_used and last_used >= now - timedelta(hours=1):
            return
        async with self.lock:
            item["last_used_at"] = now.isoformat()
            await self.store.async_save(self.state)

    async def _authenticate(self, request: web.Request) -> Principal:
        header = request.headers.get("Authorization", "")
        cache_key = hashlib.sha256(header.encode()).hexdigest()
        cached = self._auth_cache.get(cache_key)
        now = dt_util.utcnow()
        if cached and cached[0] > now:
            return cached[1]
        attempts = self._authentication_attempts(request, now)
        username, password = self._decode_basic_credentials(header, attempts, now)
        matches = [
            (credential_id, item)
            for credential_id, item in self.state.get("credentials", {}).items()
            if item.get("username") == username and not item.get("revoked_at")
        ]
        for credential_id, item in matches:
            expires = dt_util.parse_datetime(str(item.get("expires_at") or ""))
            if expires and expires <= now:
                continue
            if not await self._password_matches(item, password):
                continue
            principal = self._principal(credential_id, item)
            self._auth_cache[cache_key] = (
                now + timedelta(seconds=AUTH_CACHE_SECONDS),
                principal,
            )
            await self._record_credential_use(item, now)
            attempts.clear()
            return principal
        attempts.append(now)
        raise self._unauthorized()

    @staticmethod
    def _unauthorized() -> CalDAVError:
        return CalDAVError(
            401,
            "Authentication required",
            headers={
                "WWW-Authenticate": 'Basic realm="Household Tasks CalDAV", charset="UTF-8"'
            },
        )

    def _classify_path(self, path: str, principal: Principal) -> tuple[str, str | None]:
        normalized = path.rstrip("/") + "/"
        root = CALDAV_BASE + "/"
        principal_root = f"{CALDAV_BASE}/principals/"
        principal_path = f"{principal_root}{quote(principal.username, safe='')}/"
        calendars_root = f"{CALDAV_BASE}/calendars/"
        home = f"{calendars_root}{quote(principal.username, safe='')}/"
        calendar = f"{home}tasks/"
        if normalized == root:
            return ("root", None)
        if normalized == principal_root:
            return ("principal_root", None)
        if normalized == principal_path:
            return ("principal", None)
        if normalized == calendars_root:
            return ("calendars_root", None)
        if normalized == home:
            return ("home", None)
        if normalized == calendar:
            return ("calendar", None)
        if path.startswith(calendar) and not path.endswith("/"):
            return ("resource", unquote(path[len(calendar) :]))
        raise CalDAVError(404, "CalDAV resource not found")

    @staticmethod
    def _dav_headers() -> dict[str, str]:
        return {
            "DAV": "1, 3, access-control, calendar-access, sync-collection",
            "MS-Author-Via": "DAV",
            "Cache-Control": "no-store",
        }

    def _allow(self, path: tuple[str, str | None]) -> str:
        methods = ["OPTIONS", "PROPFIND", "REPORT", "GET", "HEAD", "ACL"]
        if path[0] == "resource":
            methods.extend(["PUT", "DELETE"])
        if path[0] == "calendar":
            methods.append("PROPPATCH")
        return ", ".join(methods)

    def _options(self, path: tuple[str, str | None]) -> web.Response:
        return web.Response(
            status=200,
            headers={**self._dav_headers(), "Allow": self._allow(path)},
        )

    def _paths(self, principal: Principal) -> dict[str, str]:
        username = quote(principal.username, safe="")
        return {
            "root": CALDAV_BASE + "/",
            "principal_root": f"{CALDAV_BASE}/principals/",
            "principal": f"{CALDAV_BASE}/principals/{username}/",
            "calendars_root": f"{CALDAV_BASE}/calendars/",
            "home": f"{CALDAV_BASE}/calendars/{username}/",
            "calendar": f"{CALDAV_BASE}/calendars/{username}/tasks/",
        }

    def _is_visible(self, occurrence: dict[str, Any], principal: Principal) -> bool:
        if occurrence.get("caldav_deleted"):
            return False
        resolved = occurrence.get("status") in {"completed", "cancelled"}
        if resolved:
            days = int(self.state["settings"].get("expose_completed_days", 90))
            resolved_at = dt_util.parse_datetime(str(occurrence.get("resolved_at", "")))
            if (
                days == 0
                or not resolved_at
                or resolved_at < dt_util.utcnow() - timedelta(days=days)
            ):
                return False
        if principal.scope == "household":
            return True
        person_id = principal.person_id
        if occurrence.get("assignee") == person_id or person_id in occurrence.get(
            "helpers", []
        ):
            return True
        if not principal.include_claimable or occurrence.get("assignee") is not None:
            return False
        allowed = occurrence.get("task", {}).get("assignment", {}).get("people", [])
        return not allowed or person_id in allowed

    def _resources(self, principal: Principal) -> dict[str, CalendarResource]:
        engine = self._engine()
        aliases = (
            self.state.get("collections", {})
            .get(principal.collection_key, {})
            .get("aliases", {})
        )
        reverse_alias = {value: key for key, value in aliases.items()}
        resources: dict[str, CalendarResource] = {}
        reminder = int(self.state["settings"].get("default_reminder_minutes", 0))
        for occurrence_id, occurrence in engine.state.get("occurrences", {}).items():
            if not self._is_visible(occurrence, principal):
                continue
            name = reverse_alias.get(occurrence_id) or _resource_basename(occurrence_id)
            data = _render_vtodo(
                occurrence_id,
                occurrence,
                default_reminder_minutes=reminder,
            )
            resources[name] = CalendarResource(
                name,
                occurrence_id,
                None,
                data,
                _etag(data),
                _as_datetime(occurrence.get("updated_at")),
            )
            for item in occurrence.get("checklist", []):
                child_name = _checklist_basename(occurrence_id, str(item.get("id")))
                child_data = _render_vtodo(
                    occurrence_id,
                    occurrence,
                    checklist=item,
                )
                resources[child_name] = CalendarResource(
                    child_name,
                    occurrence_id,
                    str(item.get("id")),
                    child_data,
                    _etag(child_data),
                    _as_datetime(occurrence.get("updated_at")),
                )
        return resources

    async def _sync_collection(
        self,
        principal: Principal,
        resources: dict[str, CalendarResource],
    ) -> dict[str, Any]:
        async with self.lock:
            collection = self.state["collections"].setdefault(
                principal.collection_key,
                {"revision": 0, "resources": {}, "changes": [], "aliases": {}},
            )
            previous = collection.setdefault("resources", {})
            current = {name: item.etag for name, item in resources.items()}
            changed = sorted(
                name for name, etag in current.items() if previous.get(name) != etag
            )
            deleted = sorted(set(previous) - set(current))
            if changed or deleted:
                collection["revision"] = int(collection.get("revision", 0)) + 1
                revision = collection["revision"]
                changes = collection.setdefault("changes", [])
                changes.extend(
                    {"revision": revision, "name": name, "deleted": False}
                    for name in changed
                )
                changes.extend(
                    {"revision": revision, "name": name, "deleted": True}
                    for name in deleted
                )
                del changes[:-MAX_SYNC_CHANGES]
                collection["resources"] = current
                await self.store.async_save(self.state)
            return collection

    def _sync_token(self, principal: Principal, revision: int) -> str:
        return (
            f"urn:uuid:{self.state['server_id']}:{principal.collection_key}:{revision}"
        )

    def _parse_sync_token(self, principal: Principal, token: str) -> int:
        prefix = f"urn:uuid:{self.state['server_id']}:{principal.collection_key}:"
        if not token.startswith(prefix):
            raise CalDAVError(409, INVALID_SYNC_TOKEN, "valid-sync-token")
        try:
            revision = int(token[len(prefix) :])
        except ValueError as err:
            raise CalDAVError(409, INVALID_SYNC_TOKEN, "valid-sync-token") from err
        if revision < 0:
            raise CalDAVError(409, INVALID_SYNC_TOKEN, "valid-sync-token")
        return revision

    async def _read_body(self, request: web.Request) -> bytes:
        if request.content_length and request.content_length > MAX_REQUEST_BYTES:
            raise CalDAVError(413, "Request is too large")
        body = await request.read()
        if len(body) > MAX_REQUEST_BYTES:
            raise CalDAVError(413, "Request is too large")
        return body

    async def _propfind(
        self,
        request: web.Request,
        principal: Principal,
        path: tuple[str, str | None],
    ) -> web.Response:
        depth = request.headers.get("Depth", "0")
        if depth not in {"0", "1"}:
            raise CalDAVError(
                403, "Depth infinity is forbidden", "propfind-finite-depth"
            )
        root = _parse_xml(await self._read_body(request))
        requested = self._requested_properties(root)
        paths = self._paths(principal)
        resources = self._resources(principal)
        collection = await self._sync_collection(principal, resources)
        multistatus = ET.Element(_tag(DAV, "multistatus"))
        kind, resource_name = path
        self._add_prop_response(
            multistatus,
            paths[kind]
            if kind != "resource"
            else paths["calendar"] + quote(resource_name or ""),
            kind,
            requested,
            principal,
            collection,
            resources.get(resource_name or ""),
        )
        if depth == "1":
            children: list[tuple[str, str, CalendarResource | None]] = []
            if kind == "root":
                children = [
                    (paths["principal_root"], "principal_root", None),
                    (paths["calendars_root"], "calendars_root", None),
                ]
            elif kind == "principal_root":
                children = [(paths["principal"], "principal", None)]
            elif kind == "calendars_root":
                children = [(paths["home"], "home", None)]
            elif kind == "home":
                children = [(paths["calendar"], "calendar", None)]
            elif kind == "calendar":
                children = [
                    (paths["calendar"] + item.href_name, "resource", item)
                    for item in list(resources.values())[:MAX_REPORT_RESOURCES]
                ]
            for href, child_kind, item in children:
                self._add_prop_response(
                    multistatus,
                    href,
                    child_kind,
                    requested,
                    principal,
                    collection,
                    item,
                )
        response = _xml_response(multistatus)
        response.headers.update(self._dav_headers())
        return response

    @staticmethod
    def _requested_properties(root: ET.Element | None) -> list[str] | None:
        if root is None or root.find(_tag(DAV, "allprop")) is not None:
            return None
        prop = root.find(_tag(DAV, "prop"))
        return [child.tag for child in prop] if prop is not None else None

    def _property_values(
        self,
        kind: str,
        principal: Principal,
        collection: dict[str, Any],
        resource: CalendarResource | None,
    ) -> dict[str, Any]:
        paths = self._paths(principal)
        display = {
            "root": "Household Tasks CalDAV",
            "principal_root": "Principals",
            "principal": principal.username,
            "calendars_root": "Calendars",
            "home": principal.username,
            "calendar": principal.calendar_name,
            "resource": _plain_title(
                self._engine()
                .state["occurrences"]
                .get(resource.occurrence_id if resource else "", {})
                .get("title")
            ),
        }[kind]
        resource_type = ET.Element(_tag(DAV, "resourcetype"))
        if kind != "resource":
            ET.SubElement(resource_type, _tag(DAV, "collection"))
        if kind == "principal":
            ET.SubElement(resource_type, _tag(DAV, "principal"))
        if kind == "calendar":
            ET.SubElement(resource_type, _tag(CALDAV, "calendar"))
        values: dict[str, Any] = {
            _tag(DAV, "displayname"): display,
            _tag(DAV, "resourcetype"): resource_type,
            _tag(DAV, "current-user-principal"): ("href", paths["principal"]),
            _tag(DAV, "owner"): ("href", paths["principal"]),
        }
        if kind == "principal":
            values.update(
                {
                    _tag(DAV, "principal-URL"): ("href", paths["principal"]),
                    _tag(CALDAV, "calendar-home-set"): ("href", paths["home"]),
                    _tag(CALDAV, "calendar-user-address-set"): (
                        "href",
                        f"mailto:{principal.username}@home-assistant.local",
                    ),
                }
            )
        if kind == "calendar":
            supported = ET.Element(_tag(CALDAV, "supported-calendar-component-set"))
            ET.SubElement(supported, _tag(CALDAV, "comp"), {"name": "VTODO"})
            reports = ET.Element(_tag(DAV, "supported-report-set"))
            for namespace, name in (
                (CALDAV, "calendar-query"),
                (CALDAV, "calendar-multiget"),
                (DAV, "sync-collection"),
            ):
                supported_report = ET.SubElement(reports, _tag(DAV, "supported-report"))
                report = ET.SubElement(supported_report, _tag(DAV, "report"))
                ET.SubElement(report, _tag(namespace, name))
            privilege = ET.Element(_tag(DAV, "current-user-privilege-set"))
            names = ["read"] + (["write"] if principal.can_write else [])
            for name in names:
                item = ET.SubElement(privilege, _tag(DAV, "privilege"))
                ET.SubElement(item, _tag(DAV, name))
            values.update(
                {
                    _tag(CALDAV, "calendar-description"): self.state["settings"][
                        "calendar_description"
                    ],
                    _tag(CALDAV, "supported-calendar-component-set"): supported,
                    _tag(
                        CALDAV, "supported-calendar-data"
                    ): self._supported_calendar_data(),
                    _tag(CALDAV, "max-resource-size"): str(MAX_REQUEST_BYTES),
                    _tag(CS, "getctag"): str(collection.get("revision", 0)),
                    _tag(DAV, "sync-token"): self._sync_token(
                        principal, int(collection.get("revision", 0))
                    ),
                    _tag(DAV, "supported-report-set"): reports,
                    _tag(DAV, "current-user-privilege-set"): privilege,
                    _tag(APPLE, "calendar-color"): principal.calendar_color,
                    _tag(APPLE, "calendar-order"): "1",
                }
            )
        if kind == "resource" and resource:
            values.update(
                {
                    _tag(DAV, "getetag"): resource.etag,
                    _tag(
                        DAV, "getcontenttype"
                    ): "text/calendar; charset=utf-8; component=vtodo",
                    _tag(DAV, "getcontentlength"): str(len(resource.data)),
                    _tag(DAV, "getlastmodified"): format_datetime(
                        resource.updated_at.astimezone(UTC), usegmt=True
                    ),
                    _tag(CALDAV, "calendar-data"): resource.data.decode(),
                }
            )
        return values

    @staticmethod
    def _supported_calendar_data() -> ET.Element:
        root = ET.Element(_tag(CALDAV, "supported-calendar-data"))
        ET.SubElement(
            root,
            _tag(CALDAV, "calendar-data"),
            {"content-type": "text/calendar", "version": "2.0"},
        )
        return root

    def _add_prop_response(
        self,
        multistatus: ET.Element,
        href: str,
        kind: str,
        requested: list[str] | None,
        principal: Principal,
        collection: dict[str, Any],
        resource: CalendarResource | None,
    ) -> None:
        response = ET.SubElement(multistatus, _tag(DAV, "response"))
        ET.SubElement(response, _tag(DAV, "href")).text = href
        available = self._property_values(kind, principal, collection, resource)
        wanted = requested or list(available)
        found = {name: available[name] for name in wanted if name in available}
        missing = [name for name in wanted if name not in available]
        self._add_propstat(response, found, 200)
        if missing:
            self._add_propstat(response, dict.fromkeys(missing), 404)

    @staticmethod
    def _add_propstat(
        response: ET.Element, values: dict[str, Any], status: int
    ) -> None:
        if not values:
            return
        propstat = ET.SubElement(response, _tag(DAV, "propstat"))
        prop = ET.SubElement(propstat, _tag(DAV, "prop"))
        for name, value in values.items():
            element = ET.SubElement(prop, name)
            if isinstance(value, ET.Element):
                element.extend(list(value))
                element.text = value.text
            elif isinstance(value, tuple) and value[0] == "href":
                ET.SubElement(element, _tag(DAV, "href")).text = value[1]
            elif value is not None:
                element.text = str(value)
        ET.SubElement(propstat, _tag(DAV, "status")).text = _status_line(status)

    async def _report(
        self,
        request: web.Request,
        principal: Principal,
        path: tuple[str, str | None],
    ) -> web.Response:
        if path[0] != "calendar":
            raise CalDAVError(403, "Reports are only available on the calendar")
        body = _parse_xml(await self._read_body(request))
        if body is None:
            raise CalDAVError(400, "REPORT body is required")
        resources = self._resources(principal)
        collection = await self._sync_collection(principal, resources)
        report_type = {
            _tag(CALDAV, "calendar-multiget"): "multiget",
            _tag(CALDAV, "calendar-query"): "query",
            _tag(DAV, "sync-collection"): "sync",
        }.get(body.tag)
        if report_type == "multiget":
            names = [
                unquote((element.text or "").rstrip("/").rsplit("/", 1)[-1])
                for element in body.findall(_tag(DAV, "href"))
            ]
            return self._resource_multistatus(principal, resources, names, body)
        if report_type == "query":
            selected = self._filter_resources(resources, body)
            return self._resource_multistatus(
                principal,
                resources,
                list(selected)[:MAX_REPORT_RESOURCES],
                body,
            )
        if report_type == "sync":
            return self._sync_report(principal, resources, collection, body)
        raise CalDAVError(403, "Unsupported REPORT", "supported-report")

    def _resource_multistatus(
        self,
        principal: Principal,
        resources: dict[str, CalendarResource],
        names: list[str],
        request_root: ET.Element,
    ) -> web.Response:
        requested = self._report_properties(request_root)
        paths = self._paths(principal)
        collection = self.state["collections"][principal.collection_key]
        root = ET.Element(_tag(DAV, "multistatus"))
        for name in names[:MAX_REPORT_RESOURCES]:
            resource = resources.get(name)
            if resource is None:
                response = ET.SubElement(root, _tag(DAV, "response"))
                ET.SubElement(response, _tag(DAV, "href")).text = paths[
                    "calendar"
                ] + quote(name)
                ET.SubElement(response, _tag(DAV, "status")).text = _status_line(404)
                continue
            self._add_prop_response(
                root,
                paths["calendar"] + resource.href_name,
                "resource",
                requested,
                principal,
                collection,
                resource,
            )
        response = _xml_response(root)
        response.headers.update(self._dav_headers())
        return response

    @staticmethod
    def _report_properties(root: ET.Element) -> list[str]:
        prop = root.find(_tag(DAV, "prop"))
        if prop is None:
            return [_tag(DAV, "getetag"), _tag(CALDAV, "calendar-data")]
        return [item.tag for item in prop]

    def _filter_resources(
        self,
        resources: dict[str, CalendarResource],
        root: ET.Element,
    ) -> dict[str, CalendarResource]:
        time_range = root.find(f".//{_tag(CALDAV, 'time-range')}")
        if time_range is None:
            return resources
        start = _parse_ical_datetime(time_range.get("start", ""), UTC)
        end = _parse_ical_datetime(time_range.get("end", ""), UTC)
        if not start and not end:
            return resources
        engine = self._engine()
        selected: dict[str, CalendarResource] = {}
        for name, resource in resources.items():
            occurrence = engine.state["occurrences"].get(resource.occurrence_id, {})
            moment = _as_datetime(
                occurrence.get("resolved_at")
                or occurrence.get("due")
                or occurrence.get("created_at")
            )
            if start and moment < start:
                continue
            if end and moment >= end:
                continue
            selected[name] = resource
        return selected

    def _sync_report(
        self,
        principal: Principal,
        resources: dict[str, CalendarResource],
        collection: dict[str, Any],
        body: ET.Element,
    ) -> web.Response:
        token_element = body.find(_tag(DAV, "sync-token"))
        token = (token_element.text or "").strip() if token_element is not None else ""
        current_revision = int(collection.get("revision", 0))
        since = self._parse_sync_token(principal, token) if token else -1
        if since > current_revision:
            raise CalDAVError(409, "Sync token is from the future", "valid-sync-token")
        changes = collection.get("changes", [])
        if changes and since >= 0 and since < int(changes[0]["revision"]) - 1:
            raise CalDAVError(409, "Sync token is too old", "valid-sync-token")
        paths = self._paths(principal)
        root = ET.Element(_tag(DAV, "multistatus"))
        selected = self._sync_changes_since(resources, changes, since)
        requested = self._report_properties(body)
        for change in selected[:MAX_REPORT_RESOURCES]:
            self._add_sync_change(
                root, paths, resources, change, requested, principal, collection
            )
        ET.SubElement(root, _tag(DAV, "sync-token")).text = self._sync_token(
            principal, current_revision
        )
        response = _xml_response(root)
        response.headers.update(self._dav_headers())
        return response

    @staticmethod
    def _sync_changes_since(
        resources: dict[str, CalendarResource],
        changes: list[dict[str, Any]],
        since: int,
    ) -> list[dict[str, Any]]:
        if since < 0:
            return [{"name": name, "deleted": False} for name in sorted(resources)]
        latest = {
            change["name"]: change
            for change in changes
            if int(change["revision"]) > since
        }
        return list(latest.values())

    def _add_sync_change(
        self,
        root: ET.Element,
        paths: dict[str, str],
        resources: dict[str, CalendarResource],
        change: dict[str, Any],
        requested: list[str],
        principal: Principal,
        collection: dict[str, Any],
    ) -> None:
        name = change["name"]
        resource = resources.get(name)
        if change.get("deleted") or resource is None:
            response = ET.SubElement(root, _tag(DAV, "response"))
            ET.SubElement(response, _tag(DAV, "href")).text = paths["calendar"] + quote(
                name
            )
            ET.SubElement(response, _tag(DAV, "status")).text = _status_line(404)
            return
        self._add_prop_response(
            root,
            paths["calendar"] + resource.href_name,
            "resource",
            requested,
            principal,
            collection,
            resource,
        )

    def _get(
        self,
        request: web.Request,
        principal: Principal,
        path: tuple[str, str | None],
    ) -> web.Response:
        if path[0] != "resource":
            raise CalDAVError(405, "GET is only available for calendar objects")
        resources = self._resources(principal)
        resource = resources.get(path[1] or "")
        if resource is None:
            raise CalDAVError(404, "Calendar object not found")
        if request.headers.get("If-None-Match") == resource.etag:
            return web.Response(status=304, headers={"ETag": resource.etag})
        return web.Response(
            body=b"" if request.method == "HEAD" else resource.data,
            status=200,
            content_type="text/calendar",
            charset="utf-8",
            headers={
                **self._dav_headers(),
                "ETag": resource.etag,
                "Last-Modified": format_datetime(
                    resource.updated_at.astimezone(UTC), usegmt=True
                ),
            },
        )

    async def _put(
        self,
        request: web.Request,
        principal: Principal,
        path: tuple[str, str | None],
    ) -> web.Response:
        if path[0] != "resource":
            raise CalDAVError(
                409, "VTODO resources must be stored in the task calendar"
            )
        self._require_write(principal, "allow_client_update")
        name = path[1] or ""
        if not name.lower().endswith(".ics") or "/" in name or "\\" in name:
            raise CalDAVError(400, "Calendar resource name must end in .ics")
        resources = self._resources(principal)
        existing = resources.get(name)
        self._check_preconditions(request, existing)
        timezone = dt_util.get_time_zone(self.hass.config.time_zone) or UTC
        parsed = parse_vtodo(await self._read_body(request), timezone)
        engine = self._engine()
        if existing:
            await self._update_caldav_resource(
                engine, principal, existing, parsed, timezone
            )
            status = 204
        else:
            await self._create_caldav_resource(
                engine, principal, resources, parsed, timezone, name
            )
            status = 201
        refreshed = self._resources(principal)
        collection = await self._sync_collection(principal, refreshed)
        result = refreshed.get(name)
        headers = {
            **self._dav_headers(),
            "Location": self._paths(principal)["calendar"] + quote(name),
        }
        if result:
            headers["ETag"] = result.etag
        headers["X-Household-Sync-Token"] = self._sync_token(
            principal, int(collection.get("revision", 0))
        )
        return web.Response(status=status, headers=headers)

    async def _update_caldav_resource(
        self,
        engine: Any,
        principal: Principal,
        existing: CalendarResource,
        parsed: ParsedVTodo,
        timezone: Any,
    ) -> None:
        if parse_vtodo(existing.data, timezone).uid != parsed.uid:
            raise CalDAVError(
                403,
                "UID does not match the existing calendar object",
                "no-uid-conflict",
            )
        occurrence = engine.state["occurrences"][existing.occurrence_id]
        await engine.async_apply_caldav_vtodo(
            existing.occurrence_id,
            parsed,
            person_id=principal.person_id,
            expected_revision=int(occurrence.get("revision", 1)),
            checklist_id=existing.checklist_id,
            complete_checklist=principal.complete_checklist_on_parent,
        )

    async def _create_caldav_resource(
        self,
        engine: Any,
        principal: Principal,
        resources: dict[str, CalendarResource],
        parsed: ParsedVTodo,
        timezone: Any,
        name: str,
    ) -> None:
        self._require_write(principal, "allow_client_create")
        if principal.person_id not in engine.people:
            raise CalDAVError(403, "A personal credential is required to create tasks")
        if self._uid_exists(resources, parsed.uid, timezone):
            raise CalDAVError(
                403, "UID already exists in this calendar", "no-uid-conflict"
            )
        occurrence_id = await engine.async_create_ad_hoc(
            parsed.summary,
            principal.person_id,
            (parsed.due or dt_util.now()).isoformat(),
            description=parsed.description,
            priority=_priority_from_ical(parsed.priority),
            points=0,
        )
        occurrence = engine.state["occurrences"][occurrence_id]
        occurrence["caldav_no_due"] = parsed.due is None
        occurrence["caldav"] = self._caldav_metadata(parsed)
        await engine.async_apply_caldav_vtodo(
            occurrence_id,
            parsed,
            person_id=principal.person_id,
            expected_revision=int(occurrence.get("revision", 1)),
            checklist_id=None,
            complete_checklist=principal.complete_checklist_on_parent,
        )
        await self._save_resource_alias(principal, name, occurrence_id)

    @staticmethod
    def _uid_exists(
        resources: dict[str, CalendarResource], uid: str, timezone: Any
    ) -> bool:
        return any(
            resource.checklist_id is None
            and parse_vtodo(resource.data, timezone).uid == uid
            for resource in resources.values()
        )

    @staticmethod
    def _caldav_metadata(parsed: ParsedVTodo) -> dict[str, Any]:
        return {
            "uid": parsed.uid,
            "categories": parsed.categories,
            "url": parsed.url,
            "alarms": parsed.alarms,
            "percent": parsed.percent_complete,
            "x_properties": parsed.raw_x_properties,
            "preserved_lines": parsed.preserved_lines,
        }

    async def _save_resource_alias(
        self, principal: Principal, name: str, occurrence_id: str
    ) -> None:
        async with self.lock:
            collection = self.state["collections"].setdefault(
                principal.collection_key,
                {"revision": 0, "resources": {}, "changes": [], "aliases": {}},
            )
            collection.setdefault("aliases", {})[name] = occurrence_id
            await self.store.async_save(self.state)

    @staticmethod
    def _check_preconditions(
        request: web.Request, existing: CalendarResource | None
    ) -> None:
        if_match = request.headers.get("If-Match")
        if_none_match = request.headers.get("If-None-Match")
        if if_match and (existing is None or if_match not in {"*", existing.etag}):
            raise CalDAVError(412, "ETag precondition failed")
        if if_none_match == "*" and existing is not None:
            raise CalDAVError(412, "Resource already exists")

    def _require_write(self, principal: Principal, setting: str) -> None:
        if not principal.can_write:
            raise CalDAVError(403, "This CalDAV credential is read-only")
        if not self.state["settings"].get(setting, False):
            raise CalDAVError(403, "This CalDAV write operation is disabled")

    async def _delete(
        self,
        request: web.Request,
        principal: Principal,
        path: tuple[str, str | None],
    ) -> web.Response:
        if path[0] != "resource":
            raise CalDAVError(405, "Only task resources can be deleted")
        self._require_write(principal, "allow_client_delete")
        resources = self._resources(principal)
        resource = resources.get(path[1] or "")
        if resource is None:
            raise CalDAVError(404, "Calendar object not found")
        if resource.checklist_id:
            raise CalDAVError(403, "Checklist items cannot be deleted independently")
        self._check_preconditions(request, resource)
        engine = self._engine()
        occurrence = engine.state["occurrences"][resource.occurrence_id]
        if occurrence.get("status") not in {"completed", "cancelled"}:
            await engine.async_set_occurrence_status(
                resource.occurrence_id,
                "cancelled",
                expected_revision=int(occurrence.get("revision", 1)),
            )
            occurrence = engine.state["occurrences"][resource.occurrence_id]
        await engine.async_mark_caldav_deleted(
            resource.occurrence_id,
            expected_revision=int(occurrence.get("revision", 1)),
        )
        refreshed = self._resources(principal)
        await self._sync_collection(principal, refreshed)
        return web.Response(status=204, headers=self._dav_headers())

    async def _proppatch(
        self,
        request: web.Request,
        principal: Principal,
        path: tuple[str, str | None],
    ) -> web.Response:
        if path[0] != "calendar":
            raise CalDAVError(403, "Only calendar presentation properties are writable")
        if not principal.can_write:
            raise CalDAVError(403, "This CalDAV credential is read-only")
        root = _parse_xml(await self._read_body(request))
        if root is None:
            raise CalDAVError(400, "PROPPATCH body is required")
        record = self.state["credentials"][principal.credential_id]
        changed: dict[str, str] = {}
        for prop in root.findall(f".//{_tag(DAV, 'prop')}/*"):
            if prop.tag == _tag(DAV, "displayname") and prop.text:
                value = prop.text.strip()[:100]
                record["calendar_name"] = value
                changed[prop.tag] = value
            elif prop.tag == _tag(APPLE, "calendar-color") and prop.text:
                color = prop.text.strip()
                if re.fullmatch(r"#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?", color):
                    record["calendar_color"] = color
                    changed[prop.tag] = color
        await self.store.async_save(self.state)
        response = ET.Element(_tag(DAV, "multistatus"))
        item = ET.SubElement(response, _tag(DAV, "response"))
        ET.SubElement(item, _tag(DAV, "href")).text = self._paths(principal)["calendar"]
        self._add_propstat(item, changed, 200)
        return _xml_response(response)


def get_caldav_service(hass: HomeAssistant) -> CalDAVService | None:
    """Return the initialized CalDAV service."""
    domain_data = hass.data.get(DOMAIN)
    return domain_data.get("caldav") if isinstance(domain_data, dict) else None


async def async_setup_caldav(hass: HomeAssistant) -> CalDAVService:
    """Initialize storage and register one catch-all CalDAV route."""
    service = CalDAVService(hass)
    await service.async_setup()
    domain_data = hass.data.setdefault(DOMAIN, {})
    domain_data["caldav"] = service
    router = hass.http.app.router
    router.add_route("*", "/.well-known/caldav", service.handle)
    router.add_route("*", CALDAV_BASE, service.handle)
    router.add_route("*", CALDAV_BASE + "/{tail:.*}", service.handle)
    return service
