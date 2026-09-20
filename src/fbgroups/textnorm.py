"""Textnormalisierung fuer den mehrsprachigen Abgleich (de / ar / translit).

Der Abgleich unterscheidet bewusst zwei Schriftsysteme:

* Lateinisch: Vergleich mit Wortgrenzen. "Arab" soll "Araber" treffen,
  aber nicht mitten in einem unverwandten Wort zufaellig anschlagen.
* Arabisch: Vergleich als Teilstring. Artikel und Praepositionen haengen
  direkt am Wort, "سوريين" steckt in "السوريين" - eine Wortgrenze gaebe es dort nicht.

Daneben steht ``parse_member_count``: Mitgliederzahlen kommen in vielen
Schreibweisen ("12.500", "12,5k", "3 Mio") und muessen zu einer Zahl werden.
Die Funktion stand bis zum 20.09.2026 im Seed-Importer und danach kurz in
``automation/actions.py``. Sie steht hier, weil **zwei** Wege dieselbe
Zeichenkette lesen - den Kopf einer Gruppenseite: der Browser liest ihn live,
``mitglieder.py`` liest ihn aus einer CSV-Spalte. Zwei Parser waeren zwei
Wahrheiten ueber dieselbe Zahl, und ein CSV-Leser soll dafuer nicht Playwright
laden muessen.
"""

from __future__ import annotations

import re
import unicodedata

# Diakritika (Fatha, Damma, Kasra, Sukun, Shadda, Tanwin) und Tatweel
_ARABIC_DIACRITICS = re.compile(r"[ً-ْٰـ]")

# Vereinheitlichung visuell/orthografisch schwankender Buchstaben
_ARABIC_UNIFY = {
    "أ": "ا",  # أ -> ا
    "إ": "ا",  # إ -> ا
    "آ": "ا",  # آ -> ا
    "ٱ": "ا",  # ٱ -> ا
    "ى": "ي",  # ى -> ي
    "ة": "ه",  # ة -> ه
    "ؤ": "و",  # ؤ -> و
    "ئ": "ي",  # ئ -> ي
}

_UMLAUTS = {
    "ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
    "Ä": "ae", "Ö": "oe", "Ü": "ue",
}

_ARABIC_RANGE = re.compile(r"[؀-ۿݐ-ݿ]")
_NON_WORD = re.compile(r"[^\w؀-ۿݐ-ݿ]+", re.UNICODE)
_WHITESPACE = re.compile(r"\s+")


def has_arabic(text: str) -> bool:
    """True, wenn der Text mindestens ein arabisches Zeichen enthaelt."""
    return bool(_ARABIC_RANGE.search(text or ""))


def normalize_arabic(text: str) -> str:
    """Entfernt Diakritika und vereinheitlicht Buchstabenvarianten."""
    if not text:
        return ""
    text = _ARABIC_DIACRITICS.sub("", text)
    return "".join(_ARABIC_UNIFY.get(ch, ch) for ch in text)


def normalize_latin(text: str) -> str:
    """Kleinschreibung, Umlaut-Aufloesung, Akzente entfernen."""
    if not text:
        return ""
    for src, dst in _UMLAUTS.items():
        text = text.replace(src, dst)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.lower()


def normalize(text: str) -> str:
    """Gemeinsame Normalform fuer den Abgleich beider Schriftsysteme."""
    if not text:
        return ""
    text = normalize_arabic(text)
    text = normalize_latin(text)
    text = _NON_WORD.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip()


def contains_term(haystack_normalized: str, term: str) -> bool:
    """Prueft, ob ``term`` im bereits normalisierten Text vorkommt.

    Waehlt die Vergleichsstrategie anhand der Schrift des Suchbegriffs.
    """
    term_norm = normalize(term)
    if not term_norm or not haystack_normalized:
        return False

    if has_arabic(term_norm):
        return term_norm in haystack_normalized

    # Lateinisch: Wortgrenze am Anfang, Wortfortsetzung am Ende erlaubt,
    # damit "arab" auch "araber"/"arabisch" trifft.
    pattern = r"(?<!\w)" + re.escape(term_norm)
    return bool(re.search(pattern, haystack_normalized))


# --- Mitgliederzahlen ------------------------------------------------------

# Die Einheit darf kein Wortanfang sein: sonst liest "4.200 Mitglieder"
# das M als Millionen-Marker.
_MEMBER_COUNT_RE = re.compile(r"(\d[\d.,\s\u00a0]*)\s*(k|tsd|mio|m)?(?![a-zA-Z])", re.IGNORECASE)


def parse_member_count(raw: str | None) -> int | None:
    """Wandelt Angaben wie ``12.500``, ``12,5k`` oder ``3 Mio`` in eine Zahl."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None

    match = _MEMBER_COUNT_RE.search(text)
    if not match:
        return None

    number_part = match.group(1).strip()
    suffix = (match.group(2) or "").lower()

    # "12.500" ist deutsch fuer 12500, "12,5" ist ein Dezimalwert.
    # Das geschuetzte Leerzeichen kommt aus Facebooks eigener Anzeige.
    cleaned = number_part.replace(" ", "").replace("\u00a0", "")
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".") if suffix else cleaned.replace(",", "")
    elif "." in cleaned and suffix:
        pass  # "1.5k"
    elif "." in cleaned:
        cleaned = cleaned.replace(".", "")

    try:
        value = float(cleaned)
    except ValueError:
        return None

    multiplier = {"k": 1_000, "tsd": 1_000, "mio": 1_000_000, "m": 1_000_000}.get(suffix, 1)
    result = int(value * multiplier)
    return result if result >= 0 else None
