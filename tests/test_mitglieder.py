"""Die eigene Mitgliederliste einlesen.

Geprueft wird vor allem das, was die Quelltabelle **anders meint, als ihre
Spaltennamen versprechen** - drei Fallen, die jede fuer sich erst weit spaeter
auffallen wuerde: eine Gruppe, die nie in Klasse A auftaucht; ein falscher
Ortsname in einem Beitrag; ein Wort aus der Facebook-Oberflaeche als
Gruppenname im Export.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fbgroups.config import AppConfig
from fbgroups.mitglieder import (
    Einlesebericht,
    kategorie_aus,
    lies_mitgliederdatei,
    lies_seitenangaben,
    zeile_zu_gruppe,
)
from fbgroups.models import ActivitySource, MemberCountSource, PrivacyHint

KOPFZEILE = "url,name,category,activity,rating,city,country,status,notes,last_updated,joined_at"
URL_A = "https://www.facebook.com/groups/739201847362915/"


def _zeile(**felder: str) -> dict[str, str]:
    grund = dict.fromkeys(
        (
            "url",
            "name",
            "category",
            "activity",
            "rating",
            "city",
            "country",
            "status",
            "notes",
        ),
        "",
    )
    grund["url"] = URL_A
    grund.update(felder)
    return grund


def _gruppe(config: AppConfig, **felder: str):
    bericht = Einlesebericht()
    return zeile_zu_gruppe(_zeile(**felder), 2, "test.csv", config, bericht), bericht


# --- Der Kopf der Gruppenseite ---------------------------------------------


@pytest.mark.parametrize(
    ("text", "sichtbar", "mitglieder", "beitraege"),
    [
        (
            "Öffentlich · 5.366 Mitglieder · 50+ Beiträge pro Tag",
            PrivacyHint.PUBLIC,
            5366,
            50.0,
        ),
        (
            "Privat · 41.449 Mitglieder · 20+ Beiträge pro Tag",
            PrivacyHint.PRIVATE,
            41449,
            20.0,
        ),
        # Geschuetztes Leerzeichen - so liefert Facebook es aus.
        (
            "Privat · 17.421 Mitglieder · 10 Beiträge pro Tag",
            PrivacyHint.PRIVATE,
            17421,
            10.0,
        ),
        ("Öffentlich · 942 Mitglieder", PrivacyHint.PUBLIC, 942, None),
        ("Öffentlich · 481.040 Mitglieder · 80+ Beiträge pro Tag",
         PrivacyHint.PUBLIC, 481040, 80.0),
        # Gar keine Zahl - und das ist ein Ergebnis, keine Luecke.
        ("Aktiv (نشط)", PrivacyHint.UNKNOWN, None, None),
        ("Sehr Aktiv (نشط جداً)", PrivacyHint.UNKNOWN, None, None),
        ("", PrivacyHint.UNKNOWN, None, None),
    ],
)
def test_der_seitenkopf_wird_zerlegt(text, sichtbar, mitglieder, beitraege) -> None:
    angaben = lies_seitenangaben(text)

    assert angaben.privacy_hint is sichtbar
    assert angaben.member_count == mitglieder
    assert angaben.posts_per_day == beitraege


def test_ungelesene_beitraege_sind_keine_beitragszahl() -> None:
    """Der teuerste Fehlgriff, den dieser Kopf anbietet.

    "25 ungelesene Beitraege" ist **unser eigener Postfachstand** - er sagt,
    wie viel wir nicht gelesen haben, und nichts darueber, wie viel in der
    Gruppe geschieht. Als Beitragszahl gelesen ergaebe er 25 Beitraege am Tag
    und damit den vollen Aktivitaetsfaktor: 25 von 100 Punkten fuer eine
    Gruppe, ueber deren Betrieb wir nichts wissen.
    """
    angaben = lies_seitenangaben(
        "Öffentlich · 174.725 Mitglieder · 25 ungelesene Beiträge · Mitglied seit September 2026"
    )

    assert angaben.member_count == 174725
    assert angaben.posts_per_day is None


def test_die_mitgliederzahl_gilt_als_erhoben(config: AppConfig) -> None:
    """Sie stand im Kopf der Gruppenseite - dieselbe Quelle wie beim Abruf."""
    gruppe, _ = _gruppe(config, activity="Öffentlich · 5.366 Mitglieder · 50+ Beiträge pro Tag")

    assert gruppe is not None
    assert gruppe.member_count == 5366
    assert gruppe.member_count_source is MemberCountSource.FACEBOOK
    assert gruppe.activity_source is ActivitySource.FACEBOOK
    assert gruppe.activity_confidence == 1.0
    assert gruppe.activity_factor is not None


def test_ohne_zahlen_bleibt_alles_leer(config: AppConfig) -> None:
    """Kein Ersatzwert - "Aktiv" ist keine Messung."""
    gruppe, _ = _gruppe(config, activity="Aktiv (نشط)")

    assert gruppe is not None
    assert gruppe.member_count is None
    assert gruppe.activity_factor is None
    assert gruppe.activity_source is None


# --- Die Kategorie: Anzeigename gegen Kennung ------------------------------


@pytest.mark.parametrize(
    ("roh", "erwartet"),
    [
        ("Reise & Transport", "reise"),
        ("reise & transport", "reise"),
        ("Versand & Mitnahme", "versand"),
        ("Community", "community"),
    ],
)
def test_der_anzeigename_wird_zur_kennung(roh, erwartet) -> None:
    """Ohne diese Uebersetzung greift die Zielprioritaet nie.

    ``marketing.zielprioritaet.kategorien`` nennt ``reise`` und ``versand``;
    stuende "Reise & Transport" im Bestand, traefe die Regel keine einzige
    Gruppe - und die Kampagne arbeitete wieder in den Gemeinschaftsgruppen,
    ohne dass irgendwo eine Fehlermeldung entstuende.
    """
    kennung, unbekannt = kategorie_aus(roh)

    assert kennung == erwartet
    assert unbekannt == ""


@pytest.mark.parametrize("roh", ["Allgemein", "Unbekannt", "Öffentlich", ""])
def test_werte_die_keine_kategorie_sind_bleiben_leer(roh) -> None:
    """"Oeffentlich" ist eine Sichtbarkeit und steht in der falschen Spalte."""
    kennung, unbekannt = kategorie_aus(roh)

    assert kennung is None
    assert unbekannt == ""


def test_eine_unbekannte_kategorie_wird_gemeldet_nicht_geraten(config: AppConfig) -> None:
    """Geraten verschoebe sie die Gruppe in der Rangfolge fuer Beitraege."""
    gruppe, bericht = _gruppe(config, category="Raumfahrt")

    assert gruppe is not None
    assert gruppe.category is None
    assert bericht.unbekannte_kategorien == ["Raumfahrt"]


# --- Die Stadt: Reiseziel gegen Sitz ---------------------------------------


def test_die_stadtspalte_wird_nicht_uebernommen(config: AppConfig) -> None:
    """Sie nennt das Reiseziel, nicht den Sitz der Gruppe.

    ``Group.city`` traegt 15 Score-Punkte und belegt in
    ``zielgruppe.bestimme_region`` ``Region.DE``. Mit "Damaskus" darin gaelte
    eine syrische Zielangabe als deutscher Sitz - die Gruppe stuende vor den
    tatsaechlich deutschen und bekaeme Punkte, die sie nicht verdient hat.
    """
    gruppe, bericht = _gruppe(config, city="Damaskus")

    assert gruppe is not None
    assert gruppe.city is None
    assert bericht.verworfene_staedte == ["Damaskus"]
    # Verloren ist die Angabe nicht - sie steht ausdruecklich benannt daneben.
    assert "Reiseziel laut Liste: Damaskus" in gruppe.notes


def test_das_land_wird_dagegen_uebernommen(config: AppConfig) -> None:
    """``country`` bedeutet genau das, was es heisst - anders als ``city``."""
    gruppe, _ = _gruppe(config, country="Deutschland / Europa")

    assert gruppe is not None
    assert gruppe.country == "Deutschland / Europa"


# --- Der Name: Gruppe gegen Oberflaeche ------------------------------------


def test_die_benachrichtigungsueberschrift_ist_kein_gruppenname(config: AppConfig) -> None:
    """"الإشعارات" heisst "Benachrichtigungen".

    Es ist die Ueberschrift **neben** der Gruppe und wurde beim Sammeln
    mitgenommen. Ungefiltert stuende es als Gruppenname im Bestand, wuerde
    bewertet und erschiene ueber einem Beitrag - dieselbe Falle wie ein
    Beitragstitel, der frueher als Gruppenname im Export landete.
    """
    gruppe, bericht = _gruppe(config, name="الإشعارات")

    assert gruppe is not None
    assert gruppe.name == ""
    assert bericht.ohne_namen == ["الإشعارات"]


def test_ein_echter_name_bleibt_stehen(config: AppConfig) -> None:
    gruppe, bericht = _gruppe(config, name="بطريقك عألمانيا")

    assert gruppe is not None
    assert gruppe.name == "بطريقك عألمانيا"
    assert bericht.ohne_namen == []


# --- Die ganze Datei --------------------------------------------------------


def test_eine_datei_mit_bom_verliert_keine_zeile(config: AppConfig, tmp_path: Path) -> None:
    """Notepad und ``Out-File`` schreiben ein BOM.

    Ohne ``utf-8-sig`` wuerde es Teil der Spaltenueberschrift ``url``, die
    Spalte waere unauffindbar und **jede** Zeile ginge verloren - lautlos.
    """
    datei = tmp_path / "mitglieder.csv"
    datei.write_text(
        KOPFZEILE
        + "\n"
        + f"{URL_A},Testgruppe,Reise & Transport,Öffentlich · 900 Mitglieder,A++,,,member,,,\n",
        encoding="utf-8-sig",
    )

    bericht = lies_mitgliederdatei(datei, config)

    assert bericht.zeilen_gesamt == 1
    assert len(bericht.gruppen) == 1
    assert bericht.gruppen[0].member_count == 900


def test_eine_datei_ohne_url_spalte_wird_gemeldet(config: AppConfig, tmp_path: Path) -> None:
    datei = tmp_path / "falsch.csv"
    datei.write_text("name,stadt\nTest,Berlin\n", encoding="utf-8")

    bericht = lies_mitgliederdatei(datei, config)

    assert bericht.gruppen == []
    assert bericht.fehler and "url" in bericht.fehler[0].grund


def test_eine_unbrauchbare_zeile_haelt_die_datei_nicht_auf(
    config: AppConfig, tmp_path: Path
) -> None:
    """Ein Tippfehler in Zeile 2 darf Zeile 3 nicht mitnehmen."""
    datei = tmp_path / "gemischt.csv"
    datei.write_text(
        KOPFZEILE
        + "\n"
        + "https://example.com/kein-gruppenlink,Kaputt,,,,,,member,,,\n"
        + f"{URL_A},Heil,Reise & Transport,,A++,,,member,,,\n",
        encoding="utf-8",
    )

    bericht = lies_mitgliederdatei(datei, config)

    assert len(bericht.gruppen) == 1
    assert bericht.gruppen[0].name == "Heil"
    assert len(bericht.fehler) == 1
    assert bericht.fehler[0].zeile == 2


def test_der_befehl_liest_ein_und_bewertet(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    """``import-mitglieder`` von vorn bis hinten - der einzige Weg in den Bestand.

    Am 25.09.2026 rief der Befehl ``score_all`` beim Entfernen des Trackings
    noch mit dem alten Resonanz-Argument auf und waere bei jedem Aufruf
    abgebrochen - kein Test hatte den Befehl selbst je gestartet.
    """
    import shutil

    from typer.testing import CliRunner

    from fbgroups import cli
    from fbgroups.config import load_config
    from fbgroups.storage import SqliteStore

    projekt = Path(__file__).resolve().parents[1]
    shutil.copytree(projekt / "config", tmp_path / "config")
    monkeypatch.setattr(cli, "load_config", lambda: load_config(tmp_path))
    datei = tmp_path / "liste.csv"
    datei.write_text(
        KOPFZEILE
        + "\n"
        + f"{URL_A},Syrer in Deutschland,Reise & Transport,"
        + "Öffentlich · 12.000 Mitglieder · 5 Beiträge pro Tag,A++,,,member,,,\n",
        encoding="utf-8-sig",
    )

    trocken = CliRunner().invoke(cli.app, ["import-mitglieder", str(datei), "--dry-run"])
    assert trocken.exit_code == 0, trocken.output
    assert not (tmp_path / "data" / "groups.sqlite").exists()

    echt = CliRunner().invoke(cli.app, ["import-mitglieder", str(datei)])
    assert echt.exit_code == 0, echt.output
    with SqliteStore(tmp_path / "data" / "groups.sqlite") as store:
        gruppen = store.load_groups()
    assert len(gruppen) == 1
    assert gruppen[0].score is not None
