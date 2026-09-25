"""Jede Gruppe einmal je Runde - der Reihe nach, gleich was dort geschieht.

Der Anlass ist ein Betriebslog vom 23.09.2026, eine Kampagne mit elf
Gruppen::

    بطريقك 🇩🇪🇸🇪🇪🇺🇸🇾🇬🇧 - Kommentar 3/10 (Fassung 3)
    Found 1 post(s) in 5 round(s); articles last seen: 5.
      fehlgeschlagen: alle sichtbaren Beitraege sind bereits kommentiert
    ... dieselben drei Zeilen gut fuenfzigmal hintereinander ...

Zwei Ursachen lagen uebereinander:

1. **"Alle sichtbaren schon kommentiert" legte die Gruppe nicht beiseite.**
   Es kam als ``erschoepft`` zurueck, nicht als "kein Anlass". Der Server
   vermerkte die Gruppe als erschoepft - aber ihr Beitrag war noch offen,
   also blieb sie ``bearbeitbar``, und ``naechste_kommentargruppe`` fragte
   nicht nach den Kommentaren. Derselbe Schritt, dieselbe Fassung, bis
   Facebook zufaellig einmal mehr als einen Beitrag anzeigte.
2. **Gewaehlt wurde immer die erste Gruppe, die gerade durfte.** Nach "kein
   Anlass" ruhte sie zwei Minuten und stand danach wieder vorn. Bei gut
   einer halben Minute je Schritt kamen so nur die ersten vier Gruppen an
   die Reihe; fuenf bis elf sahen den Lauf nie.

Verlangt ist das Gegenteil: ``1 → 2 → ... → 11``, dann wieder ``1 → ...``.
Eine Gruppe, die Probleme macht, ist in dieser Runde dran gewesen wie jede
andere; uebergangen wird nur, wer gerade ruht - und der kommt in derselben
Runde zurueck.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from fbgroups.marketing import automatik, lauf
from fbgroups.marketing.lauf import (
    Gruppenfortschritt,
    Kampagnenfortschritt,
    Lauffortschritt,
    LaufStatus,
)
from fbgroups.marketing.models import (
    Campaign,
    CampaignGroup,
    CampaignStatus,
    GroupMarketing,
    MarketingStatus,
    PostStatus,
    Texttyp,
)
from fbgroups.marketing.store import MarketingStore
from fbgroups.models import Group
from fbgroups.storage import SqliteStore

KAMPAGNE = "runden"
#: Elf Gruppen, absteigend bewertet - die Arbeitsliste ist damit g01 ... g11.
GRUPPEN = [f"g{i:02d}" for i in range(1, 12)]

#: Wie lange ein Schritt im Test "dauert". Im Log waren es 30-90 Sekunden;
#: entscheidend ist nur, dass mehrere Schritte in eine Ruhezeit passen - genau
#: dann stand die erste Gruppe wieder vorn, bevor die hinteren dran waren.
SCHRITT_SEKUNDEN = 40
RUHE_MINUTEN = 2


@pytest.fixture()
def bestand(tmp_path: Path) -> Path:
    pfad = tmp_path / "groups.sqlite"
    with SqliteStore(pfad) as store:
        store.upsert_groups(
            [
                Group(
                    group_id=gid,
                    url_canonical=f"https://www.facebook.com/groups/{gid}",
                    name=f"Gruppe {gid}",
                    score=float(100 - i),
                    score_max=100.0,
                )
                for i, gid in enumerate(GRUPPEN)
            ]
        )
    with MarketingStore(pfad) as store:
        store.save_campaign(
            Campaign(
                campaign_id=KAMPAGNE,
                name="Runden",
                language="ar",
                status=CampaignStatus.ACTIVE,
            )
        )
        for i, gid in enumerate(GRUPPEN, start=1):
            store.save_marketing(
                GroupMarketing(group_id=gid, marketing_status=MarketingStatus.MEMBER)
            )
            store.add_link(
                CampaignGroup(
                    campaign_id=KAMPAGNE,
                    group_id=gid,
                    tracking_code=f"FB-TST-BER-{i:03d}",
                    tracking_url=f"https://example.invalid/r/FB-TST-BER-{i:03d}",
                )
            )
            for nummer in range(1, lauf.ZIEL_JE_GRUPPE + 1):
                store.setze_erzeugten_vorschlag(
                    KAMPAGNE,
                    gid,
                    Texttyp.KOMMENTAR,
                    nummer,
                    text=f"Text {nummer}\n{{link}}",
                    vorlage_key="k",
                )
            # Der Beitrag ist offen - wie im Betrieb, in dem er wegen
            # ``kommentare_zuerst`` hinten steht. Genau das hielt die
            # erschoepfte Gruppe bis zum 23.09.2026 in der Kommentarrunde.
            store.setze_erzeugten_vorschlag(
                KAMPAGNE, gid, Texttyp.POST, 1, text="Beitrag {link}", vorlage_key="p"
            )
    return pfad


class Konfig:
    """Die echte Projektkonfiguration mit den Werten des Betriebs, die hier zaehlen.

    ``kommentare_zuerst`` und ``ruhe_minuten`` stehen so, wie sie im Betrieb
    stehen (``settings.yaml`` vom 21.09.2026) - aber ausdruecklich hier und
    nicht aus der Datei gelesen: Der Test darf nicht davon abhaengen, was dort
    gerade eingestellt ist. Takt und Tagesmengen der Kommentare sind weit
    gestellt; **Beitraege sind abgeschaltet** (``limits.posts.daily: 0``),
    hier geht es um die Kommentarrunde. Offen bleiben sie trotzdem - und
    genau das hielt die erschoepfte Gruppe bis zum 23.09.2026 in ihr fest.
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
        if pfad[:2] == ("automatik", "kommentare_zuerst"):
            return True
        if pfad[:2] == ("automatik", "ruhe_minuten"):
            return RUHE_MINUTEN
        if pfad[:2] == ("limits", "posts"):
            return 0 if pfad[-1:] == ("daily",) else default
        if pfad[:1] == ("limits",) and pfad[-1:] == ("daily",):
            return 1000
        if pfad[-1:] == ("je_gruppe_taeglich",):
            return 0
        if pfad[:1] == ("delays",):
            return 0
        if pfad[:2] == ("beitritt", "mindestabstand_minuten"):
            return 0
        return self._echt.get(*pfad, default=default)


