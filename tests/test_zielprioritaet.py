"""Die Zielprioritaet: welche Gruppe zuerst, und wo ueberhaupt nicht.

Die Anforderung vom 13.09.2026 in Tests. Ihr Kern ist ein Satz: *"Bitte nicht
nur ein Feld priority speichern, das spaeter keine Wirkung hat."* Geprueft
wird deshalb nicht die Einstufung allein, sondern was sie **bewirkt** -
Reihenfolge der Arbeit, wer eine Beitrittsanfrage bekommt, wie viel ein
einzelner Beitrag hergeben muss.
"""

from __future__ import annotations

import pytest

from fbgroups.marketing import entscheidung as entscheidung_modul
from fbgroups.marketing import inhalt, lauf, zielgruppe
from fbgroups.marketing.inhalt import Anlass, Relevanz
from fbgroups.marketing.models import PostStatus, Texttyp
from fbgroups.marketing.zielgruppe import Merkmale, Zielprioritaet

# Ein kleiner, vollstaendiger Regelsatz - vier Zeilen statt einer YAML-Datei.
# Genau dafuer steht ``Regeln`` neben ``einstufe``: Die Regel ist ohne
# Konfiguration pruefbar.
REGELN = zielgruppe.Regeln(
    kategorien=frozenset({"reise", "versand"}),
    kategoriebegriffe=("Reise", "Reisen", "Flug", "Versand", "Paket", "سفر", "شحن"),
    ziele=("Syrien", "Damaskus", "سوريا", "الشام"),
    herkunft=("Deutschland", "Berlin", "المانيا"),
    audiences=frozenset({"syrians", "arabs"}),
    audiencebegriffe=("Syrer", "Araber", "سوريين"),
)


# --- Die Einstufung selbst -------------------------------------------------
@pytest.mark.parametrize(
    ("name", "kategorie", "audiences", "stadt", "erwartet"),
    [
        # A: Thema UND Ziel. Erst beides zusammen beschreibt den Markt.
        ("Reisen Deutschland Syrien", "reise", (), None, Zielprioritaet.A),
        ("Versand nach Syrien", "versand", (), None, Zielprioritaet.A),
        ("شحن من ألمانيا إلى سوريا", "versand", (), None, Zielprioritaet.A),
        # Auch dann A, wenn die Kategorieerkennung die Gemeinschaft gekuert
        # hat: Der Name traegt das Thema, und "community" gewinnt nur, weil
        # es in categories.yaml irgendwo steht.
        ("Syrer in Berlin - Reisen nach Damaskus", "community", ("syrians",), "Berlin",
         Zielprioritaet.A),
        # B: Gemeinschaft in Deutschland.
        ("Syrer in Berlin", "community", ("syrians",), "Berlin", Zielprioritaet.B),
        ("سوريين في المانيا", "community", ("syrians",), None, Zielprioritaet.B),
        # C: ein Anzeichen, aber nicht beide. Syrische Nachrichten sind keine
        # Gemeinschaft **hier**, eine Reisegruppe ohne Ziel keine fuer uns.
        ("Nachrichten aus Syrien", None, (), None, Zielprioritaet.C),
        ("Reisegruppe Thailand", "reise", (), None, Zielprioritaet.C),
        # D: gar nichts.
        ("Wohnungen in Bochum", "wohnen", (), None, Zielprioritaet.D),
    ],
)
def test_die_vier_klassen(name, kategorie, audiences, stadt, erwartet) -> None:
    befund = zielgruppe.einstufe(
        Merkmale(name=name, kategorie=kategorie, audiences=audiences, stadt=stadt),
        REGELN,
    )
    assert befund.prioritaet is erwartet, befund.grund


def test_das_thema_muss_im_namen_stehen_nicht_im_beschreibungstext() -> None:
    """Ein Beitrag ueber ein Paket macht aus einer Wohnungsgruppe keine Versandgruppe.

    Der Beschreibungstext stammt bei Facebook-Gruppen oft aus einem einzelnen
    Beitrag - derselbe Grund, aus dem er ueberall im Projekt nur die halbe
    Konfidenz traegt. Fuer die A-Klasse zaehlt deshalb allein der Name (oder
    die erkannte Kategorie).
    """
    befund = zielgruppe.einstufe(
        Merkmale(
            name="Wohnungen Berlin",
            beschreibung="Wer faehrt nach Syrien und kann ein Paket mitnehmen?",
            kategorie="wohnen",
        ),
        REGELN,
    )
    assert befund.prioritaet is not Zielprioritaet.A


