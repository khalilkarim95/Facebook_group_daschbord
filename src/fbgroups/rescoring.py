"""Den gespeicherten Bestand neu bewerten - an einer Stelle, fuer beide Wege.

Der Kampagnenlauf ruft das hier auf (seit 12.09.2026): Bevor eine Kampagne
bearbeitet wird, werden ihre Gruppen mit dem bewertet, was inzwischen ueber sie
bekannt ist - Mitgliederzahl, Aktivitaet und Resonanz aus den Klicks. Erst
danach steht die Rangfolge fest, nach der gearbeitet wird.

Zwei Fassungen dieser Rechnung waeren zwei Ranglisten. Die eine entschiede,
was die Uebersicht zeigt, die andere, wo die naechsten dreihundert Beitraege
hingehen - und niemand koennte sagen, welche gilt.

**Neu bewertet wird, nicht neu klassifiziert** (seit 20.09.2026). Bis dahin
lief hier ``pipeline.classify_group`` mit und leitete Zielgruppe, Stadt und
Kategorie aus Begriffslisten ab. Die Listen (``audiences.yaml``,
``cities.yaml``, ``categories.yaml``) sind mit der Entdeckungsschicht
entfernt; die drei Felder stehen seither im Bestand und werden von Hand
gepflegt. Eine Neubewertung, die sie ueberschriebe, machte aus jedem Lauf ein
stilles Zuruecksetzen dieser Handarbeit.

Kein Netz, keine Suchanfrage, kein Guthaben: Es wird gelesen, gerechnet und
zurueckgeschrieben. Geschrieben wird ueber ``update_scores`` und damit nur in
abgeleitete Felder - eine Neubewertung ist kein Fund und zaehlt ``times_seen``
nicht hoch.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fbgroups.config import AppConfig
from fbgroups.models import Group
from fbgroups.scoring import score_all
from fbgroups.storage import SqliteStore


@dataclass(frozen=True)
class Bewertung:
    """Was eine Neubewertung ergeben hat - Zahlen und die bewerteten Gruppen.

    ``fehler`` ist kein leeres Feld aus Hoeflichkeit: Die Resonanz kann
    fehlen (eine Datei aus alter Zeit), und dann wird **ohne** sie bewertet,
    statt den Lauf abzubrechen. Wer die Zahl liest, soll sehen, dass ein Teil
    der Grundlage gefehlt hat.
    """

    bewertet: int = 0
    geaendert: int = 0
    mit_resonanz: int = 0
    fehler: str = ""
    gruppen: list[Group] = field(default_factory=list)


def _resonanz(config: AppConfig) -> tuple[dict, str]:
    """Die gemessene Resonanz - oder nichts und der Grund dafuer.

    Der Kern in ``scoring.py`` kennt die Marketing-Erweiterung nicht; die
    Zahlen werden hereingereicht. Fehlt die Tabelle, bleibt die Bewertung die
    bisherige, statt den ganzen Lauf abzubrechen.
    """
    try:
        from fbgroups.marketing.resonanz import resonanz_je_gruppe
        from fbgroups.marketing.store import MarketingStore

        with MarketingStore(config.path("sqlite_path")) as store:
            return resonanz_je_gruppe(store), ""
    except Exception as exc:  # noqa: BLE001 - eine fehlende Grundlage ist kein Abbruch
        return {}, str(exc).splitlines()[0][:120]


def bewerte_neu(
    config: AppConfig,
    *,
    nur: set[str] | None = None,
    dry_run: bool = False,
) -> Bewertung:
    """Bewertet den Bestand neu und schreibt die Scores zurueck.

    ``nur`` schraenkt auf bestimmte Gruppen ein - der Kampagnenlauf bewertet
    die Gruppen **seiner** Kampagne und nicht dreihundert fremde. Gerechnet
    wird trotzdem ueber den ganzen geladenen Bestand, denn ``score_all``
    bekommt die Liste, die es bewerten soll; eingeschraenkt wird die Auswahl,
    nicht die Rechnung.

    ``dry_run`` rechnet und schreibt nichts - dieselbe Zusage wie ueberall:
    Vorschau und Ernstfall lesen denselben Weg.
    """
    gemessen, fehler = _resonanz(config)

    with SqliteStore(config.path("sqlite_path")) as store:
        alle = store.load_groups()
        gruppen = [g for g in alle if nur is None or g.group_id in nur]
        vorher = {g.group_id: g.score for g in gruppen}

        bewertet = score_all(gruppen, config, gemessen)
        geaendert = sum(1 for g in bewertet if g.score != vorher.get(g.group_id))

        if not dry_run and bewertet:
            store.update_scores(bewertet)

    return Bewertung(
        bewertet=len(bewertet),
        geaendert=geaendert,
        mit_resonanz=len(gemessen),
        fehler=fehler,
        gruppen=bewertet,
    )


__all__ = ["Bewertung", "bewerte_neu"]
