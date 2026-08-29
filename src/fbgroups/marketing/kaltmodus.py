"""Kaltmodus: die Kampagne auf Tage verteilen, statt sie an einem Tag zu fahren.

## Warum wieder ein Tagesmass

Am 27.08.2026 wurde das Tageslimit entfernt, und der Grund steht im Kopf von
``arbeit.py``: Es war eine **Sperre**, die ausgerechnet den traf, der gerade
arbeitete. Wer dreissig Gruppen vor sich hatte, stand vor der einundzwanzigsten.

Der Einwand ist richtig und trifft dieses Modul nicht, weil es etwas anderes
tut. Es **teilt vorher zu**, statt hinterher zu blockieren:

* Das Tageslimit zeigte dreissig Gruppen und hielt bei zwanzig an.
* Die Tagesportion zeigt **die heutigen** und sagt dazu, wann der Rest kommt.

Man laeuft nie gegen eine Wand, weil dahinter nichts steht. Wer trotzdem
weitermachen will, hebt die Portion auf - eine Entscheidung, kein Umweg.

## Warum ueberhaupt ein Takt

Dreihundert Beitraege an einem Tag sind dasselbe Muster, ob ein Programm sie
absetzt oder ein Mensch sie einfuegt: Gezaehlt wird der Beitrag, nicht die
Hand. Der Takt ist deshalb kein Preis fuer Sicherheit, sondern die Bedingung
dafuer, dass die dreihundert ueberhaupt ankommen.

## Was dieses Modul nicht tut

Es veroeffentlicht nichts, oeffnet nichts und kennt facebook.com nicht. Es
rechnet: welche Gruppen heute, wie viele noch, ab wann die naechste, wann die
Kampagne durch ist. Reine Funktionen ueber uebergebene Werte - ohne Netz, ohne
Datenbank und ohne Uhr testbar.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from fbgroups.marketing.models import CampaignGroup

# Voreinstellungen, wenn in settings.yaml nichts steht. Bewusst an einer Stelle.
STANDARD_PRO_TAG = 25
STANDARD_ABSTAND_MINUTEN = 4


@dataclass(frozen=True)
class Tagesportion:
    """Was heute ansteht - und was das fuer den Rest bedeutet."""

    grenze: int
    """Wie viele Beitraege heute vorgesehen sind."""

    erledigt: int
    """Wie viele heute schon gemeldet wurden - veroeffentlicht oder gescheitert."""

    gruppen: list[CampaignGroup]
    """Genau die Gruppen, die heute drankommen.

    Hier liegt der Unterschied zum alten Tageslimit: Die Liste ist bereits
    zugeschnitten. Was nicht drinsteht, laeuft einem auch nicht als gesperrter
    Eintrag ueber den Weg.
    """

    verbleibend_gesamt: int
    """Wie viele Gruppen der Kampagne insgesamt noch offen sind."""

    @property
    def offen_heute(self) -> int:
        return max(self.grenze - self.erledigt, 0)

    @property
    def fertig_fuer_heute(self) -> bool:
        return self.offen_heute == 0

    def resttage(self) -> int:
        """Wie viele Tage der Rest bei diesem Takt braucht.

        Aufgerundet - ein angefangener Tag ist ein Tag. Bei ``grenze <= 0``
        steht die Kampagne; dann waere jede Zahl eine falsche Auskunft.
        """
        if self.grenze <= 0:
            return 0
        rest = max(self.verbleibend_gesamt - self.offen_heute, 0)
        return (rest + self.grenze - 1) // self.grenze

    def fertig_am(self, heute: date) -> date | None:
        if self.grenze <= 0:
            return None
        return heute + timedelta(days=self.resttage())


def tagesportion(
    reihe: list[CampaignGroup],
    *,
    erledigt_heute: int,
    grenze: int = STANDARD_PRO_TAG,
) -> Tagesportion:
    """Die heutige Portion aus der bereits gerankten Reihe.

    ``reihe`` kommt fertig sortiert herein und wird hier **nicht** noch einmal
    geordnet - dieselbe Ueberlegung wie bei ``auswahlliste``: Zwei Berechnungen
    derselben Rangfolge koennen auseinanderlaufen, und dann zeigte die Portion
    andere Gruppen als die Arbeitsliste daneben.

    Die besten zuerst gilt weiter: Falls die Kampagne nie zu Ende gefahren
    wird, sollen es die richtigen fuenfundzwanzig gewesen sein.
    """
    grenze = max(int(grenze), 0)
    erledigt = max(int(erledigt_heute), 0)
    offen = max(grenze - erledigt, 0)
    return Tagesportion(
        grenze=grenze,
        erledigt=erledigt,
        gruppen=list(reihe[:offen]),
        verbleibend_gesamt=len(reihe),
    )


def naechster_zeitpunkt(
    letzter_versuch: datetime | None,
    *,
    abstand_minuten: int = STANDARD_ABSTAND_MINUTEN,
    jetzt: datetime,
) -> datetime | None:
    """Ab wann der naechste Beitrag drankommt, oder ``None`` fuer sofort.

    Der Abstand ist die zweite Haelfte des Taktes: Fuenfundzwanzig Beitraege in
    zehn Minuten sind dasselbe Muster wie dreihundert an einem Tag, nur
    schneller. Die Pause wird bewusst **nicht** zufaellig gestreut - eine
    gleichmaessige Pause reicht, und eine gestreute waere der Anfang davon,
    unauffaellig aussehen zu wollen. Das ist nicht die Aufgabe dieses Projekts.
    """
    if letzter_versuch is None or abstand_minuten <= 0:
        return None
    frei_ab = letzter_versuch + timedelta(minutes=abstand_minuten)
    return frei_ab if frei_ab > jetzt else None


def wartezeit_text(frei_ab: datetime | None, *, jetzt: datetime) -> str:
    """``noch 3 Min`` - oder leer, wenn nichts zu warten ist."""
    if frei_ab is None or frei_ab <= jetzt:
        return ""
    # Aufgerundet, aber nur was angebrochen ist: 120 Sekunden sind zwei
    # Minuten, 121 sind drei. Ein glattes ``// 60 + 1`` machte aus jeder
    # vollen Minute eine zu viel - die Anzeige liefe der Uhr voraus.
    sekunden = (frei_ab - jetzt).total_seconds()
    minuten = -int(-sekunden // 60)
    return f"noch {minuten} Min"


def einstellungen(config) -> tuple[bool, int, int]:
    """(aktiv, Beitraege je Tag, Mindestabstand) aus ``settings.yaml``.

    Fehlt der Block, ist der Kaltmodus **aus**: Eine bestehende Installation
    soll sich durch ein Update nicht anders verhalten als gestern.
    """
    aktiv = bool(config.get("kaltmodus", "aktiv", default=False))
    pro_tag = int(config.get("kaltmodus", "beitraege_pro_tag", default=STANDARD_PRO_TAG))
    abstand = int(
        config.get("kaltmodus", "mindestabstand_minuten", default=STANDARD_ABSTAND_MINUTEN)
    )
    return aktiv, pro_tag, abstand