def test_eine_nebenkategorie_zaehlt_wie_die_hauptkategorie() -> None:
    """``classify_category`` kuert einen Sieger - das darf nicht entscheiden.

    Bei zwei gleich starken Treffern gewinnt der, der in ``categories.yaml``
    weiter oben steht. Welches von beiden das ist, ist eine Eigenschaft der
    Datei und keine der Gruppe.
    """
    befund = zielgruppe.einstufe(
        Merkmale(
            name="Gemeinschaft und Fahrten",
            kategorie="community",
            nebenkategorien=("reise",),
            beschreibung="nach Syrien",
        ),
        REGELN,
    )
    assert befund.prioritaet is Zielprioritaet.A


# --- Was die Einstufung bewirkt --------------------------------------------
def _gruppe(gid: str, prioritaet: Zielprioritaet) -> lauf.Gruppenfortschritt:
    return lauf.Gruppenfortschritt(
        campaign_id="k",
        group_id=gid,
        name=f"Gruppe {gid}",
        veroeffentlicht=0,
        mitglied=True,
        mitgliedschaft_noetig=False,
        zielprioritaet=prioritaet,
        post_status=PostStatus.VEROEFFENTLICHT,
    )


def test_prioritaet_a_wird_zuerst_bearbeitet() -> None:
    """Der Kern der Anforderung: zwanzig A-Gruppen vor hundert B-Gruppen.

    Die Liste kommt score-sortiert herein (hier: B steht vorn). Die
    Zielprioritaet ordnet sie um - **stabil**, damit der Score innerhalb
    einer Klasse weiter entscheidet.
    """
    kampagne = lauf.Kampagnenfortschritt(
        campaign_id="k",
        name="k",
        gruppen=[
            _gruppe("b1", Zielprioritaet.B),
            _gruppe("c1", Zielprioritaet.C),
            _gruppe("a1", Zielprioritaet.A),
            _gruppe("b2", Zielprioritaet.B),
            _gruppe("a2", Zielprioritaet.A),
        ],
    )
    assert [g.group_id for g in kampagne.arbeitsliste] == ["a1", "a2", "b1", "b2", "c1"]
    assert kampagne.naechste_gruppe is not None
    assert kampagne.naechste_gruppe.group_id == "a1"


def test_in_klasse_d_wird_nichts_versucht() -> None:
    """Eine Gruppe ohne erkennbaren Bezug ist kein Platz fuer einen Beitrag.

    Sie faellt aus ``bearbeitbar``, nicht aus dem Bestand: Sie bleibt stehen,
    behaelt ihren Tracking-Code und wird im naechsten Lauf neu beurteilt -
    dieselbe Behandlung wie bei einer gesperrten Gruppe.
    """
    ohne_bezug = _gruppe("d1", Zielprioritaet.D)
    assert ohne_bezug.bearbeitbar is False

    kampagne = lauf.Kampagnenfortschritt(
        campaign_id="k", name="k", gruppen=[ohne_bezug, _gruppe("c1", Zielprioritaet.C)]
    )
    assert kampagne.naechste_gruppe is not None
    assert kampagne.naechste_gruppe.group_id == "c1"


def test_nur_a_und_b_bekommen_eine_beitrittsanfrage() -> None:
    """Beitreten heisst "aktiv bearbeiten" - und das ist bei C ausgeschlossen.

    In einer allgemeinen Gruppe passt hoechstens einmal ein einzelner
    Beitrag. Dafuer beizutreten waere die riskanteste Handlung des Projekts
    fuer die schwaechste Aussicht.
    """

    def kandidat(gid: str, prioritaet: Zielprioritaet) -> lauf.Gruppenfortschritt:
        return lauf.Gruppenfortschritt(
            campaign_id="k",
            group_id=gid,
            name=gid,
            veroeffentlicht=0,
            beitritt_noetig=True,
            regeln_gelesen=True,
            zielprioritaet=prioritaet,
        )

    kampagne = lauf.Kampagnenfortschritt(
        campaign_id="k",
        name="k",
        gruppen=[
            kandidat("a1", Zielprioritaet.A),
            kandidat("b1", Zielprioritaet.B),
            kandidat("c1", Zielprioritaet.C),
            kandidat("d1", Zielprioritaet.D),
        ],
    )
    assert [g.group_id for g in kampagne.beitritt_offen] == ["a1", "b1"]


