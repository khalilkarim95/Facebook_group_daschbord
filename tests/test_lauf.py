"""Die Kommentarautomatik: Reihenfolge, Rotation, Fortsetzen und Abschluss.

Der grosse Teil ist offline und ohne Datenbank - ``lauf.py`` rechnet ueber
uebergebene Werte, so wie ``kaltmodus.py``. Erst die letzten Tests fassen
einen echten Speicher an; sie pruefen genau das, was sich nicht rechnen
laesst: dass der Fortschritt einen Abbruch ueberlebt.

Die wichtigste Zusage steht in
``test_eine_fertige_gruppe_macht_die_kampagne_nicht_fertig``: Eine Kampagne
gilt erst als abgeschlossen, wenn **jede** Gruppe durch ist. Das ist Punkt 8
der Anforderung und der Grund, warum die Bedingung an genau einer Stelle
steht.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fbgroups.marketing import lauf
from fbgroups.marketing.models import (
    Campaign,
    CampaignGroup,
    CampaignStatus,
    GroupMarketing,
    KampagnenLaufStatus,
    LaufStatus,
    MarketingStatus,
    Texttyp,
    VorschlagStatus,
)
from fbgroups.marketing.store import MarketingStore
from fbgroups.models import Group
from fbgroups.storage import SqliteStore


def _gruppe(
    gid: str,
    veroeffentlicht: int = 0,
    *,
    erschoepft: bool = False,
    gescheitert: frozenset[int] = frozenset(),
    ziel: int = lauf.ZIEL_JE_GRUPPE,
    mitglied: bool = True,
) -> lauf.Gruppenfortschritt:
    # ``mitglied=True`` als Vorgabe: Diese Tests pruefen Reihenfolge und
    # Rotation. Die Mitgliedschaft hat ihre eigenen Tests weiter unten - sie
    # ueberall mitzudenken machte jeden Test um eine Aussage unschaerfer.
    return lauf.Gruppenfortschritt(
        campaign_id="k",
        group_id=gid,
        name=f"Gruppe {gid}",
        veroeffentlicht=veroeffentlicht,
        ziel=ziel,
        erschoepft=erschoepft,
        gescheiterte_fassungen=gescheitert,
        mitglied=mitglied,
    )


def _kampagne(cid: str, gruppen: list[lauf.Gruppenfortschritt]) -> lauf.Kampagnenfortschritt:
    return lauf.Kampagnenfortschritt(campaign_id=cid, name=f"Kampagne {cid}", gruppen=gruppen)


def _lauf(kampagnen: list[lauf.Kampagnenfortschritt]) -> lauf.Lauffortschritt:
    return lauf.Lauffortschritt(lauf_id=1, status=LaufStatus.LAEUFT, kampagnen=kampagnen)


# --- Szenario D: die Vorlagenrotation --------------------------------------
def test_die_fassungen_kommen_der_reihe_nach() -> None:
    """1-2-3-4-5, und danach ist Schluss.

    Es ist keine Rotation mit Zeiger, sondern "die kleinste, die noch nicht
    heraus ist". Dieselbe Folge, aber ohne gespeicherten Stand, der von der
    Wirklichkeit abweichen koennte.
    """
    folge = [lauf.naechste_nummer(set(range(1, i + 1))) for i in range(0, 11)]
    assert folge == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, None]

    # Und die Vorlage dahinter dreht sich: 1-5, dann wieder 1-5.
    assert [lauf.vorlage_zu_nummer(n) for n in range(1, 11)] == [1, 2, 3, 4, 5, 1, 2, 3, 4, 5]


def test_eine_aufgegebene_fassung_wird_uebersprungen() -> None:
    """Sonst haengt der Lauf an derselben Fassung fest, bis jemand eingreift."""
    assert lauf.naechste_nummer({1}, {2}) == 3


def test_keine_fassung_geht_zweimal_hinaus() -> None:
    """Fuenf Fassungen, fuenf Kommentare - kein Text doppelt in derselben Gruppe.

    Zwei wortgleiche Kommentare untereinander sind das deutlichste Zeichen
    einer Maschine, das man hinterlassen kann.
    """
    heraus: set[int] = set()
    for _ in range(lauf.ZIEL_JE_GRUPPE):
        nummer = lauf.naechste_nummer(heraus)
        assert nummer is not None
        assert nummer not in heraus
        heraus.add(nummer)
    assert heraus == set(range(1, lauf.ZIEL_JE_GRUPPE + 1))
    assert lauf.naechste_nummer(heraus) is None


# --- Szenario A und E: der Abschluss ---------------------------------------
def test_drei_volle_gruppen_schliessen_die_kampagne_ab() -> None:
    """Szenario A: 3 Gruppen a 5 Kommentare = 15, dann fertig."""
    voll = lauf.ZIEL_JE_GRUPPE
    kampagne = _kampagne("k", [_gruppe(str(i), veroeffentlicht=voll) for i in range(3)])
    assert kampagne.kommentare_veroeffentlicht == 3 * voll
    assert kampagne.kommentare_ziel == 3 * voll
    assert kampagne.gruppen_fertig == 3
    assert kampagne.fertig


def test_eine_fertige_gruppe_macht_die_kampagne_nicht_fertig() -> None:
    """Punkt 8: Gruppe 1 fertig heisst **nicht** Kampagne fertig."""
    kampagne = _kampagne(
        "k", [_gruppe("a", veroeffentlicht=lauf.ZIEL_JE_GRUPPE), _gruppe("b", veroeffentlicht=0)]
    )
    assert not kampagne.fertig
    assert kampagne.gruppen_fertig == 1


def test_eine_fast_volle_gruppe_zaehlt_nicht_als_fertig() -> None:
    kampagne = _kampagne("k", [_gruppe("a", veroeffentlicht=lauf.ZIEL_JE_GRUPPE - 1)])
    assert not kampagne.fertig


def test_eine_kampagne_ohne_gruppen_ist_nicht_erfolgreich() -> None:
    """``all()`` ueber eine leere Liste ist wahr - hier waere das ein Unfall.

    Eine gerade angelegte Kampagne ohne Zuordnungen waere sonst im selben
    Augenblick "erfolgreich abgeschlossen", in dem sie entsteht. Genau diese
    Art falscher Erfolgsmeldung soll der Lauf nie geben.
    """
    assert not _kampagne("leer", []).fertig
    assert not _lauf([]).fertig


def test_der_lauf_ist_erst_mit_der_letzten_kampagne_fertig() -> None:
    """Szenario E."""
    voll = _kampagne("eins", [_gruppe("a", veroeffentlicht=lauf.ZIEL_JE_GRUPPE)])
    offen = _kampagne("zwei", [_gruppe("b", veroeffentlicht=2)])
    assert not _lauf([voll, offen]).fertig
    assert _lauf([voll]).fertig


# --- Szenario B: die Reihenfolge -------------------------------------------
def test_erst_wenn_eine_kampagne_durch_ist_kommt_die_naechste() -> None:
    """Sequentiell, nicht parallel - Punkt 13."""
    eins = _kampagne("eins", [_gruppe("a", veroeffentlicht=2)])
    zwei = _kampagne("zwei", [_gruppe("b")])
    schritt = lauf.naechster_schritt(_lauf([eins, zwei]))
    assert schritt is not None
    assert schritt.campaign_id == "eins"
    assert schritt.group_id == "a"


def test_nach_der_ersten_gruppe_kommt_die_zweite_derselben_kampagne() -> None:
    kampagne = _kampagne(
        "k", [_gruppe("a", veroeffentlicht=lauf.ZIEL_JE_GRUPPE), _gruppe("b", veroeffentlicht=1)]
    )
    schritt = lauf.naechster_schritt(_lauf([kampagne]))
    assert schritt is not None
    assert schritt.group_id == "b"
    assert schritt.nummer == 2


def test_ein_fertiger_lauf_hat_keinen_naechsten_schritt() -> None:
    kampagne = _kampagne("k", [_gruppe("a", veroeffentlicht=lauf.ZIEL_JE_GRUPPE)])
    assert lauf.naechster_schritt(_lauf([kampagne])) is None


# --- Szenario C: fortsetzen statt neu beginnen -----------------------------
def test_der_schritt_zaehlt_beim_stand_weiter_nicht_bei_eins() -> None:
    """Szenario C: nach dem Neustart geht es bei Kommentar 3 weiter, nicht bei 1."""
    kampagne = _kampagne("k", [_gruppe("a", veroeffentlicht=2)])
    schritt = lauf.naechster_schritt(_lauf([kampagne]))
    assert schritt is not None
    assert schritt.nummer == 3
    assert schritt.kommentar_nr == 3


# --- Mitgliedschaft: die Vorbedingung fuer alles Uebrige -------------------
def test_ohne_mitgliedschaft_wird_nichts_versucht() -> None:
    """Facebook laesst Nichtmitglieder nicht schreiben.

    Am 31.08.2026 waren von 36 zugeordneten Gruppen **null** auf ``mitglied``.
    Ohne diese Regel haette der erste Lauf 180 Versuche gemacht, die alle
    fehlschlagen mussten - und genau diese Folge aus einem Konto ist das
    Muster, das zur Sperre fuehrt.
    """
    kampagne = _kampagne("k", [_gruppe("a", mitglied=False)])
    assert lauf.naechster_schritt(_lauf([kampagne])) is None
    assert kampagne.gruppen_wartend == 1


def test_eine_wartende_gruppe_ist_nicht_fertig() -> None:
    """Blockiert ist nicht erledigt.

    Sonst meldete eine Kampagne "erfolgreich abgeschlossen", in der kein
    einziger Kommentar steht - die Arbeit ist nicht getan, sie ist noch nicht
    moeglich.
    """
    gruppe = _gruppe("a", mitglied=False)
    assert gruppe.wartet
    assert not gruppe.fertig
    assert not gruppe.bearbeitbar
    assert not _kampagne("k", [gruppe]).fertig


def test_mitgliedslose_gruppen_werden_uebersprungen_nicht_blockiert() -> None:
    """Die naechste bearbeitbare Gruppe kommt dran, nicht die naechste ueberhaupt."""
    kampagne = _kampagne(
        "k", [_gruppe("ohne", mitglied=False), _gruppe("mit", mitglied=True)]
    )
    schritt = lauf.naechster_schritt(_lauf([kampagne]))
    assert schritt is not None
    assert schritt.group_id == "mit"


def test_die_meldung_nennt_die_wartenden_gruppen() -> None:
    """Der haeufigste Grund fuer einen kurzen Lauf - er muss dastehen.

    Ohne diesen Satz sucht man den Fehler in der Technik statt im Konto.
    """
    kampagne = _kampagne("k", [_gruppe("a", mitglied=False)])
    text = lauf.abschlusstext(_lauf([kampagne]))
    assert "ohne Mitgliedschaft" in text
    assert "marketing set" in text
    assert "erfolgreich abgeschlossen" not in text


def test_eine_offene_beitrittsanfrage_ist_keine_mitgliedschaft(bestand: Path) -> None:
    """``beitritt_angefragt`` zaehlt nicht - Facebook laesst oft wochenlang offen.

    Wer sie mitzaehlte, liefe genau in die Fehlversuche, die diese Regel
    verhindern soll.
    """
    with SqliteStore(bestand) as s:
        gruppen = {g.group_id: g for g in s.load_groups()}

    with MarketingStore(bestand) as store:
        store.save_marketing(
            GroupMarketing(group_id="111", marketing_status=MarketingStatus.JOIN_REQUESTED)
        )
        lauf_id = store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)
        stand = lauf.lies_fortschritt(store, lauf_id, gruppen)

    erste = next(g for g in stand.kampagnen[0].gruppen if g.group_id == "111")
    assert not erste.mitglied
    assert erste.wartet


# --- Der dritte Ausgang ----------------------------------------------------
def test_eine_erschoepfte_gruppe_haelt_die_kampagne_nicht_auf() -> None:
    """Ohne diesen Ausgang haengt die Kampagne fuer immer bei 2 von 5."""
    kampagne = _kampagne("k", [_gruppe("a", veroeffentlicht=2, erschoepft=True)])
    assert kampagne.fertig
    assert kampagne.gruppen_erschoepft == 1
    assert kampagne.gruppen_voll == 0


def test_erschoepft_ist_kein_erfolg() -> None:
    """Erledigt und erfolgreich sind zwei Aussagen - die Meldung nennt beide."""
    voll = lauf.ZIEL_JE_GRUPPE
    kampagne = _kampagne(
        "k", [_gruppe("a", veroeffentlicht=voll), _gruppe("b", veroeffentlicht=1, erschoepft=True)]
    )
    text = lauf.abschlusstext(_lauf([kampagne]))
    assert "erfolgreich abgeschlossen" in text
    assert "erschoepft" in text
    # Die belegte Zahl, nicht das Ziel: 11 von 20, nicht 20 von 20.
    assert f"{voll + 1} / {2 * voll}" in text


def test_alle_fassungen_verbraucht_heisst_erschoepft() -> None:
    """Wenn keine Fassung mehr offen ist, das Ziel aber nicht erreicht wurde."""
    rest = frozenset(range(3, lauf.ZIEL_JE_GRUPPE + 1))
    gruppe = _gruppe("a", veroeffentlicht=2, gescheitert=rest)
    assert lauf.gruppe_ist_erschoepft(gruppe)


def test_eine_volle_gruppe_ist_nicht_erschoepft() -> None:
    assert not lauf.gruppe_ist_erschoepft(
        _gruppe("a", veroeffentlicht=lauf.ZIEL_JE_GRUPPE)
    )


# --- Die Meldungen ---------------------------------------------------------
def test_die_abschlussmeldung_behauptet_keinen_erfolg_bei_resten() -> None:
    """Punkt 8: kein falscher Erfolg."""
    voll = lauf.ZIEL_JE_GRUPPE
    kampagne = _kampagne("k", [_gruppe("a", veroeffentlicht=voll), _gruppe("b", veroeffentlicht=3)])
    text = lauf.abschlusstext(_lauf([kampagne]))
    assert "NICHT vollstaendig" in text
    assert "erfolgreich abgeschlossen" not in text
    assert f"{voll + 3} / {2 * voll}" in text


def test_der_fortschrittstext_nennt_die_laufende_gruppe() -> None:
    voll = lauf.ZIEL_JE_GRUPPE
    kampagne = _kampagne("k", [_gruppe("a", veroeffentlicht=voll), _gruppe("b", veroeffentlicht=3)])
    text = lauf.fortschrittstext(_lauf([kampagne]))
    assert "Gruppe b" in text
    assert f"3 / {voll}" in text


# --- Mit echtem Speicher: ueberlebt der Stand einen Abbruch? ---------------
KAMPAGNE = "lauf-test"
GRUPPEN = {"111": "Gruppe Eins", "222": "Gruppe Zwei"}


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
                name="Lauftest",
                language="ar",
                audiences=["syrians"],
                # Aktiv, weil ein Lauf ausschliesslich aktive Kampagnen
                # einfriert - ein Entwurf ist nicht in Betrieb.
                status=CampaignStatus.ACTIVE,
            )
        )
        # Ohne Mitgliedschaft versucht die Automatik in einer Gruppe nichts -
        # Facebook laesst Nichtmitglieder nicht schreiben.
        for gid in GRUPPEN:
            store.save_marketing(
                GroupMarketing(group_id=gid, marketing_status=MarketingStatus.MEMBER)
            )
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


def test_der_fortschritt_wird_gelesen_nicht_gefuehrt(bestand: Path) -> None:
    """Der Kern: Der Stand steht in den Fassungen, nicht in einem Zaehler.

    Genau deshalb ueberlebt er einen Abbruch ohne Aufraeumarbeit - es wurde
    nie etwas anderes behauptet, als was in der Tabelle steht.
    """
    with SqliteStore(bestand) as s:
        gruppen = {g.group_id: g for g in s.load_groups()}

    with MarketingStore(bestand) as store:
        lauf_id = store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)

        # Nichts getan: alles offen.
        stand = lauf.lies_fortschritt(store, lauf_id, gruppen)
        assert stand.kommentare_veroeffentlicht == 0
        assert stand.kommentare_ziel == 2 * lauf.ZIEL_JE_GRUPPE
        assert not stand.fertig

        # Zwei Fassungen einer Gruppe veroeffentlichen - ohne den Lauf
        # anzufassen. Der Fortschritt muss das trotzdem sehen.
        for nummer in (1, 2):
            store.setze_erzeugten_vorschlag(
                KAMPAGNE, "111", Texttyp.KOMMENTAR, nummer, text="Text", vorlage_key="k"
            )
            store.setze_vorschlag_stand(
                KAMPAGNE, "111", Texttyp.KOMMENTAR, nummer, VorschlagStatus.VEROEFFENTLICHT
            )

        stand = lauf.lies_fortschritt(store, lauf_id, gruppen)
        assert stand.kommentare_veroeffentlicht == 2

        # Und der naechste Schritt ist Fassung 3 - nicht 1.
        schritt = lauf.naechster_schritt(stand)
        assert schritt is not None
        assert schritt.group_id == "111"
        assert schritt.nummer == 3


def test_ein_neuer_lauf_friert_die_kampagnenliste_ein(bestand: Path) -> None:
    """Punkt 16: Eine spaeter aktivierte Kampagne greift nicht in den Lauf ein."""
    with MarketingStore(bestand) as store:
        lauf_id = store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)

        store.save_campaign(
            Campaign(campaign_id="spaeter", name="Spaeter", language="ar", audiences=["syrians"])
        )

        zeilen = store.lauf_kampagnen(lauf_id)
        assert [z["campaign_id"] for z in zeilen] == [KAMPAGNE]


def test_ein_offener_lauf_wird_fortgesetzt_nicht_neu_begonnen(bestand: Path) -> None:
    """Punkt 17: Der zweite Start nimmt den ersten Lauf wieder auf."""
    from fbgroups.marketing import automatik

    with MarketingStore(bestand) as store:
        erste, neu1 = automatik.hole_oder_starte_lauf(store, ziel_je_gruppe=5)
        zweite, neu2 = automatik.hole_oder_starte_lauf(store, ziel_je_gruppe=5)

    assert neu1 is True
    assert neu2 is False
    assert erste == zweite


def test_erschoepfung_ueberlebt_den_neustart(bestand: Path) -> None:
    """Sie ist ein Urteil nach dem Versuch - deshalb gespeichert, nicht abgeleitet."""
    with SqliteStore(bestand) as s:
        gruppen = {g.group_id: g for g in s.load_groups()}

    with MarketingStore(bestand) as store:
        lauf_id = store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)
        store.setze_kommentar_erschoepft(KAMPAGNE, "111", "nur 2 Beitraege vorhanden")

    with MarketingStore(bestand) as store:
        stand = lauf.lies_fortschritt(store, lauf_id, gruppen)
        erste = next(g for g in stand.kampagnen[0].gruppen if g.group_id == "111")
        assert erste.erschoepft
        assert "2 Beitraege" in erste.erschoepft_grund
        # Und sie haelt den Lauf nicht mehr auf:
        schritt = lauf.naechster_schritt(stand)
        assert schritt is not None
        assert schritt.group_id == "222"


def test_der_lauf_wird_erst_am_ende_fertig_gemeldet(bestand: Path) -> None:
    """Szenario E mit echtem Speicher - beide Gruppen, dann erst 'fertig'."""
    with SqliteStore(bestand) as s:
        gruppen = {g.group_id: g for g in s.load_groups()}

    with MarketingStore(bestand) as store:
        lauf_id = store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)

        for gid in GRUPPEN:
            for nummer in range(1, lauf.ZIEL_JE_GRUPPE + 1):
                store.setze_erzeugten_vorschlag(
                    KAMPAGNE, gid, Texttyp.KOMMENTAR, nummer, text="Text", vorlage_key="k"
                )
            if gid == "111":  # nur die erste Gruppe fertig machen
                for nummer in range(1, lauf.ZIEL_JE_GRUPPE + 1):
                    store.setze_vorschlag_stand(
                        KAMPAGNE, gid, Texttyp.KOMMENTAR, nummer, VorschlagStatus.VEROEFFENTLICHT
                    )

        stand = lauf.lies_fortschritt(store, lauf_id, gruppen)
        assert stand.gruppen_fertig == 1
        assert not stand.fertig, "eine fertige Gruppe ist keine fertige Kampagne"

        for nummer in range(1, lauf.ZIEL_JE_GRUPPE + 1):
            store.setze_vorschlag_stand(
                KAMPAGNE, "222", Texttyp.KOMMENTAR, nummer, VorschlagStatus.VEROEFFENTLICHT
            )

        stand = lauf.lies_fortschritt(store, lauf_id, gruppen)
        assert stand.fertig
        assert stand.kommentare_veroeffentlicht == 2 * lauf.ZIEL_JE_GRUPPE
        assert "erfolgreich abgeschlossen" in lauf.abschlusstext(stand)


def test_der_lauf_status_kommt_aus_der_datenbank(bestand: Path) -> None:
    with SqliteStore(bestand) as s:
        gruppen = {g.group_id: g for g in s.load_groups()}
    with MarketingStore(bestand) as store:
        lauf_id = store.starte_lauf([KAMPAGNE], ziel_je_gruppe=5)
        assert lauf.lies_fortschritt(store, lauf_id, gruppen).status is LaufStatus.LAEUFT
        store.setze_lauf_status(lauf_id, LaufStatus.FERTIG.value)
        assert lauf.lies_fortschritt(store, lauf_id, gruppen).status is LaufStatus.FERTIG
        assert store.lauf(lauf_id)["beendet_am"] is not None


# --- Die Schleife selbst: Szenario A und B ohne Browser -------------------
class _Konfig:
    """Die echte Projektkonfiguration, nur mit umgebogenem Datenbankpfad.

    Ein duennerer Stub waere verlockend, ginge aber am Zweck vorbei: Der
    Treiber reicht ``config`` bis in ``beitrag.mit_link`` durch, wo ``{datum}``
    aus ``textvorlagen`` aufgeloest wird. Mit einem Stub prueft der Test dann
    eine Konfiguration, die es nicht gibt.

    Der Kaltmodus ist hier aus, weil er eine andere Frage beantwortet (in
    welchem Takt?) als dieser Test (in welcher Reihenfolge?). Seine eigenen
    Tests stehen in ``test_kaltmodus.py``.
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
        return self._echt.get(*pfad, default=default)


