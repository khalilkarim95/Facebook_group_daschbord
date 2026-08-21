"""Validierung der Seed-URLs und Bewertung der Datenqualitaet.

Leitsatz dieses Moduls: Fehlende Information wird als fehlend ausgewiesen -
niemals geraten, niemals durch einen Ersatzwert aufgefuellt. Ein Datensatz ohne
belastbare Angaben erhaelt deshalb keinen Score, sondern ``None``.

Die Placeholder-Erkennung arbeitet rein strukturell auf der Kennung. Sie fragt
nicht bei Facebook nach, ob eine Gruppe existiert - das waere ein Zugriff auf
facebook.com und ist ausgeschlossen. Eine als ``test_data`` markierte URL ist
also ein begruendeter Verdacht, keine Existenzaussage.
"""

from __future__ import annotations

import re

from fbgroups.models import DataQuality, Group, PrivacyHint, RecordStatus, ValidationStatus

# Tokens, die in einer Kennung auf eine Beispiel- oder Testeingabe hindeuten.
_PLACEHOLDER_TOKENS = {
    "test", "tests", "testing", "testgroup", "example", "examples", "demo",
    "dummy", "sample", "placeholder", "platzhalter", "beispiel", "muster",
    "foo", "bar", "baz", "xxx", "yyy", "zzz", "abc", "xyz", "asdf", "qwerty",
    "todo", "changeme", "yourgroup", "mygroup", "groupname", "gruppenname",
    "lorem", "ipsum", "none", "null", "undefined", "unknown",
}

# Teilzeichenketten, die auch innerhalb eines Wortes eindeutig sind.
_PLACEHOLDER_SUBSTRINGS = ("example", "placeholder", "platzhalter", "dummy", "lorem")

# Wortstaemme: erfasst zusammengesetzte Formen wie "testgruppe" oder "demoseite".
_PLACEHOLDER_PREFIXES = (
    "test", "demo", "dummy", "sample", "beispiel", "muster", "example",
)

_TOKEN_SPLIT = re.compile(r"[._\-]+")

# Echte Gruppenkennungen sind in der Praxis deutlich laenger.
_MIN_NUMERIC_LENGTH = 8
_MIN_SEQUENTIAL_RUN = 8
_MIN_IDENTICAL_RUN = 6


def _longest_identical_run(digits: str) -> int:
    best = run = 1
    for prev, cur in zip(digits, digits[1:], strict=False):
        run = run + 1 if cur == prev else 1
        best = max(best, run)
    return best if digits else 0


def _longest_sequential_run(digits: str) -> int:
    """Laengster auf- oder absteigender Lauf, Uebergang 9->0 zaehlt mit."""
    best = run_up = run_down = 1
    for prev, cur in zip(digits, digits[1:], strict=False):
        step = (int(cur) - int(prev)) % 10
        run_up = run_up + 1 if step == 1 else 1
        run_down = run_down + 1 if step == 9 else 1
        best = max(best, run_up, run_down)
    return best if digits else 0


def _is_short_cycle(digits: str) -> bool:
    """Erkennt Wiederholungsmuster wie 121212... oder 123123123..."""
    for unit_length in (1, 2, 3):
        if len(digits) < unit_length * 4:
            continue
        repeats = len(digits) // unit_length
        unit = digits[:unit_length]
        if unit * repeats == digits[: repeats * unit_length]:
            return True
    return False


def is_placeholder_identifier(group_id: str) -> bool:
    """Strukturelle Pruefung, ob eine Gruppenkennung offensichtlich erfunden ist."""
    if not group_id:
        return True

    identifier = group_id.strip().lower()

    if identifier.isdigit():
        if len(identifier) < _MIN_NUMERIC_LENGTH:
            return True
        if _longest_identical_run(identifier) >= _MIN_IDENTICAL_RUN:
            return True
        if _longest_sequential_run(identifier) >= _MIN_SEQUENTIAL_RUN:
            return True
        return _is_short_cycle(identifier)

    if any(sub in identifier for sub in _PLACEHOLDER_SUBSTRINGS):
        return True

    tokens = {t for t in _TOKEN_SPLIT.split(identifier) if t}
    if tokens & _PLACEHOLDER_TOKENS:
        return True
    return any(token.startswith(_PLACEHOLDER_PREFIXES) for token in tokens)