def test_die_regeln_werden_vor_der_anfrage_gelesen() -> None:
    """Erst nachsehen, was die Gruppe erlaubt - dann anfragen.

    Vorher ging die Anfrage an jede Gruppe, und ob dort ueberhaupt
    kommentiert werden darf, stellte sich Tage spaeter heraus: nach der
    Aufnahme, nach dem ersten Versuch, nach dem ersten Fehlschlag.
    """
    ungelesen = lauf.Gruppenfortschritt(
        campaign_id="k",
        group_id="111",
        name="111",
        veroeffentlicht=0,
        beitritt_noetig=True,
        regeln_gelesen=False,
        zielprioritaet=Zielprioritaet.A,
    )
    kampagne = lauf.Kampagnenfortschritt(campaign_id="k", name="k", gruppen=[ungelesen])

    assert [g.group_id for g in kampagne.regeln_offen] == ["111"]
    assert kampagne.beitritt_offen == [], "ohne gelesene Regeln keine Anfrage"
    assert kampagne.phase(beitritt_frei=True) is lauf.Phase.REGELN

    fortschritt = lauf.Lauffortschritt(
        lauf_id=1, status=lauf.LaufStatus.LAEUFT, kampagnen=[kampagne]
    )
    schritt = lauf.naechster_schritt(fortschritt)
    assert schritt is not None
    assert schritt.art is lauf.Schrittart.REGELN
    assert schritt.group_id == "111"


def test_ohne_regelpflicht_gilt_die_alte_reihenfolge() -> None:
    """Der Schalter ist kein Zierrat: Manche Gruppenseite laesst sich nie lesen.

    Anmeldewand, geschlossene Gruppe - dann bliebe ``regeln_gelesen`` fuer
    immer falsch, und die Anfrage ginge nie hinaus.
    """
    ungelesen = lauf.Gruppenfortschritt(
        campaign_id="k",
        group_id="111",
        name="111",
        veroeffentlicht=0,
        beitritt_noetig=True,
        regeln_gelesen=False,
        regeln_noetig=False,
        zielprioritaet=Zielprioritaet.A,
    )
    kampagne = lauf.Kampagnenfortschritt(campaign_id="k", name="k", gruppen=[ungelesen])

    assert kampagne.regeln_offen == []
    assert [g.group_id for g in kampagne.beitritt_offen] == ["111"]


def test_die_tagesmenge_je_gruppe_ist_kein_urteil_ueber_die_gruppe() -> None:
    """Voll fuer heute heisst nicht erschoepft - morgen ist sie offen.

    Die Tagesmenge schuetzt das Konto, diese Zahl die Gruppe: Fuenf
    Kommentare am Tag, alle in derselben Gruppe, sind fuer deren Leser
    dasselbe Bild wie fuenfzig.
    """
    voll = lauf.Gruppenfortschritt(
        campaign_id="k",
        group_id="111",
        name="111",
        veroeffentlicht=0,
        mitglied=True,
        heute_in_gruppe=1,
        gruppenlimit=1,
    )
    assert voll.tageslimit_erreicht is True
    assert voll.bearbeitbar is False
    assert voll.erschoepft is False, "kein Urteil ueber die Gruppe"
    assert voll.gesperrt is False, "und kein Urteil ueber ihre Regeln"

    ohne_schranke = lauf.Gruppenfortschritt(
        campaign_id="k", group_id="111", name="111", veroeffentlicht=0,
        mitglied=True, heute_in_gruppe=9, gruppenlimit=0,
    )
    assert ohne_schranke.tageslimit_erreicht is False, "0 heisst ohne Schranke"


# --- Was ein Beitrag hergeben muss -----------------------------------------
def test_die_gruppenklasse_entscheidet_ueber_die_schwelle() -> None:
    """Derselbe Beitrag, zwei Orte, zwei Antworten.

    In einer Reisegruppe ist die Frage nach einem Koffer das Thema des
    Hauses; in einer allgemeinen Gruppe ist sie ein Zufall. Der Unterschied
    liegt nicht am Satz, sondern am Ort.
    """
    befund = inhalt.lies("بدي ابعت امانة صغيرة")
    assert befund.relevanz is Relevanz.MITTEL

    erlaubt = entscheidung_modul.Erlaubnis(werbung=True, links=False, regeln_gelesen=True)

    locker = entscheidung_modul.entscheide(
        befund, erlaubt, entscheidung_modul.Anspruch(mindestrelevanz=Relevanz.MITTEL)
    )
    streng = entscheidung_modul.entscheide(
        befund, erlaubt, entscheidung_modul.Anspruch(mindestrelevanz=Relevanz.HOCH)
    )

    assert locker.antwortet is True
    assert streng.antwortet is False
    assert "zu schwach" in streng.grund


