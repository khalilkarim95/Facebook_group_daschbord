"""Wie fbgroups eine SQLite-Datei oeffnet - an einer Stelle.

Seit dem Umzug (25.09.2026) teilen sich auf diesem Rechner mehrere Prozesse
die Datei: der Lauf (``campaign automatik``), die Uebersicht (``fbgroups
serve``) und die Sicherung des Waechters. Auf dem Server gab es nur den
Dienst. Schreibt einer gerade, wartet der andere - bis zu
``SPERRFRIST_SEKUNDEN``, statt nach den fuenf Sekunden, die ``sqlite3`` von
sich aus wartet, mit "database is locked" abzubrechen.

Ein eigenes Modul ohne Abhaengigkeiten, weil beide Speicher es brauchen und
``storage.sqlite_store`` und ``marketing.store`` einander schon importieren.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

#: Wie lange auf eine Datei gewartet wird, in die gerade ein anderer
#: Prozess schreibt. Eine Buchung dauert Millisekunden; dreissig Sekunden
#: decken auch eine Sicherung ab, die gerade liest.
SPERRFRIST_SEKUNDEN = 30.0


def verbinde(pfad: Path | str) -> sqlite3.Connection:
    """Eine Verbindung, die auf einen anderen Schreiber wartet, statt zu scheitern."""
    return sqlite3.connect(pfad, timeout=SPERRFRIST_SEKUNDEN)


__all__ = ["SPERRFRIST_SEKUNDEN", "verbinde"]
