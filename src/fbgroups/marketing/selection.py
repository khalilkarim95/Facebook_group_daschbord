"""Welche Gruppen eine Kampagne erfasst - und welche Codes daraus entstehen.

Dieses Modul ist die **einzige** Stelle, an der die Auswahlregel ausgewertet
wird. ``campaign sync``, ``--dry-run``, ``campaign add-groups`` und die Anzeige
lesen denselben Plan. Eine zweite Zaehlung koennte von der Ausfuehrung
abweichen und wuerde damit eine falsche Zahl versprechen - derselbe Grund, aus
dem ``search.build_plan`` die einzige Quelle fuer den Verbrauch ist.

Zwei Entscheidungen tragen das Modul:

- **Leer heisst: keine Einschraenkung.** Eine Kampagne ohne Angaben erfasst den
  gesamten Bestand. "Alle Gruppen" ist damit ein Normalfall der Regel und kein
  Sonderweg, und die Regel bleibt bei 310 wie bei 10.000 Gruppen dieselbe.
- **Der Plan nimmt nur hinzu.** Er kennt keinen Schritt, der eine Zuordnung
  entfernt oder einen Code neu berechnet. Was eine Gruppe an Code hat, behaelt
  sie - auch dann, wenn sie der Regel nicht mehr entspricht. Der Code steht
  moeglicherweise in einem veroeffentlichten Beitrag; ``nicht_mehr_passend``
  meldet solche Faelle, statt sie stillschweigend zu bereinigen.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fbgroups.config import AppConfig
from fbgroups.marketing.models import Campaign, CampaignGroup
from fbgroups.marketing.store import MarketingStore
from fbgroups.marketing.tracking import CodeAllocator, tracking_url
from fbgroups.models import Group

# Wert, mit dem sich eine Einschraenkung auf der Kommandozeile aufheben laesst:
# "--stadt alle" loescht die Staedteliste. Ohne ein solches Wort gaebe es keinen
# Weg, eine einmal gesetzte Einschraenkung wieder loszuwerden - genau daran
# scheiterte die erste Fassung.
ALLE = "alle"


@dataclass(frozen=True)
class Auswahl:
    """Die Auswahlregel einer Kampagne, ausgewertet gegen die Konfiguration.

    Jede leere Menge bedeutet "keine Einschraenkung in dieser Hinsicht". Die
    Mengen sind bereits kleingeschrieben und - bei den Staedten - von der
    Kennung (``berlin``) auf den Namen im Bestand (``Berlin``) uebersetzt.
    """

    audiences: frozenset[str] = frozenset()
    cities: frozenset[str] = frozenset()
    categories: frozenset[str] = frozenset()
    statuses: frozenset[str] = frozenset()
    min_score: float | None = None
    include_unscored: bool = False

    @property
    def ohne_einschraenkung(self) -> bool:
        """Erfasst diese Regel den gesamten Bestand?"""
        return not (
            self.audiences
            or self.cities
            or self.categories
            or self.statuses
            or self.min_score is not None
        ) and self.include_unscored

    def beschreibung(self) -> str:
        """Die Regel in einem Satz - fuer die Anzeige."""
        if self.ohne_einschraenkung:
            return "alle Gruppen im Bestand"

        teile = []
        if self.audiences:
            teile.append(f"Zielgruppe: {', '.join(sorted(self.audiences))}")
        if self.cities:
            teile.append(f"Stadt: {', '.join(sorted(self.cities))}")
        if self.categories:
            teile.append(f"Kategorie: {', '.join(sorted(self.categories))}")
        if self.statuses:
            teile.append(f"Status: {', '.join(sorted(self.statuses))}")
        if self.min_score is not None:
            teile.append(f"Score ab {self.min_score:g}")
        teile.append(
            "auch ohne Score" if self.include_unscored else "nur bewertete Gruppen"
        )
        return " · ".join(teile)


def auswahl_der_kampagne(campaign: Campaign, config: AppConfig) -> Auswahl:
    """Liest die Regel einer Kampagne.

    Die Staedte stehen in der Kampagne als Kennung (``berlin``), im Bestand
    aber als Name (``Berlin``) - uebersetzt wird ueber ``config/cities.yaml``,
    damit es keine zweite Liste von Stadtnamen gibt. Eine Kennung ohne Eintrag
    in der Konfiguration wird unveraendert uebernommen, sonst verschwaende ein
    Tippfehler die Einschraenkung stillschweigend.
    """
    kennungen = {c.lower() for c in campaign.target_cities}
    namen = {
        stadt.name_de.lower()
        for kennung, stadt in config.cities.items()
        if kennung.lower() in kennungen
    }
    ohne_treffer = kennungen - {
        kennung.lower() for kennung in config.cities if kennung.lower() in kennungen
    }

    return Auswahl(
        audiences=frozenset(a.lower() for a in campaign.target_audiences),
        cities=frozenset(namen | ohne_treffer),
        categories=frozenset(k.lower() for k in campaign.target_categories),
        statuses=frozenset(s.lower() for s in campaign.target_statuses),
        min_score=campaign.target_min_score,
        include_unscored=campaign.target_include_unscored,
    )


def passt(group: Group, auswahl: Auswahl) -> bool:
    """Erfuellt diese Gruppe die Regel?"""
    if group.score is None:
        if not auswahl.include_unscored:
            return False
    elif auswahl.min_score is not None and group.score < auswahl.min_score:
        return False

    if auswahl.audiences and not (auswahl.audiences & {t.lower() for t in group.audience_tags}):
        return False
    if auswahl.cities and (group.city or "").lower() not in auswahl.cities:
        return False
    if auswahl.categories and (group.category or "").lower() not in auswahl.categories:
        return False
    return not (auswahl.statuses and group.status.value.lower() not in auswahl.statuses)


def vergabereihenfolge(group: Group) -> tuple[str, str]:
    """Schluessel, nach dem Codes vergeben werden.

    Bewusst **nicht** der Score: Er aendert sich bei jedem ``rescore``, und
    damit bekaeme dieselbe Gruppe beim naechsten Lauf eine andere Nummer.
    ``first_seen_at`` steht ab dem ersten Fund fest; ``group_id`` entscheidet
    bei gleichem Zeitpunkt, damit auch ein Sammelimport eine feste Reihenfolge
    hat. Gleiche Eingabe, gleiche Codes - unabhaengig von der Bewertung.

    Oeffentlich, weil nicht nur die Regel danach vergibt: Auch das Zuordnen
    von Hand (angehakte Zeilen in der Uebersicht) muss dieselbe Reihenfolge
    benutzen. Zwei Sortierungen fuer dieselbe Frage waeren zwei Wahrheiten
    darueber, welche Gruppe welche Nummer bekommt.
    """
    return (group.first_seen_at.isoformat(), group.group_id)


def waehle_gruppen(groups: list[Group], auswahl: Auswahl) -> list[Group]:
    """Die passenden Gruppen in fester Vergabereihenfolge."""
    return sorted((g for g in groups if passt(g, auswahl)), key=vergabereihenfolge)


@dataclass
class Zuordnungsplan:
    """Was ein Lauf tun wuerde - dieselbe Rechnung fuer --dry-run und Ernstfall."""

    neu: list[tuple[Group, CampaignGroup]] = field(default_factory=list)
    bereits_zugeordnet: int = 0
    # Zugeordnet, entspricht aber der heutigen Regel nicht mehr. Wird gemeldet,
    # nie entfernt: Der Code kann schon veroeffentlicht sein.
    nicht_mehr_passend: list[str] = field(default_factory=list)

    @property
    def anzahl_neu(self) -> int:
        return len(self.neu)


def baue_plan(
    groups: list[Group],
    campaign: Campaign,
    config: AppConfig,
    vorhandene_gruppen: set[str],
    vergebene_codes: set[str],
    auswahl: Auswahl | None = None,
    top: int = 0,
) -> Zuordnungsplan:
    """Berechnet die neuen Zuordnungen einer Kampagne.

    ``vorhandene_gruppen`` sind die bereits zugeordneten Gruppen dieser
    Kampagne, ``vergebene_codes`` alle Codes ueber **alle** Kampagnen - ein Code
    ist projektweit eindeutig.

    ``top`` begrenzt die Zahl der neuen Zuordnungen (0 = alle). Die Begrenzung
    greift auf die *neuen*, nicht auf die passenden: Ein zweiter Lauf mit
    derselben Grenze kaeme sonst nie ueber die bereits zugeordneten hinaus -
    dieselbe Ueberlegung wie bei ``--limit`` im Suchlauf.
    """
    regel = auswahl if auswahl is not None else auswahl_der_kampagne(campaign, config)
    passende = waehle_gruppen(groups, regel)
    passende_ids = {g.group_id for g in passende}

    plan = Zuordnungsplan(
        nicht_mehr_passend=sorted(vorhandene_gruppen - passende_ids),
    )
    allocator = CodeAllocator(config, vergebene_codes)

    for group in passende:
        if group.group_id in vorhandene_gruppen:
            plan.bereits_zugeordnet += 1
            continue
        if top > 0 and plan.anzahl_neu >= top:
            break

        code = allocator.next_for(group)
        plan.neu.append(
            (
                group,
                CampaignGroup(
                    campaign_id=campaign.campaign_id,
                    group_id=group.group_id,
                    tracking_code=code,
                    tracking_url=tracking_url(code, config),
                ),
            )
        )

    return plan


def synchronisiere(
    store: MarketingStore,
    groups: list[Group],
    campaign: Campaign,
    config: AppConfig,
) -> Zuordnungsplan:
    """Wendet die Regel einer Kampagne an und speichert die neuen Zuordnungen.

    Wiederholbar: Ein zweiter Aufruf ohne neue Gruppen legt nichts an. Damit
    ist derselbe Aufruf nach jedem Import, nach jedem Suchlauf und von Hand
    unbedenklich - und der Aufwand haengt an der Zahl der *neuen* Gruppen, nicht
    am Gesamtbestand.
    """
    plan = baue_plan(
        groups,
        campaign,
        config,
        vorhandene_gruppen=store.assigned_group_ids(campaign.campaign_id),
        vergebene_codes=store.assigned_codes(),
    )
    if plan.neu:
        store.add_links([link for _group, link in plan.neu])
        store.audit(
            "kampagne_sync", campaign.campaign_id, f"{plan.anzahl_neu} neue Zuordnungen"
        )
    return plan


# --- Prioritaet fuer die Warteschlange ---------------------------------

# Reihenfolge von hoch nach niedrig. Die Namen sind die Schluessel in
# config/settings.yaml unter marketing.posting.prioritaet; "niedrig" steht
# nicht darin - es ist der Rest.
PRIORITAETEN: tuple[str, ...] = ("hoch", "mittel", "normal", "niedrig")


def anteil(group: Group) -> float | None:
    """Anteil der erreichten an den erreichbaren Punkten - oder None.

    **Nicht der rohe Score.** Der ist bewusst nicht auf 100 normiert:
    ``score_max`` ist 100 ohne veroeffentlichten Beitrag und 175 mit gemessener
    Resonanz. Eine feste Schwelle auf dem Rohwert stellte "100 von 100" ueber
    "130 von 175" - genau verkehrt herum, und ausgerechnet die belegten
    Gruppen fielen zurueck.

    ``None`` heisst nicht bewertbar. Es gibt keinen Ersatzwert: Eine Gruppe
    ohne Score ist keine Gruppe mit Score 0, und sie einzureihen, als waere
    sie schlecht, waere eine Behauptung ueber sie statt ueber unsere Datenlage.
    """
    if group.score is None or not group.score_max:
        return None
    return group.score / group.score_max


def prioritaet(group: Group, config: AppConfig) -> str:
    """Prioritaetsklasse einer Gruppe - "hoch" bis "niedrig".

    Die Schwellen stehen in ``config/settings.yaml``. Sie sind eine fachliche
    Entscheidung ueber die Reihenfolge der eigenen Arbeit; verteilt auf
    mehrere Dateien waere die Frage "warum kommt diese Gruppe zuerst?" nur
    noch durch Lesen des Programms zu beantworten.
    """
    wert = anteil(group)
    if wert is None:
        return "niedrig"

    schwellen = config.get("marketing", "posting", "prioritaet", default={}) or {}
    for name in ("hoch", "mittel", "normal"):
        grenze = schwellen.get(name)
        if grenze is not None and wert >= float(grenze):
            return name
    return "niedrig"


def nach_prioritaet(gruppen: list[Group], config: AppConfig) -> list[Group]:
    """Sortiert die besten zuerst - dieselbe Rangfolge wie ``sort_by_rank``.

    Bei 310 Gruppen bringt niemand die Liste an einem Tag zu Ende. Wer
    abbricht, soll die wertvollsten Beitraege geschrieben haben und nicht die
    alphabetisch ersten. Deshalb wird beim **Einreihen** sortiert und nicht
    beim Abarbeiten: Sonst entschiede eine Neubewertung mitten im Lauf, welcher
    Beitrag als naechstes hinausgeht.
    """
    from fbgroups.scoring import sort_by_rank

    return sort_by_rank(gruppen)
