"""Welcher Beitrag bekommt den Kommentar - und bekommt ueberhaupt einer einen?

Die Auswahlregel aus ``automatik.waehle_und_kommentiere``, geprueft ohne
Browser: ``kommentieren`` wird durch eine Funktion ersetzt, die mitschreibt
statt zu kommentieren.

Die Beitragstexte hier sind **Eingaben** der Tests. Was das Programm davon
behaelt, ist das Urteil (``versand``, ``hoch``) - ``GroupPost`` hat kein
Textfeld, und ``upsert_group_posts`` koennte einen Text gar nicht speichern.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fbgroups.marketing import automatik
from fbgroups.marketing.entscheidung import Antwortart, Erlaubnis
from fbgroups.marketing.models import Campaign, CampaignGroup, GroupMarketing, MarketingStatus
from fbgroups.marketing.qualifikation import Qualifikation, Regelbefund, lies_regeln
from fbgroups.marketing.store import MarketingStore
from fbgroups.models import Group
from fbgroups.storage import SqliteStore

#: Die fertige Adresse dieser Gruppe. Sie gehoert in jeden Aufruf: Der
#: Anlasstext traegt ``{link}``, und ohne Adresse weist der Lauf ihn
#: zurueck, statt ihn abzusenden (14.09.2026).
LINK_URL = "https://go.b-tarikak.de/r/k7m2x9q"

GID = "111"
OFFEN = Erlaubnis.aus_regeln(Regelbefund(gelesen=True), Qualifikation.GEEIGNET)

PAKET = "في حدا مسافر من ألمانيا لسوريا يقدر ياخد أمانة صغيرة؟"
WOHNUNG = "Suche dringend eine 2-Zimmer-Wohnung in Stuttgart, zahle Kaution."
RESTAURANT = "شو أحسن مطعم عربي بشتوتغارت؟"


def _post(url: str, text: str, reaktionen: int = 0, kommentare: int = 0) -> dict:
    return {
        "post_url": url,
        "interactions": reaktionen,
        "comments": kommentare,
        "text": text,
    }


@pytest.fixture()
def bestand(tmp_path: Path) -> Path:
    """Eine Gruppe, eine Kampagne - mehr braucht die Auswahlregel nicht."""
    pfad = tmp_path / "groups.sqlite"
    with SqliteStore(pfad) as store:
        store.upsert_groups(
            [
                Group(
                    group_id=GID,
                    url_canonical=f"https://www.facebook.com/groups/{GID}",
                    name="Syrer in Bremen",
                )
            ]
        )
    with MarketingStore(pfad) as store:
        store.save_campaign(Campaign(campaign_id="k", name="K", language="ar"))
        store.add_link(
            CampaignGroup(
                campaign_id="k",
                group_id=GID,
                tracking_code="FB-TST-BRE-001",
                tracking_url="https://example.invalid/r/FB-TST-BRE-001",
            )
        )
    return pfad


class _Konfig:
    """Die echte Konfiguration mit umgebogenem Datenbankpfad."""

    def __init__(self, pfad: Path) -> None:
        from fbgroups.config import load_config

        self._echt = load_config()
        self._pfad = pfad

    def __getattr__(self, name: str):  # noqa: ANN204
        return getattr(self._echt, name)

    def path(self, name: str) -> Path:
        return self._pfad if name == "sqlite_path" else self._echt.path(name)


def _lauf(bestand: Path, posts: list[dict], erlaubnis=OFFEN):  # noqa: ANN001, ANN202
    """Die Auswahl einmal durchspielen. Returns: (Ergebnis, kommentierte URLs)."""
    gesehen: list[str] = []

    def kommentieren(context, url: str, text: str) -> bool:  # noqa: ANN001
        gesehen.append(url)
        return True

    ergebnis = automatik.waehle_und_kommentiere(
        None,
        _Konfig(bestand),
        posts,
        GID,
        "Text {link}",
        kommentieren=kommentieren,
        link_url=LINK_URL,
        erlaubnis=erlaubnis,
    )
    return ergebnis, gesehen


# --- Der passendste Beitrag, nicht der lauteste ---------------------------
def test_der_passende_beitrag_schlaegt_den_lauteren(bestand: Path) -> None:
    """Punkt 42: Qualitaet vor Menge - und vor Reichweite.

    Das Wohnungsgesuch hat hundert Reaktionen, die Paketfrage zwei. Frueher
    entschied allein ``interactions + comments``: Der Kommentar ueber
    Paketmitnahme stand dann unter dem Wohnungsgesuch - und zwar dort, wo ihn
    die meisten sehen.
    """
    ergebnis, gesehen = _lauf(
        bestand,
        [
            _post("https://www.facebook.com/groups/111/posts/1", WOHNUNG, 100, 40),
            _post("https://www.facebook.com/groups/111/posts/2", PAKET, 2, 0),
        ],
    )

    assert ergebnis.erfolg
    assert gesehen == ["https://www.facebook.com/groups/111/posts/2"]


def test_unter_gleich_passenden_gewinnt_der_belebtere(bestand: Path) -> None:
    """Die Betriebsamkeit bleibt das **zweite** Kriterium, nicht das erste."""
    _ergebnis, gesehen = _lauf(
        bestand,
        [
            _post("https://www.facebook.com/groups/111/posts/1", PAKET, 1, 0),
            _post("https://www.facebook.com/groups/111/posts/2", PAKET, 50, 10),
        ],
    )

    assert gesehen == ["https://www.facebook.com/groups/111/posts/2"]


def test_ohne_passenden_beitrag_wird_nichts_geschrieben(bestand: Path) -> None:
    """Punkt 45: ``NO_REPLY`` ist ein Ergebnis, kein Fehlschlag.

    Und ausdruecklich **keine Erschoepfung**: Die Gruppe gibt heute nichts
    her, nicht ueberhaupt nichts. Morgen stehen dort andere Beitraege.
    """
    ergebnis, gesehen = _lauf(
        bestand,
        [
            _post("https://www.facebook.com/groups/111/posts/1", WOHNUNG, 100, 40),
            _post("https://www.facebook.com/groups/111/posts/2", RESTAURANT, 5, 2),
        ],
    )

    assert not gesehen, "es wurde nichts kommentiert"
    assert not ergebnis.erfolg
    assert ergebnis.kein_anlass
    assert not ergebnis.erschoepft, "kein Anlass ist keine Erschoepfung"
    assert "kein passender Beitrag" in ergebnis.fehler


def test_ohne_lesbaren_text_entscheiden_die_kennzahlen(bestand: Path) -> None:
    """Der Rueckfall, und er steht an der ehrlichen Stelle.

    Wenn kein einziger Beitrag einen lesbaren Text hat, wissen wir nichts
    ueber sie - das ist etwas anderes als "sie passen nicht". Dann gilt die
    alte Regel, statt die Gruppe auszulassen.
    """
    _ergebnis, gesehen = _lauf(
        bestand,
        [
            _post("https://www.facebook.com/groups/111/posts/1", "", 1, 0),
            _post("https://www.facebook.com/groups/111/posts/2", "", 9, 3),
        ],
    )

    assert gesehen == ["https://www.facebook.com/groups/111/posts/2"]


def test_ein_bereits_kommentierter_beitrag_kommt_nicht_zweimal(bestand: Path) -> None:
    """Zwei Kommentare von uns unter demselben Beitrag sind das deutlichste
    Zeichen einer Maschine, das man hinterlassen kann."""
    from fbgroups.marketing.models import PostVersuch

    erste = "https://www.facebook.com/groups/111/posts/1"
    with MarketingStore(bestand) as store:
        versuch_id = store.beginne_versuch(
            PostVersuch(
                campaign_id="k",
                group_id=GID,
                tracking_code="FB-TST-BRE-001",
                texttyp="kommentar",
                nummer=1,
            )
        )
        store.beende_versuch(versuch_id, erfolg=True, post_url=erste)

    _ergebnis, gesehen = _lauf(
        bestand,
        [
            _post(erste, PAKET, 99, 20),
            _post("https://www.facebook.com/groups/111/posts/2", PAKET, 1, 0),
        ],
    )

    assert gesehen == ["https://www.facebook.com/groups/111/posts/2"]


def test_die_gruppenregeln_wirken_auf_die_auswahl(bestand: Path) -> None:
    """Eine Gruppe, die Werbung verbietet, bekommt keine Werbeantwort.

    Der Paketbeitrag bleibt relevant - aber die einzige Form, die hier noch
    erlaubt waere, traegt nichts von uns. Geprueft wird, dass die Erlaubnis
    ueberhaupt bis in die Auswahl durchschlaegt.
    """
    ohne_werbung = Erlaubnis.aus_regeln(
        lies_regeln("Regeln: Keine Werbung, keine Angebote."), Qualifikation.UNGEEIGNET
    )
    assert not ohne_werbung.kommentare, "UNGEEIGNET schliesst beides aus"

    ergebnis, gesehen = _lauf(
        bestand,
        [_post("https://www.facebook.com/groups/111/posts/2", PAKET, 5, 1)],
        erlaubnis=ohne_werbung,
    )

    # Die Gruppe laesst keine Kommentare zu (UNGEEIGNET) - also nichts.
    assert not gesehen
    assert ergebnis.kein_anlass


def test_die_erlaubnis_wird_aus_dem_bestand_gelesen(bestand: Path) -> None:
    """Ohne uebergebene Erlaubnis fragt die Auswahl den Speicher.

    So bleibt die Rangfolge dieselbe wie im Rest des Programms: Regeln
    binden, Beobachtung schraenkt ein - gerechnet in
    ``qualifikation.beurteile``, nicht hier zum zweiten Mal.
    """
    with MarketingStore(bestand) as store:
        store.save_marketing(
            GroupMarketing(group_id=GID, marketing_status=MarketingStatus.MEMBER)
        )
        store.merke_regeln(GID, lies_regeln("Willkommen! Bitte freundlich bleiben."))
        erlaubnis = automatik.erlaubnis_fuer(store, GID)

    assert erlaubnis.regeln_gelesen
    assert erlaubnis.kommentare
    assert erlaubnis.links, "nichts verboten, Regeln gelesen"


def test_ohne_gelesene_regeln_bleibt_die_erlaubnis_vorsichtig(bestand: Path) -> None:
    """Punkt 4: Die Abwesenheit einer Regel ist keine Erlaubnis."""
    with MarketingStore(bestand) as store:
        store.save_marketing(
            GroupMarketing(group_id=GID, marketing_status=MarketingStatus.MEMBER)
        )
        erlaubnis = automatik.erlaubnis_fuer(store, GID)

    assert not erlaubnis.regeln_gelesen
    # Vorsichtig heisst seit dem 21.09.2026 noch genau eines: kein **Link**.
    # Kommentiert wird trotzdem - die App wird dann genannt, nicht verlinkt.
    assert not erlaubnis.links


def test_der_beitragstext_wird_nicht_gespeichert(bestand: Path) -> None:
    """Die harte Grenze des Projekts, technisch abgesichert.

    Gelesen wird der Text im Browser, gespeichert wird das Urteil. Dass
    ``GroupPost`` kein Textfeld hat, ist der Grund, warum diese Zusage nicht
    von der Sorgfalt des naechsten Aufrufers abhaengt.
    """
    from fbgroups.models import GroupPost

    _lauf(bestand, [_post("https://www.facebook.com/groups/111/posts/9", PAKET, 3, 1)])

    assert not hasattr(GroupPost(group_id=GID, post_url="u"), "text")
    with SqliteStore(bestand) as store:
        spalten = {
            row[1] for row in store.conn.execute("PRAGMA table_info(group_posts)")
        }
    assert "text" not in spalten
    assert not (spalten & {"author", "autor", "author_name"})


def test_die_entscheidung_steht_im_ergebnis(bestand: Path) -> None:
    """Damit im Protokoll steht, **warum** dort etwas steht (Punkt 49)."""
    from fbgroups.marketing.inhalt import lies

    gelegenheiten = automatik.beurteile_beitraege(
        [_post("u1", PAKET), _post("u2", WOHNUNG)], OFFEN
    )

    assert gelegenheiten[0].entscheidung.art is not Antwortart.NO_REPLY
    assert gelegenheiten[1].entscheidung.art is Antwortart.NO_REPLY
    assert gelegenheiten[0].befund.thema is lies(PAKET).thema
    assert gelegenheiten[0].entscheidung.grund


# --- Was aus einem abgeschickten Kommentar geworden ist --------------------
#
# Frueher meldete ``comment_on_post`` **immer** Erfolg: Enter druecken, drei
# Sekunden warten, fertig. Am 12.09.2026 fiel auf, was das verschweigt - in
# einer Gruppe mit Freigabepflicht standen die Kommentare als "Ausstehend"
# und waren fuer niemanden sichtbar, waehrend der Lauf sie zaehlte.


def test_ein_gruppenlimit_ist_kein_urteil_ueber_den_text(bestand: Path) -> None:
    """"Du hast das Limit fuer freizugebende Inhalte erreicht" heisst: warten.

    Nicht "der Text taugt nicht" (das waere Moderation) und nicht "das Konto
    ist gebremst" (das waere eine Sperre aller Kommentare). Die naechste
    Gruppe geht sofort.
    """
    from fbgroups.automation.actions import Kommentarausgang
    from fbgroups.marketing.qualifikation import Ausgangsart, klassifiziere

    meldung = "Du hast das Limit für freizugebende Inhalte in dieser Gruppe erreicht."
    assert klassifiziere(meldung) is Ausgangsart.GRUPPENLIMIT

    def gruppenlimit(context, url: str, text: str) -> Kommentarausgang:  # noqa: ANN001
        return Kommentarausgang(False, hinweis=meldung, gruppenlimit=True)

    ergebnis = automatik.waehle_und_kommentiere(
        None,
        _Konfig(bestand),
        [_post("https://www.facebook.com/groups/111/posts/2", PAKET, 5, 1)],
        GID,
        "Text {link}",
        kommentieren=gruppenlimit,
        link_url=LINK_URL,
        erlaubnis=OFFEN,
    )

    assert not ergebnis.erfolg
    assert "Limit" in ergebnis.fehler
    assert not ergebnis.erschoepft, "die Gruppe gibt morgen wieder etwas her"
    assert ergebnis.post_url == "", "kommentiert wurde nichts"


def test_ein_gruppenlimit_verbraucht_keine_fassung(bestand: Path) -> None:
    """Sonst waere eine volle Warteschlange nach drei Laeufen eine Erschoepfung."""
    from fbgroups.marketing.models import Texttyp, VorschlagStatus

    meldung = "Du hast das Limit für freizugebende Inhalte in dieser Gruppe erreicht."
    with MarketingStore(bestand) as store:
        store.setze_erzeugten_vorschlag(
            "k", GID, Texttyp.KOMMENTAR, 1, text="Text {link}", vorlage_key="k"
        )
        for _ in range(3):
            store.setze_vorschlag_stand(
                "k", GID, Texttyp.KOMMENTAR, 1, VorschlagStatus.FEHLGESCHLAGEN, fehler=meldung
            )
        aufgegeben = store.gescheiterte_kommentarfassungen("k", 3)

    assert aufgegeben.get(GID, set()) == set()


def test_ein_ausstehender_kommentar_zaehlt_aber_wird_genannt(bestand: Path) -> None:
    """Abgeschickt ist er, sichtbar ist er nicht.

    Als Erfolg gezaehlt, weil der Tracking-Link heraus und die Fassung
    verbraucht ist - aber mit Ansage: Bis zur Freigabe klickt ihn niemand,
    und eine Null bei den Klicks ist dann kein Urteil ueber die Gruppe.
    """
    from fbgroups.automation.actions import Kommentarausgang

    def ausstehend(context, url: str, text: str) -> Kommentarausgang:  # noqa: ANN001
        return Kommentarausgang(True, hinweis="ausstehend", wartet_auf_freigabe=True)

    ergebnis = automatik.waehle_und_kommentiere(
        None,
        _Konfig(bestand),
        [_post("https://www.facebook.com/groups/111/posts/2", PAKET, 5, 1)],
        GID,
        "Text {link}",
        kommentieren=ausstehend,
        link_url=LINK_URL,
        erlaubnis=OFFEN,
    )

    assert ergebnis.erfolg
    assert ergebnis.post_url.endswith("/posts/2")


def test_ein_blosses_true_bleibt_gueltig(bestand: Path) -> None:
    """Die eingereichte Funktion darf im Test zaehlen statt zu kommentieren.

    Sonst waere die Auswahlregel nur noch mit Browser pruefbar - und genau
    das soll die Aufteilung verhindern.
    """
    ergebnis, gesehen = _lauf(
        bestand, [_post("https://www.facebook.com/groups/111/posts/2", PAKET, 5, 1)]
    )

    assert ergebnis.erfolg
    assert gesehen