def _texte_anlegen(store: MarketingStore, campaign_id: str, gruppen: list[str]) -> None:
    """Fuenf Kommentarfassungen je Gruppe - mit ``{link}`` wie jede echte Vorlage.

    Der Platzhalter gehoert dazu und ist nicht Beiwerk: ``beitrag.mit_link``
    setzt dort den Tracking-Link ein, und ein Text ohne ihn ergaebe einen
    Kommentar, dessen Gruppe nie einen Klick gutgeschrieben bekaeme.
    """
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


def test_die_schleife_arbeitet_alle_gruppen_ab(bestand: Path) -> None:
    """Szenario A/B: ein Start, danach laeuft alles allein durch.

    ``ausfuehren`` zaehlt, statt zu kommentieren - damit prueft dieser Test
    genau die Schleife und nicht den Browser.
    """
    from fbgroups.marketing import automatik

    with MarketingStore(bestand) as store:
        _texte_anlegen(store, KAMPAGNE, list(GRUPPEN))
        store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)

    gesehen: list[str] = []

    def ausfuehren(url: str, group_id: str, text: str) -> automatik.Schrittergebnis:
        gesehen.append(group_id)
        return automatik.Schrittergebnis(erfolg=True, post_url=f"p{len(gesehen)}")

    fortschritt = automatik.fuehre_lauf_aus(_Konfig(bestand), ausfuehren=ausfuehren)

    voll = lauf.ZIEL_JE_GRUPPE
    assert len(gesehen) == 2 * voll, "zwei Gruppen a zehn Kommentare"
    assert fortschritt.fertig
    assert fortschritt.kommentare_veroeffentlicht == 2 * voll
    # Erst die eine Gruppe ganz, dann die andere - nicht abwechselnd.
    assert gesehen[:voll] == [gesehen[0]] * voll
    assert gesehen[voll:] == [gesehen[voll]] * voll
    assert gesehen[0] != gesehen[voll]


