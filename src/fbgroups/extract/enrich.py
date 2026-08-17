"""Auswertung eines Suchtreffers zu einer Gruppe.

Verarbeitet wird ausschliesslich, was der Suchdienst ohnehin ausliefert:
Titel, Beschreibungsausschnitt und URL. Es wird nichts nachgeladen und nichts
ergaenzt - fehlt eine Angabe im Treffer, bleibt sie leer.

Mitgliederzahl und Sichtbarkeit werden nur uebernommen, wenn sie im Text
ausdruecklich benannt sind ("1.234 Mitglieder", "Oeffentliche Gruppe").
Eine blosse Zahl im Titel ist kein Beleg und wird ignoriert.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import replace
from urllib.parse import unquote, urlsplit

from fbgroups.importers.manual_seed import parse_member_count
from fbgroups.models import Group, PrivacyHint, Provenance, SourceType
from fbgroups.providers.base import SearchHit
from fbgroups.urls import ParsedGroupUrl, parse_group_url
from fbgroups.validation import validate_group

# Suchmaschinen haengen den Seitennamen an den Titel an.
_TITLE_SUFFIXES = re.compile(
    r"\s*[|\-–—]\s*(facebook|fb)\s*$|\s*\|\s*facebook\s*$",
    re.IGNORECASE,
)
_FACEBOOK_PREFIX = re.compile(r"^\s*facebook\s*[|\-–—:]\s*", re.IGNORECASE)

# "1.234 Mitglieder", "12K members", "‏٣٠٠٠ عضو"
_MEMBER_PATTERNS = [
    re.compile(r"([\d.,\s]+\s*(?:k|tsd|mio|m)?)\s*(?:mitglieder|members|عضو|أعضاء)", re.IGNORECASE),
    re.compile(r"(?:mitglieder|members)\s*:?\s*([\d.,\s]+\s*(?:k|tsd|mio|m)?)", re.IGNORECASE),
]

_PUBLIC_MARKERS = ("öffentliche gruppe", "oeffentliche gruppe", "public group", "مجموعة عامة")
_PRIVATE_MARKERS = ("private gruppe", "private group", "geschlossene gruppe", "مجموعة خاصة")

# Pfadsegmente hinter der Gruppenkennung, die auf einen einzelnen Beitrag
# innerhalb der Gruppe zeigen - nicht auf die Gruppe selbst.
_POST_SEGMENTS = {
    "posts", "permalink", "permalink.php", "photo", "photos", "videos", "video",
    "media", "topic", "comment",
}


def points_to_post(url: str) -> bool:
    """Zeigt die URL auf einen Beitrag innerhalb der Gruppe statt auf die Gruppe?

    Suchmaschinen liefern regelmaessig Beitrags-Links wie
    ``/groups/123/posts/456``. Titel und Beschreibungstext gehoeren dann zum
    **Beitrag**, nicht zur Gruppe.
    """
    try:
        parts = urlsplit(url if "://" in url else f"https://{url.lstrip('/')}")
    except ValueError:
        return False
    segments = [unquote(s).lower() for s in parts.path.split("/") if s]
    if len(segments) < 3 or segments[0] != "groups":
        return False
    return segments[2] in _POST_SEGMENTS


def drop_shared_snippets(hits: list[SearchHit]) -> list[SearchHit]:
    """Verwirft Beschreibungstexte, die sich **verschiedene Gruppen** teilen.

    Google liefert fuer Facebook-Gruppen regelmaessig denselben Text zu
    mehreren Ergebnissen - er stammt dann von einer einzigen Gruppe und
    beschreibt die uebrigen nachweislich nicht. Beobachtet im ersten Livelauf:
    eine Anfrage lieferte zehn Gruppen, fuenf davon mit dem Beschreibungstext
    einer sechsten; derselbe Fremdtext tauchte zudem in anderen Anfragen auf.

    Bliebe er stehen, wanderten Mitgliederzahl, Sichtbarkeit und Stadt einer
    Gruppe in die Datensaetze fremder Gruppen - genau die Art erfundener
    Metadaten, die dieses Projekt ausschliesst. Ein geteilter Text ist fuer
    alle Beteiligten unbrauchbar, auch fuer seinen rechtmaessigen Eigentuemer:
    welcher das ist, geht aus der Antwort nicht hervor.

    Entscheidend ist die Zahl **verschiedener URLs**, nicht die Zahl der
    Treffer: Dieselbe Gruppe wird von mehreren Anfragen gefunden und bringt
    dann voellig zu Recht jedes Mal denselben, eigenen Text mit. Nur wenn ein
    Text an zwei verschiedenen Gruppen haengt, ist er als Beleg wertlos.

    Deshalb sollte die Funktion ueber den **gesamten Lauf** laufen, nicht ueber
    eine einzelne Antwort - der Fremdtext verteilt sich ueber Anfragen hinweg.
    Die Rohantwort im Anfragespeicher bleibt unangetastet; verworfen wird erst
    bei der Auswertung.
    """
    urls_je_text: dict[str, set[str]] = defaultdict(set)
    for hit in hits:
        text = (hit.snippet or "").strip()
        if text:
            urls_je_text[text].add(hit.url)

    geteilt = {text for text, urls in urls_je_text.items() if len(urls) > 1}
    if not geteilt:
        return hits

    return [
        replace(hit, snippet=None)
        if (hit.snippet or "").strip() in geteilt
        else hit
        for hit in hits
    ]


POST_NOTE_PREFIX = "Fundstelle war ein Beitrag, kein Gruppenprofil."


def post_note(title: str) -> str:
    """Haelt den Beitragstitel als Hinweis fest - ausdruecklich als solchen."""
    titel = clean_group_name(title)
    if not titel:
        return POST_NOTE_PREFIX
    return f'{POST_NOTE_PREFIX} Titel des Beitrags: "{titel}"'


def clean_group_name(title: str) -> str:
    """Entfernt den von der Suchmaschine angehaengten Seitennamen."""
    if not title:
        return ""
    name = _FACEBOOK_PREFIX.sub("", title)
    name = _TITLE_SUFFIXES.sub("", name)
    return name.strip(" |-–—\t")


def parse_member_count_from_text(text: str | None) -> int | None:
    """Liest eine Mitgliederzahl nur mit ausdruecklicher Benennung."""
    if not text:
        return None
    for pattern in _MEMBER_PATTERNS:
        match = pattern.search(text)
        if match:
            value = parse_member_count(match.group(1))
            if value:
                return value
    return None


def parse_privacy_hint(text: str | None) -> PrivacyHint:
    if not text:
        return PrivacyHint.UNKNOWN
    lowered = text.lower()
    if any(marker in lowered for marker in _PUBLIC_MARKERS):
        return PrivacyHint.PUBLIC
    if any(marker in lowered for marker in _PRIVATE_MARKERS):
        return PrivacyHint.PRIVATE
    return PrivacyHint.UNKNOWN


def hit_to_group(hit: SearchHit, query_id: str, provider: str) -> Group | None:
    """Wandelt einen Suchtreffer in eine Gruppe - oder None, wenn es keine ist.

    Zeigt der Treffer auf einen einzelnen Beitrag, bleiben Name und
    Beschreibung leer: Der Titel gehoert dann dem Beitrag. Uebernommen wurde er
    frueher trotzdem - so stand "Deutschland geht erst unter seit Mutter Merkel
    den Syrern ..." als Gruppenname im Export, wurde klassifiziert und bewertet.
    Belegt ist bei einem Beitrags-Link allein, dass es die Gruppe gibt; findet
    ein anderer Treffer dieselbe Gruppe ueber ihre eigene URL, ergaenzt
    ``merge_duplicate`` den Namen.

    Verworfen wird der Titel deshalb nicht, sondern als Notiz hinterlegt - als
    Beitragstitel benannt und damit ohne Wirkung auf Klassifikation und Score.
    Manche dieser Titel enthalten den Gruppennamen; welcher Teil das ist, laesst
    sich ohne Zugriff auf facebook.com nicht entscheiden. Das bleibt Handarbeit,
    und dafuer ist der Hinweis da.
    """
    parsed = parse_group_url(hit.url)
    if not isinstance(parsed, ParsedGroupUrl):
        return None

    aus_beitrag = points_to_post(hit.url)
    combined = "" if aus_beitrag else f"{hit.title or ''} {hit.snippet or ''}"

    group = Group(
        group_id=parsed.group_id,
        url_canonical=parsed.canonical_url,
        url_variants=([hit.url] if hit.url != parsed.canonical_url else []),
        name="" if aus_beitrag else clean_group_name(hit.title),
        description_snippet=None if aus_beitrag else ((hit.snippet or "").strip() or None),
        notes=post_note(hit.title) if aus_beitrag else "",
        member_count_hint=parse_member_count_from_text(combined),
        privacy_hint=parse_privacy_hint(combined),
        sources=[
            Provenance(
                source_type=SourceType.SEARCH,
                source_ref=f"{provider}:{query_id}",
            )
        ],
    )
    group.validation_status = validate_group(group)
    return group
