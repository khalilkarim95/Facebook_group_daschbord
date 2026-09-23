"""Grenzen je Aktion: Tagesmenge, Takt und die Bremse der Gegenseite.

Rein wie das Modul selbst - kein Netz, keine Datenbank. Geprueft wird die
Zusage, um die es in der Anforderung vom 12.09.2026 geht (Punkte 7 bis 11):
Eine gebremste Aktion haelt **nur sich selbst** an.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta

import pytest

from fbgroups.marketing import grenzen
from fbgroups.marketing.ausgang import Ausgangsart, klassifiziere
from fbgroups.marketing.grenzen import Aktion, Grenze

JETZT = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)
OFFEN = Grenze(pro_tag=6, abstand_min=30, abstand_max=90)


@pytest.fixture()
def einstellbar(config):  # noqa: ANN001, ANN201 - AppConfig aus conftest
    """Die echte Konfiguration, aber Aenderungen gelten nur fuer diesen Test.

    Die ``config``-Fixture ist **sitzungsweit**, und ``load_config`` ist
    gepuffert: Wer hier einen Block herausnimmt, nimmt ihn jedem spaeteren
    Test heraus - und der faellt dann an einer Stelle um, die mit ihm nichts
    zu tun hat. Deshalb wird der Stand vorher kopiert und hinterher
    zurueckgelegt.
    """
    vorher = copy.deepcopy(config.settings)
    yield config
    config.settings.clear()
    config.settings.update(vorher)


# --- Die Tagesmenge --------------------------------------------------------
def test_die_tagesmenge_wird_eingehalten() -> None:
    lage = grenzen.pruefe(
        Aktion.KOMMENTAR, OFFEN, heute=6, letzte=None, jetzt=JETZT
    )

    assert not lage.moeglich
    assert "Tagesmenge erreicht" in lage.grund
    assert not lage.wartet, "heute nicht mehr ist etwas anderes als noch nicht"


def test_null_schaltet_eine_aktion_ganz_ab() -> None:
    """Nicht "unbegrenzt", sondern "gar nicht" - wie Gewicht 0 im Scoring."""
    aus = Grenze(pro_tag=0, abstand_min=30, abstand_max=90)

    lage = grenzen.pruefe(Aktion.POST, aus, heute=0, letzte=None, jetzt=JETZT)

    assert not lage.moeglich
    assert "abgeschaltet" in lage.grund


def test_eine_antwort_zaehlt_als_kommentar() -> None:
    """Punkt 25: Sonst waere die eigene Grenze mit zwei Woertern umgangen."""
    assert grenzen.aus_texttyp("kommentar") is Aktion.KOMMENTAR
    assert grenzen.aus_texttyp("kommentar_antwort") is Aktion.KOMMENTAR
    assert grenzen.aus_texttyp("post") is Aktion.POST


# --- Der Takt --------------------------------------------------------------
def test_der_abstand_haelt_an_ohne_die_menge_zu_verbrauchen() -> None:
    lage = grenzen.pruefe(
        Aktion.KOMMENTAR,
        OFFEN,
        heute=1,
        letzte=JETZT - timedelta(minutes=5),
        jetzt=JETZT,
    )

    assert not lage.moeglich
    assert lage.wartet, "nur eine Frage der Zeit"
    assert "Min" in lage.wartezeit
    assert lage.rest_heute == 5


def test_nach_dem_abstand_geht_es_weiter() -> None:
    lage = grenzen.pruefe(
        Aktion.KOMMENTAR,
        OFFEN,
        heute=1,
        letzte=JETZT - timedelta(hours=4),
        jetzt=JETZT,
    )

    assert lage.moeglich
    assert lage.rest_heute == 5


def test_der_abstand_ist_gestreut_aber_derselbe_beim_zweiten_lesen() -> None:
    """Deterministisch geseedet wie im Kaltmodus.

    Sonst zeigte eine Anzeige bei jedem Neuladen eine andere Wartezeit, und
    keine davon waere die geltende.
    """
    letzte = JETZT - timedelta(minutes=1)
    erst = grenzen.naechster_zeitpunkt(letzte, OFFEN, jetzt=JETZT)
    wieder = grenzen.naechster_zeitpunkt(letzte, OFFEN, jetzt=JETZT)

    assert erst == wieder
    assert erst is not None
    assert OFFEN.abstand_min <= (erst - letzte).total_seconds() / 60 <= OFFEN.abstand_max


# --- Die Bremse ------------------------------------------------------------
@pytest.mark.parametrize(
    "meldung",
    [
        "Du hast diese Funktion zu oft verwendet",
        "Dein Konto ist voruebergehend gesperrt",
        "You're temporarily blocked from commenting",
        "Please try again later",
        "تم حظر هذا الإجراء مؤقتا",
    ],
)
def test_eine_bremse_ist_weder_technik_noch_moderation(meldung: str) -> None:
    """Punkt 5 und 8 zusammen: Sie klingt wie eine Ablehnung und ist keine.

    "Voruebergehend gesperrt" enthaelt "gesperrt". Wer das als Moderation
    liest, verurteilt eine Gruppe fuer **unsere** Eile.
    """
    assert klassifiziere(meldung) is Ausgangsart.RATE_LIMIT


def test_eine_echte_ablehnung_bleibt_moderation() -> None:
    """Sonst waere die Unterscheidung eine Abschaltung der Moderation."""
    assert klassifiziere("Der Kommentar wurde abgelehnt: Spam") is Ausgangsart.MODERATION
    assert klassifiziere("Link in Kommentar") is Ausgangsart.MODERATION


def test_eine_gebremste_aktion_ruht_und_nennt_die_zeit() -> None:
    lage = grenzen.pruefe(
        Aktion.KOMMENTAR,
        OFFEN,
        heute=0,
        letzte=None,
        gesperrt_bis=JETZT + timedelta(minutes=45),
        jetzt=JETZT,
    )

    assert not lage.moeglich
    assert lage.wartet
    assert "gebremst" in lage.grund


def test_eine_bremse_gilt_nur_ihrer_eigenen_aktion() -> None:
    """Der Kern von Punkt 7 und 8.

    Kommentare sind gebremst - Beitraege und Beitrittsanfragen laufen weiter.
    Wer hier alles anhaelt, verliert die Arbeit in den Gruppen, in denen
    nichts dagegen spricht.
    """
    gesperrt = JETZT + timedelta(hours=1)

    kommentar = grenzen.pruefe(
        Aktion.KOMMENTAR, OFFEN, heute=0, letzte=None, gesperrt_bis=gesperrt, jetzt=JETZT
    )
    beitrag = grenzen.pruefe(
        Aktion.POST,
        Grenze(pro_tag=3, abstand_min=120, abstand_max=240),
        heute=0,
        letzte=None,
        gesperrt_bis=None,
        jetzt=JETZT,
    )

    assert not kommentar.moeglich
    assert beitrag.moeglich


def test_der_backoff_verdoppelt_sich_und_ist_gedeckelt() -> None:
    """Punkt 11 und 37: kein sofortiger zweiter Versuch, aber auch kein Monat."""
    assert grenzen.backoff_minuten(1) == grenzen.BACKOFF_BASIS_MINUTEN
    assert grenzen.backoff_minuten(2) == 2 * grenzen.BACKOFF_BASIS_MINUTEN
    assert grenzen.backoff_minuten(3) == 4 * grenzen.BACKOFF_BASIS_MINUTEN
    assert grenzen.backoff_minuten(99) == grenzen.BACKOFF_HOECHSTENS_MINUTEN
    # Die nullte Bremsung soll niemand ausrechnen muessen.
    assert grenzen.backoff_minuten(0) == grenzen.BACKOFF_BASIS_MINUTEN


def test_die_sperre_endet_von_selbst() -> None:
    lage = grenzen.pruefe(
        Aktion.KOMMENTAR,
        OFFEN,
        heute=0,
        letzte=None,
        gesperrt_bis=JETZT - timedelta(minutes=1),
        jetzt=JETZT,
    )

    assert lage.moeglich


# --- Die Konfiguration -----------------------------------------------------
def test_die_grenzen_kommen_aus_der_konfiguration(config) -> None:
    """Und die Vorgaben im Code sind die vorsichtigen."""
    gelesen = grenzen.einstellungen(config)

    # ``0`` ist erlaubt und heisst "abgeschaltet" - so steht es seit dem
    # 23.09.2026 in ``settings.yaml``, bis festgelegt ist, welche Bezuege eine
    # Anfrage rechtfertigen.
    assert 0 <= gelesen.fuer(Aktion.BEITRITT).pro_tag <= 50
    assert gelesen.fuer(Aktion.KOMMENTAR).pro_tag > 0


def test_der_beitrag_bleibt_langsamer_als_der_kommentar(config) -> None:
    """Ein Beitrag steht oben in der Gruppe und faellt jedem auf, der sie
    oeffnet - ein Kommentar haengt unter einem fremden Beitrag.

    Hier stand bis zum 15.09.2026 eine feste Untergrenze (``>= 30`` Minuten).
    Sie ist der falsche Waechter: Was den Beitrag begrenzt, ist vor allem
    seine **Tagesmenge** - bei ``posts.daily: 3`` sind drei Beitraege am Tag
    das Ende, gleich wie eng der Takt steht. Geprueft wird deshalb die
    Eigenschaft, um die es wirklich geht: Der Beitrag darf nie schneller
    getaktet sein als der Kommentar, und seine Tagesmenge bleibt klein.
    """
    post = gelesen_post = grenzen.einstellungen(config).fuer(Aktion.POST)
    kommentar = grenzen.einstellungen(config).fuer(Aktion.KOMMENTAR)

    assert post.abstand_min >= kommentar.abstand_min
    assert gelesen_post.pro_tag <= 10, "ein Beitrag ist kein Kommentar"


def test_der_alte_schluessel_gilt_als_rueckfall(einstellbar) -> None:  # noqa: ANN001
    """Eine bestehende Installation verhaelt sich durch ein Update nicht anders.

    Auf dem Server steht eine ``settings.yaml`` mit
    ``beitritt.anfragen_pro_tag: 50`` und ohne die neuen Bloecke. Ohne
    Rueckfall faenden 50 Anfragen am Tag ploetzlich bei 4 ihr Ende - eine
    Aenderung, die niemand angeordnet hat.
    """
    einstellbar.settings.pop("limits", None)
    einstellbar.settings.pop("delays", None)
    einstellbar.settings["beitritt"] = {"anfragen_pro_tag": 50, "mindestabstand_minuten": 3}

    gelesen = grenzen.einstellungen(einstellbar)

    assert gelesen.fuer(Aktion.BEITRITT).pro_tag == 50
    assert gelesen.fuer(Aktion.BEITRITT).abstand_min == 3


def test_die_neuen_schluessel_gewinnen(einstellbar) -> None:  # noqa: ANN001
    """Sonst waere die Verlegung eine Verdopplung statt einer Verlegung."""
    einstellbar.settings["limits"] = {"join_requests": {"daily": 2}}
    einstellbar.settings["beitritt"] = {"anfragen_pro_tag": 50}

    assert grenzen.einstellungen(einstellbar).fuer(Aktion.BEITRITT).pro_tag == 2


def test_beitritt_liest_dieselbe_zahl(einstellbar) -> None:  # noqa: ANN001
    """Zwei Zahlen fuer dieselbe Frage waeren zwei Wahrheiten."""
    from fbgroups.marketing import beitritt

    einstellbar.settings["limits"] = {"join_requests": {"daily": 5}}

    assert beitritt.einstellungen(einstellbar)[0] == 5
    assert grenzen.einstellungen(einstellbar).fuer(Aktion.BEITRITT).pro_tag == 5


# --- Die Meldung muss den Grund nennen ------------------------------------
def test_die_abschlussmeldung_nennt_die_erreichte_tagesmenge() -> None:
    """Der schwerste Fehler zu finden ist der, der wie keiner aussieht.

    Am 12.09.2026 endete ein Lauf sofort: "Automatik NICHT vollstaendig
    abgeschlossen ... Offen bei: koeln (5/10 Kommentare)". Es war offene
    Arbeit da und trotzdem geschah nichts - weil die Tagesmengen schon
    erschoepft waren. Wer das liest, sucht den Fehler in der Technik.
    """
    from fbgroups.marketing import lauf
    from fbgroups.marketing.models import LaufStatus

    gruppe = lauf.Gruppenfortschritt(
        campaign_id="k", group_id="1", name="Arab in Koeln", veroeffentlicht=5, mitglied=True
    )
    kampagne = lauf.Kampagnenfortschritt(campaign_id="k", name="koeln", gruppen=[gruppe])
    erschoepft = {
        aktion: grenzen.pruefe(
            aktion,
            Grenze(pro_tag=menge, abstand_min=30, abstand_max=90),
            heute=menge,
            letzte=None,
            jetzt=JETZT,
        )
        for aktion, menge in ((Aktion.BEITRITT, 4), (Aktion.POST, 3), (Aktion.KOMMENTAR, 6))
    }

    meldung = lauf.abschlusstext(
        lauf.Lauffortschritt(
            lauf_id=1, status=LaufStatus.LAEUFT, kampagnen=[kampagne], aktionen=erschoepft
        )
    )

    assert "Heute nicht mehr moeglich" in meldung
    assert "kommentar: Tagesmenge erreicht (6/6)" in meldung
    assert "kein Fehler" in meldung, "sonst sucht man in der Technik"
    assert "limits" in meldung, "und findet die Stelle nicht"


def test_ohne_sperre_steht_kein_hinweis_in_der_meldung() -> None:
    """Sonst stuende unter jedem Lauf eine Warnung, die keine ist."""
    from fbgroups.marketing import lauf
    from fbgroups.marketing.models import LaufStatus

    gruppe = lauf.Gruppenfortschritt(
        campaign_id="k", group_id="1", name="G", veroeffentlicht=5, mitglied=True
    )
    kampagne = lauf.Kampagnenfortschritt(campaign_id="k", name="k", gruppen=[gruppe])

    meldung = lauf.abschlusstext(
        lauf.Lauffortschritt(lauf_id=1, status=LaufStatus.LAEUFT, kampagnen=[kampagne])
    )

    assert "Heute nicht mehr moeglich" not in meldung
