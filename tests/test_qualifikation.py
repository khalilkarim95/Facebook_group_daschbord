"""Die Frage vor dem Text: Darf in dieser Gruppe ueberhaupt etwas stehen?

Der Ablauf, den diese Datei festhaelt, ist
``Entdecken -> Regeln lesen -> Beitreten -> Warten -> Bewerten -> Arbeiten``
und ausdruecklich **nicht** ``Entdecken -> ueberall posten``.

Der groesste Teil ist offline und ohne Datenbank - ``qualifikation.py``
rechnet ueber uebergebene Werte, so wie ``kaltmodus.py`` und ``beitritt.py``.
Nur die letzten Tests fassen einen echten Speicher an; sie pruefen genau das,
was sich nicht rechnen laesst: dass eine gesperrte Gruppe wirklich keinen
Auftrag bekommt.

Die wichtigste Zusage steht in
``test_die_regeln_der_gruppe_binden_auch_gegen_die_beobachtung``: Dass ein
Link einmal durchgegangen ist, hebt eine Regel nicht auf. Die Regeln der
Gruppe zu umgehen ist kein Ziel dieses Projekts.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fbgroups.marketing import lauf
from fbgroups.marketing.grenzen import Aktion, Lage
from fbgroups.marketing.models import (
    Campaign,
    CampaignGroup,
    CampaignStatus,
    GroupMarketing,
    MarketingStatus,
    Texttyp,
    VorschlagStatus,
)
from fbgroups.marketing.qualifikation import (
    Ausgangsart,
    Beobachtung,
    Qualifikation,
    Regelbefund,
    beurteile,
    darf,
    ist_linkablehnung,
    klassifiziere,
    lies_regeln,
)
from fbgroups.marketing.store import MarketingStore
from fbgroups.models import Group
from fbgroups.storage import SqliteStore

KAMPAGNE = "qual-test"


# --- 1-3: Beitritt, Warten, Bewerten ---------------------------------------
def test_ohne_mitgliedschaft_steht_die_beitrittsanfrage_an() -> None:
    """Punkt 1: Nichtmitglied -> Beitritt, nicht Beitrag.

    Facebook laesst Nichtmitglieder in den meisten Gruppen weder posten noch
    kommentieren; ein Versuch dort scheitert nicht zufaellig, sondern immer.
    Hundert solcher Versuche aus einem Konto sind genau das Muster, das zur
    Sperre fuehrt.
    """
    befund = beurteile(mitglied=False)
    assert befund.qualifikation is Qualifikation.BEITRITT_NOETIG
    assert befund.grund
    assert not darf(befund.qualifikation, Texttyp.POST, mit_link=True)
    assert not darf(befund.qualifikation, Texttyp.KOMMENTAR, mit_link=False)


def test_eine_laufende_anfrage_ist_ein_eigener_zustand() -> None:
    """Punkt 2: angefragt heisst warten - und nicht noch einmal anfragen.

    Der Zustand ist von ``BEITRITT_NOETIG`` unterschieden, weil genau daran
    haengt, ob eine zweite Anfrage hinausgeht. Facebook laesst
    Beitrittsanfragen oft wochenlang offen; sie zu wiederholen waere die
    riskanteste Handlung des Projekts, zweimal.
    """
    befund = beurteile(mitglied=False, beitritt_angefragt=True)
    assert befund.qualifikation is Qualifikation.BEITRITT_ANGEFRAGT
    assert not darf(befund.qualifikation, Texttyp.KOMMENTAR, mit_link=False)


def test_keine_zweite_beitrittsanfrage_an_dieselbe_gruppe(bestand: Path) -> None:
    """Punkt 2 im Speicher: Wer angefragt ist, steht nicht mehr auf der Liste."""
    with MarketingStore(bestand) as store:
        vorher = store.gruppen_ohne_anfrage()
        assert "111" in vorher

        store.merke_anfrage("111")
        nachher = store.gruppen_ohne_anfrage()

        assert "111" not in nachher, "eine gestellte Anfrage wird nicht wiederholt"
        assert store.load_marketing("111").marketing_status is MarketingStatus.JOIN_REQUESTED

        # Und ein zweiter Aufruf dreht den erreichten Stand nicht zurueck.
        store.merke_anfrage("111", mitglied=True)
        store.merke_anfrage("111")
        assert store.load_marketing("111").marketing_status is MarketingStatus.MEMBER


def test_frisch_aufgenommen_heisst_bewerten_und_nicht_gleich_geeignet() -> None:
    """Punkt 3: Mitglied ist die Voraussetzung, nicht das Urteil.

    Solange niemand die Regeln der Gruppe gelesen hat, ist "nichts verboten"
    eine Aussage ueber **uns**. ``BEWERTUNG`` erlaubt den Versuch - aus nichts
    entstuende sonst nie eine Beobachtung -, nennt ihn aber beim Namen.
    """
    befund = beurteile(mitglied=True)
    assert befund.qualifikation is Qualifikation.BEWERTUNG
    assert "ungelesen" in befund.grund
    assert darf(befund.qualifikation, Texttyp.KOMMENTAR, mit_link=True)


# --- 4: die durchlaessige Gruppe -------------------------------------------
def test_eine_durchlaessige_gruppe_ist_geeignet() -> None:
    """Punkt 4: Regeln gelesen, nichts verboten, Versuche gegluckt."""
    befund = beurteile(
        mitglied=True,
        regeln=Regelbefund(gelesen=True),
        beobachtung=Beobachtung(mit_link_erfolg=3, beitrag_erfolg=1),
    )
    assert befund.qualifikation is Qualifikation.GEEIGNET
    assert darf(befund.qualifikation, Texttyp.POST, mit_link=True)
    assert darf(befund.qualifikation, Texttyp.KOMMENTAR, mit_link=True)


# --- 5-7: die drei Einschraenkungen ----------------------------------------
def test_ohne_links_nimmt_denselben_kommentar_ohne_link() -> None:
    """Punkt 5: Die Einschraenkung trifft den Link, nicht die Gruppe.

    Ein Kommentar ohne Link ist dort weiterhin willkommen - alles zu sperren
    waere dasselbe Urteil fuer zwei verschiedene Sachverhalte.
    """
    q = Qualifikation.OHNE_LINKS
    assert not darf(q, Texttyp.KOMMENTAR, mit_link=True)
    assert darf(q, Texttyp.KOMMENTAR, mit_link=False)
    # Der Beitrag traegt immer einen Link (``pruefe_platzhalter`` verlangt
    # ihn), faellt hier also ebenfalls aus.
    assert not darf(q, Texttyp.POST, mit_link=True)


def test_ohne_kommentare_laesst_den_beitrag_stehen() -> None:
    """Punkt 6: Kommentare gesperrt, Beitrag nicht."""
    q = Qualifikation.OHNE_KOMMENTARE
    assert not darf(q, Texttyp.KOMMENTAR, mit_link=False)
    assert not darf(q, Texttyp.KOMMENTAR, mit_link=True)
    assert darf(q, Texttyp.POST, mit_link=True)


def test_ohne_beitraege_laesst_die_kommentare_stehen() -> None:
    """Punkt 7: Beitrag gesperrt, Kommentare nicht."""
    q = Qualifikation.OHNE_BEITRAEGE
    assert not darf(q, Texttyp.POST, mit_link=True)
    assert darf(q, Texttyp.KOMMENTAR, mit_link=True)


# --- 8-9: was die Ausgaenge verraten ---------------------------------------
def test_mit_link_abgelehnt_und_ohne_link_durchgegangen_ergibt_ohne_links() -> None:
    """Punkte 8 und 9 zusammen - erst der Vergleich ergibt die Aussage.

    "Mit Link abgelehnt" allein koennte am Text liegen, an der Uhrzeit, an
    einem Moderator. Erst daneben gestellt, dass derselbe Kommentar **ohne**
    Link durchging, zeigt es auf den Link.
    """
    befund = beurteile(
        mitglied=True,
        regeln=Regelbefund(gelesen=True),
        beobachtung=Beobachtung(mit_link_moderation=2, ohne_link_erfolg=3),
    )
    assert befund.qualifikation is Qualifikation.OHNE_LINKS
    assert "ohne Link durchgegangen" in befund.grund


def test_eine_ausdrueckliche_linkablehnung_genuegt_allein() -> None:
    """Wo Facebook den Grund hinschreibt, braucht es keine zwei Beobachtungen.

    Der Dialog vom 11.09.2026 nennt ihn: "Link in Kommentar - Der Kommentar
    enthaelt einen Link." Zwei Versuche abzuwarten hiesse, eine Auskunft zu
    ignorieren, die schon dasteht.
    """
    assert ist_linkablehnung("Link in Kommentar: Der Kommentar enthaelt einen Link.")
    befund = beurteile(
        mitglied=True,
        regeln=Regelbefund(gelesen=True),
        beobachtung=Beobachtung(mit_link_moderation=1, link_ausdruecklich=1),
    )
    assert befund.qualifikation is Qualifikation.OHNE_LINKS
    assert "Facebook nennt den Link" in befund.grund


# --- 10: Technik ist kein Urteil -------------------------------------------
def test_ein_technischer_fehler_ist_keine_ablehnung() -> None:
    """Punkt 10 - und der Fehler, der am 11.09.2026 45 Gruppen gekostet hat.

    Ein geschlossener Browser liess zehn Fassungen dreimal scheitern; danach
    galten die Gruppen als erschoepft. Im Protokoll sieht das aus wie eine
    Ablehnung und ist das Gegenteil: eine Aussage ueber uns.
    """
    assert klassifiziere(
        "BrowserContext.new_page: Target page, context or browser has been closed"
    ) is Ausgangsart.TECHNISCH
    assert klassifiziere("Zeitablauf") is Ausgangsart.TECHNISCH
    assert klassifiziere("") is Ausgangsart.TECHNISCH
    assert klassifiziere("Dein Kommentar wurde abgelehnt") is Ausgangsart.MODERATION

    # Im Zweifel technisch: Eine geratene Ablehnung verurteilte eine Gruppe,
    # gegen die nichts vorliegt.
    assert klassifiziere("irgendein unbekannter Text") is Ausgangsart.TECHNISCH

    befund = beurteile(
        mitglied=True,
        regeln=Regelbefund(gelesen=True),
        beobachtung=Beobachtung(),  # technische Fehlschlaege zaehlen nirgends
    )
    assert befund.qualifikation is Qualifikation.BEWERTUNG


# --- 11: was die Gruppe selbst schreibt ------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        "Regeln: Keine Links im Kommentar!",
        "Group rules: no links, please.",
        "قوانين المجموعة: ممنوع الروابط نهائياً",
    ],
)
def test_eine_regel_gegen_links_wird_gelesen(text: str) -> None:
    """Punkt 11 - in allen drei Sprachen, die im Bestand vorkommen."""
    befund = lies_regeln(text)
    assert befund.gelesen
    assert befund.verbietet_links


def test_eine_regel_gegen_werbung_macht_die_gruppe_ungeeignet() -> None:
    """Punkt 11: "Keine Werbung" trifft nicht den Link, sondern den Zweck.

    Dort noch den Beitrag ohne Link zu setzen waere kein Ausweg, sondern
    genau die Umgehung, die dieses Projekt nicht betreibt.
    """
    regeln = lies_regeln("1. Keine Werbung. 2. Freundlich bleiben.")
    assert regeln.keine_werbung
    befund = beurteile(mitglied=True, regeln=regeln)
    assert befund.qualifikation is Qualifikation.UNGEEIGNET
    assert not darf(befund.qualifikation, Texttyp.KOMMENTAR, mit_link=False)


def test_nicht_gelesen_ist_etwas_anderes_als_nichts_verboten() -> None:
    """Aus nichts folgt keine Erlaubnis.

    Derselbe Gedanke wie bei ``Group.score is None`` und
    ``Seitenbefund.erreichbar``: Eine fehlende Angabe ist eine Aussage ueber
    uns, keine ueber die Gruppe.
    """
    assert lies_regeln("").gelesen is False
    assert lies_regeln("   ").gelesen is False
    assert lies_regeln("Willkommen in der Gruppe").gelesen is True


def test_die_regeln_der_gruppe_binden_auch_gegen_die_beobachtung() -> None:
    """Die wichtigste Zusage: Beobachtungen koennen nur enger machen.

    Dass ein Link einmal durchging, heisst nicht, dass er erlaubt war. Eine
    Gruppe, deren Regeln Links verbieten, bleibt ``OHNE_LINKS`` - sonst waere
    der erste gegluckte Versuch die Erlaubnis fuer alle folgenden, und genau
    das ist die Umgehung, die hier nicht stattfindet.
    """
    befund = beurteile(
        mitglied=True,
        regeln=Regelbefund(gelesen=True, keine_links=True),
        beobachtung=Beobachtung(mit_link_erfolg=5),
    )
    assert befund.qualifikation is Qualifikation.OHNE_LINKS
    assert "Regeln der Gruppe" in befund.grund


# --- 12: wiederholte Ablehnung -> aufhoeren --------------------------------
def test_wiederholte_ablehnung_beendet_die_versuche() -> None:
    """Punkt 12: zwei gleichlautende Ablehnungen sind ein Muster.

    Eine einzelne kann alles sein. Weiterzumachen, bis es zwanzig sind, ist
    dagegen genau die Folge aus einem Konto, die zur Sperre fuehrt.
    """
    nur_kommentare = beurteile(
        mitglied=True,
        regeln=Regelbefund(gelesen=True),
        beobachtung=Beobachtung(mit_link_moderation=2, ohne_link_moderation=2),
    )
    assert nur_kommentare.qualifikation is Qualifikation.OHNE_KOMMENTARE

    alles = beurteile(
        mitglied=True,
        regeln=Regelbefund(gelesen=True),
        beobachtung=Beobachtung(
            mit_link_moderation=2, ohne_link_moderation=2, beitrag_moderation=2
        ),
    )
    assert alles.qualifikation is Qualifikation.UNGEEIGNET

    # Eine einzelne Ablehnung genuegt ausdruecklich nicht.
    einmal = beurteile(
        mitglied=True,
        regeln=Regelbefund(gelesen=True),
        beobachtung=Beobachtung(ohne_link_moderation=1),
    )
    assert einmal.qualifikation is not Qualifikation.OHNE_KOMMENTARE


# --- Der Speicher ----------------------------------------------------------
@pytest.fixture()
def bestand(tmp_path: Path) -> Path:
    """Zwei Gruppen, eine Kampagne, zehn Kommentarfassungen mit Link."""
    pfad = tmp_path / "groups.sqlite"
    with SqliteStore(pfad) as store:
        store.upsert_groups(
            [
                Group(
                    group_id=gid,
                    url_canonical=f"https://www.facebook.com/groups/{gid}",
                    name=name,
                    city="Berlin",
                    audience_tags=["syrians"],
                    score=50.0,
                    score_max=100.0,
                )
                for gid, name in (("111", "Syrer in Berlin"), ("222", "Syrer in Hamburg"))
            ]
        )
    with MarketingStore(pfad) as store:
        store.save_campaign(
            Campaign(
                campaign_id=KAMPAGNE,
                name="Qualifikationstest",
                language="ar",
                status=CampaignStatus.ACTIVE,
            )
        )
        for i, gid in enumerate(("111", "222"), start=1):
            store.add_link(
                CampaignGroup(
                    campaign_id=KAMPAGNE,
                    group_id=gid,
                    tracking_code=f"FB-QUA-BER-{i:03d}",
                    tracking_url=f"https://example.invalid/r/FB-QUA-BER-{i:03d}",
                )
            )
            for nummer in range(1, lauf.ZIEL_JE_GRUPPE + 1):
                store.setze_erzeugten_vorschlag(
                    KAMPAGNE, gid, Texttyp.KOMMENTAR, nummer,
                    text=f"Kommentar {nummer} {{link}}", vorlage_key="k",
                )
    return pfad


def test_ein_gelesener_regelbefund_wird_von_einem_ungelesenen_nicht_geloescht(
    bestand: Path,
) -> None:
    """Eine Anmeldewand ist kein Beleg dafuer, dass die Regel weg ist.

    Dieselbe Regel wie bei ``upsert_groups`` und COALESCE: Was nicht gefunden
    wurde, loescht nicht, was gefunden worden war.
    """
    with MarketingStore(bestand) as store:
        store.merke_regeln("111", lies_regeln("Regeln: keine Links."))
        assert store.load_marketing("111").regel_keine_links is True

        store.merke_regeln("111", lies_regeln(""))
        assert store.load_marketing("111").regel_keine_links is True

        # Und ein Speichern des Arbeitsstands fasst den Befund nicht an.
        store.save_marketing(
            GroupMarketing(group_id="111", marketing_status=MarketingStatus.MEMBER)
        )
        stand = store.load_marketing("111")
        assert stand.regel_keine_links is True
        assert stand.regeln_gelesen_am is not None


def test_ein_technischer_fehlschlag_erzeugt_keine_beobachtung(bestand: Path) -> None:
    """Punkt 10 im Speicher - der Fall vom 11.09.2026.

    Zehn Fassungen, jede mit dem Browserfehler gescheitert: Danach darf die
    Gruppe nicht als ablehnend dastehen. Sie hat nichts abgelehnt.
    """
    with MarketingStore(bestand) as store:
        for nummer in range(1, 4):
            store.setze_vorschlag_stand(
                KAMPAGNE, "111", Texttyp.KOMMENTAR, nummer,
                VorschlagStatus.FEHLGESCHLAGEN,
                fehler="BrowserContext.new_page: Target page has been closed",
            )
            _protokolliere(store, "111", Texttyp.KOMMENTAR, nummer, erfolg=False,
                           fehler="BrowserContext.new_page: Target page has been closed")

        beobachtet = store.beobachtungen()

    assert beobachtet.get("111", Beobachtung()).kommentar_moderation == 0
    assert beobachtet.get("111", Beobachtung()).leer


def test_nur_qualifizierte_gruppen_bekommen_einen_auftrag(bestand: Path) -> None:
    """Punkt 13: Die Sperre wirkt dort, wo der Auftrag entsteht.

    Gruppe eins ist Mitglied und durchlaessig, Gruppe zwei ist es nicht.
    Ohne den Schalter bekommen beide etwas - beobachtet wird ab sofort,
    gesperrt auf Ansage.
    """
    with MarketingStore(bestand) as store:
        store.save_marketing(
            GroupMarketing(group_id="111", marketing_status=MarketingStatus.MEMBER)
        )
        store.merke_regeln("111", lies_regeln("Willkommen! Bitte freundlich bleiben."))
        # Gruppe zwei: nicht einmal angefragt.
        lauf_id = store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)
        # Hier geht es um die Sperre, nicht um den Ablauf: Ohne diesen Vermerk
        # waere der naechste Schritt die Neubewertung der Kampagne.
        store.merke_bewertung(lauf_id, KAMPAGNE)

        with SqliteStore(bestand) as bestand_store:
            gruppen = {g.group_id: g for g in bestand_store.load_groups()}

        # Ohne Beitrittsschritte: Dieser Test fragt, **wer Arbeit bekommt**,
        # nicht, wer zuerst eine Anfrage bekommt. Gruppe zwei ist nicht
        # einmal angefragt - mit offener Beitrittsaktion stuende sie als
        # erster Schritt da, und die eigentliche Frage bliebe unbeantwortet.
        #
        # ``regeln_pflicht=False`` aus demselben Grund (13.09.2026): Vor der
        # Anfrage steht seit dem 13.09.2026 der Regelschritt, und der haette
        # hier dieselbe Wirkung - er schoebe sich vor die Frage, die dieser
        # Test stellt. Seine eigene Pruefung steht in ``test_lauf.py``.
        ohne_beitritt = {
            Aktion.BEITRITT: Lage(Aktion.BEITRITT, False, grund="im Test abgeschaltet")
        }
        ohne_sperre = lauf.naechster_schritt(
            lauf.lies_fortschritt(
                store, lauf_id, gruppen,
                mitgliedschaft_pflicht=False, qualifikation_pflicht=False,
                aktionen=ohne_beitritt, regeln_pflicht=False,
            )
        )
        mit_sperre = lauf.lies_fortschritt(
            store, lauf_id, gruppen,
            mitgliedschaft_pflicht=False, qualifikation_pflicht=True,
            aktionen=ohne_beitritt, regeln_pflicht=False,
        )

    assert ohne_sperre is not None, "ohne Schalter bleibt alles wie bisher"

    stand = {g.group_id: g for k in mit_sperre.kampagnen for g in k.gruppen}
    assert stand["111"].qualifikation is Qualifikation.BEWERTUNG
    assert stand["222"].qualifikation is Qualifikation.BEITRITT_NOETIG
    assert stand["222"].gesperrt is True
    assert stand["111"].gesperrt is False

    # Der Auftrag geht an die eine Gruppe, die etwas bekommen darf - und die
    # gesperrte gilt nicht als erschoepft: Sie kann aufgenommen werden.
    schritt = lauf.naechster_schritt(mit_sperre)
    assert schritt is not None
    assert schritt.group_id == "111"
    assert lauf.gruppe_ist_erschoepft(stand["222"]) is False


def test_eine_linkscheue_gruppe_bekommt_nur_kommentare_ohne_link(bestand: Path) -> None:
    """Punkt 5 im Speicher: Der Lauf ueberspringt die Fassungen mit Link.

    Alle zehn Fassungen dieser Gruppe tragen einen ``{link}`` - also bleibt
    kein Kommentar uebrig, und der Lauf haelt trotzdem nicht an: Die Gruppe
    ist gesperrt, nicht erschoepft, denn eine linklose Fassung waere dort
    willkommen.
    """
    with MarketingStore(bestand) as store:
        for gid in ("111", "222"):
            store.save_marketing(
                GroupMarketing(group_id=gid, marketing_status=MarketingStatus.MEMBER)
            )
        store.merke_regeln("111", lies_regeln("Regeln: Keine Links!"))
        store.merke_regeln("222", lies_regeln("Willkommen, bitte freundlich."))
        lauf_id = store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)
        store.merke_bewertung(lauf_id, KAMPAGNE)  # der Ablauf ist hier nicht die Frage

        with SqliteStore(bestand) as bestand_store:
            gruppen = {g.group_id: g for g in bestand_store.load_groups()}
        fortschritt = lauf.lies_fortschritt(
            store, lauf_id, gruppen,
            mitgliedschaft_pflicht=False, qualifikation_pflicht=True,
        )

    stand = {g.group_id: g for k in fortschritt.kampagnen for g in k.gruppen}
    assert stand["111"].qualifikation is Qualifikation.OHNE_LINKS
    assert stand["111"].fassungen_mit_link == frozenset(range(1, lauf.ZIEL_JE_GRUPPE + 1))
    # Keine der zehn Fassungen ist erlaubt - aber die Gruppe ist nicht am Ende.
    assert stand["111"].erlaubt(Texttyp.KOMMENTAR, 1) is False
    assert lauf.gruppe_ist_erschoepft(stand["111"]) is False

    # Gearbeitet wird deshalb in der anderen.
    schritt = lauf.naechster_schritt(fortschritt)
    assert schritt is not None
    assert schritt.group_id == "222"


def _protokolliere(
    store: MarketingStore,
    group_id: str,
    texttyp: Texttyp,
    nummer: int,
    *,
    erfolg: bool,
    fehler: str = "",
) -> None:
    """Eine Zeile im Versuchsprotokoll - der Weg, den ``melde_vorschlag`` geht."""
    from datetime import UTC, datetime

    from fbgroups.marketing.models import JobStatus, PostVersuch

    versuch_id = store.beginne_versuch(
        PostVersuch(
            campaign_id=KAMPAGNE,
            group_id=group_id,
            texttyp=texttyp.value,
            nummer=nummer,
            tracking_code="FB-QUA-BER-001",
            job_status=JobStatus.PROCESSING,
            begonnen_am=datetime.now(UTC),
        )
    )
    store.beende_versuch(versuch_id, erfolg=erfolg, fehler=fehler)


def test_eine_moderationsablehnung_landet_als_solche_in_der_beobachtung(
    bestand: Path,
) -> None:
    """Punkt 8 im Speicher - samt der Frage, ob ein Link im Spiel war.

    Ob ein Kommentar einen Link trug, sagt der Text der Fassung, an der der
    Versuch hing. Ohne diesen Verbund waeren "mit Link abgelehnt" und "ohne
    Link abgelehnt" eine einzige Zahl - und das Muster, auf das es ankommt,
    unsichtbar.
    """
    with MarketingStore(bestand) as store:
        # Fassung 1 traegt einen Link (aus der Fixture), Fassung 2 bekommt
        # hier ausdruecklich keinen.
        store.setze_erzeugten_vorschlag(
            KAMPAGNE, "111", Texttyp.KOMMENTAR, 2,
            text="Ein Kommentar ganz ohne Adresse.", vorlage_key="k",
            ueberschreiben=True,
        )
        _protokolliere(
            store, "111", Texttyp.KOMMENTAR, 1, erfolg=False,
            fehler="Dein Kommentar wurde abgelehnt: Link in Kommentar",
        )
        _protokolliere(store, "111", Texttyp.KOMMENTAR, 2, erfolg=True)

        beobachtet = store.beobachtungen()

    b = beobachtet["111"]
    assert b.mit_link_moderation == 1
    assert b.link_ausdruecklich == 1
    assert b.ohne_link_erfolg == 1
    assert b.mit_link_erfolg == 0

    befund = beurteile(
        mitglied=True, regeln=Regelbefund(gelesen=True), beobachtung=b
    )
    assert befund.qualifikation is Qualifikation.OHNE_LINKS