def _vorspulen(pfad: Path, sekunden: int) -> None:
    """Die Uhr vorstellen: jede Ruhezeit um ``sekunden`` naeher an ihr Ende.

    Derselbe Weg wie in ``test_ruhezeit.py`` - geprueft wird die Abfrage, die
    im Betrieb laeuft, nur liegen die Zeitpunkte frueher.
    """
    with MarketingStore(pfad) as store:
        zeilen = store.conn.execute(
            "SELECT lauf_id, campaign_id, group_id, wiederholen_ab "
            "FROM automatik_lauf_uebersprungen WHERE wiederholen_ab IS NOT NULL"
        ).fetchall()
        for zeile in zeilen:
            frueher = datetime.fromisoformat(zeile["wiederholen_ab"]) - timedelta(
                seconds=sekunden
            )
            store.conn.execute(
                "UPDATE automatik_lauf_uebersprungen SET wiederholen_ab = ? "
                "WHERE lauf_id = ? AND campaign_id = ? AND group_id = ?",
                (
                    frueher.isoformat(),
                    zeile["lauf_id"],
                    zeile["campaign_id"],
                    zeile["group_id"],
                ),
            )
        store.conn.commit()


def _ausgang(group_id: str, besuch: int) -> automatik.Schrittergebnis:
    """Was der Browser in welcher Gruppe meldet - jede Art Problem ist dabei.

    * g01 - Facebook zeigt nur einen Beitrag, und der ist schon kommentiert
      (die Zeile aus dem Log).
    * g04 - das Kommentarfeld laesst sich nicht beschreiben (technisch).
    * g07 - ein Erfolg. Danach kommt **die naechste Gruppe**, nicht dieselbe.
    * alle anderen - kein passender Beitrag.
    """
    if group_id == "g01":
        return automatik.Schrittergebnis(
            erfolg=False,
            fehler="alle sichtbaren Beitraege sind bereits kommentiert",
            kein_anlass=True,
        )
    if group_id == "g04":
        return automatik.Schrittergebnis(
            erfolg=False,
            fehler="Kommentarfeld nicht beschreibbar",
            gruppe_beiseite=True,
        )
    if group_id == "g07":
        return automatik.Schrittergebnis(
            erfolg=True, post_url=f"https://www.facebook.com/groups/g07/posts/{besuch}"
        )
    return automatik.Schrittergebnis(
        erfolg=False, fehler="kein passender Beitrag", kein_anlass=True
    )