def test_die_schleife_setzt_nach_einem_abbruch_fort(bestand: Path) -> None:
    """Szenario C: Nach sechs Kommentaren abbrechen, dann weiter - nicht von vorn."""
    from fbgroups.marketing import automatik

    with MarketingStore(bestand) as store:
        _texte_anlegen(store, KAMPAGNE, list(GRUPPEN))
        store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)

    def erfolgreich(url: str, group_id: str, text: str) -> automatik.Schrittergebnis:
        return automatik.Schrittergebnis(erfolg=True, post_url="p")

    erster = automatik.fuehre_lauf_aus(_Konfig(bestand), ausfuehren=erfolgreich, max_schritte=6)
    assert erster.kommentare_veroeffentlicht == 6
    assert not erster.fertig
    rest = 2 * lauf.ZIEL_JE_GRUPPE - 6

    weitere: list[int] = []

    def zaehlend(url: str, group_id: str, text: str) -> automatik.Schrittergebnis:
        weitere.append(1)
        return automatik.Schrittergebnis(erfolg=True, post_url="p")

    zweiter = automatik.fuehre_lauf_aus(_Konfig(bestand), ausfuehren=zaehlend)

    assert len(weitere) == rest, "nur die restlichen, nicht wieder alle"
    assert zweiter.fertig
    assert zweiter.kommentare_veroeffentlicht == 2 * lauf.ZIEL_JE_GRUPPE