def test_klasse_c_verlangt_die_ausgeschriebene_strecke() -> None:
    """"Klarer und konkreter Bedarf" - und das heisst: beide Enden des Wegs.

    Ein Beitrag ueber ein Paket kann in einer allgemeinen Gruppe alles
    moegliche meinen. Verlangt wird der Weg, den die App vermittelt.
    """
    ohne_herkunft = inhalt.lies("بدي ابعت غرض صغير ع سوريا")
    mit_herkunft = inhalt.lies("بدي ابعت غرض صغير من ألمانيا ع سوريا")
    assert ohne_herkunft.relevanz is Relevanz.HOCH
    assert ohne_herkunft.strecke is False
    assert mit_herkunft.strecke is True

    erlaubt = entscheidung_modul.Erlaubnis(werbung=True, regeln_gelesen=True)
    anspruch_c = entscheidung_modul.Anspruch(
        mindestrelevanz=Relevanz.HOCH, verlangt_strecke=True
    )

    assert entscheidung_modul.entscheide(ohne_herkunft, erlaubt, anspruch_c).antwortet is False
    assert entscheidung_modul.entscheide(mit_herkunft, erlaubt, anspruch_c).antwortet is True


# --- Anlass und Linkmodus --------------------------------------------------
def test_eine_reiseankuendigung_allein_ist_kein_anlass() -> None:
    """Punkt 11 der Anforderung, woertlich.

    "Ich fliege naechste Woche nach Syrien" ist eine Mitteilung. Erst der
    Halbsatz danach macht daraus eine Gelegenheit - und genau den erkennt
    ``Anlass``.
    """
    blosse_ankuendigung = inhalt.lies("Ich fliege naechste Woche nach Syrien.")
    mit_platz = inhalt.lies(
        "Ich fliege naechste Woche nach Syrien und habe noch Platz im Koffer."
    )

    assert blosse_ankuendigung.anlass is Anlass.KEINER
    assert mit_platz.anlass is Anlass.PLATZ_IM_KOFFER


@pytest.mark.parametrize(
    ("text", "erwartet"),
    [
        ("مين مسافر ع سوريا؟ بدي ابعت غرض", Anlass.SUCHT_REISENDEN),
        ("بدي ابعت هدية لأهلي بسوريا", Anlass.GESCHENK),
        ("كيف ابعت دواء لأمي بالشام؟", Anlass.MEDIKAMENTE),
        ("شو افضل طريقة لارسال اغراض من برلين لدمشق؟", Anlass.VERSANDWEG),
        ("بدي ارسل وثائق من المانيا ع سوريا", Anlass.GEGENSTAND),
        ("Suche Wohnung in Berlin, 2 Zimmer", Anlass.KEINER),
        ("مبروك للعروسين", Anlass.KEINER),
    ],
)
def test_der_anlass_waehlt_die_vorlage(text, erwartet) -> None:
    assert inhalt.lies(text).anlass is erwartet


def test_medikamente_gehen_vor_den_allgemeinen_anlaessen() -> None:
    """Die engste Beobachtung gewinnt - sie sagt etwas, was die anderen nicht sagen.

    Die Antwort zum Medikament traegt einen Satz mehr: Was ueber die Grenze
    darf, entscheidet nicht die App.
    """
    befund = inhalt.lies("مسافر ع الشام الاسبوع الجاي، بقدر آخد دواء لحدا")
    assert befund.anlass is Anlass.MEDIKAMENTE


def test_der_linkmodus_haengt_an_der_antwortart() -> None:
    """Eine Entscheidung, nicht zwei - sonst koennten sie auseinanderlaufen.

    Wer die App nicht nennt, traegt auch keinen Link; wer sie mit Link
    empfiehlt, nennt sie erst recht.
    """
    Linkmodus = entscheidung_modul.Linkmodus
    Antwortart = entscheidung_modul.Antwortart

    assert entscheidung_modul.Entscheidung(art=Antwortart.NO_REPLY).linkmodus is Linkmodus.NO_LINK
    assert (
        entscheidung_modul.Entscheidung(art=Antwortart.HELPFUL_REPLY).linkmodus
        is Linkmodus.NO_LINK
    )
    assert (
        entscheidung_modul.Entscheidung(art=Antwortart.CONTEXTUAL_APP_MENTION).linkmodus
        is Linkmodus.APP_NAME_ONLY
    )
    assert (
        entscheidung_modul.Entscheidung(art=Antwortart.DIRECT_APP_RECOMMENDATION).linkmodus
        is Linkmodus.TRACKING_LINK
    )


