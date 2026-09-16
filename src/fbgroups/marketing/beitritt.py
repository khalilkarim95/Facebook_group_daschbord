"""Beitrittsanfragen: Takt und Tagesmenge - ohne Netz, ohne Datenbank.

Dieselbe Aufteilung wie bei ``kaltmodus.py``: Hier steht die Rechnung, nicht
die Handlung. Wer die Anfrage stellt, ist ``automation.actions.request_join``;
wer sie zaehlt und bucht, ist der Server.

## Warum ein eigener, strengerer Takt

Die Beitrittsanfrage ist die riskanteste Handlung des Projekts - nicht wegen
ihres Inhalts (sie traegt keinen Text), sondern wegen der Menge: Am 31.08.2026
standen **311 von 314** Gruppen ohne jeden Eintrag. Facebook begrenzt
Beitrittsanfragen haerter als Beitraege, und an diesem Konto haengen alle
bestehenden Mitgliedschaften.

Fuenfzig am Tag sind die Vorgabe des Nutzers. Bei 311 Gruppen sind das gut
sechs Tage - und das ist der Handel, den dieser Takt beschreibt.

## Zwei Staende, nicht mehr

Angefragt oder nicht. Gezaehlt wird ueber ``group_marketing.join_requested_at``;
ein eigener Zaehler daneben waere eine zweite Wahrheit ueber dieselbe Zahl.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

STANDARD_PRO_TAG = 50
STANDARD_ABSTAND_MINUTEN = 3


def einstellungen(config) -> tuple[int, int]:
    """``(Anfragen je Tag, Mindestabstand in Minuten)`` - gelesen in ``grenzen``.

    Seit dem 12.09.2026 steht die Zahl nicht mehr hier, sondern in
    ``grenzen.einstellungen``: Dort haben alle drei Aktionen ihre Tagesmenge,
    und zwei Zahlen fuer dieselbe Frage waeren zwei Wahrheiten - eine
    Aenderung an einer von beiden haette die andere still ueberstimmt, je
    nachdem, welcher Aufrufer gerade fragt.

    Die Funktion bleibt, weil ihre Aufrufer bleiben; sie reicht jetzt durch.
    Die alten Schluessel (``beitritt.anfragen_pro_tag``) gelten dort als
    Rueckfall weiter.
    """
    from fbgroups.marketing import grenzen

    grenze = grenzen.einstellungen(config).fuer(grenzen.Aktion.BEITRITT)
    return grenze.pro_tag, grenze.abstand_min


def naechster_zeitpunkt(
    letzte: datetime | None, *, abstand_minuten: int, jetzt: datetime
) -> datetime | None:
    """Ab wann die naechste Anfrage darf - oder ``None`` fuer sofort.

    Gestreut wie beim Kaltmodus (80-130 % der Grundzeit) und aus demselben
    Grund deterministisch geseedet: Sonst zeigte eine Anzeige bei jedem
    Neuladen eine andere Wartezeit, und keine davon waere die geltende.
    """
    if letzte is None or abstand_minuten <= 0:
        return None
    rng = random.Random(int(letzte.timestamp()))
    frei_ab = letzte + timedelta(minutes=abstand_minuten * rng.uniform(0.8, 1.3))
    return frei_ab if frei_ab > jetzt else None


def wartezeit(letzte_iso: str, abstand_minuten: int, *, jetzt: datetime) -> str:
    """``noch 2 Min`` - oder leer, wenn nichts zu warten ist."""
    if not letzte_iso:
        return ""
    frei_ab = naechster_zeitpunkt(
        datetime.fromisoformat(letzte_iso), abstand_minuten=abstand_minuten, jetzt=jetzt
    )
    if frei_ab is None:
        return ""
    sekunden = (frei_ab - jetzt).total_seconds()
    return f"noch {-int(-sekunden // 60)} Min"


def resttage(offen: int, pro_tag: int) -> int:
    """Wie viele Tage der Rest bei diesem Takt braucht - aufgerundet."""
    if pro_tag <= 0:
        return 0
    return (max(offen, 0) + pro_tag - 1) // pro_tag


__all__ = [
    "STANDARD_ABSTAND_MINUTEN",
    "STANDARD_PRO_TAG",
    "einstellungen",
    "naechster_zeitpunkt",
    "resttage",
    "wartezeit",
]