def test_dauerhafte_fehlschlaege_erschoepfen_die_gruppe(bestand: Path) -> None:
    """Punkt 4: Ein Fehler darf die Kampagne nicht faelschlich erfolgreich machen.

    Und er darf sie auch nicht ewig aufhalten: Nach ``MAX_VERSUCHE_JE_FASSUNG``
    gilt die Fassung als aufgegeben, nach allen Fassungen die Gruppe als
    erschoepft - erledigt, aber ausdruecklich nicht erfolgreich.
    """
    from fbgroups.marketing import automatik

    with MarketingStore(bestand) as store:
        _texte_anlegen(store, KAMPAGNE, list(GRUPPEN))
        store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)

    def scheitert(url: str, group_id: str, text: str) -> automatik.Schrittergebnis:
        return automatik.Schrittergebnis(erfolg=False, fehler="Kommentare abgeschaltet")

    fortschritt = automatik.fuehre_lauf_aus(
        _Konfig(bestand), ausfuehren=scheitert, max_schritte=60
    )

    assert fortschritt.kommentare_veroeffentlicht == 0
    text = lauf.abschlusstext(fortschritt)
    assert f"0 / {2 * lauf.ZIEL_JE_GRUPPE}" in text
    assert "erfolgreich abgeschlossen" not in text or "erschoepft" in text