def test_ohne_gelesene_regeln_gibt_es_keinen_link() -> None:
    """Die Abwesenheit einer Regel ist keine Erlaubnis, die jemand erteilt hat.

    Dass in einer Gruppe schon einmal ein Link durchging, heisst nicht, dass
    er erlaubt war - und eine ungelesene Seite sagt gar nichts.
    """
    befund = inhalt.lies("مين مسافر من ألمانيا ع سوريا؟ بدي ابعت غرض صغير")
    assert befund.relevanz is Relevanz.HOCH

    ungelesen = entscheidung_modul.Erlaubnis(regeln_gelesen=False)
    entschieden = entscheidung_modul.entscheide(befund, ungelesen)
    assert entschieden.mit_link is False
    assert entschieden.linkmodus is not entscheidung_modul.Linkmodus.TRACKING_LINK


# --- Der Vorrat ------------------------------------------------------------
def test_die_anlassvorlagen_tragen_keinen_link(config) -> None:
    """Der Regelfall ist "kein Link" - viele Gruppen lehnen ihn automatisch ab.

    Der Name (بطريقك) steht darin; die Adresse haengt ``anlasstext``
    mechanisch an, und nur wo die Gruppe Links erlaubt.
    """
    from fbgroups.marketing import vorlagen

    anlaesse = (config.textvorlagen.get("anlaesse") or {}).get("ar") or {}
    assert anlaesse, "config/textvorlagen.yaml hat keinen Anlassvorrat"

    for anlass, fassungen in anlaesse.items():
        vorrat = vorlagen.anlassvorrat(config, "ar", anlass)
        assert len(vorrat) == len(fassungen)
        for fassung in vorrat:
            assert "{link}" not in fassung.text, anlass
            assert "بطريقك" in fassung.text, f"{anlass} nennt die App nicht"


def test_dieselbe_gruppe_bekommt_dieselbe_fassung(config) -> None:
    """Stabil ueber Neustarts - ``blake2b`` statt des gesalzenen ``hash``.

    Derselbe Gedanke wie bei der Vorlagenwahl fuer Beitraege: Der Text soll
    sich nicht unter demjenigen aendern, der ihn gerade gelesen hat.
    """
    from fbgroups.marketing import vorlagen

    daten = vorlagen.Personalisierung(zielgruppe="", stadt="")
    erste = vorlagen.anlasstext(
        config, sprache="ar", anlass="sucht_reisenden", group_id="42", daten=daten
    )
    zweite = vorlagen.anlasstext(
        config, sprache="ar", anlass="sucht_reisenden", group_id="42", daten=daten
    )
    assert erste is not None
    assert erste == zweite


def test_eine_verbrauchte_fassung_wird_nicht_wiederholt(config) -> None:
    """Duplikatkontrolle auf Textebene: nicht zweimal derselbe Satz in einer Gruppe."""
    from fbgroups.marketing import vorlagen

    daten = vorlagen.Personalisierung(zielgruppe="", stadt="")
    erste = vorlagen.anlasstext(
        config, sprache="ar", anlass="sucht_reisenden", group_id="42", daten=daten
    )
    assert erste is not None
    zweite = vorlagen.anlasstext(
        config,
        sprache="ar",
        anlass="sucht_reisenden",
        group_id="42",
        daten=daten,
        bisherige=frozenset({erste[0]}),
    )
    assert zweite is not None
    assert zweite[0] != erste[0]


def test_ein_unbekannter_anlass_ergibt_keinen_text(config) -> None:
    """Leer statt Ausnahme: "dazu haben wir nichts vorbereitet" ist eine Auskunft.

    Der Aufrufer schweigt dann, statt etwas anderes zu sagen - eine
    Ersatzfassung aus einem anderen Topf waere eine Antwort auf eine andere
    Frage.
    """
    from fbgroups.marketing import vorlagen

    daten = vorlagen.Personalisierung(zielgruppe="", stadt="")
    assert (
        vorlagen.anlasstext(
            config, sprache="ar", anlass=Anlass.KEINER.value, group_id="42", daten=daten
        )
        is None
    )


def test_der_link_wird_angehaengt_nicht_erfunden(config) -> None:
    """Genau ein ``{link}``, in eigener Zeile - und erst beim Lesen aufgeloest.

    Der gespeicherte Text traegt den Platzhalter, nie den Tracking-Code:
    ``beitrag.mit_link`` ist weiterhin die einzige Stelle, die ihn einsetzt.
    """
    from fbgroups.marketing import vorlagen

    daten = vorlagen.Personalisierung(zielgruppe="", stadt="")
    treffer = vorlagen.anlasstext(
        config,
        sprache="ar",
        anlass="geschenk",
        group_id="42",
        daten=daten,
        mit_link=True,
    )
    assert treffer is not None
    _schluessel, text = treffer
    assert text.count("{link}") == 1
    assert text.endswith("{link}")
    assert "http" not in text


