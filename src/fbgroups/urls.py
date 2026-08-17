"""Erkennung und Kanonisierung oeffentlicher Facebook-Gruppen-URLs.

Verarbeitet ausschliesslich die Gruppen-URL selbst. Es werden keine Inhalte
von Facebook abgerufen - diese Modul arbeitet rein auf dem uebergebenen String.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

CANONICAL_PREFIX = "https://www.facebook.com/groups/"

_FACEBOOK_HOSTS = {
    "facebook.com",
    "www.facebook.com",
    "m.facebook.com",
    "web.facebook.com",
    "mbasic.facebook.com",
    "de-de.facebook.com",
    "ar-ar.facebook.com",
    "fb.com",
    "www.fb.com",
}

# Pfadsegmente hinter /groups/, die keine Gruppenkennung sind.
_RESERVED_SEGMENTS = {
    "feed", "search", "create", "discover", "joins", "my", "category",
    "browse", "notifications", "member_requests",
}

# Gruppenkennung: numerische ID oder Slug aus Buchstaben/Ziffern/.-_
_IDENTIFIER_RE = re.compile(r"^[\w.\-]+$", re.UNICODE)


class UrlRejectReason:
    EMPTY = "empty"
    NOT_A_URL = "not_a_url"
    WRONG_HOST = "not_a_facebook_host"
    NOT_A_GROUP = "not_a_group_url"
    RESERVED = "reserved_path_segment"
    BAD_IDENTIFIER = "invalid_group_identifier"


@dataclass(frozen=True)
class ParsedGroupUrl:
    group_id: str
    canonical_url: str
    original_url: str


@dataclass(frozen=True)
class UrlParseError:
    original_url: str
    reason: str


def _prepare(raw: str) -> str:
    """Trimmt und ergaenzt ein fehlendes Schema, damit urlsplit greift."""
    text = (raw or "").strip().strip("<>\"'")
    if not text:
        return ""
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", text):
        text = "https://" + text.lstrip("/")
    return text


def parse_group_url(raw: str) -> ParsedGroupUrl | UrlParseError:
    """Erkennt eine Facebook-Gruppen-URL und liefert ihre kanonische Form.

    Akzeptiert u. a. mobile Hosts, Sprach-Subdomains, fehlendes Schema,
    Tracking-Parameter sowie Unterpfade wie ``/about`` oder ``/permalink/...``.
    """
    original = (raw or "").strip()
    if not original:
        return UrlParseError(original, UrlRejectReason.EMPTY)

    prepared = _prepare(original)
    try:
        parts = urlsplit(prepared)
    except ValueError:
        return UrlParseError(original, UrlRejectReason.NOT_A_URL)

    host = (parts.hostname or "").lower()
    if host not in _FACEBOOK_HOSTS:
        return UrlParseError(original, UrlRejectReason.WRONG_HOST)

    segments = [unquote(s) for s in parts.path.split("/") if s]
    if len(segments) < 2 or segments[0].lower() != "groups":
        return UrlParseError(original, UrlRejectReason.NOT_A_GROUP)

    identifier = segments[1]
    if identifier.lower() in _RESERVED_SEGMENTS:
        return UrlParseError(original, UrlRejectReason.RESERVED)
    if not _IDENTIFIER_RE.match(identifier):
        return UrlParseError(original, UrlRejectReason.BAD_IDENTIFIER)

    # Numerische IDs bleiben unveraendert, Slugs werden kleingeschrieben,
    # da Facebook sie case-insensitiv aufloest.
    group_id = identifier if identifier.isdigit() else identifier.lower()

    return ParsedGroupUrl(
        group_id=group_id,
        canonical_url=f"{CANONICAL_PREFIX}{group_id}",
        original_url=original,
    )


def is_group_url(raw: str) -> bool:
    """Bequemer Wahrheitstest ohne Fehlerdetails."""
    return isinstance(parse_group_url(raw), ParsedGroupUrl)