def test_ein_erschoepfter_schritt_haelt_die_gruppe_nicht_fest(bestand: Path) -> None:
    """Meldet der Schritt 'keine Beitraege mehr', geht es zur naechsten Gruppe."""
    from fbgroups.marketing import automatik

    with MarketingStore(bestand) as store:
        _texte_anlegen(store, KAMPAGNE, list(GRUPPEN))
        store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)

    gesehen: list[str] = []

    def leer(url: str, group_id: str, text: str) -> automatik.Schrittergebnis:
        gesehen.append(group_id)
        return automatik.Schrittergebnis(
            erfolg=False, fehler="keine Beitraege zum Kommentieren gefunden", erschoepft=True
        )

    automatik.fuehre_lauf_aus(_Konfig(bestand), ausfuehren=leer, max_schritte=20)

    # Je Gruppe genau ein Anlauf: Danach steht sie als erschoepft fest.
    assert sorted(set(gesehen)) == sorted(GRUPPEN)
    assert len(gesehen) == 2


# --- Die Weboberflaeche: Zahlen, keine Knoepfe ----------------------------
def test_der_stand_ist_ohne_lauf_untaetig(bestand: Path) -> None:
    from fastapi.testclient import TestClient

    from fbgroups.marketing.web import create_app

    antwort = TestClient(create_app(db_path=bestand)).get("/automatik")
    assert antwort.status_code == 200
    assert antwort.json()["status"] == "untaetig"


def test_der_stand_nennt_die_zahlen_des_laufs(bestand: Path) -> None:
    """Punkt 9: Die Zahl steht vorn - 'laeuft' allein beantwortet nichts."""
    from fastapi.testclient import TestClient

    from fbgroups.marketing.web import create_app

    with MarketingStore(bestand) as store:
        store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)

    daten = TestClient(create_app(db_path=bestand)).get("/automatik").json()
    assert daten["status"] == LaufStatus.LAEUFT.value
    assert daten["kommentare"] == {"fertig": 0, "gesamt": 2 * lauf.ZIEL_JE_GRUPPE}
    assert daten["gruppen"] == {"fertig": 0, "gesamt": 2}
    assert daten["fertig"] is False


def test_der_stand_startet_nichts(bestand: Path) -> None:
    """Es gibt bewusst keinen Startweg im Dienst.

    Ein Lauf braucht einen sichtbaren Browser mit angemeldeter Sitzung. Auf
    dem Server gibt es beides nicht - ein Startknopf dort waere ein Knopf, der
    zuverlaessig fehlschlaegt und dabei einen Fehlversuch protokolliert.
    """
    from fastapi.testclient import TestClient

    client = TestClient(create_app_fuer_test(bestand))
    assert client.post("/automatik/start").status_code in (404, 405)