# --- Die Konfiguration haelt, was sie verspricht ---------------------------
def test_die_kategorien_der_klasse_a_gibt_es_wirklich(config) -> None:
    """Ein Kategoriename, den es nicht gibt, ist ein Tippfehler und keine Erweiterung.

    Dieselbe Regel wie bei einem Score-Gewicht fuer einen Bestandteil, den es
    nicht gibt - nur faellt es hier schwerer auf: Die Klasse A verschwaende
    still, und die Kampagne arbeitete wieder in den Gemeinschaftsgruppen.
    """
    regeln = zielgruppe.regeln_aus_config(config)
    vorhanden = {k.id for k in config.categories}

    assert regeln.kategorien, "marketing.zielprioritaet.kategorien ist leer"
    assert regeln.kategorien <= vorhanden
    assert regeln.audiences <= set(config.audiences)
    assert regeln.ziele and regeln.herkunft


def test_die_schwellen_je_klasse_stehen_in_der_konfiguration(config) -> None:
    """A lockerer als B, B lockerer als C - und D hat gar keine."""
    tabelle = zielgruppe.anspruch_aus_config(config)

    assert tabelle[Zielprioritaet.A][0] is Relevanz.MITTEL
    assert tabelle[Zielprioritaet.B][0] is Relevanz.HOCH
    assert tabelle[Zielprioritaet.C] == (Relevanz.HOCH, True)
    assert Zielprioritaet.D not in tabelle, "in D wird nicht geantwortet"


def test_ein_beitrag_traegt_immer_einen_link_ein_kommentar_nicht() -> None:
    """Die Grenze bleibt, wo sie war - das Neue betrifft nur den Kommentar."""
    from fbgroups.marketing.vorlagen import UngueltigerText, pruefe_platzhalter

    pruefe_platzhalter("Ein Satz ohne Link.", texttyp=Texttyp.KOMMENTAR)
    with pytest.raises(UngueltigerText):
        pruefe_platzhalter("Ein Satz ohne Link.", texttyp=Texttyp.POST)


def test_der_deckel_kann_den_link_ganz_abschalten(config, monkeypatch) -> None:
    """"Kein Link" ist eine Zeile in der Konfiguration, keine Codeaenderung.

    Ein **Deckel**, keine zweite Entscheidung: Er wirkt nur nach unten. Was
    die Gruppe verbietet, bleibt verboten; was sie erlaubt, darf man trotzdem
    sein lassen.
    """
    from fbgroups.marketing import automatik

    Linkmodus = entscheidung_modul.Linkmodus

    class Gedeckelt:
        def __init__(self, wert: str) -> None:
            self._wert = wert

        def __getattr__(self, name: str):
            return getattr(config, name)

        def get(self, *pfad, default=None):
            if pfad == ("marketing", "linkmodus_max"):
                return self._wert
            return config.get(*pfad, default=default)

    offen = Gedeckelt("tracking_link")
    assert automatik._gedeckelt(offen, Linkmodus.TRACKING_LINK) is Linkmodus.TRACKING_LINK

    zu = Gedeckelt("app_name_only")
    assert automatik._gedeckelt(zu, Linkmodus.TRACKING_LINK) is Linkmodus.APP_NAME_ONLY
    # Nach unten laesst er durch - er hebt nichts an.
    assert automatik._gedeckelt(zu, Linkmodus.NO_LINK) is Linkmodus.NO_LINK

    # Ein Tippfehler schaltet nicht heimlich alles ab.
    vertippt = Gedeckelt("kein-link-bitte")
    assert automatik._gedeckelt(vertippt, Linkmodus.TRACKING_LINK) is Linkmodus.TRACKING_LINK


def test_die_regeln_werden_auch_fuer_bestandsmitglieder_gelesen() -> None:
    """Sonst schwiege eine Kampagne in lauter Bestandsgruppen - ohne Grund.

    In einer Gruppe, in der das Konto laengst Mitglied ist, steht keine
    Anfrage mehr aus. Haenge der Regelschritt allein daran, blieben ihre
    Regeln fuer immer ungelesen - und ``Erlaubnis.aus_regeln`` lieferte dort
    dauerhaft ``werbung=False``. Die Folge waere nicht ein vorsichtiger
    Kommentar, sondern **gar keiner**: Fuer ``NO_LINK`` gibt es keinen Vorrat.
    """
    mitglied = lauf.Gruppenfortschritt(
        campaign_id="k",
        group_id="111",
        name="111",
        veroeffentlicht=0,
        mitglied=True,
        beitritt_noetig=False,
        regeln_gelesen=False,
        zielprioritaet=Zielprioritaet.A,
    )
    kampagne = lauf.Kampagnenfortschritt(campaign_id="k", name="k", gruppen=[mitglied])
    assert [g.group_id for g in kampagne.regeln_offen] == ["111"]