def validate_group(group: Group) -> ValidationStatus:
    """Bestimmt den Validierungsstatus einer bereits geparsten Gruppe."""
    if not group.group_id or not group.url_canonical:
        return ValidationStatus.INVALID
    if is_placeholder_identifier(group.group_id):
        return ValidationStatus.TEST_DATA
    return ValidationStatus.VALID


def present_fields(group: Group) -> list[str]:
    """Listet die tatsaechlich **erhobenen** Metadatenfelder auf.

    Gezaehlt wird nur, was aus der Quelle stammt. Zielgruppe, Stadt und
    Kategorie bleiben aussen vor: Sie werden aus dem Namen abgeleitet und sind
    keine zusaetzliche Information. Wurden sie mitgezaehlt, meldete ein
    Datensatz mit nichts als Name und Beschreibungstext bereits "complete" -
    im Export stand das direkt neben einer unbekannten Mitgliederzahl.
    """
    present: list[str] = []
    if group.name.strip():
        present.append("name")
    if group.member_count_hint is not None:
        present.append("member_count")
    if group.description_snippet:
        present.append("description")
    if group.privacy_hint is not PrivacyHint.UNKNOWN:
        present.append("privacy")
    return present


def assess_data_quality(group: Group) -> DataQuality:
    """Stuft ein, wie belastbar die vorhandenen Metadaten sind.

    ``complete`` bedeutet: Name, Beschreibung, Mitgliederzahl und Sichtbarkeit
    liegen vor - mehr erhebt dieses Projekt nicht.
    """
    count = len(present_fields(group))
    if count == 0:
        return DataQuality.NONE
    if count == 1:
        return DataQuality.MINIMAL
    if count <= 3:
        return DataQuality.PARTIAL
    return DataQuality.COMPLETE


def has_sufficient_data(group: Group) -> bool:
    """Mindestanforderung fuer eine Bewertung.

    Ohne Gruppennamen ist keine Klassifikation moeglich - alles Weitere waere
    geraten. Der Name allein genuegt aber auch nicht: es muss mindestens ein
    zusaetzliches Signal geben.
    """
    if not group.name.strip():
        return False
    return bool(group.audience_tags or group.city or group.member_count_hint is not None)


def determine_status(group: Group) -> RecordStatus:
    """Leitet den Datensatzstatus ab.

    Rangfolge (der erste zutreffende Fall gewinnt):
    ``invalid`` > ``insufficient_data`` > ``validated``.

    **``duplicate`` wird hier nicht mehr vergeben.** Frueher galt
    ``times_seen > 1`` als Dublette - eine Verwechslung: ``times_seen`` zaehlt
    jeden *Fund*, nicht jeden Datensatz, und ``sqlite_store`` haelt das an
    ``count_distinct_sources`` selbst fest. Echte Dubletten sind an dieser
    Stelle laengst zusammengefuehrt: ``deduplicate_exact`` laeuft vor der
    Bewertung, ein ueberlebender Datensatz ist also nie eine offene Dublette.
    Was ``times_seen > 1`` tatsaechlich anzeigt, ist das Gegenteil eines
    Mangels - die Gruppe wurde von mehreren Anfragen gefunden, ist also
    sichtbar und einschlaegig.

    Der Schaden war messbar: Nach der Ausweitung der Suchmuster trugen 146 von
    273 bewerteten Gruppen den Stempel ``duplicate``, darunter zwei der drei
    bestbewerteten. Wer auf ``validated`` filterte, verlor mehr als die Haelfte
    des Bestands - und ausgerechnet die sichtbarsten Gruppen.

    Der Wert bleibt im Enum: Bestandsdaten tragen ihn noch, und eine von Hand
    festgestellte Dublette bliebe ein gueltiges Urteil. Abgeleitet wird er
    nicht mehr.
    """
    if group.validation_status is not ValidationStatus.VALID:
        return RecordStatus.INVALID
    if not has_sufficient_data(group):
        return RecordStatus.INSUFFICIENT_DATA
    return RecordStatus.VALIDATED
