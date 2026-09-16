"""Welche Gruppe ist die richtige? - die Frage vor der Reihenfolge.

## Was dieses Modul ist

Der fehlende Schritt zwischen "wir kennen 314 Gruppen" und "in dieser hier
wird als naechstes gearbeitet". Es beantwortet **eine** Frage: Wie nah steht
diese Gruppe am Zielmarkt der Kampagne - Deutschland nach Syrien, kleine
Gegenstaende, freier Platz im Koffer?

Rein wie ``qualifikation.py``, ``inhalt.py`` und ``entscheidung.py``: kein
Netz, keine Datenbank, kein Playwright. Der Aufrufer reicht die Merkmale
herein und bekommt einen Befund zurueck.

## Warum es das braucht

Bis zum 13.09.2026 sortierte der Lauf allein nach ``sort_by_rank``, also nach
dem Score. Der beantwortet "welche Gruppe ist gut?" - **nicht** "welche
Gruppe ist die richtige". Eine Gemeinschaftsgruppe mit 40.000 Mitgliedern
steht damit vor einer Reisegruppe mit 900, und die 900 sind genau die
Menschen, die einen Mitnehmer suchen. Wer zwanzig geeignete Reisegruppen hat
und zuerst hundert allgemeine Gemeinschaftsgruppen abarbeitet, hat die
Kampagne nicht betrieben, sondern nur beschaeftigt.

## Vier Klassen, und nur die erste ist der Zielmarkt

* ``A`` Reise- und Versandgruppen mit einem genannten Ziel. Dort **ist** das
  Thema, weswegen es die App gibt.
* ``B`` syrische und arabische Gemeinschaftsgruppen in Deutschland. Dort wird
  ueber alles moegliche geredet - Wohnungen, Behoerden, Autos -, und manchmal
  eben auch ueber ein Paket nach Damaskus. Sekundaer, und nur der einzelne
  Beitrag entscheidet.
* ``C`` alles, was irgendeinen Bezug traegt, aber weder das eine noch das
  andere ist: syrische Nachrichten, allgemeine Verkaufsgruppen. Kein Platz,
  an dem gearbeitet wird - hoechstens ein einzelner Beitrag, der ausdruecklich
  danach fragt.
* ``D`` kein erkennbarer Bezug. Wird nicht bearbeitet.

## Gerechnet, nicht gespeichert

Keine Spalte haelt die Klasse. Sie ergibt sich aus dem, was ohnehin im
Bestand steht - Name, Beschreibungstext, Kategorie, Zielgruppe, Stadt -, und
ein gespeichertes Urteil neben seinen eigenen Grundlagen laeuft von ihnen
weg, sobald sich eine aendert. Dieselbe Ueberlegung wie bei
``qualifikation.beurteile`` und beim Lauffortschritt.

Das ist zugleich die Antwort auf die Anforderung vom 13.09.2026: *"Bitte
nicht nur ein Feld priority speichern, das spaeter keine Wirkung hat."* Ein
Feld koennte veralten; eine Rechnung kann es nicht. Was sie beeinflusst,
steht in ``lauf.py``: Reihenfolge der Arbeitsliste, wer eine Beitrittsanfrage
bekommt, und wie viel ein einzelner Beitrag hergeben muss.

## Die Begriffe stehen in der Konfiguration

``config/settings.yaml`` unter ``marketing.zielprioritaet``, die
Kategoriebegriffe in ``config/categories.yaml``, die Zielgruppenbegriffe in
``config/audiences.yaml``. Hier steht die **Regel**, dort die Woerter - wie
ueberall im Projekt. ``regeln_aus_config`` ist die einzige Stelle, die die
Konfiguration kennt; alles darunter ist ohne sie pruefbar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from fbgroups.textnorm import contains_term, normalize


class Zielprioritaet(StrEnum):
    """Wie nah eine Gruppe am Zielmarkt der Kampagne steht.

    Die Reihenfolge ist die Rangfolge der Arbeit, und sie ist keine
    Geschmacksfrage: Sie entscheidet, wohin die naechsten dreihundert
    Beitraege gehen.
    """

    A = "a"
    B = "b"
    C = "c"
    D = "d"


class Region(StrEnum):
    """Wo die Gruppe steht - die zweite Achse der Rangfolge (14.09.2026).

    **Warum es sie gibt.** Die Klasse beantwortet "worum geht es dort?", und
    das genuegte nicht: "نقل من لبنان إلى سورية" ist eine lupenreine
    Versandgruppe mit genanntem Ziel - und beschreibt trotzdem eine Strecke,
    auf der diese App niemandem hilft. Wer nach Klasse allein sortiert,
    arbeitet solche Gruppen vor den deutschen ab.

    **Warum eine eigene Achse und keine neuen Klassen.** ``A-DE``, ``A-EU``,
    ``A-sonst`` waeren drei Werte fuer zwei Fragen; jede Auswertung, jeder
    Filter und jede Beschriftung muesste sie wieder auseinandernehmen.
    Dieselbe Ueberlegung wie bei ``GroupMarketing.bearbeiten`` neben
    ``marketing_status``: Zwei Fragen, zwei Spalten.
    """

    DE = "de"
    EU = "eu"
    UNBEKANNT = "unbekannt"
    AUSSERHALB = "ausserhalb"


#: Rang fuer die Sortierung - klein ist frueh. Neben der Aufzaehlung und nicht
#: darin, dieselbe Trennung wie bei ``qualifikation.BESCHRIFTUNG``: Der Wert
#: gehoert der Datenbank, die Zahl der Sortierung, der Text dem Menschen.
RANG: dict[Zielprioritaet, int] = {
    Zielprioritaet.A: 0,
    Zielprioritaet.B: 1,
    Zielprioritaet.C: 2,
    Zielprioritaet.D: 3,
}

#: **``UNBEKANNT`` steht vor ``AUSSERHALB``, und das ist der ganze Punkt.**
#: Eine Gruppe, die kein Land nennt, ist keine Gruppe ausserhalb Europas -
#: sie ist eine, ueber deren Land wir nichts wissen. Die meisten deutschen
#: Reisegruppen schreiben "Deutschland" nirgends hin; sie deswegen hinter
#: eine libanesische Versandgruppe zu stellen hiesse, sie fuer eine
#: Auslassung zu bestrafen. Dieselbe Unterscheidung wie ueberall hier:
#: "nicht gemessen" ist etwas anderes als "gemessen und schlecht".
RANG_REGION: dict[Region, int] = {
    Region.DE: 0,
    Region.EU: 1,
    Region.UNBEKANNT: 2,
    Region.AUSSERHALB: 3,
}

BESCHRIFTUNG_REGION: dict[Region, str] = {
    Region.DE: "Deutschland",
    Region.EU: "uebriges Europa",
    Region.UNBEKANNT: "Land unbekannt",
    Region.AUSSERHALB: "ausserhalb Europas",
}

BESCHRIFTUNG: dict[Zielprioritaet, str] = {
    Zielprioritaet.A: "A - Reise & Versand",
    Zielprioritaet.B: "B - Gemeinschaft",
    Zielprioritaet.C: "C - allgemein",
    Zielprioritaet.D: "D - ohne Bezug",
}

#: Welche Klassen ueberhaupt bearbeitet werden. ``D`` steht nicht darin - eine
#: Gruppe ohne jeden Bezug ist kein Platz fuer einen Beitrag, und sie zu
#: versuchen kostet einen Versuch aus einem Konto, an dem alles haengt.
BEARBEITBAR: frozenset[Zielprioritaet] = frozenset(
    {Zielprioritaet.A, Zielprioritaet.B, Zielprioritaet.C}
)

#: Welche Klassen eine **Beitrittsanfrage** wert sind. ``C`` steht bewusst
#: nicht darin: Punkt 3 der Anforderung sagt "nicht die gesamte Gruppe aktiv
#: bearbeiten" - beizutreten waere genau das. Eine Beitrittsanfrage ist die
#: riskanteste Handlung des Projekts; sie gehoert an die Gruppen, an denen
#: gearbeitet werden soll, und nicht an die, in denen vielleicht einmal ein
#: einzelner Beitrag passt.
BEITRITT_WERT: frozenset[Zielprioritaet] = frozenset(
    {Zielprioritaet.A, Zielprioritaet.B}
)


@dataclass(frozen=True)
class Regeln:
    """Die Woerter, an denen die Klassen haengen - aus der Konfiguration.

    Getrennt von der Regel selbst, damit ``einstufe`` ohne Konfiguration
    pruefbar bleibt: Ein Test baut sich die drei Listen in vier Zeilen
    zusammen und muss dafuer keine YAML-Datei anlegen. Dieselbe Aufteilung
    wie ``grenzen.Grenzen`` / ``grenzen.einstellungen``.
    """

    #: Die Kennungen der A-Kategorien (``versand``, ``reise``) - fuer den
    #: Abgleich mit ``Group.category``.
    kategorien: frozenset[str] = frozenset()

    #: Alle Begriffe dieser Kategorien. Sie werden **zusaetzlich** gegen den
    #: Namen geprueft, denn ``classify_category`` kuert nur einen Sieger: Eine
    #: Gruppe "Syrer in Berlin - Reisen nach Damaskus" kann als ``community``
    #: im Bestand stehen und trotzdem eine Reisegruppe sein.
    kategoriebegriffe: tuple[str, ...] = ()

    #: Wohin der Weg geht. Ohne eines dieser Worte ist eine Reisegruppe keine
    #: Gruppe fuer uns - "Reisen nach Thailand" traegt dieselbe Kategorie.
    ziele: tuple[str, ...] = ()

    #: Woher. Ein Weg hat zwei Enden; wer beide nennt, beschreibt den Markt.
    #: Das sind die **deutschen** Woerter; sie belegen zugleich
    #: ``Region.DE``.
    herkunft: tuple[str, ...] = ()

    #: Die Namen der deutschen Staedte aus ``cities.yaml`` - alle, auch die
    #: aus Phase 2. ``phase`` entscheidet, wonach **gesucht** wird; ob eine
    #: Stadt in Deutschland liegt, entscheidet sie nicht.
    #:
    #: Sie stehen neben ``Merkmale.stadt`` und nicht statt ihrer: Jenes ist
    #: die **erkannte** Stadt aus dem Bestand und damit der bessere Beleg,
    #: dieses faengt den Fall ab, in dem die Erkennung nie gelaufen ist. Ohne
    #: sie galt "مشاوير برلين - بيروت - دمشق" als aussereuropaeisch, weil nur
    #: Beirut in einer Laenderliste stand - eine Berliner Gruppe, verloren an
    #: einen Zwischenstopp.
    staedte: tuple[str, ...] = ()

    #: Die uebrigen europaeischen Laender (Oesterreich, Schweden, ...).
    #: Zweite Wahl und nicht dritte: Ein Mitnehmer von Wien nach Damaskus
    #: nimmt dieselbe Sendung mit wie einer von Berlin - er ist nur nicht der
    #: Markt, in dem die Kampagne zuerst arbeitet.
    europa: tuple[str, ...] = ()

    #: Laender, die den Weg **an Europa vorbei** beschreiben (Libanon,
    #: Tuerkei, Aegypten ...). Sie sind der Grund, warum es diese Achse gibt:
    #: "نقل من لبنان إلى سورية" traegt Thema und Ziel und meint trotzdem eine
    #: Strecke, auf der diese App niemandem hilft.
    ausserhalb: tuple[str, ...] = ()

    #: Die Zielgruppen, die eine Gemeinschaftsgruppe ausmachen (``syrians``,
    #: ``arabs``) - fuer den Abgleich mit ``Group.audience_tags``.
    audiences: frozenset[str] = frozenset()

    #: Ihre Begriffe, aus demselben Grund wie ``kategoriebegriffe``: Eine
    #: Gruppe kann arabisch heissen, ohne dass die Erkennung gelaufen ist.
    audiencebegriffe: tuple[str, ...] = ()


@dataclass(frozen=True)
class Merkmale:
    """Was ueber eine Gruppe bekannt ist - so viel, wie der Bestand hergibt.

    Bewusst kein ``Group``: Dieses Modul soll ohne den Gruppenbestand
    pruefbar sein, und der Aufrufer weiss besser als wir, woher die Angaben
    kommen. ``aus_group`` uebersetzt den Regelfall.

    Punkt 5 der Anforderung verlangt ausdruecklich mehr als den Namen:
    Beschreibung, Kategorie, erkannte Zielgruppe und Stadt gehen mit ein.
    Was der Bestand nicht hat, fehlt eben - und fehlt sichtbar, nicht als
    geratener Ersatzwert.
    """

    name: str = ""
    beschreibung: str = ""
    kategorie: str | None = None

    nebenkategorien: tuple[str, ...] = ()
    """Die weiteren erkannten Themen (``Group.secondary_categories``).

    Sie zaehlen fuer die Einstufung genauso wie die Hauptkategorie, und das
    ist kein Entgegenkommen: ``classify_category`` kuert **einen** Sieger,
    und bei "Syrer in Berlin - Reisen nach Damaskus" gewinnt der, der in der
    Datei weiter oben steht. Welches von zwei gleich starken Themen das ist,
    darf nicht darueber entscheiden, ob die Gruppe zum Zielmarkt gehoert.
    """

    audiences: tuple[str, ...] = ()

    stadt: str | None = None
    """Die erkannte Stadt - ihr Anzeigename, nicht ihre Kennung.

    ``Group.city`` haelt den deutschen Namen ("Duesseldorf"); der Bestand ist
    so gewachsen (siehe ``vorlagen._stadt_der_gruppe``). Hier zaehlt ohnehin
    nur, **dass** eine erkannt wurde: Sie ist der Beleg fuer den
    Deutschlandbezug, und welche es ist, entscheidet an dieser Stelle nichts.
    """


@dataclass(frozen=True)
class Zielbefund:
    """Die Klasse und ihr Grund - nie das eine ohne das andere.

    Dieselbe Regel wie bei ``Group.score_reason`` und
    ``qualifikation.Befund.grund``: Eine Einstufung, deren Begruendung man
    nicht nachlesen kann, wird nicht nachgeschlagen, sondern geglaubt. Und
    diese hier entscheidet, welche Gruppe ueberhaupt drankommt.
    """

    prioritaet: Zielprioritaet = Zielprioritaet.D
    grund: str = "kein erkennbarer Bezug"
    treffer: tuple[str, ...] = field(default_factory=tuple)

    region: Region = Region.UNBEKANNT
    """In welchem Land die Gruppe arbeitet, so weit erkennbar.

    Steht **neben** der Klasse, nicht darin: "worum geht es dort?" und "wo
    ist das?" sind zwei Fragen, und verrechnet waeren beide unlesbar -
    dieselbe Ueberlegung wie bei ``data_confidence`` neben dem Score.
    """

    region_treffer: tuple[str, ...] = field(default_factory=tuple)
    """Die Woerter, an denen die Region haengt. Nie ein Urteil ohne Beleg."""

    @property
    def rang(self) -> tuple[int, int]:
        """Die Rangfolge der Arbeit: **erst die Klasse, dann das Land.**

        Ein Paar und keine Summe. Die Klasse ist die erste Frage und bleibt
        es: Eine deutsche Gemeinschaftsgruppe steht nicht vor einer
        oesterreichischen Versandgruppe, nur weil sie in Deutschland ist.
        Erst innerhalb einer Klasse entscheidet das Land - also genau die
        geforderte Reihenfolge: alle A in Deutschland, dann alle A im
        uebrigen Europa, und erst danach B.
        """
        return (RANG[self.prioritaet], RANG_REGION[self.region])

    @property
    def beschriftung(self) -> str:
        return BESCHRIFTUNG[self.prioritaet]

    @property
    def region_beschriftung(self) -> str:
        return BESCHRIFTUNG_REGION[self.region]

    @property
    def bearbeitbar(self) -> bool:
        return self.prioritaet in BEARBEITBAR

    @property
    def beitritt_wert(self) -> bool:
        return self.prioritaet in BEITRITT_WERT


def _treffer(text: str, begriffe: tuple[str, ...]) -> list[str]:
    """Welche der Begriffe vorkommen - mit der Strategie ihrer Schrift.

    ``contains_term`` waehlt selbst: lateinisch mit Wortgrenze, arabisch als
    Teilstring. Wer das vereinheitlicht, zerstoert die arabische Erkennung.
    """
    return [begriff for begriff in begriffe if contains_term(text, begriff)]


def bestimme_region(merkmale: Merkmale, regeln: Regeln) -> tuple[Region, tuple[str, ...]]:
    """Wo diese Gruppe arbeitet - ``(Region, Belege)``.

    Die Reihenfolge ist eine Rangfolge und keine Willkuer:

    1. **Deutschland**, sobald eine Stadt aus ``cities.yaml`` erkannt ist,
       eine im Text steht oder ein deutsches Herkunftswort faellt. Die
       Staedteliste ist ausschliesslich deutsch (jede traegt ein
       ``bundesland``), also ist eine Stadt bereits der Beleg.
    2. **Uebriges Europa**, wenn ein europaeisches Land genannt wird.
       Deutschland geht vor: "Versand Deutschland - Oesterreich - Syrien" ist
       eine deutsche Gruppe, die auch Wien bedient, und keine oesterreichische.
    3. **Ausserhalb**, wenn *nur* ein aussereuropaeisches Land genannt wird.
       Das "nur" ist entscheidend - siehe unten.
    4. **Unbekannt**, wenn kein Land vorkommt. Das ist der Regelfall und kein
       Mangel: Die meisten deutschen Gruppen heissen "شحن الى سوريا" und
       nennen Deutschland nirgends.

    **Ein europaeisches Wort schlaegt ein aussereuropaeisches.** "Mitnahme
    Berlin - Beirut - Damaskus" ist eine Gruppe, in der ein Mensch aus
    Deutschland sitzt; sie wegen des Zwischenstopps herabzustufen hiesse, eine
    richtige Gruppe an ein Detail zu verlieren. Umgekehrt bleibt "نقل من لبنان
    إلى سورية" das, was es ist: eine Strecke ohne uns.

    Syrien selbst zaehlt **nicht** als Herkunft - es ist das Ziel, und jede
    Gruppe hier nennt es. Es steht deshalb in ``ziele`` und in keiner der
    Laenderlisten; stuende es in ``ausserhalb``, waere jede Gruppe des
    Zielmarkts aussereuropaeisch.
    """
    text = normalize(f"{merkmale.name} {merkmale.beschreibung}")

    if merkmale.stadt:
        return Region.DE, (merkmale.stadt,)
    if deutsch := _treffer(text, regeln.herkunft + regeln.staedte):
        return Region.DE, tuple(deutsch[:2])
    if europa := _treffer(text, regeln.europa):
        return Region.EU, tuple(europa[:2])
    if aussen := _treffer(text, regeln.ausserhalb):
        return Region.AUSSERHALB, tuple(aussen[:2])
    return Region.UNBEKANNT, ()


def einstufe(merkmale: Merkmale, regeln: Regeln) -> Zielbefund:
    """Die Klasse **einer** Gruppe. Rein, ohne Speicher, ohne Konfiguration.

    Die Reihenfolge der Pruefungen ist die Rangfolge selbst - die erste, die
    zutrifft, gewinnt:

    1. **A** - Reise oder Versand **und** ein genanntes Ziel. Beides muss
       zusammenkommen: "Reisegruppe" allein kann Thailand meinen, "Syrien"
       allein ist eine Nachrichtenseite. Erst die Kombination beschreibt den
       Markt der App.
    2. **B** - eine syrische oder arabische Gemeinschaft **in Deutschland**.
       Der Deutschlandbezug ist der Unterschied zu C: Eine Gruppe ueber
       Syrien, die Deutschland nie erwaehnt, ist keine Gemeinschaft hier.
       Erkannt wird er an einer Stadt aus ``cities.yaml`` oder an einem
       Herkunftswort - eine Gemeinschaftsgruppe nennt beides fast immer, denn
       sie heisst danach.
    3. **C** - irgendein Bezug, aber keiner der beiden. Kein Platz, an dem
       gearbeitet wird; hoechstens ein einzelner Beitrag, der ausdruecklich
       danach fragt.
    4. **D** - gar nichts. Wird nicht bearbeitet.

    **Der Name wiegt schwerer als der Beschreibungstext**, wie ueberall im
    Projekt: Ein Name sagt, wofuer eine Gruppe da ist; ein Beschreibungstext
    stammt bei Facebook-Gruppen oft aus einem einzelnen Beitrag. Fuer die
    A-Klasse muss das Thema deshalb **im Namen** stehen oder als Kategorie
    erkannt sein - ein Beitrag ueber ein Paket macht aus einer
    Wohnungsgruppe keine Versandgruppe.
    """
    name = normalize(merkmale.name)
    text = normalize(f"{merkmale.name} {merkmale.beschreibung}")
    region, region_treffer = bestimme_region(merkmale, regeln)

    # --- A: das Thema, das Ziel und ein Weg, der durch Europa fuehrt ----
    kategorien = {merkmale.kategorie or "", *merkmale.nebenkategorien}
    kategorie_passt = bool(kategorien & regeln.kategorien)
    thema_im_namen = _treffer(name, regeln.kategoriebegriffe)
    ziel = _treffer(text, regeln.ziele)

    if (kategorie_passt or thema_im_namen) and ziel:
        thema = thema_im_namen or [merkmale.kategorie or "kategorie"]
        teile = [", ".join(thema[:2]), ", ".join(ziel[:2])]
        if region_treffer:
            teile.append(", ".join(region_treffer[:1]))

        if region is Region.AUSSERHALB:
            # **Thema und Ziel genuegen nicht.** "نقل من لبنان إلى سورية"
            # traegt beides und beschreibt trotzdem eine Strecke, auf der
            # diese App niemandem hilft: Ihre Nutzer sitzen in Europa. Die
            # Gruppe faellt deshalb aus dem Zielmarkt - nach ``C`` und nicht
            # nach ``D``, denn sie ist nicht bezuglos: Ein einzelner Beitrag,
            # in dem jemand aus Deutschland schreibt, bleibt erreichbar. Was
            # sie verliert, ist die Beitrittsanfrage und der Vortritt, und
            # genau darum geht es.
            return Zielbefund(
                prioritaet=Zielprioritaet.C,
                grund="Reise/Versand, aber Strecke ausserhalb Europas",
                treffer=tuple(t for t in teile if t),
                region=region,
                region_treffer=region_treffer,
            )

        # Die Strecke wird genannt, nicht verlangt: Eine syrische Reisegruppe
        # in Deutschland nennt Deutschland nicht immer im Namen, und sie
        # deswegen herabzustufen hiesse, sie wegen einer Auslassung zu
        # verlieren. Im Grund steht sie trotzdem - sie ist das staerkste
        # Anzeichen, das es gibt. Die Region entscheidet dann ueber die
        # **Reihenfolge** (Deutschland vor Europa vor unbekannt), nicht ueber
        # die Zugehoerigkeit.
        return Zielbefund(
            prioritaet=Zielprioritaet.A,
            grund="Reise/Versand + Ziel"
            + (f" + {BESCHRIFTUNG_REGION[region]}" if region_treffer else ""),
            treffer=tuple(t for t in teile if t),
            region=region,
            region_treffer=region_treffer,
        )

    # --- B: Gemeinschaft in Deutschland --------------------------------
    zielgruppe = [tag for tag in merkmale.audiences if tag in regeln.audiences]
    zielgruppe_im_text = _treffer(text, regeln.audiencebegriffe)
    hat_zielgruppe = bool(zielgruppe or zielgruppe_im_text)
    deutschlandbezug = bool(merkmale.stadt) or bool(_treffer(text, regeln.herkunft))

    if hat_zielgruppe and deutschlandbezug:
        belege = zielgruppe or zielgruppe_im_text[:2]
        ort = merkmale.stadt or "Deutschland"
        return Zielbefund(
            prioritaet=Zielprioritaet.B,
            grund="Gemeinschaftsgruppe in Deutschland",
            treffer=(", ".join(belege[:2]), ort),
            # ``B`` verlangt den Deutschlandbezug ohnehin - die Region ist
            # hier also stets ``DE`` und steht trotzdem da: Eine Auskunft,
            # die nur manchmal gefuellt ist, wird beim Lesen zur Raterei.
            region=region,
            region_treffer=region_treffer,
        )

    # --- C: irgendein Bezug --------------------------------------------
    # Alles, was eines der drei Anzeichen traegt, aber nicht beide, die eine
    # der oberen Klassen verlangt: eine syrische Nachrichtenseite (Zielgruppe
    # ohne Deutschland), eine Reisegruppe ohne Ziel, eine deutsche Gruppe mit
    # einem Syrienwort im Text. Bearbeitet wird sie nicht - aber ein einzelner
    # Beitrag, der ausdruecklich nach einem Mitnehmer fragt, bleibt erreichbar.
    anzeichen = []
    if hat_zielgruppe:
        anzeichen.append("Zielgruppe")
    if ziel:
        anzeichen.append("Ziel")
    if kategorie_passt or thema_im_namen:
        anzeichen.append("Reise/Versand")
    if anzeichen:
        return Zielbefund(
            prioritaet=Zielprioritaet.C,
            grund="nur " + " + ".join(anzeichen),
            treffer=tuple(anzeichen),
            region=region,
            region_treffer=region_treffer,
        )

    return Zielbefund(region=region, region_treffer=region_treffer)


def aus_group(group, regeln: Regeln) -> Zielbefund:  # noqa: ANN001 - Group
    """Der Regelfall: die Klasse einer Gruppe aus dem Bestand.

    Die Uebersetzung steht hier und nicht in ``einstufe``, damit die Regel
    selbst ohne ``models.Group`` pruefbar bleibt - dieselbe Aufteilung wie
    zwischen ``lies_regeln`` und ``fetch_group_html``.
    """
    return einstufe(
        Merkmale(
            name=group.name or "",
            beschreibung=group.description_snippet or "",
            kategorie=group.category,
            nebenkategorien=tuple(group.secondary_categories or ()),
            audiences=tuple(group.audience_tags or ()),
            # ``Group.city`` haelt den Anzeigenamen, nicht die Kennung - hier
            # zaehlt ohnehin nur, dass eine Stadt erkannt wurde.
            stadt=group.city,
        ),
        regeln,
    )


def regeln_aus_config(config) -> Regeln:  # noqa: ANN001 - AppConfig, ohne Import
    """Die einzige Stelle dieses Moduls, die die Konfiguration kennt.

    Die Begriffe kommen aus drei Dateien, und jede ist dort die fachliche
    Wahrheit: die Kategoriebegriffe aus ``categories.yaml``, die
    Zielgruppenbegriffe aus ``audiences.yaml``, Ziele und Herkunft aus
    ``settings.yaml``. Eine vierte Liste hier waere eine vierte Wahrheit.

    Eine Kategorie- oder Zielgruppenkennung, die es nicht gibt, wird
    **uebergangen** und von ``config-check`` gemeldet - dieselbe Behandlung
    wie bei einem Gewicht fuer einen Bestandteil, den es nicht gibt: Ein
    Tippfehler ist keine Erweiterung.
    """
    block = config.get("marketing", "zielprioritaet", default={}) or {}

    kategorien = {str(k) for k in (block.get("kategorien") or [])}
    begriffe: list[str] = []
    for kategorie in config.categories:
        if kategorie.id in kategorien:
            begriffe.extend(kategorie.all_terms())

    audiences = {str(a) for a in (block.get("audiences") or [])}
    audiencebegriffe: list[str] = []
    for kennung, audience in config.audiences.items():
        if kennung in audiences:
            audiencebegriffe.extend(audience.all_terms())

    return Regeln(
        kategorien=frozenset(kategorien),
        kategoriebegriffe=tuple(begriffe),
        ziele=tuple(str(z) for z in (block.get("ziele") or [])),
        herkunft=tuple(str(h) for h in (block.get("herkunft") or [])),
        staedte=tuple(
            name for stadt in config.cities.values() for name in stadt.all_names() if name
        ),
        europa=tuple(str(e) for e in (block.get("europa") or [])),
        ausserhalb=tuple(str(a) for a in (block.get("ausserhalb") or [])),
        audiences=frozenset(audiences),
        audiencebegriffe=tuple(audiencebegriffe),
    )


# --- Was eine Klasse von einem Beitrag verlangt ----------------------------
#: Vorgaben, falls ``settings.yaml`` nichts sagt. Sie sind die vorsichtigen:
#: In einer Gemeinschaftsgruppe muss der Bezug belegt sein, in einer
#: allgemeinen zusaetzlich die Strecke genannt. Der Gedanke ist derselbe wie
#: bei ``entscheidung.Erlaubnis``: Wer nichts gesagt hat, bekommt die engere
#: Regel und nicht die weitere.
_VORGABE_RELEVANZ: dict[Zielprioritaet, str] = {
    Zielprioritaet.A: "mittel",
    Zielprioritaet.B: "hoch",
    Zielprioritaet.C: "hoch",
}


def anspruch_aus_config(config) -> dict:  # noqa: ANN001 - AppConfig
    """Je Klasse: welche Relevanz ein Beitrag mindestens haben muss.

    Zurueck kommt ``{Zielprioritaet: (Relevanz, verlangt_strecke)}``. Der
    Import von ``inhalt`` steht hier und nicht oben: Dieses Modul urteilt
    ueber **Gruppen**, jenes ueber **Beitraege**, und die Richtung der
    Abhaengigkeit soll sichtbar bleiben.

    ``D`` fehlt in der Tabelle, und das ist kein Versehen: Dort wird gar
    nicht geantwortet, also gibt es auch keine Schwelle. Ein Eintrag waere
    die Behauptung, es gaebe einen Fall, in dem doch geantwortet wird.
    """
    from fbgroups.marketing.inhalt import Relevanz

    block = config.get("marketing", "zielprioritaet", default={}) or {}
    stufen = block.get("mindestrelevanz") or {}
    strecke = bool(block.get("c_verlangt_strecke", True))

    tabelle: dict[Zielprioritaet, tuple[Relevanz, bool]] = {}
    for klasse, vorgabe in _VORGABE_RELEVANZ.items():
        roh = str(stufen.get(klasse.value, vorgabe)).strip().lower()
        try:
            stufe = Relevanz(roh)
        except ValueError:
            stufe = Relevanz(vorgabe)
        tabelle[klasse] = (stufe, strecke and klasse is Zielprioritaet.C)
    return tabelle


__all__ = [
    "BEARBEITBAR",
    "BEITRITT_WERT",
    "BESCHRIFTUNG",
    "BESCHRIFTUNG_REGION",
    "RANG",
    "RANG_REGION",
    "Merkmale",
    "Regeln",
    "Region",
    "Zielbefund",
    "Zielprioritaet",
    "anspruch_aus_config",
    "aus_group",
    "bestimme_region",
    "einstufe",
    "regeln_aus_config",
]
