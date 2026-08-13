"""Secure, versioned community template bundle handling."""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import re
import socket
from copy import deepcopy
from typing import Any
from urllib.parse import urlparse

import voluptuous as vol

PACK_KIND = "household_tasks_template_pack"
PACK_SCHEMA_VERSION = 1
MAX_PACK_BYTES = 256 * 1024
_VERSION = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:[-+][0-9A-Za-z.-]+)?$"
)


def validate_pack(payload: Any, trusted_publishers: dict[str, str]) -> dict[str, Any]:
    """Validate and authenticate one detached Ed25519 template pack."""
    if not isinstance(payload, dict):
        raise vol.Invalid("Das Vorlagenpaket muss ein JSON-Objekt sein.")
    if (
        payload.get("kind") != PACK_KIND
        or payload.get("schema_version") != PACK_SCHEMA_VERSION
    ):
        raise vol.Invalid("Unbekanntes Community-Vorlagenformat.")
    pack_id = _slug(payload.get("id"), "Paket-ID")
    version = str(payload.get("version", ""))
    if not _VERSION.fullmatch(version):
        raise vol.Invalid("Die Paketversion muss Semantic Versioning verwenden.")
    publisher = payload.get("publisher")
    signature = payload.get("signature")
    if not isinstance(publisher, dict) or not isinstance(signature, dict):
        raise vol.Invalid("Publisher und Signatur fehlen.")
    if signature.get("algorithm") != "ed25519":
        raise vol.Invalid("Nur Ed25519-Signaturen werden akzeptiert.")
    public_key = _decode(
        str(publisher.get("public_key", "")), 32, "Publisher-Schlüssel"
    )
    signed = deepcopy(payload)
    signed.pop("signature", None)
    canonical = json.dumps(
        signed, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    signature_value = _decode(str(signature.get("value", "")), 64, "Signatur")
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(public_key).verify(
            signature_value, canonical
        )
    except ImportError as err:
        raise vol.Invalid(
            "Ed25519-Prüfung ist in dieser HA-Installation nicht verfügbar."
        ) from err
    except InvalidSignature as err:
        raise vol.Invalid("Die Community-Vorlage hat eine ungültige Signatur.") from err
    fingerprint = hashlib.sha256(public_key).hexdigest()
    publisher_id = _slug(publisher.get("id"), "Publisher-ID")
    trusted = trusted_publishers.get(publisher_id)
    if trusted and trusted != fingerprint:
        raise vol.Invalid("Der Publisher-Schlüssel hat sich unerwartet geändert.")
    templates = payload.get("templates")
    if not isinstance(templates, list) or not templates or len(templates) > 50:
        raise vol.Invalid("Das Paket muss 1 bis 50 Vorlagen enthalten.")
    normalized = [_validate_template(item) for item in templates]
    digest = hashlib.sha256(canonical).hexdigest()
    return {
        "pack_id": pack_id,
        "name": _text(payload.get("name"), "Paketname", 120),
        "version": version,
        "publisher": {
            "id": publisher_id,
            "name": _text(publisher.get("name"), "Publisher-Name", 120),
            "fingerprint": fingerprint,
            "trusted": trusted == fingerprint,
        },
        "digest": digest,
        "templates": normalized,
    }


def validate_source_url(url: str) -> tuple[str, str]:
    """Validate an HTTPS source before DNS resolution and download."""
    parsed = urlparse(str(url).strip())
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise vol.Invalid(
            "Community-Vorlagen benötigen eine HTTPS-URL ohne Zugangsdaten."
        )
    if parsed.port not in {None, 443}:
        raise vol.Invalid("Community-Vorlagen dürfen nur HTTPS-Port 443 verwenden.")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        pass
    else:
        _reject_address(address)
    return parsed.geturl(), parsed.hostname


async def validate_resolved_host(hostname: str) -> None:
    """Reject private, local, multicast, and otherwise special destinations."""
    loop = __import__("asyncio").get_running_loop()
    try:
        records = await loop.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
    except OSError as err:
        raise vol.Invalid("Der Vorlagenserver konnte nicht aufgelöst werden.") from err
    if not records:
        raise vol.Invalid("Der Vorlagenserver hat keine Adresse geliefert.")
    for record in records:
        _reject_address(ipaddress.ip_address(record[4][0]))


def substitute_entities(
    task: dict[str, Any], mappings: dict[str, str]
) -> dict[str, Any]:
    """Replace declared entity placeholders recursively."""

    def replace(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: replace(item) for key, item in value.items()}
        if isinstance(value, list):
            return [replace(item) for item in value]
        if isinstance(value, str):
            match = re.fullmatch(r"\{\{([a-z0-9_]+)\}\}", value)
            if match:
                key = match.group(1)
                if key not in mappings:
                    raise vol.Invalid(f"Die erforderliche Entität '{key}' fehlt.")
                return mappings[key]
        return value

    return replace(deepcopy(task))


def is_newer(candidate: str, installed: str) -> bool:
    """Compare the numeric core of semantic versions."""

    def core(value: str) -> tuple[int, int, int]:
        match = _VERSION.fullmatch(value)
        return tuple(int(item) for item in match.groups()[:3]) if match else (0, 0, 0)

    return core(candidate) > core(installed)


def _validate_template(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict) or not isinstance(item.get("task"), dict):
        raise vol.Invalid("Jeder Vorlageneintrag benötigt ein task-Objekt.")
    template_id = _slug(item.get("id"), "Vorlagen-ID")
    requirements = item.get("required_entities", [])
    if not isinstance(requirements, list) or len(requirements) > 30:
        raise vol.Invalid(
            "required_entities muss eine Liste mit höchstens 30 Einträgen sein."
        )
    normalized_requirements = []
    for requirement in requirements:
        if not isinstance(requirement, dict):
            raise vol.Invalid("Eine Entitätsanforderung ist ungültig.")
        domains = requirement.get("domains", [])
        if not isinstance(domains, list) or not all(
            isinstance(value, str) for value in domains
        ):
            raise vol.Invalid("Entitäts-Domains müssen eine Liste sein.")
        normalized_requirements.append(
            {
                "key": _slug(requirement.get("key"), "Entitäts-Schlüssel"),
                "name": _text(requirement.get("name"), "Entitätsname", 120),
                "description": str(requirement.get("description", ""))[:500],
                "domains": domains,
            }
        )
    encoded = json.dumps(item["task"], ensure_ascii=False).encode()
    if len(encoded) > 64 * 1024:
        raise vol.Invalid("Eine einzelne Vorlage ist zu groß.")
    return {
        "id": template_id,
        "name": _text(item.get("name"), "Vorlagenname", 120),
        "description": str(item.get("description", ""))[:1000],
        "category": str(item.get("category", "Community"))[:80],
        "required_entities": normalized_requirements,
        "task": deepcopy(item["task"]),
    }


def _decode(value: str, size: int, label: str) -> bytes:
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as err:
        raise vol.Invalid(f"{label} ist nicht gültig base64-kodiert.") from err
    if len(decoded) != size:
        raise vol.Invalid(f"{label} hat eine ungültige Länge.")
    return decoded


def _slug(value: Any, label: str) -> str:
    result = str(value or "")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{1,79}", result):
        raise vol.Invalid(f"{label} ist ungültig.")
    return result


def _text(value: Any, label: str, maximum: int) -> str:
    result = " ".join(str(value or "").split())
    if not result or len(result) > maximum:
        raise vol.Invalid(f"{label} ist ungültig.")
    return result


def _reject_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    if not address.is_global:
        raise vol.Invalid(
            "Lokale oder private Vorlagenserver sind aus Sicherheitsgründen gesperrt."
        )
