"""Die Kommentarautomatik: Kampagne fuer Kampagne, Gruppe fuer Gruppe.

## Was dieses Modul ist - und was nicht

``arbeit.py`` haelt **einen Schritt**: einen Auftrag holen, ein Ergebnis
melden. Kommandozeile und Weboberflaeche rufen ihn seit jeher auf. Dieses
Modul ist die **Schleife darueber** - es beantwortet allein die Frage "was
kommt als naechstes?" und fuehrt selbst nichts aus.

Die Trennung ist dieselbe wie zwischen ``scoring.py`` und den Modulen, die
Zahlen beschaffen: Hier steht keine Zeile Playwright, kein ``webbrowser``,
kein Netz. Der Treiber (``campaign automatik``, spaeter die Weboberflaeche)
holt sich den naechsten Schritt, fuehrt ihn aus und meldet zurueck.

## Wo der Fortschritt steht

**Nicht hier.** Welche Fassung einer Gruppe veroeffentlicht ist, steht in
``campaign_group_texte.status`` - dort steht es, seit es Fassungen gibt. Ein
eigener Zaehler daneben waere eine zweite Wahrheit ueber dieselbe Zahl, und
die beiden liefen beim ersten Abbruch auseinander.

Der Fortschritt wird deshalb **gelesen, nicht gefuehrt**. Das hat eine
angenehme Folge: Ein Abbruch braucht keine Aufraeumarbeit. Wer den Lauf
mitten in Gruppe 7 abwuergt, findet beim naechsten Start genau die Fassungen
offen, die noch nicht heraus sind - weil nie etwas anderes behauptet wurde.

Gespeichert wird nur, was sich **nicht** ableiten laesst:

* dass gerade ein Lauf im Gange ist (``automatik_lauf``),
* welche Kampagnen zu ihm gehoeren (``automatik_lauf_kampagnen``),
* und dass eine Gruppe nichts mehr hergibt (``kommentar_erschoepft``).

## Die Vorlagenwahl ist keine Rotation, sondern eine Folge

Gefordert war ein Wechsel 1-2-3-4-5. Herausgekommen ist die einfachere
Regel: **die kleinste Nummer, die noch nicht veroeffentlicht ist.** Sie
ergibt genau diese Folge, braucht aber keinen gespeicherten Zeiger und
ueberlebt jeden Abbruch - nach einem Neustart in Gruppe 7 bei Kommentar 3
steht Fassung 3 immer noch als naechste da. Ein Zaehler haette hier gemerkt
werden muessen, und ein gemerkter Zaehler kann von der Wirklichkeit abweichen.

Fuenf Fassungen, fuenf Kommentare, jede genau einmal: Kein Text geht zweimal
in dieselbe Gruppe. Zwei wortgleiche Kommentare untereinander sind das
deutlichste Zeichen einer Maschine, das man hinterlassen kann.

## Warum die Kampagnenliste eingefroren wird

Ein Lauf arbeitet die Liste ab, die beim Start galt. Wer waehrend des Laufs
eine Kampagne auf ``active`` setzt, greift nicht in den laufenden Vorgang
ein - sie kommt beim naechsten Start dran. Sonst waere "alle aktiven
Kampagnen" bei jedem Schritt eine andere Menge, und der Lauf koennte nie
fertig werden, weil die Bedingung unter ihm wegwandert.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fbgroups.marketing.models import (
    KampagnenLaufStatus,
    LaufStatus,
    Texttyp,
)

# Zehn Kommentare je Gruppe, aus fuenf Vorlagen. Die beiden Zahlen sind
# **nicht** dasselbe, und genau deshalb stehen sie getrennt:
#
#   MAX_VORSCHLAEGE  = 5   die Zahl der *Texte*
#   ZIEL_JE_GRUPPE   = 10  die Zahl des *Vorhabens*
#
# Fassung 6 traegt wieder Vorlage 1, Fassung 7 wieder Vorlage 2 und so fort
# (``vorlage_zu_nummer``). Zwei gleiche Texte gehen dabei nie unter denselben
# Beitrag: ``bisherige_post_urls`` sorgt dafuer, dass jeder Kommentar einen
# anderen Beitrag bekommt.
ZIEL_JE_GRUPPE = 10

# Wie viele verschiedene Texte im Topf stehen. Aus ihnen wird gedreht.
VORLAGEN_JE_TOPF = 5

# Wie oft eine einzelne Fassung in einem Lauf wiederholt wird, bevor der Lauf
# sie liegenlaesst und weitergeht. Drei, weil der haeufigste Fehlschlag ein
# voruebergehender ist (Seite langsam, Element noch nicht da) und der
# zweithaeufigste ein dauerhafter (Kommentare abgeschaltet) - beim dritten Mal
# ist die Unterscheidung getroffen.
MAX_VERSUCHE_JE_FASSUNG = 3


# --- Fortschritt -----------------------------------------------------------
@dataclass(frozen=True)
class Gruppenfortschritt:
    """Wie weit **eine** Gruppe in **einer** Kampagne ist.

    ``erschoepft`` ist der dritte Ausgang neben "voll" und "offen": Die Gruppe
    gibt nicht mehr her - zu wenige Beitraege zum Kommentieren, Kommentare
    abgeschaltet, kein Zugang. Ohne diesen Zustand haengt eine Kampagne fuer
    immer bei vier von fuenf, weil eine einzige Gruppe nur vier Beitraege hat.
    """

    campaign_id: str
    group_id: str
    name: str
    veroeffentlicht: int
    ziel: int = ZIEL_JE_GRUPPE
    erschoepft: bool = False
    erschoepft_grund: str = ""
    gescheiterte_fassungen: frozenset[int] = field(default_factory=frozenset)

    mitglied: bool = False
    """Ist das Konto Mitglied dieser Gruppe?

    **Die Vorbedingung fuer alles Uebrige.** Facebook laesst Nichtmitglieder
    in den meisten Gruppen weder posten noch kommentieren; ein Versuch dort
    scheitert nicht zufaellig, sondern immer. Am 31.08.2026 waren von 36
    zugeordneten Gruppen **null** auf ``mitglied`` - ein Lauf haette 180
    Versuche gemacht, die alle fehlschlagen mussten, und genau diese Folge
    aus einem Konto ist das Muster, das zur Sperre fuehrt.

    Der Weg dorthin ist Handarbeit und bleibt es: ``marketing beitritt``
    schreibt mit, was ein Mensch im Browser getan hat. Die Automatik wartet.
    """

    @property
    def offen(self) -> int:
        return max(self.ziel - self.veroeffentlicht, 0)

    @property
    def voll(self) -> bool:
        """Alle vorgesehenen Kommentare sind heraus."""
        return self.offen == 0

    @property
    def wartet(self) -> bool:
        """Noch keine Mitgliedschaft - die Gruppe ist blockiert, nicht erledigt."""
        return not self.mitglied and not self.voll and not self.erschoepft

    @property
    def bearbeitbar(self) -> bool:
        """Darf die Automatik hier ueberhaupt etwas versuchen?"""
        return self.mitglied and not self.voll and not self.erschoepft

    @property
    def fertig(self) -> bool:
        """Nichts mehr zu tun - entweder voll oder nachweislich am Ende.

        Beides zaehlt als abgeschlossen, aber nur eines davon als Erfolg; die
        Abschlussmeldung weist sie deshalb getrennt aus.

        Eine wartende Gruppe ist **nicht** fertig. Sie fehlt nicht wegen
        unserer Daten, sondern weil die Arbeit dort noch nicht getan werden
        *kann* - und eine Kampagne, die daraufhin "erfolgreich abgeschlossen"
        meldete, behauptete Beitraege, die es nicht gibt.
        """
        return self.voll or self.erschoepft


@dataclass(frozen=True)
class Kampagnenfortschritt:
    """Wie weit eine Kampagne ist - aus den Staenden ihrer Gruppen."""

    campaign_id: str
    name: str
    gruppen: list[Gruppenfortschritt]
    status: KampagnenLaufStatus = KampagnenLaufStatus.WARTET

    @property
    def gruppen_gesamt(self) -> int:
        return len(self.gruppen)

    @property
    def gruppen_fertig(self) -> int:
        return sum(1 for g in self.gruppen if g.fertig)

    @property
    def gruppen_voll(self) -> int:
        return sum(1 for g in self.gruppen if g.voll)

    @property
    def gruppen_erschoepft(self) -> int:
        return sum(1 for g in self.gruppen if g.erschoepft and not g.voll)

    @property
    def kommentare_veroeffentlicht(self) -> int:
        return sum(g.veroeffentlicht for g in self.gruppen)

    @property
    def kommentare_ziel(self) -> int:
        return sum(g.ziel for g in self.gruppen)

    @property
    def fertig(self) -> bool:
        """Jede Gruppe ist durch - **und es gibt ueberhaupt Gruppen**.

        Die zweite Haelfte ist kein Zierrat: ``all()`` ueber eine leere Liste
        ist wahr, und eine Kampagne ohne Zuordnungen waere damit im selben
        Augenblick "erfolgreich abgeschlossen", in dem sie angelegt wurde.
        Genau diese Art falscher Erfolgsmeldung soll der Lauf nicht geben.
        """
        return bool(self.gruppen) and all(g.fertig for g in self.gruppen)

    @property
    def gruppen_wartend(self) -> int:
        """Gruppen ohne Mitgliedschaft - blockiert, nicht erledigt."""
        return sum(1 for g in self.gruppen if g.wartet)

    @property
    def naechste_gruppe(self) -> Gruppenfortschritt | None:
        """Die erste Gruppe, an der **gearbeitet werden kann** - in Listenreihenfolge.

        Ohne Mitgliedschaft wird uebersprungen, nicht versucht: Ein Kommentar
        in einer Gruppe, in der das Konto nicht Mitglied ist, scheitert nicht
        zufaellig, sondern immer.

        Die Reihenfolge kommt von aussen (``sort_by_rank``) und wird hier
        nicht neu gebildet: Zwei Berechnungen derselben Rangfolge koennen
        auseinanderlaufen, und dann zeigte die Fortschrittsanzeige eine andere
        Gruppe als die, an der gerade gearbeitet wird.
        """
        return next((g for g in self.gruppen if g.bearbeitbar), None)


@dataclass(frozen=True)
class Lauffortschritt:
    """Der ganze Lauf ueber alle eingefrorenen Kampagnen."""

    lauf_id: int
    status: LaufStatus
    kampagnen: list[Kampagnenfortschritt]

    @property
    def kampagnen_gesamt(self) -> int:
        return len(self.kampagnen)

    @property
    def kampagnen_fertig(self) -> int:
        return sum(1 for k in self.kampagnen if k.fertig)

    @property
    def gruppen_gesamt(self) -> int:
        return sum(k.gruppen_gesamt for k in self.kampagnen)

    @property
    def gruppen_fertig(self) -> int:
        return sum(k.gruppen_fertig for k in self.kampagnen)

    @property
    def kommentare_veroeffentlicht(self) -> int:
        return sum(k.kommentare_veroeffentlicht for k in self.kampagnen)

    @property
    def kommentare_ziel(self) -> int:
        return sum(k.kommentare_ziel for k in self.kampagnen)

    @property
    def fertig(self) -> bool:
        """Alles durch. Siehe ``Kampagnenfortschritt.fertig`` zur leeren Liste."""
        return bool(self.kampagnen) and all(k.fertig for k in self.kampagnen)

    @property
    def gruppen_wartend(self) -> int:
        return sum(k.gruppen_wartend for k in self.kampagnen)

    @property
    def naechste_kampagne(self) -> Kampagnenfortschritt | None:
        """Streng der Reihe nach: erst wenn eine fertig ist, kommt die naechste.

        Eine Kampagne, in der nur noch Gruppen ohne Mitgliedschaft warten,
        ist nicht fertig - aber sie gibt auch nichts her. ``naechster_schritt``
        geht deshalb zur naechsten weiter, statt vor ihr stehenzubleiben.
        """
        return next(
            (k for k in self.kampagnen if not k.fertig and k.naechste_gruppe is not None),
            next((k for k in self.kampagnen if not k.fertig), None),
        )


# --- Der naechste Schritt --------------------------------------------------
@dataclass(frozen=True)
class Schritt:
    """Genau eine auszufuehrende Handlung: dieser Kommentar in dieser Gruppe.

    Der Treiber bekommt hier alles, was er braucht, und trifft selbst keine
    Entscheidung mehr. Waere die Wahl der Fassung beim Treiber, gaebe es sie
    zweimal - einmal in der Kommandozeile, einmal im Web -, und die beiden
    koennten verschiedene Fassungen waehlen.
    """

    campaign_id: str
    group_id: str
    nummer: int
    texttyp: Texttyp = Texttyp.KOMMENTAR

    # Nur zur Anzeige - der Treiber soll den Fortschritt nennen koennen, ohne
    # ihn selbst auszurechnen.
    gruppe_name: str = ""
    kommentar_nr: int = 0
    kommentar_ziel: int = ZIEL_JE_GRUPPE


def vorlage_zu_nummer(nummer: int, *, vorlagen: int = VORLAGEN_JE_TOPF) -> int:
    """Welche der fuenf Vorlagen der n-te Kommentar traegt.

    ``1..5 → 1..5``, ``6..10 → 1..5``. Eine Rechnung und kein gespeicherter
    Zeiger: Nach einem Abbruch ergibt dieselbe Nummer wieder dieselbe Vorlage,
    ohne dass sich jemand etwas merken muss.
    """
    return ((nummer - 1) % max(vorlagen, 1)) + 1


def ziel_zu_nummer(nummer: int) -> str:
    """Welches Ziel der n-te Kommentar traegt - ``browser`` oder ``store``.

    Ungerade in den Browser, gerade in den Store: ``1 → browser``,
    ``2 → store``, ``3 → browser`` ... Eine Rechnung und kein gespeicherter
    Zeiger, aus demselben Grund wie bei der Vorlagenwahl - dieselbe Fassung
    ergibt nach einem Abbruch wieder dasselbe Ziel.

    Beide Ziele werden **vollstaendig gezaehlt**; der Unterschied ist allein,
    wohin die Weiterleitung fuehrt. Dadurch laesst sich im Trichter
    unterscheiden, ob ein Mensch ueber den Play Store oder ueber die
    Web-Anwendung kam.
    """
    return "browser" if nummer % 2 == 1 else "store"


def naechste_nummer(
    veroeffentlicht: set[int],
    gescheitert: set[int] | None = None,
    *,
    max_nummer: int = ZIEL_JE_GRUPPE,
) -> int | None:
    """Die kleinste Fassung, die weder heraus noch aufgegeben ist.

    Das ist die ganze "Rotation": 1-2-3-4-5 ergibt sich von selbst, und nach
    einem Abbruch steht dieselbe Nummer wieder an - ohne gespeicherten Zeiger,
    der von der Wirklichkeit abweichen koennte.

    ``gescheitert`` sind die Fassungen, die in diesem Lauf zu oft erfolglos
    waren. Sie werden uebersprungen, statt den Lauf an derselben Stelle
    festzuhalten; ``None`` heisst "keine Fassung ist im Lauf aufgegeben
    worden".
    """
    aufgegeben = gescheitert or set()
    return next(
        (n for n in range(1, max_nummer + 1) if n not in veroeffentlicht and n not in aufgegeben),
        None,
    )


def naechster_schritt(fortschritt: Lauffortschritt) -> Schritt | None:
    """Was jetzt zu tun ist - oder ``None``, wenn der Lauf durch ist.

    Streng sequentiell: erste unfertige Kampagne, darin erste unfertige
    Gruppe, darin naechste Fassung. Nie zwei Kampagnen gleichzeitig.
    """
    kampagne = fortschritt.naechste_kampagne
    if kampagne is None:
        return None

    gruppe = kampagne.naechste_gruppe
    if gruppe is None:
        return None

    nummer = naechste_nummer(
        set(range(1, gruppe.veroeffentlicht + 1)),
        set(gruppe.gescheiterte_fassungen),
    )
    if nummer is None:
        # Alle Fassungen sind heraus oder aufgegeben, die Gruppe gilt aber
        # noch nicht als fertig: Dann ist sie erschoepft, und der Treiber
        # traegt das ein. Ein Schritt waere hier eine Endlosschleife.
        return None

    return Schritt(
        campaign_id=kampagne.campaign_id,
        group_id=gruppe.group_id,
        nummer=nummer,
        gruppe_name=gruppe.name,
        kommentar_nr=gruppe.veroeffentlicht + 1,
        kommentar_ziel=gruppe.ziel,
    )


def gruppe_ist_erschoepft(gruppe: Gruppenfortschritt) -> bool:
    """Hat die Gruppe alle Fassungen verbraucht, ohne voll zu werden?

    Der Treiber fragt das, nachdem ein Schritt ``None`` ergeben hat: Dann ist
    keine Fassung mehr offen, aber das Ziel nicht erreicht - die Gruppe gibt
    nichts mehr her.
    """
    if gruppe.voll or gruppe.erschoepft:
        return False
    return (
        naechste_nummer(
            set(range(1, gruppe.veroeffentlicht + 1)),
            set(gruppe.gescheiterte_fassungen),
        )
        is None
    )


# --- Den Stand aus dem Bestand lesen ---------------------------------------
def lies_fortschritt(store, lauf_id: int, gruppen: dict) -> Lauffortschritt:
    """Baut den ganzen Stand aus den vorhandenen Tabellen.

    Hier faellt zusammen, was oben behauptet wurde: Es wird **gelesen**. Kein
    Zaehler wird fortgeschrieben, nichts muss nach einem Abbruch geradegerueckt
    werden. Die einzigen gespeicherten Angaben sind die eingefrorene
    Kampagnenliste und die Erschoepfung - alles andere ergibt sich.

    ``gruppen`` bringt die Namen mit (``group_id -> Group``); sie kommen aus
    dem Gruppenbestand und nicht aus dem Marketingspeicher, der sie nicht
    kennt. Fehlt eine Gruppe dort, wird ihre Kennung angezeigt - eine Zeile
    ohne Namen ist besser als eine fehlende Zeile.
    """
    from fbgroups.marketing.models import MarketingStatus
    from fbgroups.scoring import sort_by_rank

    # Wo ist das Konto Mitglied? Alles ab ``mitglied`` zaehlt - wer die
    # Zusammenarbeit angebahnt oder abgeschlossen hat, ist erst recht drin.
    # ``beitritt_angefragt`` zaehlt ausdruecklich **nicht**: Eine offene
    # Anfrage ist keine Mitgliedschaft, und Facebook laesst oft wochenlang
    # offen. Wer sie mitzaehlte, liefe genau in die Fehlversuche, die dieser
    # Wert verhindern soll.
    mitgliedschaft = {
        MarketingStatus.MEMBER,
        MarketingStatus.CONTACTED,
        MarketingStatus.INTERESTED,
        MarketingStatus.APPROVED,
        MarketingStatus.ACTIVE,
    }
    ist_mitglied = {
        gid
        for gid, stand in store.load_all_marketing().items()
        if stand.marketing_status in mitgliedschaft
    }

    kopf = store.lauf(lauf_id)
    if kopf is None:
        return Lauffortschritt(lauf_id=lauf_id, status=LaufStatus.GESCHEITERT, kampagnen=[])

    ziel = int(kopf["ziel_je_gruppe"])
    kampagnen: list[Kampagnenfortschritt] = []

    for zeile in store.lauf_kampagnen(lauf_id):
        campaign_id = zeile["campaign_id"]
        kampagne = store.load_campaign(campaign_id)
        stand = store.kommentarstand(campaign_id)
        erschoepft = store.erschoepfte_gruppen(campaign_id)
        gescheitert = store.gescheiterte_kommentarfassungen(campaign_id, MAX_VERSUCHE_JE_FASSUNG)

        links = store.links_for_campaign(campaign_id)
        # Dieselbe Reihenfolge wie in der Arbeitsliste: die besten zuerst.
        # Wird ein Lauf nie zu Ende gefahren, sollen es die richtigen Gruppen
        # gewesen sein. Gruppen ohne Datensatz wandern ans Ende, statt zu
        # verschwinden - eine Gruppe, die nicht in der Liste steht, bekommt
        # nie einen Kommentar.
        bekannt = [link for link in links if link.group_id in gruppen]
        unbekannt = [link for link in links if link.group_id not in gruppen]
        geordnet = sort_by_rank([gruppen[link.group_id] for link in bekannt])
        reihenfolge = [g.group_id for g in geordnet] + [link.group_id for link in unbekannt]

        eintraege = [
            Gruppenfortschritt(
                campaign_id=campaign_id,
                group_id=gid,
                name=(gruppen[gid].name if gid in gruppen and gruppen[gid].name else gid),
                veroeffentlicht=stand.get(gid, 0),
                ziel=ziel,
                erschoepft=gid in erschoepft,
                erschoepft_grund=erschoepft.get(gid, ""),
                gescheiterte_fassungen=frozenset(gescheitert.get(gid, set())),
                mitglied=gid in ist_mitglied,
            )
            for gid in reihenfolge
        ]

        kampagnen.append(
            Kampagnenfortschritt(
                campaign_id=campaign_id,
                name=(kampagne.name if kampagne else campaign_id),
                gruppen=eintraege,
                status=KampagnenLaufStatus(zeile["status"]),
            )
        )

    return Lauffortschritt(
        lauf_id=lauf_id,
        status=LaufStatus(kopf["status"]),
        kampagnen=kampagnen,
    )


# --- Anzeige ---------------------------------------------------------------
def fortschrittstext(fortschritt: Lauffortschritt) -> str:
    """Der laufende Stand als Text - fuer Kommandozeile und Weboberflaeche.

    Eine Quelle, damit beide dasselbe sagen. Die Zahl steht vorn: "3 von 20"
    beantwortet die Frage, "laeuft" beantwortet sie nicht.
    """
    zeilen = [
        f"Automatik: {fortschritt.status.upper()}",
        "",
        f"Kampagnen:  {fortschritt.kampagnen_fertig} / {fortschritt.kampagnen_gesamt}",
        f"Gruppen:    {fortschritt.gruppen_fertig} / {fortschritt.gruppen_gesamt}",
        f"Kommentare: {fortschritt.kommentare_veroeffentlicht} / {fortschritt.kommentare_ziel}",
    ]
    if fortschritt.gruppen_wartend:
        zeilen += [
            "",
            f"{fortschritt.gruppen_wartend} Gruppe(n) warten auf Mitgliedschaft "
            "- dort wird nichts versucht.",
        ]
    kampagne = fortschritt.naechste_kampagne
    if kampagne is not None:
        zeilen += ["", f"Aktuelle Kampagne: {kampagne.name}"]
        gruppe = kampagne.naechste_gruppe
        if gruppe is not None:
            zeilen += [
                f"Aktuelle Gruppe:   {gruppe.name}",
                f"Kommentare:        {gruppe.veroeffentlicht} / {gruppe.ziel}",
            ]
    return "\n".join(zeilen)


def abschlusstext(fortschritt: Lauffortschritt) -> str:
    """Die Abschlussmeldung - und sie behauptet keinen Erfolg, den es nicht gab.

    Erst wenn **jede** Gruppe **jeder** Kampagne durch ist, steht hier
    "erfolgreich abgeschlossen". Ist eine Gruppe nur erschoepft, wird das
    genannt statt verschwiegen: Sie zaehlt als erledigt, nicht als Erfolg.
    """
    erschoepft = sum(k.gruppen_erschoepft for k in fortschritt.kampagnen)

    if not fortschritt.fertig:
        zeilen = [
            "Automatik NICHT vollstaendig abgeschlossen",
            "",
            f"Kampagnen:  {fortschritt.kampagnen_fertig} / {fortschritt.kampagnen_gesamt}",
            f"Gruppen:    {fortschritt.gruppen_fertig} / {fortschritt.gruppen_gesamt}",
            f"Kommentare: {fortschritt.kommentare_veroeffentlicht} / "
            f"{fortschritt.kommentare_ziel}",
        ]
        kampagne = fortschritt.naechste_kampagne
        if kampagne is not None and (gruppe := kampagne.naechste_gruppe) is not None:
            zeilen += [
                "",
                f"Offen bei: {kampagne.name} / {gruppe.name} "
                f"({gruppe.veroeffentlicht} / {gruppe.ziel} Kommentare)",
            ]
        if fortschritt.gruppen_wartend:
            # Der haeufigste Grund, warum ein Lauf frueh endet, und ohne
            # diesen Satz sucht man ihn in der Technik statt im Konto.
            zeilen += [
                "",
                f"{fortschritt.gruppen_wartend} Gruppe(n) ohne Mitgliedschaft. "
                "Dort kann nicht kommentiert werden, solange die Beitrittsanfrage "
                "offen ist - Facebook laesst Nichtmitglieder nicht schreiben.",
                "Beitritt von Hand stellen, dann:  fbgroups marketing set <gruppe> "
                "--status mitglied",
            ]
        return "\n".join(zeilen)

    zeilen = [
        "Automatik erfolgreich abgeschlossen",
        "",
        f"Kampagnen:  {fortschritt.kampagnen_fertig} / {fortschritt.kampagnen_gesamt}",
        f"Gruppen:    {fortschritt.gruppen_fertig} / {fortschritt.gruppen_gesamt}",
        f"Kommentare: {fortschritt.kommentare_veroeffentlicht} / {fortschritt.kommentare_ziel}",
    ]
    if erschoepft:
        zeilen += [
            "",
            f"Davon {erschoepft} Gruppe(n) erschoepft: Sie hatten nicht genug "
            "Beitraege zum Kommentieren.",
        ]
    return "\n".join(zeilen)


__all__ = [
    "MAX_VERSUCHE_JE_FASSUNG",
    "ZIEL_JE_GRUPPE",
    "Gruppenfortschritt",
    "Kampagnenfortschritt",
    "Lauffortschritt",
    "Schritt",
    "abschlusstext",
    "fortschrittstext",
    "gruppe_ist_erschoepft",
    "lies_fortschritt",
    "naechste_nummer",
    "naechster_schritt",
]
