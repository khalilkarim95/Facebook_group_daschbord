"""Die gepflegte Note entscheidet, nicht die gerechnete Klasse (21.09.2026).

Die Mitgliederliste traegt je Gruppe eine Note - A++, A+, A, B+, B. Ein
Mensch hat die Gruppe angesehen und sie eingestuft. Bis hierhin entschied
trotzdem die **erschlossene** Klasse (A-D aus Name, Kategorie, Zielgruppe,
Stadt), wie viel ein Beitrag dort hergeben muss - und weil dieselbe Liste in
``category`` durchgehend "Unbekannt" traegt, war das fast ueberall
"C: hoch + Strecke": die Schwelle, an der im Betrieb jeder Kommentar
scheiterte.

Seit dem 21.09.2026 gilt: **Note vor Klasse.** Dieselbe Rangfolge wie
zwischen gepflegter Kategorie und ``kategoriebegriffe``, und aus demselben
Grund - Handarbeit schlaegt Worterkennung.

Drei Dinge haengen daran, und jedes wird hier einzeln festgehalten: die
Schwelle je Beitrag, die Frage, ob in der Gruppe ueberhaupt gearbeitet wird,
und die Reihenfolge der Arbeit.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fbgroups.config import AppConfig
from fbgroups.marketing import automatik, zielgruppe
from fbgroups.marketing.inhalt import Relevanz
from fbgroups.marketing.lauf import Gruppenfortschritt, Kampagnenfortschritt
from fbgroups.marketing.models import PostStatus
from fbgroups.marketing.zielgruppe import Zielprioritaet
from fbgroups.models import Group
from fbgroups.storage import SqliteStore


class _Bestand:
    """Eine Konfiguration, deren Bestand in einer Wegwerfdatei liegt."""

    def __init__(self, config: AppConfig, pfad: Path) -> None:
        self._config = config
        self._pfad = pfad

    def get(self, *pfad: str, default: object = None) -> object:
        return self._config.get(*pfad, default=default)

    def path(self, *_name: str) -> Path:
        return self._pfad


@pytest.fixture
def bestand(config: AppConfig, tmp_path: Path) -> _Bestand:
    return _Bestand(config, tmp_path / "groups.sqlite")


def _speichere(bestand: _Bestand, *gruppen: Group) -> None:
    with SqliteStore(bestand.path()) as store:
        store.upsert_groups(list(gruppen))


def _gruppe(gid: str, name: str, note: str | None = None) -> Group:
    return Group(
        group_id=gid,
        url_canonical=f"https://www.facebook.com/groups/{gid}/",
        name=name,
        listenprioritaet=note,
    )


def _stand(note: str = "", klasse: Zielprioritaet = Zielprioritaet.D) -> Gruppenfortschritt:
    return Gruppenfortschritt(
        campaign_id="k1",
        group_id="1",
        name="Gruppe",
        veroeffentlicht=0,
        ziel=10,
        mitglied=True,
        regeln_gelesen=True,
        post_status=PostStatus.VEROEFFENTLICHT,
        zielprioritaet=klasse,
        bearbeitbare_klassen=zielgruppe.BEARBEITBAR,
        note=note,
    )


# --- Die Schwelle je Beitrag ----------------------------------------------


@pytest.mark.parametrize(
    ("note", "erwartet"),
    [("A++", Relevanz.MITTEL), ("A+", Relevanz.MITTEL), ("A", Relevanz.MITTEL),
     ("B+", Relevanz.HOCH), ("B", Relevanz.HOCH)],
)
def test_die_note_bestimmt_die_schwelle(
    bestand: _Bestand, note: str, erwartet: Relevanz
) -> None:
    """Genau das, was der Nutzer verlangt hat: A-Noten mittel, B-Noten hoch."""
    _speichere(bestand, _gruppe("1", "شركة شحن دولي سوريا", note))
    anspruch = automatik.anspruch_fuer(bestand, "1")
    assert anspruch.mindestrelevanz is erwartet


def test_die_note_verlangt_keine_ausgeschriebene_strecke(bestand: _Bestand) -> None:
    """Die Zusatzforderung, an der im Betrieb jeder Kommentar scheiterte.

    Sie ergibt keinen Sinn, wo ein Mensch die Gruppe bereits beurteilt hat -
    sie ist der Ersatz fuer ein Urteil, das hier vorliegt.
    """
    _speichere(bestand, _gruppe("1", "سوق المستعمل في برلين", "A++"))
    assert not automatik.anspruch_fuer(bestand, "1").verlangt_strecke


def test_ohne_note_entscheidet_weiterhin_die_klasse(bestand: _Bestand) -> None:
    """Die alte Regel bleibt - sie gilt nur nicht mehr gegen ein Urteil."""
    _speichere(bestand, _gruppe("1", "سوق المستعمل في برلين"))
    anspruch = automatik.anspruch_fuer(bestand, "1")
    assert anspruch.mindestrelevanz is Relevanz.HOCH
    assert anspruch.verlangt_strecke


def test_die_note_schlaegt_die_klasse_auch_wenn_diese_strenger_waere(
    bestand: _Bestand,
) -> None:
    """Der Fall aus dem Protokoll: Klasse C, Note A++.

    "سوق المستعمل في برلين" traegt kein Thema und kein Ziel im Namen - die
    Worterkennung kommt auf ``D``. Auf der Liste steht sie mit ``A++``, und
    die Liste hat ein Mensch gefuehrt.
    """
    _speichere(bestand, _gruppe("1", "سوق المستعمل في برلين", "A++"))
    regeln = zielgruppe.regeln_aus_config(bestand)
    with SqliteStore(bestand.path()) as store:
        gruppe = store.get_group("1")
    assert gruppe is not None
    assert zielgruppe.aus_group(gruppe, regeln).prioritaet is Zielprioritaet.D
    assert automatik.anspruch_fuer(bestand, "1").mindestrelevanz is Relevanz.MITTEL


# --- Wird hier ueberhaupt gearbeitet? -------------------------------------


def test_eine_benotete_gruppe_wird_immer_bearbeitet() -> None:
    """Sie steht auf einer Liste, die ein Mensch von Hand gefuehrt hat."""
    assert _stand(note="A++", klasse=Zielprioritaet.D).bearbeitbar


def test_ohne_note_schliesst_die_klasse_d_weiterhin_aus() -> None:
    assert not _stand(note="", klasse=Zielprioritaet.D).bearbeitbar


def test_eine_benotete_gruppe_gilt_nicht_als_uebergangen() -> None:
    """Sonst meldete die Zeile Gruppen als uebergangen, an denen gearbeitet wird."""
    kampagne = Kampagnenfortschritt(
        campaign_id="k1",
        name="K1",
        gruppen=[
            _stand(note="A++", klasse=Zielprioritaet.D),
            _stand(note="", klasse=Zielprioritaet.D),
        ],
    )
    assert kampagne.gruppen_ausserhalb == 1


# --- Die Reihenfolge ------------------------------------------------------


def test_die_note_ordnet_die_arbeitsliste() -> None:
    """A++ zuerst, B zuletzt, ohne Note dahinter - und zwar **vor** der Klasse.

    Die zweite Haelfte ist der Punkt: Eine Gruppe mit Note ``A++`` und
    erschlossener Klasse ``D`` steht vor einer ohne Note mit Klasse ``A``.
    Das Urteil eines Menschen kommt vor einem Schluss aus einem Namen.
    """
    kampagne = Kampagnenfortschritt(
        campaign_id="k1",
        name="K1",
        gruppen=[
            _stand(note="", klasse=Zielprioritaet.A),
            _stand(note="B", klasse=Zielprioritaet.C),
            _stand(note="A++", klasse=Zielprioritaet.D),
            _stand(note="A+", klasse=Zielprioritaet.C),
        ],
    )
    assert [g.note for g in kampagne.arbeitsliste] == ["A++", "A+", "B", ""]


def test_der_rang_kennt_die_schreibweise_nicht() -> None:
    assert zielgruppe.notenrang("a++") == 0
    assert zielgruppe.notenrang(" B ") == 4
    assert zielgruppe.notenrang(None) == zielgruppe.OHNE_NOTE


# --- Die Tabelle ist Konfiguration ----------------------------------------


def test_ohne_tabelle_bleibt_alles_bei_der_klasse() -> None:
    """Der Code erfindet keine Schwelle je Note - eine geratene waere genau das,
    was diese Aenderung abschafft."""

    class _Leer:
        def get(self, *_pfad, default=None):  # noqa: ANN002, ANN003
            return {}

    assert zielgruppe.anspruch_aus_note(_Leer()) == {}


def test_die_tabelle_steht_in_der_konfiguration(config: AppConfig) -> None:
    tabelle = zielgruppe.anspruch_aus_note(config)
    assert set(tabelle) == {"A++", "A+", "A", "B+", "B"}
    assert tabelle["A++"] == (Relevanz.MITTEL, False)
    assert tabelle["B"] == (Relevanz.HOCH, False)


def test_eine_unbekannte_note_in_der_konfiguration_wird_uebergangen() -> None:
    """Ein Tippfehler darf keine Schwelle erfinden."""

    class _Tippfehler:
        def get(self, *_pfad, default=None):  # noqa: ANN002, ANN003
            return {"mindestrelevanz_note": {"A++": "sehr_hoch", "A+": "mittel"}}

    tabelle = zielgruppe.anspruch_aus_note(_Tippfehler())
    assert set(tabelle) == {"A+"}


def test_der_bestand_bleibt_unberuehrt(bestand: _Bestand) -> None:
    """Die Note wird gelesen, nicht geschrieben - sie ist Handarbeit."""
    _speichere(bestand, _gruppe("1", "Test", "A++"))
    automatik.anspruch_fuer(bestand, "1")
    with SqliteStore(bestand.path()) as store:
        gruppe = store.get_group("1")
    assert gruppe is not None
    assert gruppe.listenprioritaet == "A++"


def test_eine_unbekannte_gruppe_bekommt_die_vorgabe(bestand: _Bestand) -> None:
    """Eine fehlende Angabe ist kein Urteil."""
    _speichere(bestand, _gruppe("1", "Test"))
    anspruch = automatik.anspruch_fuer(bestand, "gibt-es-nicht")
    assert anspruch.mindestrelevanz is Relevanz.MITTEL
