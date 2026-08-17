"""Importer fuer manuell gesammelte Gruppen-URLs.

Unterstuetzte Formate:

* ``.csv``  - Spalte ``url`` ist Pflicht; optional ``name``, ``member_count``,
  ``description``, ``privacy``, ``notes``, ``erreichbar``.
* ``.txt``  - eine URL pro Zeile, ``#`` leitet einen Kommentar ein.

Der Importer liest ausschliesslich die uebergebene Datei. Es werden keine
Inhalte von Facebook abgerufen und keine personenbezogenen Daten verarbeitet.
"""

from __future__ import annotations

import csv
import re
from datetime import UTC, datetime
from pathlib import Path

from fbgroups.config import AppConfig
from fbgroups.models import (
    Group,
    ImportRun,
    PrivacyHint,
    Provenance,
    RejectedRow,
    SourceType,
    ValidationStatus,
)
from fbgroups.urls import ParsedGroupUrl, parse_group_url
from fbgroups.validation import validate_group

# Erlaubte Schreibweisen der Kopfzeile -> internes Feld
_COLUMN_ALIASES = {
    "url": "url", "link": "url", "gruppe": "url", "group_url": "url",
    "name": "name", "gruppenname": "name", "title": "name", "titel": "name",
    "member_count": "member_count", "mitglieder": "member_count",
    "members": "member_count", "mitgliederzahl": "member_count",
    "description": "description", "beschreibung": "description",
    "snippet": "description",
    "privacy": "privacy", "sichtbarkeit": "privacy",
    "notes": "notes", "notiz": "notes", "bemerkung": "notes",
    "erreichbar": "reachable", "reachable": "reachable",
    "verfuegbar": "reachable", "verfügbar": "reachable",
}

# Urteil aus der Pruefliste. Nur der Mensch kann es faellen - das Programm ruft
# facebook.com nicht auf. Eine leere Zelle bedeutet "nicht geprueft" und
# aendert nichts; nur ein ausdrueckliches Nein setzt den Status.
_REACHABLE_NO = {"nein", "no", "n", "0", "tot", "weg", "geloescht", "gelöscht", "nicht"}
_REACHABLE_YES = {"ja", "yes", "y", "1", "ok", "erreichbar"}

_PRIVACY_VALUES = {
    "public": PrivacyHint.PUBLIC, "oeffentlich": PrivacyHint.PUBLIC,
    "öffentlich": PrivacyHint.PUBLIC, "offen": PrivacyHint.PUBLIC,
    "private": PrivacyHint.PRIVATE, "privat": PrivacyHint.PRIVATE,
    "geschlossen": PrivacyHint.PRIVATE,
}

# Die Einheit darf kein Wortanfang sein: sonst liest "4.200 Mitglieder"
# das M als Millionen-Marker.
_MEMBER_COUNT_RE = re.compile(r"(\d[\d.,\s]*)\s*(k|tsd|mio|m)?(?![a-zA-Z])", re.IGNORECASE)


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
    cleaned = number_part.replace(" ", "")
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


def _normalize_header(fieldnames: list[str] | None) -> dict[str, str]:
    """Bildet die tatsaechliche Kopfzeile auf interne Feldnamen ab."""
    mapping: dict[str, str] = {}
    for raw_name in fieldnames or []:
        key = (raw_name or "").strip().lstrip("﻿").lower()
        if key in _COLUMN_ALIASES:
            mapping[raw_name] = _COLUMN_ALIASES[key]
    return mapping


def _build_group(
    parsed: ParsedGroupUrl,
    row: dict[str, str],
    source_ref: str,
    line_no: int,
) -> Group:
    privacy_raw = (row.get("privacy") or "").strip().lower()
    name = (row.get("name") or "").strip()

    group = Group(
        group_id=parsed.group_id,
        url_canonical=parsed.canonical_url,
        url_variants=([parsed.original_url] if parsed.original_url != parsed.canonical_url else []),
        name=name,
        description_snippet=(row.get("description") or "").strip() or None,
        member_count_hint=parse_member_count(row.get("member_count")),
        privacy_hint=_PRIVACY_VALUES.get(privacy_raw, PrivacyHint.UNKNOWN),
        notes=(row.get("notes") or "").strip(),
        sources=[
            Provenance(
                source_type=SourceType.MANUAL_SEED,
                source_ref=source_ref,
                source_line=line_no,
            )
        ],
    )

    # Strukturelle Pruefung der Kennung. Fehlende Metadaten werden hier
    # ausdruecklich nicht ergaenzt - was nicht in der Datei stand, bleibt leer.
    group.validation_status = validate_group(group)

    # Ein Urteil aus der Pruefliste schlaegt die strukturelle Pruefung: Wer die
    # Gruppe im Browser geoeffnet hat, weiss mehr als jede Mustererkennung.
    reachable = (row.get("reachable") or "").strip().lower()
    if reachable in _REACHABLE_NO:
        group.validation_status = ValidationStatus.UNREACHABLE
    elif reachable in _REACHABLE_YES and group.validation_status is ValidationStatus.TEST_DATA:
        group.validation_status = ValidationStatus.VALID

    return group