def test_der_automatikknopf_fehlt_ohne_facebook_sitzung() -> None:
    """Ein Knopf, der zuverlaessig scheitert, ist schlimmer als kein Knopf.

    Er laesst den **Dienst** einen sichtbaren Browser oeffnen. Auf dem Server
    gibt es weder $DISPLAY noch eine angemeldete Sitzung; dort endete jeder
    Druck in "Executable doesn't exist" - und hinterliess einen Fehlversuch im
    Protokoll und eine Kaltmodus-Sperre. Der Weg
    ``POST /arbeit/{k}/vorschlag/auto`` bleibt trotzdem bestehen: Was fehlt,
    ist der Knopf, nicht der Weg.
    """
    from fbgroups.marketing.arbeitsseite import _knopfreihe

    ohne = _knopfreihe("post", "Beitrag", "https://example.invalid/g/1", automatik_moeglich=False)
    mit = _knopfreihe("post", "Beitrag", "https://example.invalid/g/1", automatik_moeglich=True)

    assert "Automatisch posten" not in ohne
    assert "Automatisch posten" in mit
    # Die uebrigen drei Knoepfe stehen in beiden Faellen - sie brauchen
    # keinen Browser auf dem Server, sondern den im Kopf des Menschen.
    for html in (ohne, mit):
        assert "speichern-post" in html
        assert "kopieren-post" in html
        assert "Gruppe bei Facebook oeffnen" in html


def create_app_fuer_test(pfad: Path):
    from fbgroups.marketing.web import create_app

    return create_app(db_path=pfad)


# --- Fernbetrieb: eine Wahrheit, und sie liegt auf dem Server -------------
def _fern_client(bestand: Path):
    """Ein Client, der wie ein Aufruf durch den SSH-Tunnel aussieht.

    ``_nur_lokal`` prueft Absenderadresse **und** Herkunft der Seite; die
    Testclient-Adresse ist bereits oertlich, der Ursprung muss gesetzt werden.
    """
    from fastapi.testclient import TestClient

    from fbgroups.marketing.web import create_app

    # Der Ursprung muss eine oertliche Adresse sein - genau das ist die zweite
    # Pruefung in ``_nur_lokal``. Im Betrieb liefert der SSH-Tunnel sie
    # (``http://127.0.0.1:8090``); ``testserver`` waere eine fremde Seite.
    return TestClient(
        create_app(db_path=bestand), headers={"Origin": "http://127.0.0.1:8090"}
    )


def test_der_server_gibt_den_naechsten_schritt_heraus(bestand: Path) -> None:
    """Der Weg, der die zweite Datenbank ueberfluessig macht.

    Er liefert den **fertigen** Text mit eingesetztem Tracking-Link: Der
    Arbeitsrechner baut ihn nie selbst, also kann er ihn auch nicht anders
    bauen als der Server.
    """
    with MarketingStore(bestand) as store:
        _texte_anlegen(store, KAMPAGNE, list(GRUPPEN))

    daten = _fern_client(bestand).post("/automatik/naechster", json={}).json()

    assert daten["weiter"] is True
    schritt = daten["schritt"]
    assert schritt["nummer"] == 1
    assert schritt["group_id"] in GRUPPEN
    assert schritt["gruppen_url"].startswith("https://www.facebook.com/groups/")
    assert "{link}" not in schritt["text"], "der Link muss eingesetzt sein"
    assert "FB-TST-BER" in schritt["text"]


def test_die_meldung_bucht_auf_dem_server(bestand: Path) -> None:
    """Nach der Meldung steht die Fassung dort als veroeffentlicht - nicht nur hier."""
    with MarketingStore(bestand) as store:
        _texte_anlegen(store, KAMPAGNE, list(GRUPPEN))

    client = _fern_client(bestand)
    schritt = client.post("/automatik/naechster", json={}).json()["schritt"]

    antwort = client.post(
        "/automatik/ergebnis",
        json={
            "campaign_id": schritt["campaign_id"],
            "group_id": schritt["group_id"],
            "nummer": schritt["nummer"],
            "erfolg": True,
            "post_url": "https://www.facebook.com/groups/1/posts/9",
        },
    )
    assert antwort.status_code == 200
    assert antwort.json()["ok"] is True

    with MarketingStore(bestand) as store:
        v = store.vorschlag(
            schritt["campaign_id"], schritt["group_id"], Texttyp.KOMMENTAR, schritt["nummer"]
        )
        assert v is not None
        assert v.status is VorschlagStatus.VEROEFFENTLICHT


def test_der_naechste_schritt_zaehlt_nach_der_meldung_weiter(bestand: Path) -> None:
    """Fassung 1 gemeldet, also kommt Fassung 2 - der Stand lebt auf dem Server."""
    with MarketingStore(bestand) as store:
        _texte_anlegen(store, KAMPAGNE, list(GRUPPEN))

    client = _fern_client(bestand)
    erster = client.post("/automatik/naechster", json={}).json()["schritt"]
    client.post(
        "/automatik/ergebnis",
        json={
            "campaign_id": erster["campaign_id"],
            "group_id": erster["group_id"],
            "nummer": erster["nummer"],
            "erfolg": True,
        },
    )
    zweiter = client.post("/automatik/naechster", json={}).json()["schritt"]

    assert zweiter["group_id"] == erster["group_id"]
    assert zweiter["nummer"] == 2


def test_die_meldung_traegt_keinen_text(bestand: Path) -> None:
    """Der Text geht nur hinaus, nie zurueck.

    Dieselbe Zusage wie beim Ergebnisformular der Arbeitsseite: Ein
    manipulierter Aufruf kann keinen anderen Text in einen Kommentar bringen
    als den, den der Server vorbereitet hat.
    """
    from fbgroups.marketing.web import AutomatikErgebnis

    assert "text" not in AutomatikErgebnis.model_fields


