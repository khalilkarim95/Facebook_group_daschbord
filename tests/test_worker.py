"""Tests fuer den Arbeiter - Grenzen, Reihenfolge, Abbruch, Protokoll.

Kein Browser, kein Netz, kein Facebook: Der Arbeiter ist die Ablaufsteuerung,
und genau die muss fuer sich pruefbar sein. Der ``Veroeffentlicher`` ist ein
Protokoll, und hier steht eine Fassung, die mitschreibt, was sie gefragt
wurde - damit laesst sich pruefen, **dass** nacheinander gearbeitet wird und
nicht nur, dass am Ende eine Zahl stimmt.

Die Wartezeit wird nicht ausgesessen, sondern mitgeschrieben: ``schlafen`` ist
ein Parameter. Ein Test, der die Grenzen auf Millisekunden setzt, pruefte die
Grenzen des Tests und nicht die des Betriebs.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from fbgroups.marketing.models import (
    Campaign,
    CampaignGroup,
    JobStatus,
    PostStatus,
    QueueZustand,
    TextQuelle,
)
from fbgroups.marketing.store import MarketingStore
from fbgroups.marketing.worker import (
    AUSLOESER,
    Abbruchgrund,
    Ergebnis,
    Grenzen,
    arbeite,
    lade_grenzen,
)
from fbgroups.models import Group
from fbgroups.storage import SqliteStore

KAMPAGNE = "batreeq"
# Drei Gruppen, damit sich Reihenfolge und Abbruch mitten im Lauf pruefen lassen.
GRUPPEN = {
    "482910573829104": ("Syrer in Koeln", "FB-SYR-KLN-002"),
    "739201847362915": ("Syrer in Berlin", "FB-SYR-BER-001"),
    "918273645510293": ("Araber in Hamburg", "FB-ARA-HAM-003"),
}


class Mitschreiber:
    """Ein ``Veroeffentlicher``, der nichts tut und alles festhaelt."""

    name = "test"

    def __init__(self, *ergebnisse: Ergebnis) -> None:
        # Was er der Reihe nach zurueckgibt. Geht die Liste aus, gilt Erfolg.
        self._ergebnisse = list(ergebnisse)
        self.gesehen: list[tuple[str, str]] = []   # (group_id, text)

    def veroeffentliche(self, *, gruppe, text, link) -> Ergebnis:
        self.gesehen.append((link.group_id, text))
        if self._ergebnisse:
            return self._ergebnisse.pop(0)
        return Ergebnis(erfolg=True)


class Wecker:
    """Ersatz fuer ``time.sleep`` - schreibt die Wartezeiten mit.

    Optional laeuft bei einer bestimmten Wartezeit ein Seiteneffekt: So laesst
    sich pruefen, was geschieht, wenn jemand **waehrend** der Pause auf
    ``pausiert`` stellt.
    """

    def __init__(self, bei=None) -> None:
        self.zeiten: list[float] = []
        self._bei = bei

    def __call__(self, sekunden: float) -> None:
        self.zeiten.append(sekunden)
        if self._bei is not None:
            self._bei(len(self.zeiten))


@pytest.fixture()
def bestand(tmp_path: Path) -> Path:
    """Drei Gruppen, alle eingereiht - der uebliche Ausgangspunkt."""
    pfad = tmp_path / "groups.sqlite"
    with SqliteStore(pfad) as store:
        store.upsert_groups(
            [
                Group(
                    group_id=gid,
                    url_canonical=f"https://www.facebook.com/groups/{gid}",
                    name=name,
                )
                for gid, (name, _) in GRUPPEN.items()
            ]
        )
    with MarketingStore(pfad) as store:
        store.save_campaign(
            Campaign(
                campaign_id=KAMPAGNE,
                name="Batreeq Syrian Germany",
                landing_page="https://b-tarikak.de/",
                message_template="Hallo! {link}",
            )
        )
        for gid, (_, code) in GRUPPEN.items():
            store.add_link(
                CampaignGroup(
                    campaign_id=KAMPAGNE,
                    group_id=gid,
                    tracking_code=code,
                    # Ohne die URL ersetzt ``{link}`` durch Leere - der Beitrag
                    # ginge ohne Link hinaus und keine Gruppe bekaeme je einen
                    # Klick gutgeschrieben. Im Betrieb setzt ``add_links`` sie.
                    tracking_url=f"https://b-tarikak.de/r/{code}",
                )
            )
            store.set_post_text(KAMPAGNE, gid, f"Text fuer {gid} {{link}}", TextQuelle.KI)
            store.set_job_status(KAMPAGNE, gid, JobStatus.AI_GENERATED)
            store.set_job_status(KAMPAGNE, gid, JobStatus.PENDING_REVIEW)
            store.set_job_status(KAMPAGNE, gid, JobStatus.APPROVED, akteur="karim")
            store.set_job_status(KAMPAGNE, gid, JobStatus.QUEUED)
    return pfad


@pytest.fixture()
def store(bestand: Path):
    with MarketingStore(bestand) as s:
        yield s


@pytest.fixture()
def campaign(store: MarketingStore) -> Campaign:
    kampagne = store.load_campaign(KAMPAGNE)
    assert kampagne is not None
    return kampagne


@pytest.fixture()
def gruppen(bestand: Path) -> dict[str, Group]:
    with SqliteStore(bestand) as s:
        return {g.group_id: g for g in s.load_groups()}


def lauf(store, campaign, gruppen, veroeffentlicher, **kwargs):
    """Ein Lauf mit weiten Grenzen - die Tests engen selbst ein."""
    grenzen = kwargs.pop("grenzen", Grenzen(tageslimit=99, max_pro_lauf=99))
    kwargs.setdefault("schlafen", lambda _: None)
    kwargs.setdefault("wuerfel", random.Random(1))
    return arbeite(store, campaign, gruppen, veroeffentlicher, grenzen, **kwargs)


# --- Nacheinander, nie zugleich -----------------------------------------

def test_arbeitet_streng_nacheinander(store, campaign, gruppen) -> None:
    """Der Kern: eine Gruppe nach der anderen, jede genau einmal."""
    adapter = Mitschreiber()

    bericht = lauf(store, campaign, gruppen, adapter)

    assert bericht.veroeffentlicht == 3
    assert len(adapter.gesehen) == 3
    assert len({gid for gid, _ in adapter.gesehen}) == 3      # keine doppelt


def test_jede_gruppe_bekommt_ihren_eigenen_text(store, campaign, gruppen) -> None:
    """Der Tracking-Link gehoert zur Gruppe, nicht zur Kampagne.

    Ein Beitrag mit dem Code einer anderen Gruppe laesst sich nicht mehr
    zurueckholen, und die Auswertung schriebe die Klicks der falschen Gruppe gut.
    """
    adapter = Mitschreiber()

    lauf(store, campaign, gruppen, adapter)

    for gid, text in adapter.gesehen:
        assert GRUPPEN[gid][1] in text            # ihr Code
        for anderer_gid, (_, code) in GRUPPEN.items():
            if anderer_gid != gid:
                assert code not in text           # und keiner sonst


def test_zwischen_den_beitraegen_wird_gewartet(store, campaign, gruppen) -> None:
    """Aber nicht vor dem ersten - die Wartezeit trennt, sie leitet nicht ein."""
    wecker = Wecker()

    lauf(store, campaign, gruppen, Mitschreiber(), schlafen=wecker)

    assert len(wecker.zeiten) == 2                # drei Beitraege, zwei Pausen
    assert all(180.0 <= s <= 420.0 for s in wecker.zeiten)


def test_die_wartezeit_kommt_aus_den_grenzen(store, campaign, gruppen) -> None:
    """Keine Zahl steht im Programm fest."""
    wecker = Wecker()
    grenzen = Grenzen(tageslimit=99, max_pro_lauf=99, pause_min=5.0, pause_max=9.0)

    lauf(store, campaign, gruppen, Mitschreiber(), grenzen=grenzen, schlafen=wecker)

    assert all(5.0 <= s <= 9.0 for s in wecker.zeiten)


# --- Die Grenzen ---------------------------------------------------------

def test_die_laufgrenze_haelt(store, campaign, gruppen) -> None:
    adapter = Mitschreiber()
    grenzen = Grenzen(tageslimit=99, max_pro_lauf=2)

    bericht = lauf(store, campaign, gruppen, adapter, grenzen=grenzen)

    assert len(adapter.gesehen) == 2
    assert bericht.grund == Abbruchgrund.LAUFGRENZE


def test_das_tageslimit_ueberlebt_einen_neustart(store, campaign, gruppen) -> None:
    """Der Grund, warum aus ``post_versuche`` gezaehlt wird und nicht im Speicher.

    Wer um 08:00 das Limit ausschoepft, abstuerzt und um 14:00 neu startet,
    saehe sonst einen leeren Zaehler und setzte noch einmal so viele Beitraege.
    """
    grenzen = Grenzen(tageslimit=2, max_pro_lauf=99)

    erster = lauf(store, campaign, gruppen, Mitschreiber(), grenzen=grenzen)
    assert erster.veroeffentlicht == 2
    assert erster.grund == Abbruchgrund.TAGESLIMIT

    # Neuer Lauf, neuer Arbeiter, derselbe Tag - und nichts geht mehr hinaus.
    zweiter_adapter = Mitschreiber()
    zweiter = lauf(store, campaign, gruppen, zweiter_adapter, grenzen=grenzen)

    assert zweiter_adapter.gesehen == []
    assert zweiter.grund == Abbruchgrund.TAGESLIMIT


def test_gestrige_versuche_zaehlen_nicht_mit(store, campaign, gruppen) -> None:
    """"20 pro Tag" heisst pro Tag - sonst waere das Limit nach einer Woche fuer immer erreicht."""
    gestern = datetime.now(UTC) - timedelta(days=1)
    store.conn.execute(
        "INSERT INTO post_versuche (campaign_id, group_id, tracking_code, job_status, "
        "erfolg, begonnen_am) VALUES (?,?,?,?,?,?)",
        (KAMPAGNE, "482910573829104", "X", "published", 1, gestern.isoformat()),
    )
    store.conn.commit()

    assert store.versuche_heute(KAMPAGNE) == 0


def test_das_tageslimit_gilt_ueber_alle_kampagnen(store, campaign, gruppen) -> None:
    """Die knappe Ressource ist das Konto, nicht die Kampagne.

    Je Kampagne gezaehlt waeren zwei Kampagnen vierzig Beitraege aus demselben
    Facebook-Konto - das Limit waere eine Beschriftung ohne Wirkung.
    """
    store.conn.execute(
        "INSERT INTO post_versuche (campaign_id, group_id, tracking_code, job_status, "
        "erfolg, begonnen_am) VALUES (?,?,?,?,?,?)",
        ("eine-andere", "1", "X", "published", 1, datetime.now(UTC).isoformat()),
    )
    store.conn.commit()

    bericht = lauf(
        store, campaign, gruppen, Mitschreiber(), grenzen=Grenzen(tageslimit=1, max_pro_lauf=99)
    )

    assert bericht.versucht == 0
    assert bericht.grund == Abbruchgrund.TAGESLIMIT


def test_die_grenzen_kommen_aus_der_konfiguration(config) -> None:
    """Die echten Werte des Projekts - nicht die Vorgaben im Code."""
    grenzen = lade_grenzen(config)

    assert grenzen.tageslimit == 20
    assert grenzen.pause_min == 180.0
    assert grenzen.pause_max == 420.0


# --- Pause, Fortsetzen, Stopp waehrend des Laufs ------------------------

def test_pause_wirkt_vor_dem_naechsten_beitrag(store, campaign, gruppen) -> None:
    """Der Zustand wird vor jedem Job frisch gelesen - sonst wirkte er nie."""
    store.set_queue_zustand(KAMPAGNE, QueueZustand.PAUSIERT)
    adapter = Mitschreiber()

    bericht = lauf(store, campaign, gruppen, adapter)

    assert adapter.gesehen == []
    assert bericht.grund == Abbruchgrund.PAUSIERT


def test_pause_waehrend_der_wartezeit_wird_bemerkt(store, campaign, gruppen) -> None:
    """Sonst wirkte "pause" erst sieben Minuten spaeter.

    In der Zwischenzeit stuende ein Beitrag in einer Gruppe, den niemand mehr
    wollte - und zurueckholen laesst er sich nicht.
    """
    adapter = Mitschreiber()
    wecker = Wecker(bei=lambda _: store.set_queue_zustand(KAMPAGNE, QueueZustand.PAUSIERT))

    bericht = lauf(store, campaign, gruppen, adapter, schlafen=wecker)

    assert len(adapter.gesehen) == 1          # der begonnene wurde zu Ende gebracht
    assert bericht.grund == Abbruchgrund.PAUSIERT


def test_stopp_beendet_den_lauf_und_raeumt_die_warteschlange(store, campaign, gruppen) -> None:
    """``gestoppt`` ist mehr als ``pausiert``: Es stellt die Jobs zurueck."""
    store.set_queue_zustand(KAMPAGNE, QueueZustand.GESTOPPT)
    adapter = Mitschreiber()

    bericht = lauf(store, campaign, gruppen, adapter)

    assert adapter.gesehen == []
    assert bericht.grund == Abbruchgrund.GESTOPPT
    zaehler = store.job_counts(KAMPAGNE)
    assert zaehler[JobStatus.QUEUED.value] == 0
    assert zaehler[JobStatus.APPROVED.value] == 3


def test_nach_resume_laeuft_es_weiter(store, campaign, gruppen) -> None:
    store.set_queue_zustand(KAMPAGNE, QueueZustand.PAUSIERT)
    assert lauf(store, campaign, gruppen, Mitschreiber()).versucht == 0

    store.set_queue_zustand(KAMPAGNE, QueueZustand.LAUFEND)
    adapter = Mitschreiber()

    assert lauf(store, campaign, gruppen, adapter).veroeffentlicht == 3


# --- Fehler ---------------------------------------------------------------

def test_ein_fehlschlag_haelt_den_lauf_nicht_auf(store, campaign, gruppen) -> None:
    """Eine zickige Gruppe darf nicht den Rest des Tages kosten."""
    adapter = Mitschreiber(Ergebnis(erfolg=False, fehler="erlaubt keine Links"))

    bericht = lauf(store, campaign, gruppen, adapter)

    assert bericht.fehlgeschlagen == 1
    assert bericht.veroeffentlicht == 2
    assert len(adapter.gesehen) == 3


def test_der_fehlergrund_wird_gespeichert(store, campaign, gruppen) -> None:
    """Ohne ihn ist ``retry`` blind - und derselbe Fehler kommt wieder."""
    adapter = Mitschreiber(Ergebnis(erfolg=False, fehler="erlaubt keine Links"))

    lauf(store, campaign, gruppen, adapter)

    gescheitert = store.jobs_mit_status(KAMPAGNE, JobStatus.FAILED)
    assert len(gescheitert) == 1
    assert gescheitert[0].post_error == "erlaubt keine Links"


def test_ein_werfender_adapter_reisst_den_lauf_nicht_mit(store, campaign, gruppen) -> None:
    """Sonst stuende der Job fuer immer auf ``processing``.

    Die Gruppe waere dann weder offen noch fertig, taucht in keiner Liste auf
    und wird nie wieder angefasst.
    """

    class Kaputt:
        name = "kaputt"

        def __init__(self) -> None:
            self.aufrufe = 0

        def veroeffentliche(self, *, gruppe, text, link):
            self.aufrufe += 1
            if self.aufrufe == 1:
                raise RuntimeError("Browser weg")
            return Ergebnis(erfolg=True)

    bericht = lauf(store, campaign, gruppen, Kaputt())

    assert bericht.fehlgeschlagen == 1
    assert bericht.veroeffentlicht == 2
    gescheitert = store.jobs_mit_status(KAMPAGNE, JobStatus.FAILED)
    assert "Browser weg" in gescheitert[0].post_error
    assert store.jobs_mit_status(KAMPAGNE, JobStatus.PROCESSING) == []


# --- Das Protokoll --------------------------------------------------------

def test_der_versuch_steht_vor_dem_absetzen_im_protokoll(store, campaign, gruppen) -> None:
    """Der Arbeiter kann mitten im Absetzen abstuerzen.

    Ohne die Zeile wuesste danach niemand, ob in der Gruppe ein Beitrag steht.
    Geprueft wird das, indem der Adapter waehrend seines Aufrufs nachsieht.
    """
    gesehen: list[int] = []

    class SiehtNach:
        name = "sieht-nach"

        def veroeffentliche(self, *, gruppe, text, link):
            gesehen.append(len(store.versuche_for(KAMPAGNE, link.group_id)))
            return Ergebnis(erfolg=True)

    lauf(store, campaign, gruppen, SiehtNach(), grenzen=Grenzen(tageslimit=99, max_pro_lauf=1))

    assert gesehen == [1]          # die Zeile stand schon, bevor er etwas tat


def test_das_protokoll_nennt_den_arbeiter_und_den_code(store, campaign, gruppen) -> None:
    """Der Code wird mitgeschrieben, nicht nachgeschlagen - die Zuordnung kann verschwinden."""
    lauf(store, campaign, gruppen, Mitschreiber(), grenzen=Grenzen(tageslimit=99, max_pro_lauf=1))

    versuche = store.versuche_for(KAMPAGNE, "482910573829104")
    assert len(versuche) == 1
    assert versuche[0].ausgeloest_von == AUSLOESER
    assert versuche[0].tracking_code == GRUPPEN["482910573829104"][1]
    assert versuche[0].erfolg is True
    assert versuche[0].beendet_am is not None


def test_kein_passwort_im_protokoll(store, campaign, gruppen) -> None:
    """``browser_session`` ist ein Name, keine Anmeldung.

    Das Modell hat kein Feld fuer ein Passwort, ein Cookie oder ein Token -
    und der Arbeiter fuellt nur den Namen des Adapters ein.
    """
    lauf(store, campaign, gruppen, Mitschreiber(), grenzen=Grenzen(tageslimit=99, max_pro_lauf=1))

    versuch = store.versuche_for(KAMPAGNE, "482910573829104")[0]
    assert versuch.browser_session == "test"


# --- Was nicht noch einmal bearbeitet wird -------------------------------

def test_veroeffentlichte_gruppen_kommen_nicht_wieder(store, campaign, gruppen) -> None:
    """Der zweite Lauf findet nichts mehr - ausser ueber ``retry``."""
    lauf(store, campaign, gruppen, Mitschreiber())

    zweiter_adapter = Mitschreiber()
    bericht = lauf(store, campaign, gruppen, zweiter_adapter)

    assert zweiter_adapter.gesehen == []
    assert bericht.grund == Abbruchgrund.FERTIG


def test_retry_holt_nur_die_fehlgeschlagenen_zurueck(store, campaign, gruppen) -> None:
    """``uebersprungen`` ist ein Urteil und kommt nicht mit."""
    adapter = Mitschreiber(
        Ergebnis(erfolg=False, fehler="Netz weg"),
        Ergebnis(erfolg=False, uebersprungen=True),
    )
    lauf(store, campaign, gruppen, adapter)

    store.fehlgeschlagene_zuruecksetzen(KAMPAGNE)

    zaehler = store.job_counts(KAMPAGNE)
    assert zaehler[JobStatus.QUEUED.value] == 1              # nur der Fehlschlag
    assert zaehler[JobStatus.CANCELLED.value] == 1           # das Urteil bleibt


def test_uebersprungen_ist_kein_fehlschlag(store, campaign, gruppen) -> None:
    adapter = Mitschreiber(Ergebnis(erfolg=False, uebersprungen=True))

    bericht = lauf(store, campaign, gruppen, adapter)

    assert bericht.uebersprungen == 1
    assert bericht.fehlgeschlagen == 0
    assert store.jobs_mit_status(KAMPAGNE, JobStatus.CANCELLED)[0].group_id in GRUPPEN


# --- Abbruch durch den Adapter -------------------------------------------

def test_der_adapter_darf_den_lauf_beenden(store, campaign, gruppen) -> None:
    """Im assistierten Betrieb sagt der Mensch "Schluss"."""
    adapter = Mitschreiber(Ergebnis(erfolg=False, abbrechen=True))

    bericht = lauf(store, campaign, gruppen, adapter)

    assert len(adapter.gesehen) == 1
    assert bericht.grund == Abbruchgrund.ADAPTER


def test_nach_schluss_steht_der_job_wieder_in_der_warteschlange(store, campaign, gruppen) -> None:
    """Wer aufhoert, verwirft nichts - der Beitrag ist morgen der naechste."""
    adapter = Mitschreiber(Ergebnis(erfolg=False, abbrechen=True))

    lauf(store, campaign, gruppen, adapter)

    zaehler = store.job_counts(KAMPAGNE)
    assert zaehler[JobStatus.QUEUED.value] == 3
    assert zaehler[JobStatus.FAILED.value] == 0      # kein Fehlschlag


# --- Der Bestand merkt nichts Neues --------------------------------------

def test_post_status_bleibt_lesbar(store, campaign, gruppen) -> None:
    """``campaign queue``, ``retry`` und die Uebersicht lesen weiter mit."""
    adapter = Mitschreiber(Ergebnis(erfolg=False, fehler="Netz weg"))

    lauf(store, campaign, gruppen, adapter)

    zaehler = store.post_counts(KAMPAGNE)
    assert zaehler[PostStatus.VEROEFFENTLICHT.value] == 2
    assert zaehler[PostStatus.FEHLGESCHLAGEN.value] == 1


def test_posted_at_wird_nur_beim_ersten_erfolg_gesetzt(store, campaign, gruppen) -> None:
    """Die Klicks gehen auf den Beitrag zurueck, der zuerst stand."""
    lauf(store, campaign, gruppen, Mitschreiber(), grenzen=Grenzen(tageslimit=99, max_pro_lauf=1))
    gid = "482910573829104"
    zuerst = store.link_for(KAMPAGNE, gid)
    assert zuerst is not None and zuerst.posted_at is not None

    # Noch einmal durch: veroeffentlicht -> draft -> ... -> queued -> veroeffentlicht
    store.set_job_status(KAMPAGNE, gid, JobStatus.DRAFT)
    store.set_job_status(KAMPAGNE, gid, JobStatus.PENDING_REVIEW)
    store.set_job_status(KAMPAGNE, gid, JobStatus.APPROVED)
    store.set_job_status(KAMPAGNE, gid, JobStatus.QUEUED)
    lauf(store, campaign, gruppen, Mitschreiber(), grenzen=Grenzen(tageslimit=99, max_pro_lauf=1))

    danach = store.link_for(KAMPAGNE, gid)
    assert danach is not None and danach.posted_at == zuerst.posted_at


# --- Die Versuchsgrenze ---------------------------------------------------

def test_aufgegebene_kommen_nicht_ewig_zurueck(store, campaign, gruppen) -> None:
    """``max_versuche`` stand seit jeher in der Konfiguration und wirkte nirgends.

    "Erlaubt keine Links" geht beim vierten Mal nicht anders aus als beim
    ersten - aber jeder Versuch kostet einen Platz im Tageslimit, den eine
    erreichbare Gruppe gebraucht haette.
    """
    gid = "482910573829104"
    for _ in range(3):
        adapter = Mitschreiber(Ergebnis(erfolg=False, fehler="erlaubt keine Links"))
        lauf(store, campaign, gruppen, adapter, grenzen=Grenzen(tageslimit=99, max_pro_lauf=1))
        store.fehlgeschlagene_zuruecksetzen(KAMPAGNE, max_versuche=3)

    link = store.link_for(KAMPAGNE, gid)
    assert link is not None and link.post_attempts >= 3
    assert link.job_status is JobStatus.FAILED           # nicht mehr zurueckgeholt


def test_die_aufgegebenen_verschwinden_nicht(store, campaign, gruppen) -> None:
    """Sie warten auf eine Entscheidung - sonst faenden sie sich nie wieder."""
    gid = "482910573829104"
    for _ in range(3):
        lauf(
            store,
            campaign,
            gruppen,
            Mitschreiber(Ergebnis(erfolg=False, fehler="erlaubt keine Links")),
            grenzen=Grenzen(tageslimit=99, max_pro_lauf=1),
        )
        store.fehlgeschlagene_zuruecksetzen(KAMPAGNE, max_versuche=3)

    aufgegeben = store.aufgegeben(KAMPAGNE, 3)

    assert [link.group_id for link in aufgegeben] == [gid]
    assert aufgegeben[0].post_error == "erlaubt keine Links"


def test_ohne_grenze_wird_alles_zurueckgeholt(store, campaign, gruppen) -> None:
    """``--alle`` uebergeht die Grenze - der Mensch entscheidet."""
    lauf(
        store,
        campaign,
        gruppen,
        Mitschreiber(Ergebnis(erfolg=False, fehler="Netz weg")),
        grenzen=Grenzen(tageslimit=99, max_pro_lauf=1),
    )

    assert store.fehlgeschlagene_zuruecksetzen(KAMPAGNE, max_versuche=0) == 1