# --- 1. Die Anforderung als Zusicherung -------------------------------------

def test_elf_gruppen_alle_elf_dann_die_naechste_runde_wieder_alle_elf(
    bestand: Path,
) -> None:
    """**Der Kern.** 11 Gruppen → alle 11 geprueft → Runde 2 → wieder alle 11.

    Mit Problemen in drei von ihnen und einer Uhr, die weiterlaeuft: Jeder
    Schritt kostet 40 Sekunden, jede Gruppe ohne Kommentar ruht zwei
    Minuten. Vor dem 23.09.2026 kamen unter genau diesen Bedingungen nur
    g01 bis g04 an die Reihe.
    """
    besucht: list[str] = []

    def ausfuehren(url, group_id, text, texttyp="kommentar", link_url=""):
        assert texttyp == "kommentar", "der Beitrag steht hinten"
        besucht.append(group_id)
        _vorspulen(bestand, SCHRITT_SEKUNDEN)
        return _ausgang(group_id, len(besucht))

    geschlafen: list[float] = []

    def warte(sekunden: float) -> None:
        geschlafen.append(sekunden)
        _vorspulen(bestand, int(sekunden))

    automatik.fuehre_lauf_aus(
        Konfig(bestand), ausfuehren=ausfuehren, max_schritte=22, warte=warte
    )

    assert besucht[:11] == GRUPPEN, "Runde 1: jede Gruppe der Reihe nach"
    assert besucht[11:22] == GRUPPEN, "Runde 2: wieder von vorn, wieder alle"
    with MarketingStore(bestand) as store:
        assert store.erschoepfte_gruppen(KAMPAGNE) == {}, (
            "'alle sichtbaren schon kommentiert' ist kein Urteil ueber die Gruppe"
        )
        lauf_id = int(store.offener_lauf()["lauf_id"])
        assert set(store.besuche(lauf_id).values()) == {2}, "jede war in Runde 2 dran"


def test_ein_erfolg_fuehrt_zur_naechsten_gruppe_nicht_zur_selben(bestand: Path) -> None:
    """Auch der Erfolg verbraucht den Platz der Gruppe in dieser Runde.

    Vorher stand die Gruppe danach wieder vorn und bekam den naechsten
    Kommentar gleich mit - die Runde war dann keine.
    """
    besucht: list[str] = []

    def ausfuehren(url, group_id, text, texttyp="kommentar", link_url=""):
        besucht.append(group_id)
        return automatik.Schrittergebnis(
            erfolg=True,
            post_url=f"https://www.facebook.com/groups/{group_id}/posts/{len(besucht)}",
        )

    automatik.fuehre_lauf_aus(Konfig(bestand), ausfuehren=ausfuehren, max_schritte=13)

    assert besucht == [*GRUPPEN, "g01", "g02"]


