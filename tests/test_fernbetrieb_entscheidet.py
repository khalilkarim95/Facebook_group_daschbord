"""Der Fernbetrieb prueft, was der oertliche Lauf prueft - keine zweite Kette.

Der Anlass vom 14.09.2026 ist eine Forderung des Nutzers: *"Ich möchte nicht,
dass wir einen erfolgreichen CLI-Test haben, während der echte Facebook-Runner
diese Prüfungen noch gar nicht durchführt."*

Genau das war der Fall. ``campaign automatik --server`` - der Weg, den der
Nutzer faehrt - lief ueber ``browser_schritt_fern``, und dort stand:

    bester = max(offen, key=lambda p: p["interactions"] + p["comments"])
    return _ausgang(comment_on_post(context, bester["post_url"], text), ...)

Der **lauteste** Beitrag, der vorbereitete Text. Kein ``inhalt.lies``, kein
``entscheide``, keine ``Erlaubnis``, kein ``Anspruch``. Die ganze Kette, die
``campaign pruefe-inhalt`` vorfuehrt, gab es nur im oertlichen Lauf.

Die Datei haelt fest, dass beide Wege jetzt dieselbe Kette gehen - und dass
die Angaben, die der Arbeitsrechner nicht nachschlagen kann, vom Server
mitkommen.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fbgroups.marketing import automatik
from fbgroups.marketing.entscheidung import Anspruch, Antwortart, Erlaubnis, Linkmodus
from fbgroups.marketing.inhalt import Relevanz

#: Die fertige Adresse der Gruppe - im echten Lauf kommt sie vom Server
#: (``link_url``). Ohne sie weist der Kern den Text zurueck, statt ihn
#: mit rohem ``{link}`` abzusenden.
LINK_URL = "https://go.b-tarikak.de/r/k7m2x9q"

WOHNUNG = "Suche dringend 2-Zimmer-Wohnung in Stuttgart"
VERSAND = "كيف فيني ابعت غرض صغير من ألمانيا لسوريا؟"


def _post(url: str, text: str, *, laut: int = 0) -> dict:
    return {
        "post_url": url,
        "text": text,
        "interactions": laut,
        "comments": 0,
        "posted_at": None,
    }


class _Kommentator:
    """Statt Playwright: haelt fest, wohin kommentiert wurde und womit."""

    def __init__(self) -> None:
        self.aufrufe: list[tuple[str, str]] = []

    def __call__(self, _context, post_url: str, text: str) -> bool:
        self.aufrufe.append((post_url, text))
        return True


@pytest.fixture()
def config():
    from fbgroups.config import load_config

    return load_config()


# --- Die Grundlagen reisen mit ---------------------------------------------

def test_der_server_schickt_die_entscheidungsgrundlagen_mit(
    tmp_path: Path, config
) -> None:
    """Ohne sie kann der Arbeitsrechner gar nicht pruefen.

    Er haelt keinen Bestand. Deshalb rechnet der Server sie und schickt
    **Wahrheitswerte und Kennungen** mit - keinen Datensatz.
    """
    quelltext = Path("src/fbgroups/marketing/web.py").read_text(encoding="utf-8")

    assert '"vorgaben": vorgaben' in quelltext
    # Seit dem 23.09.2026 fuer jede Gruppe dieselbe Erlaubnis.
    assert "erlaubnis = Erlaubnis()" in quelltext
    # Seit dem 23.09.2026 eine Schwelle fuer alle Gruppen, nicht je Klasse.
    assert "automatik.anspruch_aus_config(cfg)" in quelltext


def test_fehlende_vorgaben_ergeben_die_gewoehnliche_erlaubnis() -> None:
    """Fehlt das Feld, gilt ``Erlaubnis()`` - seit dem 23.09.2026 mit Link."""
    erlaubnis, anspruch, verbraucht = automatik.vorgaben_lesen(None)

    assert erlaubnis == Erlaubnis()
    assert erlaubnis.links is True
    assert anspruch.mindestrelevanz is Relevanz.MITTEL
    assert verbraucht == set()


def test_die_vorgaben_werden_vollstaendig_uebersetzt() -> None:
    """Was der Server rechnet, muss hier ankommen - sonst gilt es nicht."""
    erlaubnis, anspruch, verbraucht = automatik.vorgaben_lesen(
        {
            "erlaubnis": {
                "kommentare": True, "beitraege": False, "links": True,
                "werbung": True, "privatkontakt": True, "regeln_gelesen": True,
            },
            "anspruch": {"mindestrelevanz": "hoch", "verlangt_strecke": True},
            "verbrauchte_vorlagen": ["ar/anlaesse/geschenk/hadiye"],
        }
    )

    assert erlaubnis == Erlaubnis(
        kommentare=True, beitraege=False, links=True,
        privatkontakt=True,
    )
    assert anspruch == Anspruch(mindestrelevanz=Relevanz.HOCH, verlangt_strecke=True)
    assert verbraucht == {"ar/anlaesse/geschenk/hadiye"}


# --- Die Kette selbst ------------------------------------------------------

def test_der_passendste_beitrag_schlaegt_den_lautesten(config) -> None:
    """**Die Zusicherung dieser Datei.**

    Vorher entschied allein ``interactions + comments``. Der Kommentar ueber
    Paketmitnahme landete dann unter dem Wohnungsgesuch mit hundert
    Reaktionen - also ausgerechnet dort, wo ihn die meisten sehen.
    """
    kommentator = _Kommentator()
    ergebnis = automatik.entscheide_und_kommentiere(
        None,
        config,
        [_post("p/laut", WOHNUNG, laut=100), _post("p/passend", VERSAND)],
        "g1",
        "Rueckfalltext {link}",
        kommentieren=kommentator,
        bisherige=[],
        # ``werbung`` erlaubt: Sonst bliebe es beim privaten Hinweis, und
        # fuer den gibt es bewusst keinen Vorrat - dann wird gar nicht
        # kommentiert. Hier geht es um die **Auswahl**, nicht um die Stufe.
        erlaubnis=Erlaubnis(),
        anspruch=Anspruch(),
        link_url=LINK_URL,
    )

    assert ergebnis.erfolg is True
    assert [url for url, _ in kommentator.aufrufe] == ["p/passend"]


def test_ohne_anlass_wird_nicht_kommentiert(config) -> None:
    """``no_reply`` ist ein Ergebnis, kein Ausfall.

    Steht in der Gruppe heute nichts, worauf eine Antwort etwas beitruege,
    geht auch nichts hinaus - und es zaehlt weder gegen die Fassung noch
    gegen die Gruppe (``kein_anlass``).
    """
    kommentator = _Kommentator()
    ergebnis = automatik.entscheide_und_kommentiere(
        None,
        config,
        [_post("p/1", WOHNUNG, laut=100), _post("p/2", "شو أحسن مطعم عربي بشتوتغارت؟")],
        "g1",
        "Rueckfalltext {link}",
        kommentieren=kommentator,
        bisherige=[],
        erlaubnis=Erlaubnis(),
        anspruch=Anspruch(),
        link_url=LINK_URL,
    )

    assert ergebnis.erfolg is False
    assert ergebnis.kein_anlass is True
    assert kommentator.aufrufe == [], "es darf nichts abgesetzt worden sein"


def test_eine_gruppe_ohne_links_bekommt_einen_text_ohne_link(config) -> None:
    """Die Regeln der Gruppe binden - auch wenn der Beitrag passt.

    Punkt 4 der Anforderung: Der Runner darf keinen Link setzen, wo Links
    verboten sind. Reagiert wird trotzdem, nur ohne Adresse.
    """
    kommentator = _Kommentator()
    automatik.entscheide_und_kommentiere(
        None,
        config,
        [_post("p/1", VERSAND)],
        "g1",
        "Rueckfall {link}",
        kommentieren=kommentator,
        bisherige=[],
        erlaubnis=Erlaubnis(links=False),
        anspruch=Anspruch(),
        link_url=LINK_URL,
    )

    assert len(kommentator.aufrufe) == 1
    _, text = kommentator.aufrufe[0]
    assert "{link}" not in text
    assert "http" not in text


def test_der_fernbetrieb_bricht_bei_einem_kommentarverbot_ab(monkeypatch) -> None:
    """Und zwar **bevor** ein Beitrag gelesen oder etwas geschrieben wird.

    ``kein_anlass`` statt ``erfolg=False``: Es ist kein Fehlschlag, der gegen
    die Fassung zaehlt. Der Server kann Kommentare fuer eine Gruppe ueber
    ``vorgaben`` abschalten, auch wenn er es derzeit fuer keine tut.
    """
    gelesen: list[str] = []

    def fake_fetch(_context, url: str, _gid: str, limit: int = 10):  # noqa: ARG001
        gelesen.append(url)
        return [_post("p/1", VERSAND)]

    monkeypatch.setattr(
        "fbgroups.automation.actions.fetch_top_posts", fake_fetch, raising=False
    )
    ergebnis = automatik.browser_schritt_fern(
        None,
        "https://www.facebook.com/groups/1",
        "g1",
        "Text",
        [],
        {"erlaubnis": {"kommentare": False}},
    )

    assert ergebnis.erfolg is False
    assert ergebnis.kein_anlass is True
    assert "Kommentare fuer diese Gruppe abgeschaltet" in ergebnis.fehler


# --- Beide Wege, eine Kette ------------------------------------------------

def test_beide_wege_rufen_denselben_kern_auf() -> None:
    """Eine zweite Fassung waere eine zweite Zaehlweise fuer dieselben Kommentare.

    ``waehle_und_kommentiere`` (oertlich, holt aus dem Bestand) und
    ``browser_schritt_fern`` (fern, bekommt es gereicht) muenden beide in
    ``entscheide_und_kommentiere``.
    """
    quelltext = Path("src/fbgroups/marketing/automatik.py").read_text(encoding="utf-8")
    kern = "entscheide_und_kommentiere("

    # Einmal die Definition, einmal je Weg der Aufruf.
    assert quelltext.count(kern) >= 3

    # Nur der **Rumpf** von ``browser_schritt_fern``, ohne seinen Docstring:
    # Der zitiert die alte Zeile, und eine Pruefung, die daran haengenbleibt,
    # prueft den Kommentar statt des Programms.
    fern = quelltext.split("def browser_schritt_fern(", 1)[1].split(chr(10) + "def ", 1)[0]
    rumpf = fern.split(chr(34) * 3, 2)[2] if fern.count(chr(34) * 3) >= 2 else fern

    assert kern in rumpf, "der Fernbetrieb muss durch den Kern gehen"
    assert "max(" not in rumpf, "der lauteste Beitrag darf nicht mehr gewinnen"


def test_die_entscheidung_faellt_vor_dem_kommentar(config) -> None:
    """Punkt 6: erst pruefen, dann handeln - nicht umgekehrt.

    Der Nachweis ist, dass bei ``no_reply`` **gar nichts** abgesetzt wird:
    Wuerde erst kommentiert und danach geprueft, stuende der Kommentar schon
    in der Gruppe.
    """
    kommentator = _Kommentator()
    automatik.entscheide_und_kommentiere(
        None,
        config,
        [_post("p/1", WOHNUNG)],
        "g1",
        "Rueckfall {link}",
        kommentieren=kommentator,
        bisherige=[],
        erlaubnis=Erlaubnis(),
        anspruch=Anspruch(mindestrelevanz=Relevanz.HOCH),
        link_url=LINK_URL,
    )

    assert kommentator.aufrufe == []


def test_die_entscheidung_steht_im_ergebnis(config) -> None:
    """Protokolliert wird, was entschieden wurde - nicht nur, was geschah.

    ``vorlage_key`` nennt die benutzte Fassung; der Text geht als
    ``Schrittergebnis.text`` zurueck, damit die Buchung genau den festhaelt,
    der wirklich hinausging.
    """
    ergebnis = automatik.entscheide_und_kommentiere(
        None,
        config,
        [_post("p/1", VERSAND)],
        "g1",
        "Rueckfall {link}",
        kommentieren=_Kommentator(),
        bisherige=[],
        erlaubnis=Erlaubnis(links=True),
        anspruch=Anspruch(),
        link_url=LINK_URL,
    )

    assert ergebnis.erfolg is True
    assert ergebnis.text, "der abgesetzte Text gehoert in den Ausgang"
    assert ergebnis.vorlage_key.startswith("ar/anlaesse/")


def test_die_antwortarten_sind_die_der_anforderung() -> None:
    """Die Namen aus Punkt 6 - sie sind bereits im Projekt definiert."""
    vorhanden = {a.value for a in Antwortart}

    assert {
        "no_reply",
        "private_contact_suggestion",
        "contextual_app_mention",
        "direct_app_recommendation",
    } <= vorhanden
    assert {m.value for m in Linkmodus} == {"no_link", "app_name_only", "tracking_link"}


def test_der_server_erfaehrt_welche_fassung_hinausging(config) -> None:
    """Eine Kennung reist zurueck, kein Text - und sie genuegt.

    Seit der Fernbetrieb seinen Kommentar selbst waehlt, weiss der Server
    nicht mehr aus seiner eigenen Vorbereitung, was in der Gruppe steht.
    Ohne diesen Weg stuende dort weiter die vorbereitete Fassung, und die
    Uebersicht zeigte einen Satz, der nie abgesetzt wurde.

    Der Text selbst bleibt draussen: Der Server baut ihn aus der Kennung neu
    (beide fahren dieselbe ``textvorlagen.yaml``) - dieselbe Sparsamkeit wie
    beim Regelbefund, und dieselbe Sicherheit wie beim Ergebnisformular der
    Arbeitsseite.
    """
    from fbgroups.marketing.vorlagen import Personalisierung, anlasstext, anlasstext_zu

    treffer = anlasstext(
        config,
        sprache="ar",
        anlass="gegenstand",
        group_id="g1",
        daten=Personalisierung(zielgruppe="", stadt=""),
        mit_link=True,
    )
    assert treffer is not None, "Aufbau: es sollte einen Vorrat geben"
    schluessel, text = treffer

    assert anlasstext_zu(config, schluessel, mit_link=True) == text
    # Ohne Link ergibt dieselbe Kennung einen anderen Satz - deshalb faehrt
    # ``mit_link`` mit.
    assert anlasstext_zu(config, schluessel, mit_link=False) != text
    assert "{link}" not in anlasstext_zu(config, schluessel, mit_link=False)


def test_eine_unbekannte_kennung_verhindert_keine_buchung(config) -> None:
    """Wer eine Vorlage entfernt, soll damit keinen Ausgang verschlucken.

    ``""`` heisst "nicht aufloesbar" und ist kein Fehler: Festgehalten wird
    dann eben nur der Ausgang - das ist weniger, aber nichts Falsches.
    """
    assert anlasstext_zu_leer(config) == ""


def anlasstext_zu_leer(config) -> str:
    from fbgroups.marketing.vorlagen import anlasstext_zu

    return anlasstext_zu(config, "ar/anlaesse/gibtsnicht/x", mit_link=False)


def test_der_ergebnisweg_nimmt_weiterhin_keinen_text_entgegen() -> None:
    """Die Regel von der Arbeitsseite gilt hier unveraendert.

    Ein manipulierter Aufruf darf keinen anderen Text in einen Kommentar
    bringen als den, den der Vorrat hergibt. Eine **Kennung** kann nur auf
    eine vorbereitete Fassung zeigen - ein Textfeld koennte alles tragen.
    """
    from fbgroups.marketing.web import AutomatikErgebnis

    assert "text" not in AutomatikErgebnis.model_fields
    assert "vorlage_key" in AutomatikErgebnis.model_fields


# --- Der Platzhalter geht nie in eine Gruppe -------------------------------

def test_der_abgesetzte_text_traegt_die_adresse_und_nicht_den_platzhalter(
    config,
) -> None:
    """**Der Fehler vom 14.09.2026, in einer Zusicherung.**

    In einer Gruppe stand:

        فيك تنشر طلبك على بطريقك وتشوف إذا في مسافر مناسب.
        {link}

    Der Anlasstext (seit 13.09.2026) ersetzt den vorbereiteten Text, und der
    vorbereitete war aufgeloest - der neue nicht. Zwischen "Text waehlen" und
    "Text absenden" fehlte die Ersetzung ganz. Der Kommentar sah richtig aus,
    und seine Gruppe bekam nie einen Klick gutgeschrieben.
    """
    kommentator = _Kommentator()
    ergebnis = automatik.entscheide_und_kommentiere(
        None,
        config,
        [_post("p/1", VERSAND)],
        "g1",
        "Rueckfall {link}",
        kommentieren=kommentator,
        bisherige=[],
        erlaubnis=Erlaubnis(links=True),
        anspruch=Anspruch(),
        link_url=LINK_URL,
    )

    assert ergebnis.erfolg is True
    _, abgesetzt = kommentator.aufrufe[0]
    assert "{link}" not in abgesetzt
    assert LINK_URL in abgesetzt

    # **Gespeichert wird die Fassung, nicht ihre Ausfertigung.** Der Text im
    # Bestand traegt weiterhin den Platzhalter - aufgeloest wird beim Lesen,
    # nie beim Ablegen. Sonst stuende der Tracking-Code in der Datenbank.
    assert "{link}" in ergebnis.text
    assert LINK_URL not in ergebnis.text


def test_ohne_adresse_wird_lieber_nicht_kommentiert(config) -> None:
    """Lieber kein Kommentar als ein kaputter.

    Fehlt die Adresse - ein aelterer Server, ein Feld vergessen -, geht
    nichts hinaus. Ein Text mit ``{link}`` sieht richtig aus und ist
    trotzdem wertlos; zurueckholen laesst er sich nicht.
    """
    kommentator = _Kommentator()
    ergebnis = automatik.entscheide_und_kommentiere(
        None,
        config,
        [_post("p/1", VERSAND)],
        "g1",
        "Rueckfall {link}",
        kommentieren=kommentator,
        bisherige=[],
        erlaubnis=Erlaubnis(links=True),
        anspruch=Anspruch(),
        link_url="",
    )

    assert ergebnis.erfolg is False
    assert "Platzhalter nicht aufgeloest" in ergebnis.fehler
    assert "{link}" in ergebnis.fehler
    assert kommentator.aufrufe == [], "es darf nichts abgesetzt worden sein"


def test_ein_text_ohne_link_geht_weiterhin_hinaus(config) -> None:
    """Eine linkscheue Gruppe bekommt einen Kommentar ohne Adresse.

    Die Pruefung darf nicht zum Linkzwang werden: Was keinen Platzhalter
    traegt, braucht auch keine Adresse.
    """
    kommentator = _Kommentator()
    ergebnis = automatik.entscheide_und_kommentiere(
        None,
        config,
        [_post("p/1", VERSAND)],
        "g1",
        "Rueckfall {link}",
        kommentieren=kommentator,
        bisherige=[],
        erlaubnis=Erlaubnis(links=False),
        anspruch=Anspruch(),
        link_url="",
    )

    assert ergebnis.erfolg is True
    _, abgesetzt = kommentator.aufrufe[0]
    assert "{" not in abgesetzt


def test_der_server_schickt_die_adresse_getrennt_vom_text() -> None:
    """Sie steht neben ``text``, nicht darin.

    Der Arbeitsrechner waehlt seinen Kommentar selbst; der vorbereitete Text
    ist dann nicht mehr der, der hinausgeht - seine eingesetzte Adresse also
    auch nicht mehr erreichbar.
    """
    quelltext = Path("src/fbgroups/marketing/web.py").read_text(encoding="utf-8")

    assert '"link_url": link.url_fuer(ziel)' in quelltext


# --- "Kein Anlass" ist auch im Fernbetrieb kein Fehlschlag -----------------

def test_kein_anlass_wird_gemeldet_und_nicht_als_fehlschlag_gebucht() -> None:
    """**Die zweite Haelfte des Fehlers vom 14.09.2026.**

    Der oertliche Lauf legt die Gruppe bei ``kein_anlass`` fuer diesen
    Durchgang beiseite und bucht **nichts**. Der Fernbetrieb meldete nur
    ``erfolg=False`` - und der Server buchte einen gescheiterten Versuch
    gegen die Fassung. Nach dreien galt sie als verbraucht, irgendwann die
    Gruppe als erschoepft.

    Das ist dieselbe Verwechslung, an der am 11.09.2026 45 Gruppen zu
    Unrecht ausgeschieden sind - nur an einer anderen Stelle und ein
    Vierteljahr spaeter.
    """
    from fbgroups.marketing.web import AutomatikErgebnis

    assert "kein_anlass" in AutomatikErgebnis.model_fields
    assert "lauf_id" in AutomatikErgebnis.model_fields

    quelltext = Path("src/fbgroups/marketing/automatik.py").read_text(encoding="utf-8")
    assert '"kein_anlass": ergebnis.kein_anlass' in quelltext, "der Treiber muss es melden"

    web = Path("src/fbgroups/marketing/web.py").read_text(encoding="utf-8")
    endpunkt = web.split("def automatik_ergebnis(", 1)[1].split("\n    @app.", 1)[0]
    assert "if meldung.kein_anlass:" in endpunkt
    assert "ueberspringe_gruppe" in endpunkt
    # Und zwar **vor** der Buchung - sonst zaehlte der Versuch trotzdem.
    assert endpunkt.index("if meldung.kein_anlass:") < endpunkt.index("melde_vorschlag(")


def test_kein_anlass_zaehlt_nicht_gegen_den_technikwaechter() -> None:
    """Der Waechter sieht "kein Anlass" nicht - und eine tote Adresse auch nicht.

    Der Browser arbeitet ja. Seit dem 20.09.2026 beendet ein technischer
    Fehlschlag den Lauf ohnehin nicht mehr (die Gruppe faellt aus der
    Kampagne, nicht der Lauf); gemeldet wird trotzdem nur, was etwas ueber
    den Rechner sagen koennte.
    """
    quelltext = Path("src/fbgroups/marketing/automatik.py").read_text(encoding="utf-8")

    assert "not ergebnis.kein_anlass" in quelltext
    assert "and not ergebnis.beitrag_weg" in quelltext
    assert "and technik.melde(ergebnis)" in quelltext