def test_ein_konkreter_anlass_belegt_den_bezug_wie_hoch() -> None:
    """"عندي مساحة بالشنطة" nennt kein Ziel - und ist trotzdem der Fall.

    In einer Reisegruppe ist das Ziel selbstverstaendlich und wird nicht
    ausgeschrieben. Der Anlass ist dort der engere Beleg: ``HOCH`` verlangt
    Thema und genanntes Ziel, der Anlass Thema und einen konkreten Halbsatz.
    """
    befund = inhalt.lies("عندي مساحة بالشنطة، مين بدو يبعت شي")
    assert befund.relevanz is Relevanz.MITTEL
    assert befund.anlass is Anlass.PLATZ_IM_KOFFER

    erlaubt = entscheidung_modul.Erlaubnis(werbung=True, regeln_gelesen=True)
    assert entscheidung_modul.soll_app_nennen(befund, erlaubt) is True

    # Ohne Anlass bleibt es bei der alten Regel: "fast" ist der Anfang von Spam.
    ohne = inhalt.lies("Ich fliege naechste Woche nach Damaskus.")
    assert ohne.anlass is Anlass.KEINER
    assert entscheidung_modul.soll_app_nennen(ohne, erlaubt) is False


def test_die_regeln_werden_je_gruppe_gelesen_nicht_als_vorlauf() -> None:
    """Eine Gruppe lesen, in ihr arbeiten - nicht erst alle 254 lesen.

    Am 13.09.2026 stand der Regelschritt zuerst als **Kampagnenphase** da:
    ``regeln_offen`` lieferte jede ungelesene Gruppe, und ``phase`` blieb auf
    ``REGELN``, bis keine mehr uebrig war. Bei einer Kampagne mit 254 Gruppen
    las der Lauf damit eine halbe Stunde, bevor der erste Beitrag hinausging -
    verlangt war aber "vor der Beitrittsanfrage die Regeln pruefen", also je
    Gruppe und nicht als Vorlauf ueber den ganzen Bestand.

    Geprueft wird genau das: Von 254 ungelesenen Gruppen steht **eine** zum
    Lesen an, und nach ihr kommt sofort ihr eigener Textschritt.
    """
    def gruppe(gid: str, *, gelesen: bool = False) -> lauf.Gruppenfortschritt:
        return lauf.Gruppenfortschritt(
            campaign_id="k",
            group_id=gid,
            name=gid,
            veroeffentlicht=0,
            mitglied=True,
            mitgliedschaft_noetig=False,
            regeln_gelesen=gelesen,
            zielprioritaet=Zielprioritaet.A,
            post_fassungen=frozenset({1}),
        )

    viele = lauf.Kampagnenfortschritt(
        campaign_id="k", name="k", gruppen=[gruppe(f"g{i:03}") for i in range(254)]
    )
    assert len(viele.regeln_offen) == 1, "nur die Gruppe, die gleich drankommt"
    assert viele.regeln_offen[0].group_id == "g000"

    erster = lauf.naechster_schritt(
        lauf.Lauffortschritt(lauf_id=1, status=lauf.LaufStatus.LAEUFT, kampagnen=[viele])
    )
    assert erster is not None
    assert erster.art is lauf.Schrittart.REGELN
    assert erster.group_id == "g000"

    # Und sobald ihre Regeln stehen, wird **in ihr** gearbeitet - nicht die
    # naechste gelesen.
    danach = lauf.Kampagnenfortschritt(
        campaign_id="k",
        name="k",
        gruppen=[gruppe("g000", gelesen=True), *(gruppe(f"g{i:03}") for i in range(1, 254))],
    )
    zweiter = lauf.naechster_schritt(
        lauf.Lauffortschritt(lauf_id=1, status=lauf.LaufStatus.LAEUFT, kampagnen=[danach])
    )
    assert zweiter is not None
    assert zweiter.art is lauf.Schrittart.TEXT
    assert zweiter.group_id == "g000"


