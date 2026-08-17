"""Textnormalisierung fuer den mehrsprachigen Abgleich (de / ar / translit).

Der Abgleich unterscheidet bewusst zwei Schriftsysteme:

* Lateinisch: Vergleich mit Wortgrenzen. "Arab" soll "Araber" treffen,
  aber nicht mitten in einem unverwandten Wort zufaellig anschlagen.
* Arabisch: Vergleich als Teilstring. Artikel und Praepositionen haengen
  direkt am Wort, "سوريين" steckt in "السوريين" - eine Wortgrenze gaebe es dort nicht.
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
