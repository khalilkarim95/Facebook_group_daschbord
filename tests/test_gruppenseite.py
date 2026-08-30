"""Tests fuer den Abruf der oeffentlichen Gruppenseite.

Der Abruf hebt eine harte Projektgrenze auf; diese Datei haelt fest, was
dabei trotzdem nicht passieren darf. Die Auswertung ist absichtlich vom Abruf
getrennt (``lies_seite`` ist rein), damit genau das ohne facebook.com
pruefbar ist: Die Auswertung ist die Stelle, an der eine erfundene Zahl
entstehen wuerde.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from fbgroups.extract.aktivitaet import (
    faktor_aus_posts_pro_tag,
    faktor_aus_treffer_daten,
    parse_relative_datum,
)
from fbgroups.extract.gruppenseite import lies_seite
from fbgroups.models import PrivacyHint

SEITE = (
    "<html><head>"
    '<meta property="og:title" content="Syrer in Bonn" />'
    '<meta property="og:description" content="Öffentliche Gruppe · 42.300 Mitglieder" />'
    "</head><body>"
    "<div><span>vor 2 Stunden</span></div>"
    "<div><span>vor 3 Tagen</span></div>"
    "</body></html>"
)


# --- Was gelesen wird ------------------------------------------------------


def test_die_seite_liefert_zahl_name_und_sichtbarkeit() -> None:
    befund = lies_seite(SEITE, "123", status_code=200)

    assert befund.erreichbar is True
    assert befund.name == "Syrer in Bonn"
    assert befund.member_count == 42300
    assert befund.privacy is PrivacyHint.PUBLIC


def test_beitragszeitpunkte_werden_gelesen_der_text_nicht() -> None:
    """Zeitpunkte ja, Beitragsinhalte nein.

    Die Grenze "keine Beitragsinhalte" hat der Nutzer **nicht** aufgehoben.
    ``Seitenbefund`` hat dafuer kein Feld, und der Zwischenspeicher haelt nur
    die Anzahl und den juengsten Zeitpunkt fest.
    """
    befund = lies_seite(SEITE, "123", status_code=200)

    assert len(befund.beitrag_daten) == 2
    assert befund.juengster_beitrag is not None
    assert not hasattr(befund, "beitrag_texte")


# --- Was NICHT passiert ----------------------------------------------------


def test_ohne_zahl_bleibt_die_mitgliederzahl_none() -> None:
    """Kein Ersatzwert, keine Schaetzung - der gefaehrlichste Fehler waere hier.

    Eine geratene Zahl saehe in der Datenbank aus wie eine gemessene und
    entschiede darueber, wo die naechsten dreihundert Beitraege hingehen.
    """
    befund = lies_seite('<meta property="og:title" content="Gruppe X" />', "1", status_code=200)

    assert befund.member_count is None
    assert befund.member_count != 0


def test_null_mitglieder_gilt_als_auswertungsfehler() -> None:
    """"0 Mitglieder" gibt es nicht - das ist ein Parserfehler, keine Gruppe."""
    befund = lies_seite("<p>0 Mitglieder</p>", "1", status_code=200)

    assert befund.member_count is None


def test_die_anmeldewand_ist_kein_befund() -> None:
    """Beantwortet, aber ohne Inhalt: ``erreichbar`` bleibt falsch.

    Facebook liefert einem nicht angemeldeten Abruf haeufig eine
    Anmeldeseite. Das ist der Normalfall und kein Fehler - aber es ist auch
    keine Auskunft ueber die Gruppe.
    """
    for wand in ("You must log in to continue", "Du musst dich anmelden"):
        befund = lies_seite(f"<html>{wand}</html>", "1", status_code=200)
        assert befund.erreichbar is False
        assert befund.member_count is None


def test_es_wird_kein_browser_nachgeahmt() -> None:
    """Die Kennung nennt das Werkzeug beim Namen.

    Der Unterschied zwischen Abrufen und Erkennung-Umgehen ist der Grund,
    warum dieses Modul ueberhaupt vertretbar ist. Ein nachgeahmter Browser
    waere Umgehung; wer uns aussperren will, soll uns erkennen koennen.
    Das gilt auch fuer die Automatisierung (Playwright).
    """
    from fbgroups.extract.gruppenseite import _KENNUNG
    from pathlib import Path

    assert "fbgroups" in _KENNUNG
    
    automation_code = Path("src/fbgroups/automation/browser.py").read_text(encoding="utf-8")
    
    for browser in ("Mozilla", "Chrome", "Safari", "AppleWebKit", "Gecko"):
        assert browser not in _KENNUNG
        assert browser not in automation_code


def test_es_gibt_keinen_login_weg() -> None:
    """Kein Login, keine Sitzungsuebernahme, keine Cookies im Modul.

    Eine angemeldete Abfrage waere die eine, die das Konto des Nutzers
    wirklich gefaehrdet - und daran haengen alle Gruppenmitgliedschaften.
    """
    from pathlib import Path

    quelle = Path("src/fbgroups/extract/gruppenseite.py").read_text(encoding="utf-8")
    code = "\n".join(z for z in quelle.splitlines() if not z.strip().startswith(("#", "*")))

    for verboten in ("c_user", "xs=", "set_cookie", "cookies=", "login(", "password"):
        assert verboten not in code, verboten


# --- Aktivitaet ------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "tage"),
    [("vor 2 Wochen", 14), ("vor 1 Tag", 1), ("vor 3 Monaten", 90), ("2 days ago", 2)],
)
def test_relative_datumsangaben(text: str, tage: int) -> None:
    jetzt = datetime(2026, 8, 27, tzinfo=UTC)
    zeitpunkt = parse_relative_datum(text, jetzt)

    assert zeitpunkt is not None
    assert abs((jetzt - zeitpunkt).days - tage) <= 1


def test_unzaehlbares_wird_nicht_geraten() -> None:
    """"gestern" ergibt kein Datum.

    Der Gewinn waere ein Datum mehr, der Preis ein ungeprueftes Datum in
    einer Spalte, die spaeter wie eine Messung aussieht.
    """
    assert parse_relative_datum("gestern") is None
    assert parse_relative_datum("letzte Woche") is None
    assert parse_relative_datum("") is None


def test_aktivitaet_ist_abgestuft_nicht_linear(config) -> None:
    """20 Beitraege am Tag sind nicht zwanzigmal so gut wie einer."""
    einer = faktor_aus_posts_pro_tag(1, config)
    zwanzig = faktor_aus_posts_pro_tag(20, config)

    assert 0 < einer < zwanzig <= 1.0
    assert zwanzig < einer * 20


def test_treffer_daten_bewerten_die_frische_nicht_die_menge(config) -> None:
    """Die Anzahl datierter Treffer haengt an unseren Anfragen, nicht an der Gruppe.

    Eine Gruppe aus der ersten Ausbaustufe haette sonst dauerhaft mehr
    "Aktivitaet" als eine gleich lebendige aus einer neuen Stadt.
    """
    jetzt = datetime(2026, 8, 27, tzinfo=UTC)
    frisch = jetzt - timedelta(days=2)

    einer = faktor_aus_treffer_daten([frisch], config, jetzt)
    viele = faktor_aus_treffer_daten([frisch] + [jetzt - timedelta(days=200)] * 8, config, jetzt)

    assert einer == viele


def test_alte_treffer_ergeben_null_keine_none(config) -> None:
    """Ein Beitrag von vor zwei Jahren ist eine Aussage, kein fehlender Wert."""
    jetzt = datetime(2026, 8, 27, tzinfo=UTC)

    assert faktor_aus_treffer_daten([jetzt - timedelta(days=730)], config, jetzt) == 0.0
    assert faktor_aus_treffer_daten([], config, jetzt) is None