def test_ein_alter_arbeitsrechner_mit_erschoepft_legt_keine_gruppe_still(
    bestand: Path,
) -> None:
    """Ein ``erschoepft`` gilt wie "kein Anlass" - die Gruppe ruht nur.

    Ein Arbeitsrechner, der noch nicht aktualisiert ist, meldet "alle
    sichtbaren schon kommentiert" weiter als ``erschoepft``. Bis zum
    23.09.2026 wurde daraus ein dauerhaftes Urteil ueber die Gruppe.
    """

    def ausfuehren(url, group_id, text, texttyp="kommentar", link_url=""):
        return automatik.Schrittergebnis(
            erfolg=False,
            fehler="alle sichtbaren Beitraege sind bereits kommentiert",
            erschoepft=True,
        )

    automatik.fuehre_lauf_aus(Konfig(bestand), ausfuehren=ausfuehren, max_schritte=3)

    with MarketingStore(bestand) as store:
        lauf_id = int(store.offener_lauf()["lauf_id"])
        assert store.erschoepfte_gruppen(KAMPAGNE) == {}
        assert store.ruhende_gruppen(lauf_id) == {
            (KAMPAGNE, "g01"),
            (KAMPAGNE, "g02"),
            (KAMPAGNE, "g03"),
        }


# --- 2. Im Fernbetrieb - dem Regelfall - genauso -----------------------------

# --- 3. Die Regel selbst, ohne Speicher -------------------------------------

def _gruppe(
    gid: str,
    *,
    runde: int = 0,
    ruht: bool = False,
    veroeffentlicht: int = 0,
    erschoepft: bool = False,
    post_offen: bool = False,
) -> Gruppenfortschritt:
    return Gruppenfortschritt(
        campaign_id="k",
        group_id=gid,
        name=gid,
        veroeffentlicht=veroeffentlicht,
        erschoepft=erschoepft,
        mitglied=True,
        post_status=PostStatus.OFFEN if post_offen else PostStatus.VEROEFFENTLICHT,
        post_fassungen=frozenset({1}) if post_offen else frozenset(),
        uebersprungen=ruht,
        uebersprungen_grund="kein Anlass" if ruht else "",
        ruht=ruht,
        runde=runde,
    )


def _lauf(gruppen: list[Gruppenfortschritt], *, kommentare_zuerst: bool = True):
    return Lauffortschritt(
        lauf_id=1,
        status=LaufStatus.LAEUFT,
        kampagnen=[Kampagnenfortschritt(campaign_id="k", name="k", gruppen=gruppen)],
        kommentare_zuerst=kommentare_zuerst,
    )


def test_eine_ruhende_gruppe_holt_ihren_platz_in_derselben_runde_nach() -> None:
    """Uebergangen wird sie nur fuer die Dauer ihrer Ruhe - nicht fuer die Runde."""
    ruhend = _lauf(
        [
            _gruppe("1", runde=1),
            _gruppe("2", ruht=True),
            _gruppe("3"),
            _gruppe("4"),
        ]
    )
    schritt = lauf.naechster_schritt(ruhend)
    assert schritt is not None
    assert (schritt.group_id, schritt.runde) == ("3", 1), "die ruhende wird uebergangen"

    zurueck = _lauf(
        [_gruppe("1", runde=1), _gruppe("2"), _gruppe("3", runde=1), _gruppe("4", runde=1)]
    )
    schritt = lauf.naechster_schritt(zurueck)
    assert schritt is not None
    assert (schritt.group_id, schritt.runde) == ("2", 1), "und holt Runde 1 nach"


def test_erst_wenn_alle_dran_waren_beginnt_die_naechste_runde() -> None:
    fortschritt = _lauf([_gruppe(str(i), runde=3) for i in range(1, 5)])

    schritt = lauf.naechster_schritt(fortschritt)

    assert schritt is not None
    assert (schritt.group_id, schritt.runde) == ("1", 4)
    assert (schritt.runde_platz, schritt.runde_gruppen) == (1, 4)