def _detect_delimiter(sample: str) -> str:
    """Erkennt das Trennzeichen. Excel schreibt im deutschen Gebietsschema ';'."""
    try:
        return csv.Sniffer().sniff(sample, delimiters=";,\t").delimiter
    except csv.Error:
        # Rueckfall: haeufigstes Trennzeichen der Kopfzeile
        header = sample.splitlines()[0] if sample else ""
        return max(";,\t", key=header.count) if header else ","


def _read_csv_rows(path: Path) -> list[tuple[int, dict[str, str]]]:
    # utf-8-sig entfernt ein von Excel geschriebenes BOM.
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        delimiter = _detect_delimiter(fh.read(4096))
        fh.seek(0)
        reader = csv.DictReader(fh, delimiter=delimiter)
        header_map = _normalize_header(reader.fieldnames)
        if "url" not in header_map.values():
            raise ValueError(
                f"{path.name}: Pflichtspalte 'url' fehlt. "
                f"Gefundene Spalten: {reader.fieldnames}"
            )

        rows: list[tuple[int, dict[str, str]]] = []
        for line_no, raw_row in enumerate(reader, start=2):
            row = {
                header_map[key]: (value or "")
                for key, value in raw_row.items()
                if key in header_map
            }
            rows.append((line_no, row))
        return rows


def _read_txt_rows(path: Path) -> list[tuple[int, dict[str, str]]]:
    rows: list[tuple[int, dict[str, str]]] = []
    # utf-8-sig auch hier: Notepad und PowerShell Out-File schreiben ein BOM,
    # das sonst die erste URL unbrauchbar machen wuerde.
    with path.open("r", encoding="utf-8-sig") as fh:
        for line_no, line in enumerate(fh, start=1):
            text = line.split("#", 1)[0].strip()
            if text:
                rows.append((line_no, {"url": text}))
    return rows


def import_seed_file(path: Path, run: ImportRun) -> list[Group]:
    """Liest eine einzelne Seed-Datei und liefert die gueltigen Gruppen."""
    source_ref = path.name
    run.source_files.append(source_ref)

    try:
        rows = _read_csv_rows(path) if path.suffix.lower() == ".csv" else _read_txt_rows(path)
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        run.errors.append(f"{source_ref}: {exc}")
        return []

    groups: list[Group] = []
    for line_no, row in rows:
        run.rows_total += 1
        raw_url = (row.get("url") or "").strip()

        result = parse_group_url(raw_url)
        if not isinstance(result, ParsedGroupUrl):
            run.rows_rejected += 1
            run.rejected.append(
                RejectedRow(
                    raw_value=raw_url,
                    reason=result.reason,
                    source_ref=source_ref,
                    source_line=line_no,
                )
            )
            continue

        run.rows_valid += 1
        groups.append(_build_group(result, row, source_ref, line_no))

    return groups


def import_seeds(
    config: AppConfig,
    paths: list[Path] | None = None,
    run_id: str | None = None,
) -> tuple[list[Group], ImportRun]:
    """Importiert alle Seed-Dateien (oder die angegebenen).

    Returns:
        (noch nicht deduplizierte Gruppen, Protokoll des Laufs)
    """
    if paths is None:
        seeds_dir = config.path("seeds_dir")
        paths = sorted(
            p for p in seeds_dir.glob("*") if p.suffix.lower() in {".csv", ".txt"}
        )

    run = ImportRun(
        run_id=run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
        source_type=SourceType.MANUAL_SEED,
    )

    if not paths:
        run.errors.append("Keine Seed-Dateien gefunden (*.csv oder *.txt).")
        run.finished_at = datetime.now(UTC)
        return [], run

    groups: list[Group] = []
    for path in paths:
        groups.extend(import_seed_file(Path(path), run))

    return groups, run