def test_die_fernwege_sind_von_aussen_nicht_erreichbar(bestand: Path) -> None:
    """Wie jeder schreibende Weg hinter ``_nur_lokal`` - 404, nicht 403.

    Wer den Dienst oeffentlich stellt, soll nicht nebenbei verraten, dass es
    hier eine Automatik gibt.
    """
    from fastapi.testclient import TestClient

    from fbgroups.marketing.web import create_app

    # Ohne Origin-Kopf gilt der Aufruf als fremde Seite.
    client = TestClient(create_app(db_path=bestand), headers={"Origin": "https://boese.invalid"})
    assert client.post("/automatik/naechster", json={}).status_code == 404
    assert (
        client.post(
            "/automatik/ergebnis",
            json={"campaign_id": KAMPAGNE, "group_id": "111", "nummer": 1, "erfolg": True},
        ).status_code
        == 404
    )


def test_ein_lauf_laesst_sich_auf_eine_kampagne_einschraenken(bestand: Path) -> None:
    """Der erste Ernstfall: an **einer** Kampagne sehen, nicht an allen aktiven.

    Ohne diese Einschraenkung fror der erste Versuch alle aktiven Kampagnen
    ein - beim Nutzer waeren das fuenf gewesen, also sofort echte Gruppen
    statt der Testgruppe.
    """
    from fbgroups.marketing import automatik

    with MarketingStore(bestand) as store:
        store.save_campaign(
            Campaign(
                campaign_id="zweite",
                name="Zweite",
                language="ar",
                audiences=["syrians"],
                status=CampaignStatus.ACTIVE,
            )
        )
        assert sorted(automatik.aktive_kampagnen(store)) == sorted([KAMPAGNE, "zweite"])

        lauf_id, neu = automatik.hole_oder_starte_lauf(
            store, ziel_je_gruppe=5, nur=[KAMPAGNE]
        )
        assert neu is True
        assert [z["campaign_id"] for z in store.lauf_kampagnen(lauf_id)] == [KAMPAGNE]


def test_die_einschraenkung_ueberstimmt_pausiert_nicht(bestand: Path) -> None:
    """Sonst waere "pausiert" eine Beschriftung ohne Wirkung."""
    from fbgroups.marketing import automatik

    with MarketingStore(bestand) as store:
        kampagne = store.load_campaign(KAMPAGNE)
        kampagne.status = CampaignStatus.PAUSED
        store.save_campaign(kampagne)

        lauf_id, _ = automatik.hole_oder_starte_lauf(store, ziel_je_gruppe=5, nur=[KAMPAGNE])
        assert store.lauf_kampagnen(lauf_id) == []


def test_die_einschraenkung_gilt_nur_beim_anlegen(bestand: Path) -> None:
    """Punkt 16: Ein offener Lauf behaelt seine eingefrorene Liste."""
    from fbgroups.marketing import automatik

    with MarketingStore(bestand) as store:
        erste, _ = automatik.hole_oder_starte_lauf(store, ziel_je_gruppe=5)
        zweite, neu = automatik.hole_oder_starte_lauf(
            store, ziel_je_gruppe=5, nur=["gibtesnicht"]
        )

    assert zweite == erste
    assert neu is False


def test_kampagnen_im_lauf_behalten_ihre_reihenfolge(bestand: Path) -> None:
    """Ohne feste Position koennte ein fortgesetzter Lauf anders waehlen."""
    with MarketingStore(bestand) as store:
        store.save_campaign(
            Campaign(campaign_id="zweite", name="Zweite", language="ar", audiences=["syrians"])
        )
        lauf_id = store.starte_lauf(["zweite", KAMPAGNE], ziel_je_gruppe=5)
        zeilen = store.lauf_kampagnen(lauf_id)

    assert [z["campaign_id"] for z in zeilen] == ["zweite", KAMPAGNE]
    assert [z["position"] for z in zeilen] == [1, 2]
    assert all(z["status"] == KampagnenLaufStatus.WARTET.value for z in zeilen)

# --- Zwei Ziele: Browser und Store, beide getrackt ------------------------
def test_die_ziele_wechseln_je_fassung() -> None:
    """1 Browser, 2 Store, 3 Browser ... - Punkt "Link-Rotation".

    Eine Rechnung, kein gespeicherter Zeiger: Dieselbe Fassung ergibt nach
    einem Abbruch wieder dasselbe Ziel.
    """
    folge = [lauf.ziel_zu_nummer(n) for n in range(1, 11)]
    assert folge == ["browser", "store"] * 5


def test_der_browsercode_leitet_sich_vom_storecode_ab(bestand: Path) -> None:
    """``FB-TST-BER-001`` → ``FB-TST-BER-001-B``.

    Man sieht der Kennung an, zu welchem Paar sie gehoert - bei einem Klick im
    Protokoll ist das die erste Frage. Und die Nummernreihe des
    ``CodeAllocator`` bleibt unberuehrt.
    """
    with MarketingStore(bestand) as store:
        code = store.vergib_browsercode(KAMPAGNE, "111", "https://go.example.invalid")
        link = store.link_for(KAMPAGNE, "111")

    assert code == link.tracking_code + "-B"
    assert link.tracking_url_browser.endswith("/r/" + code)


def test_ein_vergebener_browsercode_wird_nie_ersetzt(bestand: Path) -> None:
    """Er steht moeglicherweise schon in einem veroeffentlichten Beitrag."""
    with MarketingStore(bestand) as store:
        erst = store.vergib_browsercode(KAMPAGNE, "111", "https://go.example.invalid")
        nochmal = store.vergib_browsercode(KAMPAGNE, "111", "https://ganz.anders.invalid")

    assert erst == nochmal


def test_beide_codes_fuehren_zum_selben_paar(bestand: Path) -> None:
    """Sonst antwortete die Weiterleitung auf jeden Browser-Code mit 404.

    Der Klick waere verloren - bei einem Code, der bereits in einem Beitrag
    steht.
    """
    with MarketingStore(bestand) as store:
        browser = store.vergib_browsercode(KAMPAGNE, "111", "https://go.example.invalid")
        link = store.link_for(KAMPAGNE, "111")

        ueber_store = store.resolve_code(link.tracking_code)
        ueber_browser = store.resolve_code(browser)

        assert ueber_store is not None
        assert ueber_browser is not None
        assert ueber_store.group_id == ueber_browser.group_id == "111"

        # Und das Ziel haengt am Code, nicht an der Kampagne.
        assert store.ziel_des_codes(link.tracking_code) == "store"
        assert store.ziel_des_codes(browser) == "browser"
        assert store.ziel_des_codes("gibtesnicht") == ""