def test_ruhen_alle_gibt_es_keinen_schritt_und_die_kampagne_bleibt() -> None:
    """Dann darf gewartet werden - der Treiber wartet auf die erste Rueckkehr."""
    fortschritt = _lauf([_gruppe(str(i), runde=1, ruht=True) for i in range(1, 12)])

    assert lauf.naechster_schritt(fortschritt) is None
    assert fortschritt.naechste_kampagne is fortschritt.kampagnen[0]


def test_eine_erschoepfte_gruppe_bekommt_keinen_kommentarschritt() -> None:
    """Die zweite Haelfte der Schleife aus dem Log.

    Erschoepft, aber mit offenem Beitrag: ``bearbeitbar`` bleibt sie (der
    Beitrag darf ja noch) - einen Kommentarschritt bekam sie trotzdem, weil
    niemand nach den Kommentaren fragte.
    """
    fortschritt = _lauf(
        [
            _gruppe("1", veroeffentlicht=2, erschoepft=True, post_offen=True),
            _gruppe("2"),
        ]
    )

    schritt = lauf.naechster_schritt(fortschritt)

    assert schritt is not None
    assert (schritt.group_id, schritt.texttyp) == ("2", Texttyp.KOMMENTAR)


def test_der_beitrag_sucht_die_erste_gruppe_mit_offenem_beitrag() -> None:
    """Nicht nur die erste Gruppe - sonst ging nach ihrem Beitrag keiner mehr."""
    fortschritt = _lauf(
        [_gruppe("1"), _gruppe("2", post_offen=True)], kommentare_zuerst=False
    )

    schritt = lauf.naechster_schritt(fortschritt)

    assert schritt is not None
    assert (schritt.group_id, schritt.texttyp) == ("2", Texttyp.POST)
    assert schritt.runde == 0, "der Beitrag gehoert nicht zur Kommentarrunde"


def test_die_runde_steht_im_fortschrittstext() -> None:
    fortschritt = _lauf(
        [_gruppe("1", runde=2), _gruppe("2", runde=2), _gruppe("3", runde=1)]
    )

    assert "Runde:             2 - 2 / 3 Gruppen geprueft" in lauf.fortschrittstext(
        fortschritt
    )


# --- 4. Der Speicher ---------------------------------------------------------

def test_der_besuch_geht_nie_eine_runde_zurueck(bestand: Path) -> None:
    with MarketingStore(bestand) as store:
        lauf_id = store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)
        store.merke_besuch(lauf_id, KAMPAGNE, "g01", 3)
        store.merke_besuch(lauf_id, KAMPAGNE, "g01", 2)

        assert store.besuche(lauf_id) == {(KAMPAGNE, "g01"): 3}
        neuer = store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)
        assert store.besuche(neuer) == {}, "ein neuer Lauf beginnt mit Runde 1"


def test_der_stand_liest_die_runde_aus_dem_speicher(bestand: Path) -> None:
    with SqliteStore(bestand) as s:
        gruppen = {g.group_id: g for g in s.load_groups()}
    with MarketingStore(bestand) as store:
        lauf_id = store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)
        store.merke_besuch(lauf_id, KAMPAGNE, "g01", 1)
        store.merke_besuch(lauf_id, KAMPAGNE, "g02", 1)

        kampagne = lauf.lies_fortschritt(store, lauf_id, gruppen).kampagnen[0]

    assert kampagne.runde == 1
    assert kampagne.runde_geprueft == 2
    assert kampagne.naechste_kommentargruppe is not None
    assert kampagne.naechste_kommentargruppe.group_id == "g03"


def test_die_zeitstempel_des_besuchs_sind_utc(bestand: Path) -> None:
    with MarketingStore(bestand) as store:
        lauf_id = store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)
        store.merke_besuch(lauf_id, KAMPAGNE, "g01", 1)
        zeile = store.conn.execute(
            "SELECT besucht_am FROM automatik_lauf_besuche WHERE lauf_id = ?", (lauf_id,)
        ).fetchone()

    wann = datetime.fromisoformat(zeile["besucht_am"])
    assert abs(wann - datetime.now(UTC)) < timedelta(minutes=1)
