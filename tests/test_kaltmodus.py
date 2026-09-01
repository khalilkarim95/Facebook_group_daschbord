"""Kaltmodus: Tagesportion, Takt und die Auskunft ueber den Rest.

Alles offline und ohne Uhr - die Zeit kommt als Argument herein. Ein Test, der
``datetime.now()`` braucht, ist um Mitternacht ein anderer Test.

Die wichtigste Zusage steht in ``test_die_portion_ist_zugeteilt_keine_sperre``:
Der Kaltmodus darf nicht das am 27.08.2026 entfernte Tageslimit unter neuem
Namen sein. Jenes zeigte dreissig Gruppen und hielt bei zwanzig an. Dieser
zeigt die heutigen - hinter ihnen steht keine Wand.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from fbgroups.marketing.kaltmodus import (
    Tagesportion,
    einstellungen,
    naechster_zeitpunkt,
    tagesportion,
    wartezeit_text,
)
from fbgroups.marketing.models import CampaignGroup


def _reihe(anzahl: int) -> list[CampaignGroup]:
    return [
        CampaignGroup(
            campaign_id="k",
            group_id=f"g{i:03d}",
            tracking_code=f"c{i:03d}",
            tracking_url="",
            added_at="2026-08-29T10:00:00+00:00",
        )
        for i in range(anzahl)
    ]


# --- die Portion ----------------------------------------------------------
def test_die_portion_ist_zugeteilt_keine_sperre() -> None:
    """Der Kern: Die Liste enthaelt nur die heutigen Gruppen.

    Das alte Tageslimit lieferte alle dreissig und verweigerte ab der
    einundzwanzigsten die Arbeit. Wer hier dreissig Gruppen hat und
    fuenfundzwanzig am Tag faehrt, bekommt fuenfundzwanzig - und laeuft gegen
    nichts, weil die anderen fuenf gar nicht erst dastehen.
    """
    portion = tagesportion(_reihe(30), erledigt_heute=0, grenze=25)
    assert len(portion.gruppen) == 25
    assert portion.verbleibend_gesamt == 30


def test_erledigtes_verkleinert_die_portion() -> None:
    portion = tagesportion(_reihe(30), erledigt_heute=10, grenze=25)
    assert portion.offen_heute == 15
    assert len(portion.gruppen) == 15
    assert not portion.fertig_fuer_heute


def test_wenn_der_tag_voll_ist_bleibt_die_liste_leer() -> None:
    portion = tagesportion(_reihe(30), erledigt_heute=25, grenze=25)
    assert portion.fertig_fuer_heute
    assert portion.gruppen == []


def test_mehr_erledigt_als_erlaubt_ergibt_keine_negative_portion() -> None:
    """Kann vorkommen, wenn die Grenze mitten am Tag gesenkt wird."""
    portion = tagesportion(_reihe(30), erledigt_heute=40, grenze=25)
    assert portion.offen_heute == 0
    assert portion.gruppen == []


def test_die_besten_zuerst_bleibt_erhalten() -> None:
    """Die Reihe kommt fertig sortiert herein und wird nicht umgeordnet -
    sonst zeigte die Portion andere Gruppen als die Arbeitsliste daneben."""
    reihe = _reihe(10)
    portion = tagesportion(reihe, erledigt_heute=0, grenze=3)
    assert [g.group_id for g in portion.gruppen] == ["g000", "g001", "g002"]


def test_weniger_gruppen_als_die_grenze() -> None:
    portion = tagesportion(_reihe(4), erledigt_heute=0, grenze=25)
    assert len(portion.gruppen) == 4
    assert portion.resttage() == 0


# --- die Auskunft ueber den Rest ------------------------------------------
def test_fertig_am_rechnet_den_rest_auf_tage() -> None:
    """310 Gruppen zu 25 am Tag: heute 25, dann 285 - macht zwoelf weitere Tage."""
    portion = tagesportion(_reihe(310), erledigt_heute=0, grenze=25)
    assert portion.resttage() == 12
    assert portion.fertig_am(date(2026, 8, 29)) == date(2026, 9, 10)


def test_ein_angefangener_tag_ist_ein_tag() -> None:
    portion = tagesportion(_reihe(30), erledigt_heute=0, grenze=25)
    assert portion.resttage() == 1


def test_bei_grenze_null_gibt_es_kein_datum() -> None:
    """Die Kampagne steht. Ein Datum waere eine falsche Auskunft, keine fehlende."""
    portion = tagesportion(_reihe(30), erledigt_heute=0, grenze=0)
    assert portion.gruppen == []
    assert portion.fertig_am(date(2026, 8, 29)) is None


# --- der Takt -------------------------------------------------------------
def test_ohne_vorherigen_versuch_geht_es_sofort() -> None:
    jetzt = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
    assert naechster_zeitpunkt(None, abstand_minuten=4, jetzt=jetzt) is None


def test_innerhalb_des_abstands_wird_gewartet() -> None:
    """Die Pause liegt im zugesagten Band - nicht auf der Minute genau.

    Seit dem Jitter streut sie zwischen 80 % und 130 % der Grundzeit. Eine
    Zusicherung auf die Sekunde waere die Zusicherung, dass **nicht** gestreut
    wird - also das Gegenteil dessen, was das Modul tut. Geprueft wird
    deshalb, was es verspricht: dass die Pause das Band nicht verlaesst.
    """
    jetzt = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
    letzter = datetime(2026, 8, 29, 11, 58, tzinfo=UTC)
    frei_ab = naechster_zeitpunkt(letzter, abstand_minuten=4, jetzt=jetzt)

    assert frei_ab is not None
    assert letzter + timedelta(minutes=4 * 0.8) <= frei_ab
    assert frei_ab <= letzter + timedelta(minutes=4 * 1.3)
    assert wartezeit_text(frei_ab, jetzt=jetzt).startswith("noch ")


def test_dieselbe_lage_ergibt_dieselbe_pause() -> None:
    """Der Jitter streut, aber er springt nicht.

    Der Seed haengt am letzten Versuch und nicht am Zufall des Prozesses:
    Sonst zeigte die Arbeitsseite bei jedem Neuladen eine andere Wartezeit,
    und keine davon waere die geltende. Derselbe Gedanke wie bei der Wahl der
    Textfassung, die ``blake2b`` folgt und nicht ``hash()``.
    """
    jetzt = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
    letzter = datetime(2026, 8, 29, 11, 58, tzinfo=UTC)

    erste = naechster_zeitpunkt(letzter, abstand_minuten=4, jetzt=jetzt)
    zweite = naechster_zeitpunkt(letzter, abstand_minuten=4, jetzt=jetzt)

    assert erste == zweite


def test_nach_dem_abstand_geht_es_wieder() -> None:
    jetzt = datetime(2026, 8, 29, 12, 10, tzinfo=UTC)
    letzter = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
    assert naechster_zeitpunkt(letzter, abstand_minuten=4, jetzt=jetzt) is None
    assert wartezeit_text(None, jetzt=jetzt) == ""


def test_abstand_null_schaltet_den_takt_ab() -> None:
    jetzt = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
    letzter = datetime(2026, 8, 29, 11, 59, tzinfo=UTC)
    assert naechster_zeitpunkt(letzter, abstand_minuten=0, jetzt=jetzt) is None


# --- die Einstellungen ----------------------------------------------------
class _Config:
    def __init__(self, settings: dict) -> None:
        self.settings = settings

    def get(self, *keys: str, default=None):
        node = self.settings
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node


def test_ohne_block_ist_der_kaltmodus_aus() -> None:
    """Ein Update darf das Verhalten von gestern nicht stillschweigend aendern."""
    aktiv, pro_tag, abstand = einstellungen(_Config({}))
    assert aktiv is False
    assert (pro_tag, abstand) == (25, 4)


def test_werte_kommen_aus_den_einstellungen() -> None:
    config = _Config(
        {"kaltmodus": {"aktiv": True, "beitraege_pro_tag": 10, "mindestabstand_minuten": 9}}
    )
    assert einstellungen(config) == (True, 10, 9)


def test_die_portion_traegt_ihre_zahlen_mit() -> None:
    """Damit die Anzeige "12 von 25 heute" sagen kann und nicht nur "gesperrt" -
    das war der ausdrueckliche Mangel der alten Sperre."""
    portion: Tagesportion = tagesportion(_reihe(30), erledigt_heute=12, grenze=25)
    assert (portion.erledigt, portion.grenze, portion.offen_heute) == (12, 25, 13)