def test_der_bestehende_code_bleibt_ein_storecode(bestand: Path) -> None:
    """Die wichtigste Zusage dieser Aenderung.

    ``tracking_code`` steht in veroeffentlichten Beitraegen. Sein Ziel
    nachtraeglich umzustellen aenderte, wohin alte Beitraege fuehren - ohne
    dass jemand sie angefasst haette.
    """
    with MarketingStore(bestand) as store:
        vorher = store.link_for(KAMPAGNE, "111").tracking_code
        store.vergib_browsercode(KAMPAGNE, "111", "https://go.example.invalid")
        nachher = store.link_for(KAMPAGNE, "111")

    assert nachher.tracking_code == vorher
    assert store_ziel(bestand, vorher) == "store"


def store_ziel(pfad: Path, code: str) -> str:
    with MarketingStore(pfad) as store:
        return store.ziel_des_codes(code)


def test_der_text_traegt_den_code_des_gewaehlten_ziels(bestand: Path) -> None:
    """``mit_link`` setzt den Code ein, der zum Ziel gehoert."""
    from fbgroups.config import load_config
    from fbgroups.marketing.beitrag import mit_link

    cfg = load_config()
    with MarketingStore(bestand) as store:
        browser = store.vergib_browsercode(KAMPAGNE, "111", "https://go.example.invalid")
        campaign = store.load_campaign(KAMPAGNE)
        link = store.link_for(KAMPAGNE, "111")

    im_browser = mit_link(campaign, link, "x {link}", config=cfg, ziel="browser")
    im_store = mit_link(campaign, link, "x {link}", config=cfg, ziel="store")

    assert browser in im_browser
    assert link.tracking_url in im_store
    assert im_browser != im_store


def test_ohne_browsercode_faellt_es_auf_den_storecode_zurueck(bestand: Path) -> None:
    """Ein Beitrag ohne Link waere schlimmer als einer mit dem anderen Ziel."""
    with MarketingStore(bestand) as store:
        link = store.link_for(KAMPAGNE, "111")

    assert link.tracking_code_browser == ""
    assert link.code_fuer("browser") == link.tracking_code
    assert link.url_fuer("browser") == link.tracking_url

# --- Anreicherung: Zahlen vom Browser auf den Server ----------------------
def test_der_server_nimmt_mitgliederzahl_und_aktivitaet_entgegen(bestand: Path) -> None:
    """Der Weg, der die zweite Datenbank vermeidet.

    ``enrich --browser`` laeuft auf dem Arbeitsrechner - dort steht der
    angemeldete Browser. Gebucht wird auf dem Server, wo auch die Klicks
    gezaehlt werden; sonst zeigte das Dashboard weiter "unknown".
    """
    client = _fern_client(bestand)
    antwort = client.post(
        "/automatik/anreichern/ergebnis",
        json={
            "group_id": "111",
            "erreichbar": True,
            "member_count": 12400,
            "privacy_hint": "public",
            "posts_per_day": 3.5,
            "activity_factor": 0.8,
        },
    )
    assert antwort.status_code == 200
    assert antwort.json()["member_count"] == 12400

    with SqliteStore(bestand) as gruppen_store:
        g = next(x for x in gruppen_store.load_groups() if x.group_id == "111")

    assert g.member_count == 12400
    assert g.member_count_source is not None
    assert g.activity_factor == 0.8
    assert g.activity_source is not None
    assert g.member_count_checked_at is not None


def test_eine_fehlende_zahl_loescht_keine_vorhandene(bestand: Path) -> None:
    """Ein Anmeldefenster ist kein Beleg dafuer, dass eine Gruppe geschrumpft ist.

    Der Zeitpunkt der Pruefung wird trotzdem gesetzt - sonst liefe derselbe
    erfolglose Abruf bei jedem Lauf erneut.
    """
    client = _fern_client(bestand)
    client.post(
        "/automatik/anreichern/ergebnis",
        json={"group_id": "111", "erreichbar": True, "member_count": 500},
    )
    client.post(
        "/automatik/anreichern/ergebnis",
        json={"group_id": "111", "erreichbar": False},
    )

    with SqliteStore(bestand) as gruppen_store:
        g = next(x for x in gruppen_store.load_groups() if x.group_id == "111")

    assert g.member_count == 500, "die Zahl von vorhin steht noch"
    assert g.last_checked_at is not None


def test_die_naechsten_gruppen_sind_die_ohne_zahlen(bestand: Path) -> None:
    daten = _fern_client(bestand).post("/automatik/anreichern/naechste", json={}).json()
    assert sorted(g["group_id"] for g in daten["gruppen"]) == ["111", "222"]
    assert daten["offen"] == 2


def test_die_anreicherungswege_sind_von_aussen_nicht_erreichbar(bestand: Path) -> None:
    from fastapi.testclient import TestClient

    from fbgroups.marketing.web import create_app

    fremd = TestClient(
        create_app(db_path=bestand), headers={"Origin": "https://boese.invalid"}
    )
    assert fremd.post("/automatik/anreichern/naechste", json={}).status_code == 404
    assert (
        fremd.post(
            "/automatik/anreichern/ergebnis", json={"group_id": "111"}
        ).status_code
        == 404
    )


def test_die_meldung_hat_kein_feld_fuer_texte() -> None:
    """Die harte Projektgrenze gilt auch hier.

    Keine Beitragsinhalte, keine Namen von Menschen - ein Feld, das es nicht
    gibt, kann auch nicht gefuellt werden.
    """
    from fbgroups.marketing.web import AnreicherungErgebnis

    felder = set(AnreicherungErgebnis.model_fields)
    assert not felder & {"text", "beitrag", "autor", "author", "name", "mitglieder_namen"}
