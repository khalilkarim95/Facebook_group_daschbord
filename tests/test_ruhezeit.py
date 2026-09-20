"""Eine Gruppe ohne passenden Beitrag ruht - sie ist nicht erledigt.

Der Anlass ist ein Lauf vom 20.09.2026 ueber zwoelf Gruppen, der nach
zwoelf Schritten aufhoerte:

    kein Anlass: kein passender Beitrag (kein Bezug zum Angebot)
    kein Anlass: kein passender Beitrag (kein Bezug zum Angebot)
    ... zehnmal weiter ...
    Kommentare: 11 / 120
    12 Gruppe(n) nach einem Fehlschlag beiseitegelegt.

Jede dieser Gruppen war damit **fuer den ganzen Lauf** weg. "Hier steht
gerade nichts Passendes" ist aber eine Aussage ueber diesen Augenblick und
nicht ueber die naechste Stunde: In einer halben Stunde stehen dort andere
Beitraege. Verlangt war, dass der Lauf zwischen den Gruppen hin und her
geht, bis jede ihre zehn Kommentare hat - und niemals aufhoert.

Die Antwort ist ``automatik_lauf_uebersprungen.wiederholen_ab``: eine
Ruhezeit statt eines Schlussstrichs.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from fbgroups.marketing import automatik, lauf
from fbgroups.marketing.models import (
    Campaign,
    CampaignGroup,
    CampaignStatus,
    GroupMarketing,
    MarketingStatus,
    Texttyp,
)
from fbgroups.marketing.qualifikation import Regelbefund
from fbgroups.marketing.store import MarketingStore
from fbgroups.models import Group
from fbgroups.storage import SqliteStore

KAMPAGNE = "ruhe-test"
GRUPPEN = {"111": "Syrer in Berlin", "222": "Syrer in Hamburg"}


@pytest.fixture()
def bestand(tmp_path: Path) -> Path:
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
                for gid, name in GRUPPEN.items()
            ]
        )
    with MarketingStore(pfad) as store:
        store.save_campaign(
            Campaign(
                campaign_id=KAMPAGNE,
                name="Ruhetest",
                language="ar",
                audiences=["syrians"],
                status=CampaignStatus.ACTIVE,
            )
        )
        for gid in GRUPPEN:
            store.save_marketing(
                GroupMarketing(group_id=gid, marketing_status=MarketingStatus.MEMBER)
            )
            store.merke_regeln(gid, Regelbefund(gelesen=True))
        for i, gid in enumerate(GRUPPEN, start=1):
            store.add_link(
                CampaignGroup(
                    campaign_id=KAMPAGNE,
                    group_id=gid,
                    tracking_code=f"FB-TST-BER-{i:03d}",
                    tracking_url=f"https://example.invalid/r/FB-TST-BER-{i:03d}",
                )
            )
    return pfad


class Konfig:
    """Die echte Projektkonfiguration, nur mit umgebogenem Datenbankpfad.

    Dieselbe Bauart wie in ``test_lauf.py``: Ein duennerer Stub ginge am
    Zweck vorbei, weil der Treiber ``config`` bis in ``beitrag.mit_link``
    durchreicht. Kaltmodus aus und Grenzen weit - hier wird nach der
    Rotation gefragt, nicht nach dem Takt.
    """

    def __init__(self, pfad: Path) -> None:
        from fbgroups.config import load_config

        self._echt = load_config()
        self._pfad = pfad

    def __getattr__(self, name: str):
        return getattr(self._echt, name)

    def path(self, name: str) -> Path:
        return self._pfad if name == "sqlite_path" else self._echt.path(name)

    def get(self, *pfad, default=None):
        if pfad[:2] == ("kaltmodus", "aktiv"):
            return False
        if pfad[:1] == ("limits",) and pfad[-1:] == ("daily",):
            return 1000
        if pfad[-1:] == ("je_gruppe_taeglich",):
            return 0
        if pfad[:1] == ("delays",):
            return 0
        if pfad[:2] == ("beitritt", "mindestabstand_minuten"):
            return 0
        return self._echt.get(*pfad, default=default)


# --- Der Speicher: Ruhezeit statt Schlussstrich ---------------------------

def test_eine_ruhende_gruppe_kommt_von_selbst_zurueck(bestand: Path) -> None:
    """Der Kern. Waehrend der Ruhezeit uebersprungen, danach wieder dabei."""
    with MarketingStore(bestand) as store:
        lauf_id = store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)
        store.ueberspringe_gruppe(lauf_id, KAMPAGNE, "111", "kein Anlass", ruhe_minuten=30)

        assert (KAMPAGNE, "111") in store.uebersprungene_gruppen(lauf_id)

        _zeit_vorspulen(store, lauf_id, minuten=31)

        assert (KAMPAGNE, "111") not in store.uebersprungene_gruppen(lauf_id)
        assert store.naechste_rueckkehr(lauf_id) is None, "sie ruht nicht mehr"


def test_ohne_ruhezeit_gilt_der_uebersprung_fuer_den_ganzen_lauf(bestand: Path) -> None:
    """Die bisherige Bedeutung bleibt - sie ist die staerkere Aussage.

    "Hier ging es nicht" haengt der Gruppe an; "hier steht gerade nichts"
    dem Augenblick. Nur das Zweite ruht.
    """
    with MarketingStore(bestand) as store:
        lauf_id = store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)
        store.ueberspringe_gruppe(lauf_id, KAMPAGNE, "111", "technisch: kein Feld")

        _zeit_vorspulen(store, lauf_id, minuten=600)

        assert (KAMPAGNE, "111") in store.uebersprungene_gruppen(lauf_id)
        assert store.naechste_rueckkehr(lauf_id) is None, "ein Schlussstrich ist keine Ruhe"


def test_eine_ruhezeit_hebt_einen_schlussstrich_nicht_auf(bestand: Path) -> None:
    """Sonst holte ein spaeteres "kein Anlass" eine ausgeschlossene Gruppe zurueck."""
    with MarketingStore(bestand) as store:
        lauf_id = store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)
        store.ueberspringe_gruppe(lauf_id, KAMPAGNE, "111", "technisch: kein Feld")
        store.ueberspringe_gruppe(lauf_id, KAMPAGNE, "111", "kein Anlass", ruhe_minuten=1)

        _zeit_vorspulen(store, lauf_id, minuten=60)

        assert (KAMPAGNE, "111") in store.uebersprungene_gruppen(lauf_id)


def test_der_erste_grund_bleibt_stehen(bestand: Path) -> None:
    """Was nach dem ersten Fehler kommt, sind meist seine Folgen."""
    with MarketingStore(bestand) as store:
        lauf_id = store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)
        store.ueberspringe_gruppe(lauf_id, KAMPAGNE, "111", "erster Grund", ruhe_minuten=5)
        store.ueberspringe_gruppe(lauf_id, KAMPAGNE, "111", "zweiter Grund", ruhe_minuten=30)

        gruende = store.uebersprungene_gruppen(lauf_id)

        assert gruende[(KAMPAGNE, "111")] == "erster Grund"
        anzahl, _wann = store.naechste_rueckkehr(lauf_id)
        assert anzahl == 1, "aber die Ruhezeit wird fortgeschrieben"


def test_die_ruhezeit_wird_verlaengert_und_nicht_verkuerzt(bestand: Path) -> None:
    """Die laengere gewinnt - sonst kaeme die Gruppe zu frueh wieder.

    Geprueft wird der **gespeicherte Zeitpunkt**, nicht das Ergebnis nach
    einer vorgespulten Uhr: Waehrend beider Ruhezeiten ist die Gruppe
    ohnehin beiseite, und der Unterschied zwischen 5 und 60 Minuten faellt
    erst in der sechsten auf.
    """
    with MarketingStore(bestand) as store:
        lauf_id = store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)
        store.ueberspringe_gruppe(lauf_id, KAMPAGNE, "111", "kein Anlass", ruhe_minuten=60)
        store.ueberspringe_gruppe(lauf_id, KAMPAGNE, "111", "kein Anlass", ruhe_minuten=5)

        _anzahl, wann = store.naechste_rueckkehr(lauf_id)

        rest = datetime.fromisoformat(wann) - datetime.now(UTC)
        assert rest > timedelta(minutes=50), "die 60 Minuten stehen noch"


def test_naechste_rueckkehr_nennt_die_fruehere(bestand: Path) -> None:
    """Der Treiber schlaeft bis zur naechsten - nicht bis zur letzten."""
    with MarketingStore(bestand) as store:
        lauf_id = store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)
        store.ueberspringe_gruppe(lauf_id, KAMPAGNE, "111", "kein Anlass", ruhe_minuten=90)
        store.ueberspringe_gruppe(lauf_id, KAMPAGNE, "222", "kein Anlass", ruhe_minuten=20)

        anzahl, wann = store.naechste_rueckkehr(lauf_id)

        assert anzahl == 2
        sekunden = automatik.ruhesekunden(wann)
        assert 15 * 60 <= sekunden <= 21 * 60, "die 20 Minuten, nicht die 90"


def test_der_schlaf_ist_gedeckelt() -> None:
    """Eine Viertelstunde, dann wird neu gefragt - wie bei jedem Warten hier."""
    spaet = (datetime.now(UTC) + timedelta(hours=6)).isoformat()
    frueh = (datetime.now(UTC) - timedelta(hours=6)).isoformat()

    assert automatik.ruhesekunden(spaet) == 15 * 60
    assert automatik.ruhesekunden(frueh) == 30.0, "ein verstrichener Zeitpunkt: kurz warten"


# --- Der Lauf: hin und her statt einmal durch -----------------------------

def test_der_lauf_kommt_zu_einer_ruhenden_gruppe_zurueck(bestand: Path) -> None:
    """**Die Forderung vom 20.09.2026**, als Zusicherung.

    Beim ersten Durchgang steht in keiner Gruppe etwas Passendes. Vorher war
    der Lauf damit zu Ende; jetzt wartet er die Ruhezeit ab und fasst
    dieselben Gruppen erneut an - und beim zweiten Mal geht der Kommentar
    hinaus.
    """
    with MarketingStore(bestand) as store:
        _texte_anlegen(store, KAMPAGNE, list(GRUPPEN))

    versuche: list[str] = []

    def ausfuehren(
        url: str,
        group_id: str,
        text: str,
        texttyp: str = "kommentar",
        link_url: str = "",
    ) -> automatik.Schrittergebnis:
        versuche.append(group_id)
        if len(versuche) <= len(GRUPPEN):
            # Erster Durchgang: in keiner Gruppe steht etwas Passendes.
            return automatik.Schrittergebnis(
                erfolg=False, fehler="kein passender Beitrag", kein_anlass=True
            )
        return automatik.Schrittergebnis(erfolg=True, post_url="https://x.invalid/p/1")

    geschlafen: list[float] = []

    def warte(sekunden: float) -> None:
        # Die Zeit vergeht im Test nicht von selbst - der Schlaf holt sie
        # nach. Genau das tut er im Betrieb auch, nur langsamer.
        geschlafen.append(sekunden)
        with MarketingStore(bestand) as store:
            _zeit_vorspulen(store, _offener_lauf(store), minuten=60)

    fortschritt = automatik.fuehre_lauf_aus(
        Konfig(bestand), ausfuehren=ausfuehren, max_schritte=6, warte=warte
    )

    assert geschlafen, "der Lauf hat gewartet, statt aufzuhoeren"
    assert versuche.count("111") >= 2, "die erste Gruppe kam wieder dran"
    assert fortschritt.kommentare_veroeffentlicht >= 1


def test_ein_technischer_fehlschlag_holt_die_gruppe_nicht_zurueck(bestand: Path) -> None:
    """Dort ist der Schlussstrich richtig - sie faellt ohnehin aus der Kampagne.

    Der Unterschied ist der ganze Zweck der Ruhezeit: "hier steht gerade
    nichts" ruht, "hier ging es nicht" nicht.
    """
    with MarketingStore(bestand) as store:
        _texte_anlegen(store, KAMPAGNE, list(GRUPPEN))

    versuche: list[str] = []

    def ausfuehren(
        url: str,
        group_id: str,
        text: str,
        texttyp: str = "kommentar",
        link_url: str = "",
    ) -> automatik.Schrittergebnis:
        versuche.append(group_id)
        return automatik.Schrittergebnis(
            erfolg=False, fehler="Kommentarfeld nicht gefunden", gruppe_beiseite=True
        )

    geschlafen: list[float] = []

    fortschritt = automatik.fuehre_lauf_aus(
        Konfig(bestand),
        ausfuehren=ausfuehren,
        max_schritte=10,
        warte=geschlafen.append,
    )

    assert not geschlafen, "hier gibt es nichts abzuwarten"
    assert sorted(set(versuche)) == sorted(GRUPPEN), "jede Gruppe genau einmal"
    assert len(versuche) == len(GRUPPEN)
    assert not fortschritt.fertig


# --- Hilfen ---------------------------------------------------------------

def _texte_anlegen(store: MarketingStore, campaign_id: str, gruppen: list[str]) -> None:
    for gid in gruppen:
        for nummer in range(1, lauf.ZIEL_JE_GRUPPE + 1):
            store.setze_erzeugten_vorschlag(
                campaign_id,
                gid,
                Texttyp.KOMMENTAR,
                nummer,
                text=f"Text {nummer}\n{{link}}",
                vorlage_key="k",
            )


def _offener_lauf(store: MarketingStore) -> int:
    zeile = store.offener_lauf()
    assert zeile is not None
    return int(zeile["lauf_id"])


def _zeit_vorspulen(store: MarketingStore, lauf_id: int, *, minuten: int) -> None:
    """Jede Ruhezeit dieses Laufs um ``minuten`` nach vorn holen.

    Der ehrlichere Weg als eine eingefrorene Uhr: Geprueft wird dieselbe
    Abfrage, die im Betrieb laeuft - nur stehen die Zeitpunkte schon in der
    Vergangenheit.
    """
    frueher = (datetime.now(UTC) - timedelta(minutes=minuten)).isoformat()
    store.conn.execute(
        "UPDATE automatik_lauf_uebersprungen SET wiederholen_ab = ? "
        "WHERE lauf_id = ? AND wiederholen_ab IS NOT NULL",
        (frueher, lauf_id),
    )
    store.conn.commit()