def test_der_beitrittstakt_haelt_die_arbeit_nicht_mehr_an() -> None:
    """Punkt 16 der Anforderung, und der Grundsatz aus ``grenzen.py``.

    Bis zum 13.09.2026 lieferte ``naechster_schritt`` ``None``, sobald der
    Beitrittstakt noch nicht um war - der Treiber legte sich schlafen. Die
    Begruendung war richtig (die Reihenfolge soll keine blosse Empfehlung
    sein), die Folge falsch: Im Betrieb stand "Beitrittstakt: noch 31 Min"
    auf dem Schirm, und bei 50 offenen Anfragen wurde daraus ein ganzer Tag
    Schlaf fuer **einen** Kommentar.

    Zwei Zusagen auf einmal gebrochen:

    * "Wenn eine Gruppe noch auf die Aufnahme wartet, soll der Runner andere
      bereits freigegebene Gruppen weiterbearbeiten koennen."
    * "Wer wegen einer gebremsten Aktion alles anhaelt, verliert die Arbeit
      dort, wo nichts dagegen spricht."

    Was **bleibt**: Laesst der Takt die Anfrage zu, geht sie vor der Arbeit
    hinaus. Nur das Warten entfaellt.
    """
    from fbgroups.marketing.grenzen import Aktion, Lage
    from fbgroups.marketing.models import Texttyp

    def gruppe(gid: str, **kw) -> lauf.Gruppenfortschritt:
        felder = dict(
            campaign_id="k",
            group_id=gid,
            name=gid,
            veroeffentlicht=0,
            mitglied=True,
            mitgliedschaft_noetig=False,
            regeln_gelesen=True,
            zielprioritaet=Zielprioritaet.A,
            post_fassungen=frozenset({1}),
        )
        felder.update(kw)
        return lauf.Gruppenfortschritt(**felder)

    kampagne = lauf.Kampagnenfortschritt(
        campaign_id="k",
        name="k",
        gruppen=[gruppe("offen", beitritt_noetig=True, mitglied=False), gruppe("arbeit")],
    )

    def stand(wartezeit: str) -> lauf.Lauffortschritt:
        return lauf.Lauffortschritt(
            lauf_id=1,
            status=lauf.LaufStatus.LAEUFT,
            kampagnen=[kampagne],
            aktionen={
                Aktion.BEITRITT: Lage(
                    Aktion.BEITRITT, not wartezeit, wartezeit=wartezeit, rest_heute=40
                ),
                Aktion.POST: Lage(Aktion.POST, True, rest_heute=9),
                Aktion.KOMMENTAR: Lage(Aktion.KOMMENTAR, True, rest_heute=99),
            },
        )

    # Takt frei: die Anfrage geht vor - die Reihenfolge ist unveraendert.
    frei = lauf.naechster_schritt(stand(""))
    assert frei is not None
    assert frei.art is lauf.Schrittart.BEITRITT

    # Takt wartet: gearbeitet statt geschlafen.
    wartend = lauf.naechster_schritt(stand("noch 8 Min"))
    assert wartend is not None, "der Lauf darf hier nicht mehr stehenbleiben"
    assert wartend.art is lauf.Schrittart.TEXT
    assert wartend.texttyp is Texttyp.POST


def test_ohne_arbeit_wartet_der_lauf_weiterhin_auf_den_takt() -> None:
    """Die Kehrseite: Wo nichts zu tun ist, bleibt das Warten richtig.

    Sonst drehte der Treiber leer und fragte den Server in Schleife - das
    Warten ist dort kein Verlust, sondern das einzig Sinnvolle.
    """
    from fbgroups.marketing.grenzen import Aktion, Lage

    nur_beitritt = lauf.Kampagnenfortschritt(
        campaign_id="k",
        name="k",
        gruppen=[
            lauf.Gruppenfortschritt(
                campaign_id="k",
                group_id="offen",
                name="offen",
                veroeffentlicht=0,
                mitglied=False,
                mitgliedschaft_noetig=True,
                regeln_gelesen=True,
                beitritt_noetig=True,
                zielprioritaet=Zielprioritaet.A,
            )
        ],
    )
    fortschritt = lauf.Lauffortschritt(
        lauf_id=1,
        status=lauf.LaufStatus.LAEUFT,
        kampagnen=[nur_beitritt],
        aktionen={
            Aktion.BEITRITT: Lage(
                Aktion.BEITRITT, False, wartezeit="noch 8 Min", rest_heute=40
            ),
            Aktion.POST: Lage(Aktion.POST, True, rest_heute=9),
            Aktion.KOMMENTAR: Lage(Aktion.KOMMENTAR, True, rest_heute=99),
        },
    )

    assert lauf.naechster_schritt(fortschritt) is None
    assert fortschritt.wartet_auf_beitritt == "noch 8 Min"
