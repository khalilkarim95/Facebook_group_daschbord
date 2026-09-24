"""Erkennung und Kanonisierung oeffentlicher Facebook-Gruppen-URLs.

Verarbeitet ausschliesslich die Gruppen-URL selbst. Es werden keine Inhalte
von Facebook abgerufen - diese Modul arbeitet rein auf dem uebergebenen String.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
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


def canonical_post_url(raw_url: str, group_id: str) -> str | None:
    """Extrahiert die Post-ID aus verschiedenen Facebook-URL-Formaten 
    und gibt eine kanonische URL zurück."""
    from urllib.parse import parse_qs, urlparse
    
    url = (raw_url or "").strip()
    if not url:
        return None
        
    if url.startswith("/"):
        url = "https://www.facebook.com" + url
        
    try:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        
        post_id = None
        if "multi_permalinks" in qs and qs["multi_permalinks"]:
            post_id = qs["multi_permalinks"][0].split(",")[0]
        elif "story_fbid" in qs and qs["story_fbid"]:
            post_id = qs["story_fbid"][0]
        else:
            # Auch nicht-numerische Kennungen: Facebook vergibt seit 2022
            # "pfbid..."-Kennungen. Die alte Fassung verlangte Ziffern und
            # liess solche Verweise als Rohadresse stehen - samt der
            # Parameter __cft__ und __tn__, die sich bei jedem Laden
            # aendern. Derselbe Beitrag sah damit bei jedem Durchgang neu
            # aus, und ein bereits kommentierter galt als unkommentiert.
            match = re.search(r"/(?:posts|permalink)/([0-9A-Za-z]+)", parsed.path)
            if match:
                post_id = match.group(1)

        if post_id:
            return f"https://www.facebook.com/groups/{group_id}/posts/{post_id}/"

        # Keine Beitragskennung gefunden: wenigstens die wechselnden
        # Parameter abschneiden, damit zweimal dieselbe Seite auch zweimal
        # dieselbe Zeichenkette ergibt.
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    except Exception:
        pass
        
    return raw_url


#: Woran ein Verweis als Beitrag zu erkennen ist. Facebook schreibt dieselbe
#: Stelle je nach Ansicht verschieden - aus der Gruppenansicht heraus als
#: ``multi_permalinks``, aus der Einzelansicht als ``/posts/``.
BEITRAGSMUSTER = ("/posts/", "/permalink/", "multi_permalinks", "story_fbid", "/share/p/")

#: Die Beitragskennung in einem Bildverweis: ``set=pcb.<id>`` (Beitrag mit
#: mehreren Bildern) oder ``set=gm.<id>`` (Gruppenmedien).
_BILD_BEITRAG = re.compile(r"[?&]set=(?:pcb|gm)\.(\d+)")


def beitragslinks(hrefs: Iterable[str | None], group_id: str) -> list[str]:
    """Aus den Verweisen einer Seite die Beitragsadressen - kanonisch, ohne Dubletten.

    Rein und ohne Browser, damit sie pruefbar ist: Der Teil, der am haeufigsten
    danebengreift, ist die Erkennung des Verweises - und den kann man nur
    pruefen, wenn er nicht in Playwright-Code eingewachsen ist.

    Die Reihenfolge der Seite bleibt erhalten: Facebook stellt oben hin, was
    es fuer das Wichtigste haelt, und diese Reihenfolge ist eine Auskunft.
    """
    heraus: list[str] = []
    gesehen: set[str] = set()
    for href in hrefs:
        # **Der Bildverweis traegt die Beitragskennung** (24.09.2026). Ein
        # Beitrag mit Bild verweist auf ``/photo/?fbid=...&set=pcb.<id>``
        # (bzw. ``set=gm.<id>``) - ohne ``/groups/`` im Pfad. Die Zeitangabe,
        # die sonst die Adresse traegt, zeigt oft nur ``#``; ohne diesen Weg
        # blieben solche Artikel ohne Adresse ("articles last seen: 5",
        # "Found 1 post(s)").
        if href and (treffer := _BILD_BEITRAG.search(href)):
            url = f"https://www.facebook.com/groups/{group_id}/posts/{treffer.group(1)}/"
            if url not in gesehen:
                gesehen.add(url)
                heraus.append(url)
            continue
        if not href or "/groups/" not in href:
            continue
        if not any(muster in href for muster in BEITRAGSMUSTER):
            continue
        url = canonical_post_url(href, group_id) or href
        if url in gesehen:
            continue
        gesehen.add(url)
        heraus.append(url)
    return heraus


#: Eine ausgeschriebene Adresse: mit Schema, mit ``www.`` oder als Pfad auf
#: ``/r/`` bzw. ``/t/`` - die beiden Formen unserer Tracking-Adressen
#: (``go.b-tarikak.de/r/wr4s9xw``, ``b-tarikak.de/t/safar-sham-12``), auch ohne
#: Schema geschrieben.
_ADRESSE_IM_TEXT = re.compile(
    r"(?:https?://|\bwww\.)\S+|\b[\w.-]+\.[a-z]{2,}(?::\d+)?/(?:r|t)/[\w-]+",
    re.IGNORECASE,
)

#: Ein innerer Tracking-Code (``FB-SYR-BER-010``) - dasselbe Muster wie in
#: ``vorlagen.pruefe_platzhalter``.
_CODE_IM_TEXT = re.compile(r"\b[A-Z]{2,4}(?:-[A-Z0-9]{2,4}){1,3}-\d{2,4}\b")


#: Was eine Adresse zur **Tracking**-Adresse macht: ein ``/r/``- oder
#: ``/t/``-Pfad (``go.b-tarikak.de/r/wr4s9xw``, ``b-tarikak.de/t/safar-sham-12``)
#: oder ein Tracking-Parameter (``?ref=``, ``referrer=``).
_TRACKING_IM_TEXT = re.compile(
    r"\b[\w.-]+\.[a-z]{2,}(?::\d+)?/(?:r|t)/[\w-]+|[?&](?:ref|referrer)=\S+",
    re.IGNORECASE,
)

#: Satzzeichen, die an einer Adresse im Fliesstext haengen koennen.
_ANGEHAENGT = ".,;:!?؟)\"'»”"


def tracking_adresse_im_text(text: str) -> str:
    """Die erste **Tracking**-Adresse oder der erste Code im Text - sonst ``""``.

    **Kein Kommentar traegt einen Tracking-Link** (23.09.2026, Anweisung des
    Nutzers). Eine schlichte Adresse wie ``https://b-tarikak.de/home`` ist
    dagegen erlaubt (``marketing.kommentar_adresse``) - sie zaehlt nichts und
    nennt keinen Code. Geprueft wird unmittelbar vor dem Absenden
    (``actions.comment_on_post``), dort, wo jeder Kommentar durchkommt.
    """
    treffer = _TRACKING_IM_TEXT.search(text) or _CODE_IM_TEXT.search(text)
    return treffer.group(0) if treffer else ""


def adresse_im_text(text: str, erlaubt: Iterable[str] = ()) -> str:
    """Die erste Adresse oder der erste Code im Text, die **nicht** erlaubt ist.

    Strenger als ``tracking_adresse_im_text``: Jede ausgeschriebene Adresse
    zaehlt, ausser genau den erlaubten (``marketing.kommentar_adresse``) - und
    eine Tracking-Adresse ist nie erlaubt, auch wenn sie dort stuende. So
    kommt im Lauf in einen Kommentar genau die eine freie Adresse und nichts
    sonst (``automatik.entscheide_und_kommentiere``).
    """
    frei = {a.strip() for a in erlaubt if a.strip() and not tracking_adresse_im_text(a)}
    if treffer := _CODE_IM_TEXT.search(text):
        return treffer.group(0)
    for treffer in _ADRESSE_IM_TEXT.finditer(text):
        adresse = treffer.group(0).rstrip(_ANGEHAENGT)
        if adresse not in frei or tracking_adresse_im_text(adresse):
            return adresse
    return ""
