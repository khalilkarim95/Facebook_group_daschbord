"""Tests fuer den gemeinsamen Arbeitsschritt und die Arbeitsseite.

Der Grund fuer ``arbeit.py`` ist, dass Schleife und Weboberflaeche **dieselben**
Regeln benutzen. Genau das wird hier geprueft: Ein zweites Tageslimit, das vom
ersten abweicht, faellt sonst erst auf, wenn vierzig Beitraege an einem Tag
hinausgegangen sind.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from fbgroups.marketing.arbeit import (
    Auftrag,
    Grund,
    Sperre,
    hole_auftrag,
    melde_ergebnis,
)
from fbgroups.marketing.models import (
    Campaign,
    CampaignGroup,
    JobStatus,
    QueueZustand,
    TextQuelle,
)
from fbgroups.marketing.store import MarketingStore
from fbgroups.marketing.veroeffentlicher import Ergebnis
from fbgroups.marketing.worker import Grenzen
from fbgroups.models import Group
from fbgroups.storage import SqliteStore

KAMPAGNE = "batreeq"
GRUPPEN = {
    "482910573829104": ("Syrer in Koeln", "FB-SYR-KLN-002"),
    "739201847362915": ("Syrer in Berlin", "FB-SYR-BER-001"),
}
# Keine Wartezeit, sofern ein Test sie nicht ausdruecklich braucht.
OHNE_PAUSE = Grenzen(tageslimit=20, max_pro_lauf=99, pause_min=0.0, pause_max=0.0)


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
                )
                for gid, (name, _) in GRUPPEN.items()
            ]
        )
    with MarketingStore(pfad) as store:
        store.save_campaign(
            Campaign(
                campaign_id=KAMPAGNE,
                name="Batreeq",
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
                    tracking_url=f"https://b-tarikak.de/r/{code}",
                )
            )
            store.set_post_text(KAMPAGNE, gid, "مرحبا! {link}", TextQuelle.KI)
            store.set_job_status(KAMPAGNE, gid, JobStatus.AI_GENERATED)
            store.set_job_status(KAMPAGNE, gid, JobStatus.PENDING_REVIEW)
            store.set_job_status(KAMPAGNE, gid, JobStatus.APPROVED)
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


def hole(store, campaign, gruppen, grenzen=OHNE_PAUSE, **kwargs):
    kwargs.setdefault("ausgeloest_von", "test")
    return hole_auftrag(store, campaign, gruppen, grenzen, **kwargs)


# --- Der Auftrag ----------------------------------------------------------

def test_ein_auftrag_traegt_text_gruppe_und_versuch(store, campaign, gruppen) -> None:
    auftrag = hole(store, campaign, gruppen)

    assert isinstance(auftrag, Auftrag)
    assert auftrag.versuch_id > 0
    assert auftrag.link.tracking_url in auftrag.text
    assert auftrag.url.startswith("https://www.facebook.com/groups/")


def test_der_versuch_steht_schon_im_protokoll(store, campaign, gruppen) -> None:
    """Vor dem Beitrag, nicht danach - sonst bliebe ein Absturz spurlos."""
    auftrag = hole(store, campaign, gruppen)

    versuche = store.versuche_for(KAMPAGNE, auftrag.link.group_id)
    assert len(versuche) == 1
    assert versuche[0].beendet_am is None          # laeuft noch
    assert versuche[0].tracking_code == auftrag.link.tracking_code


def test_der_job_steht_auf_processing(store, campaign, gruppen) -> None:
    auftrag = hole(store, campaign, gruppen)

    link = store.link_for(KAMPAGNE, auftrag.link.group_id)
    assert link is not None and link.job_status is JobStatus.PROCESSING


# --- Der geschlossene Reiter ---------------------------------------------

def test_ein_angefangener_auftrag_kommt_zurueck(store, campaign, gruppen) -> None:
    """Wer den Reiter schliesst, verliert den Beitrag nicht.

    Ohne das blutete die Warteschlange bei jedem geschlossenen Fenster einen
    Beitrag aus: Der Job stuende auf ``processing``, in keiner Liste, und
    niemand faende ihn wieder.
    """
    erster = hole(store, campaign, gruppen)

    zweiter = hole(store, campaign, gruppen)

    assert isinstance(zweiter, Auftrag)
    assert zweiter.link.group_id == erster.link.group_id
    assert zweiter.versuch_id == erster.versuch_id       # dieselbe Zeile
    assert len(store.versuche_for(KAMPAGNE, erster.link.group_id)) == 1


def test_erst_nach_der_meldung_kommt_die_naechste(store, campaign, gruppen) -> None:
    erster = hole(store, campaign, gruppen)
    melde_ergebnis(
        store, KAMPAGNE, erster.link.group_id, erster.versuch_id, Ergebnis(erfolg=True)
    )

    zweiter = hole(store, campaign, gruppen)

    assert isinstance(zweiter, Auftrag)
    assert zweiter.link.group_id != erster.link.group_id


# --- Die Sperren ----------------------------------------------------------

def test_pausiert_sagt_pausiert(store, campaign, gruppen) -> None:
    """Nicht "leer" und nicht "Wartezeit" - der Grund, der wirklich gilt."""
    store.set_queue_zustand(KAMPAGNE, QueueZustand.PAUSIERT)

    sperre = hole(store, campaign, gruppen)

    assert isinstance(sperre, Sperre)
    assert sperre.grund == Grund.PAUSIERT


def test_pausiert_faengt_keinen_versuch_an(store, campaign, gruppen) -> None:
    """Eine Sperre darf nichts anfassen - sonst zaehlte Nachsehen als Arbeit."""
    store.set_queue_zustand(KAMPAGNE, QueueZustand.PAUSIERT)

    hole(store, campaign, gruppen)

    assert store.versuche_heute() == 0
    assert store.job_counts(KAMPAGNE)[JobStatus.QUEUED.value] == 2


def test_das_tageslimit_gilt_auch_hier(store, campaign, gruppen) -> None:
    """Dieselbe Regel wie in der Schleife - deshalb steht sie an einer Stelle."""
    grenzen = Grenzen(tageslimit=1, max_pro_lauf=99, pause_min=0.0, pause_max=0.0)
    erster = hole(store, campaign, gruppen, grenzen)
    assert isinstance(erster, Auftrag)
    melde_ergebnis(
        store, KAMPAGNE, erster.link.group_id, erster.versuch_id, Ergebnis(erfolg=True)
    )

    sperre = hole(store, campaign, gruppen, grenzen)

    assert isinstance(sperre, Sperre)
    assert sperre.grund == Grund.TAGESLIMIT
    assert sperre.heute_schon == 1


def test_leere_warteschlange_meldet_fertig(store, campaign, gruppen) -> None:
    for _ in range(2):
        auftrag = hole(store, campaign, gruppen)
        assert isinstance(auftrag, Auftrag)
        melde_ergebnis(
            store, KAMPAGNE, auftrag.link.group_id, auftrag.versuch_id, Ergebnis(erfolg=True)
        )

    sperre = hole(store, campaign, gruppen)

    assert isinstance(sperre, Sperre)
    assert sperre.grund == Grund.FERTIG


# --- Die Wartezeit --------------------------------------------------------

def test_die_wartezeit_gilt_zwischen_zwei_beitraegen(store, campaign, gruppen) -> None:
    """Sie kommt aus dem Bestand, nicht aus einem ``sleep``.

    Ein Mensch, der die Seite neu laedt, umginge ein ``sleep`` muehelos;
    ``letzter_versuch`` laesst sich nicht neu laden.
    """
    grenzen = Grenzen(tageslimit=20, max_pro_lauf=99, pause_min=180.0, pause_max=180.0)
    erster = hole(store, campaign, gruppen, grenzen)
    assert isinstance(erster, Auftrag)
    melde_ergebnis(
        store, KAMPAGNE, erster.link.group_id, erster.versuch_id, Ergebnis(erfolg=True)
    )

    sperre = hole(store, campaign, gruppen, grenzen)

    assert isinstance(sperre, Sperre)
    assert sperre.grund == Grund.WARTEZEIT
    assert 0 < sperre.wartet_noch <= 180


def test_neuladen_verkuerzt_die_wartezeit_nicht(store, campaign, gruppen) -> None:
    """Der Kern der Entscheidung: Die Sperre haengt nicht am Ablauf."""
    grenzen = Grenzen(tageslimit=20, max_pro_lauf=99, pause_min=180.0, pause_max=180.0)
    erster = hole(store, campaign, gruppen, grenzen)
    melde_ergebnis(
        store, KAMPAGNE, erster.link.group_id, erster.versuch_id, Ergebnis(erfolg=True)
    )

    for _ in range(5):                       # fuenfmal F5
        sperre = hole(store, campaign, gruppen, grenzen)
        assert isinstance(sperre, Sperre)
        assert sperre.grund == Grund.WARTEZEIT


def test_nach_der_wartezeit_geht_es_weiter(store, campaign, gruppen) -> None:
    grenzen = Grenzen(tageslimit=20, max_pro_lauf=99, pause_min=180.0, pause_max=180.0)
    erster = hole(store, campaign, gruppen, grenzen)
    melde_ergebnis(
        store, KAMPAGNE, erster.link.group_id, erster.versuch_id, Ergebnis(erfolg=True)
    )

    spaeter = datetime.now(UTC) + timedelta(seconds=200)
    zweiter = hole(store, campaign, gruppen, grenzen, jetzt=spaeter)

    assert isinstance(zweiter, Auftrag)


def test_ein_zurueckgegebener_auftrag_wartet_nicht(store, campaign, gruppen) -> None:
    """Er ist kein neuer Beitrag - die Pause traefe den falschen Fall.

    Sonst saehe jemand, der seinen Reiter neu laedt, drei Minuten lang eine
    Wartezeit fuer einen Beitrag, den er noch gar nicht abgesetzt hat.
    """
    grenzen = Grenzen(tageslimit=20, max_pro_lauf=99, pause_min=180.0, pause_max=180.0)
    erster = hole(store, campaign, gruppen, grenzen)

    zweiter = hole(store, campaign, gruppen, grenzen)

    assert isinstance(zweiter, Auftrag)
    assert zweiter.versuch_id == erster.versuch_id


# --- Die Rueckmeldung -----------------------------------------------------

def test_veroeffentlicht_schliesst_den_versuch_ab(store, campaign, gruppen) -> None:
    auftrag = hole(store, campaign, gruppen)

    stand = melde_ergebnis(
        store, KAMPAGNE, auftrag.link.group_id, auftrag.versuch_id, Ergebnis(erfolg=True)
    )

    assert stand is JobStatus.PUBLISHED
    versuch = store.versuche_for(KAMPAGNE, auftrag.link.group_id)[0]
    assert versuch.erfolg is True
    assert versuch.beendet_am is not None
    link = store.link_for(KAMPAGNE, auftrag.link.group_id)
    assert link is not None and link.posted_at is not None


def test_fehlgeschlagen_speichert_den_grund(store, campaign, gruppen) -> None:
    auftrag = hole(store, campaign, gruppen)

    stand = melde_ergebnis(
        store,
        KAMPAGNE,
        auftrag.link.group_id,
        auftrag.versuch_id,
        Ergebnis(erfolg=False, fehler="erlaubt keine Links"),
    )

    assert stand is JobStatus.FAILED
    link = store.link_for(KAMPAGNE, auftrag.link.group_id)
    assert link is not None and link.post_error == "erlaubt keine Links"


def test_uebersprungen_wird_nicht_zu_fehlgeschlagen(store, campaign, gruppen) -> None:
    auftrag = hole(store, campaign, gruppen)

    stand = melde_ergebnis(
        store,
        KAMPAGNE,
        auftrag.link.group_id,
        auftrag.versuch_id,
        Ergebnis(erfolg=False, uebersprungen=True),
    )

    assert stand is JobStatus.CANCELLED


def test_schluss_legt_den_job_zurueck(store, campaign, gruppen) -> None:
    """Wer aufhoert, verwirft nichts - der Beitrag ist morgen der naechste."""
    auftrag = hole(store, campaign, gruppen)

    stand = melde_ergebnis(
        store,
        KAMPAGNE,
        auftrag.link.group_id,
        auftrag.versuch_id,
        Ergebnis(erfolg=False, abbrechen=True),
    )

    assert stand is JobStatus.QUEUED
    assert store.job_counts(KAMPAGNE)[JobStatus.QUEUED.value] == 2


# --- Die Seite ------------------------------------------------------------

def test_die_seite_zeigt_text_code_und_gruppe(store, campaign, gruppen) -> None:
    from fbgroups.marketing.arbeitsseite import render_auftrag

    auftrag = hole(store, campaign, gruppen)
    seite = render_auftrag(auftrag, KAMPAGNE)

    assert auftrag.link.tracking_code in seite
    assert auftrag.url in seite
    assert "name='versuch_id'" in seite


def test_arabisch_laeuft_von_rechts_nach_links(store, campaign, gruppen) -> None:
    """Sonst steht die Satzzeichenfolge falsch und der Text sieht kaputt aus."""
    from fbgroups.marketing.arbeitsseite import render_auftrag

    auftrag = hole(store, campaign, gruppen)
    seite = render_auftrag(auftrag, KAMPAGNE)

    assert "مرحبا" in seite
    assert "dir='rtl'" in seite


def test_die_seite_nennt_den_grund_der_sperre(store, campaign, gruppen) -> None:
    from fbgroups.marketing.arbeitsseite import render_sperre

    seite = render_sperre(Sperre(Grund.WARTEZEIT, wartet_noch=42), KAMPAGNE)

    assert "42" in seite
    assert "location.reload" in seite          # laedt von selbst neu


# --- Der Weg im Dienst ----------------------------------------------------

def test_die_arbeitsseite_ist_von_aussen_nicht_erreichbar(bestand: Path, config) -> None:
    """Sie **beginnt** einen Versuch und ist damit kein Lesen."""
    pytest.importorskip("fastapi", reason="nur mit dem optionalen web-Zusatz")
    from fastapi.testclient import TestClient

    from fbgroups.marketing.web import create_app

    fremd = TestClient(
        create_app(config=config, db_path=bestand), client=("203.0.113.7", 44321)
    )

    assert fremd.get(f"/arbeit/{KAMPAGNE}").status_code == 404


def test_die_arbeitsseite_liefert_einen_beitrag(bestand: Path, config) -> None:
    pytest.importorskip("fastapi", reason="nur mit dem optionalen web-Zusatz")
    from fastapi.testclient import TestClient

    from fbgroups.marketing.web import create_app

    client = TestClient(create_app(config=config, db_path=bestand))

    antwort = client.get(f"/arbeit/{KAMPAGNE}")

    assert antwort.status_code == 200
    assert "FB-SYR" in antwort.text


def test_die_meldung_leitet_zur_naechsten_gruppe(bestand: Path, config) -> None:
    """303 statt 200: Ein Neuladen soll den Ausgang nicht zweimal melden."""
    pytest.importorskip("fastapi", reason="nur mit dem optionalen web-Zusatz")
    from fastapi.testclient import TestClient

    from fbgroups.marketing.web import create_app

    client = TestClient(create_app(config=config, db_path=bestand), follow_redirects=False)
    client.get(f"/arbeit/{KAMPAGNE}")
    with MarketingStore(bestand) as store:
        offen = store.offene_versuche(KAMPAGNE)
    assert offen and offen[0].versuch_id

    antwort = client.post(
        f"/arbeit/{KAMPAGNE}/ergebnis",
        data={
            "ausgang": "veroeffentlicht",
            "group_id": offen[0].group_id,
            "versuch_id": str(offen[0].versuch_id),
        },
    )

    assert antwort.status_code == 303
    assert antwort.headers["location"] == f"/arbeit/{KAMPAGNE}"
    with MarketingStore(bestand) as store:
        link = store.link_for(KAMPAGNE, offen[0].group_id)
        assert link is not None and link.job_status is JobStatus.PUBLISHED


def test_eine_unvollstaendige_meldung_wird_abgewiesen(bestand: Path, config) -> None:
    """Ohne ``versuch_id`` liesse sich nicht sagen, welche Zeile gemeint ist."""
    pytest.importorskip("fastapi", reason="nur mit dem optionalen web-Zusatz")
    from fastapi.testclient import TestClient

    from fbgroups.marketing.web import create_app

    client = TestClient(create_app(config=config, db_path=bestand))

    assert client.post(
        f"/arbeit/{KAMPAGNE}/ergebnis", data={"ausgang": "veroeffentlicht"}
    ).status_code == 400


def test_der_arbeiten_knopf_fehlt_im_nur_lesen_zugang(bestand: Path, config) -> None:
    """Er schreibt - von aussen fuehrte er ins Leere.

    Ein Knopf, dessen Weg mit 404 antwortet, sieht aus wie ein Fehler der
    Seite. Dieselbe Ueberlegung wie bei den uebrigen schreibenden Knoepfen.
    """
    from fbgroups.marketing.dashboard import render, sammle_daten

    daten = sammle_daten(config, bestand)

    # Auf den Link pruefen, nicht auf die Klasse: Der CSS-Block bleibt in
    # beiden Faellen stehen, entfernt wird nur der Weg dorthin.
    assert "href='/arbeit/" in render(daten, nur_lesen=False)
    assert "href='/arbeit/" not in render(daten, nur_lesen=True)


# --- Zuruecksetzen fuer Testlaeufe ---------------------------------------

def test_reset_laesst_den_tracking_code_unberuehrt(store, campaign, gruppen) -> None:
    """Die wichtigste Zusage: Ein Code steht moeglicherweise in einem Beitrag.

    Ein Klick darauf muss weiterhin ankommen. Zurueckgesetzt wird der Stand,
    nie die Zuordnung.
    """
    vorher = {
        link.group_id: (link.tracking_code, link.tracking_url)
        for link in store.links_for_campaign(KAMPAGNE)
    }
    auftrag = hole(store, campaign, gruppen)
    melde_ergebnis(
        store, KAMPAGNE, auftrag.link.group_id, auftrag.versuch_id, Ergebnis(erfolg=True)
    )

    store.setze_kampagne_zurueck(KAMPAGNE)

    nachher = {
        link.group_id: (link.tracking_code, link.tracking_url)
        for link in store.links_for_campaign(KAMPAGNE)
    }
    assert nachher == vorher


def test_reset_stellt_den_beitragsstand_auf_anfang(store, campaign, gruppen) -> None:
    auftrag = hole(store, campaign, gruppen)
    melde_ergebnis(
        store, KAMPAGNE, auftrag.link.group_id, auftrag.versuch_id, Ergebnis(erfolg=True)
    )

    store.setze_kampagne_zurueck(KAMPAGNE)

    link = store.link_for(KAMPAGNE, auftrag.link.group_id)
    assert link is not None
    assert link.job_status is JobStatus.APPROVED      # Text ist da
    assert link.posted_at is None
    assert link.post_attempts == 0
    assert link.post_error == ""
    assert store.versuche_heute() == 0                # Protokoll geleert


def test_reset_laesst_die_ereignisse_stehen(store, campaign, gruppen) -> None:
    """Gemessene Resonanz ist das Einzige, was nicht wiederkommt.

    Sie ist von aussen entstanden; deshalb braucht ihr Loeschen einen eigenen
    Schalter und geschieht nicht nebenbei.
    """
    from fbgroups.marketing.models import EventType, TrackingEvent

    store.record_event(
        TrackingEvent(
            tracking_code=GRUPPEN["482910573829104"][1],
            campaign_id=KAMPAGNE,
            group_id="482910573829104",
            event_type=EventType.CLICK,
        )
    )

    store.setze_kampagne_zurueck(KAMPAGNE)

    assert store.event_counts().get(EventType.CLICK.value, 0) == 1


def test_mit_schalter_verschwinden_auch_die_ereignisse(store, campaign, gruppen) -> None:
    from fbgroups.marketing.models import EventType, TrackingEvent

    store.record_event(
        TrackingEvent(
            tracking_code=GRUPPEN["482910573829104"][1],
            campaign_id=KAMPAGNE,
            group_id="482910573829104",
            event_type=EventType.CLICK,
        )
    )

    zahlen = store.setze_kampagne_zurueck(KAMPAGNE, auch_ereignisse=True)

    assert zahlen["ereignisse"] == 1
    assert store.event_counts().get(EventType.CLICK.value, 0) == 0


def test_reset_faengt_die_warteschlange_wieder_an(store, campaign, gruppen) -> None:
    """Eine gestoppte Warteschlange bliebe sonst nach dem Reset gestoppt."""
    store.set_queue_zustand(KAMPAGNE, QueueZustand.GESTOPPT)

    store.setze_kampagne_zurueck(KAMPAGNE)

    assert store.queue_zustand(KAMPAGNE) is QueueZustand.LAUFEND


def test_die_vorschau_nennt_dieselben_zahlen(store, campaign, gruppen) -> None:
    """``--dry-run`` und Ernstfall lesen dieselbe Zaehlung.

    Dieselbe Ueberlegung wie bei ``search.build_plan``: Eine zweite Zaehlung
    koennte abweichen, und der Mensch bestaetigte eine Zahl und bekaeme eine
    andere.
    """
    auftrag = hole(store, campaign, gruppen)
    melde_ergebnis(
        store, KAMPAGNE, auftrag.link.group_id, auftrag.versuch_id, Ergebnis(erfolg=True)
    )

    vorschau = store.zaehle_zuruecksetzbar(KAMPAGNE)
    getan = store.setze_kampagne_zurueck(KAMPAGNE, auch_ereignisse=True)

    assert getan["zuordnungen"] == vorschau["zuordnungen"] == 2
    assert getan["versuche"] == vorschau["versuche"] == 1
    assert getan["veroeffentlicht"] == vorschau["veroeffentlicht"] == 1


# --- Text aus der Vorlage (ohne KI) --------------------------------------

def test_ohne_text_kommt_nichts_in_die_warteschlange(store) -> None:
    """Der Grund, warum es ``campaign text`` gibt.

    ``pruefe_uebergang`` verlangt einen Text; ohne ihn ist die Freigabe
    versperrt und die Warteschlange bleibt fuer immer leer. Wer keine KI
    benutzt, haette sonst keinen Weg dorthin.
    """
    from fbgroups.marketing.queue import UngueltigerUebergang

    store.set_post_text(KAMPAGNE, "482910573829104", "", TextQuelle.VORLAGE)
    store.set_job_status(KAMPAGNE, "482910573829104", JobStatus.DRAFT, erzwingen=True)

    with pytest.raises(UngueltigerUebergang):
        store.set_job_status(KAMPAGNE, "482910573829104", JobStatus.PENDING_REVIEW)


def test_die_vorlage_wird_je_gruppe_zur_eigenen_kopie(store, campaign) -> None:
    """Kein Umweg: ``{link}`` wird spaeter durch den Code *dieser* Gruppe ersetzt.

    Wer einen einzelnen Text nachtraeglich anpasst, soll damit nicht alle
    anderen aendern.
    """
    for gid in GRUPPEN:
        store.set_post_text(KAMPAGNE, gid, campaign.message_template, TextQuelle.VORLAGE)

    store.set_post_text(KAMPAGNE, "482910573829104", "Anders! {link}", TextQuelle.HAND)

    a = store.link_for(KAMPAGNE, "482910573829104")
    b = store.link_for(KAMPAGNE, "739201847362915")
    assert a is not None and b is not None
    assert a.post_text == "Anders! {link}"
    assert b.post_text == campaign.message_template


# --- Die beiden Fehler aus dem ersten Livelauf ---------------------------

def test_leere_warteschlange_schlaegt_die_wartezeit(store, campaign, gruppen) -> None:
    """Der Fehler, der beim ersten Test auffiel.

    Bei einer Kampagne mit genau einer Gruppe war nach dem einzigen Beitrag
    die Schlange leer - angezeigt wurde aber "Wartezeit laeuft noch". Der
    Zaehler lief ab, die Seite lud neu, und dann stand doch "leer" da. Eine
    Wartezeit verspricht einen naechsten Beitrag; ohne einen ist sie eine
    falsche Auskunft.
    """
    grenzen = Grenzen(tageslimit=20, max_pro_lauf=99, pause_min=180.0, pause_max=180.0)
    # Die Zeit ruecken lassen, sonst haengt der Test in der eigenen Wartezeit.
    for nummer in range(len(GRUPPEN)):
        spaeter = datetime.now(UTC) + timedelta(seconds=600 * nummer)
        auftrag = hole(store, campaign, gruppen, grenzen, jetzt=spaeter)
        assert isinstance(auftrag, Auftrag)
        melde_ergebnis(
            store, KAMPAGNE, auftrag.link.group_id, auftrag.versuch_id, Ergebnis(erfolg=True)
        )

    # Unmittelbar nach dem letzten Beitrag - die Wartezeit laeuft also noch,
    # aber es steht nichts mehr an. Genau der Fall aus dem Livelauf.
    sperre = hole(store, campaign, gruppen, grenzen)

    assert isinstance(sperre, Sperre)
    assert sperre.grund == Grund.FERTIG          # nicht WARTEZEIT


def test_die_wartezeit_springt_beim_neuladen_nicht(store, campaign, gruppen) -> None:
    """Sonst waere sie durch haeufiges Neuladen zu unterlaufen.

    Der Zufall kommt aus dem Zeitpunkt des letzten Versuchs, nicht aus einem
    frischen Wurf je Seitenaufruf: Wer oft genug F5 druecke, erwischte sonst
    irgendwann die kurze Zahl - und der Takt waere eine Empfehlung statt einer
    Grenze.
    """
    grenzen = Grenzen(tageslimit=20, max_pro_lauf=99, pause_min=180.0, pause_max=420.0)
    erster = hole(store, campaign, gruppen, grenzen, wuerfel=None)
    melde_ergebnis(
        store, KAMPAGNE, erster.link.group_id, erster.versuch_id, Ergebnis(erfolg=True)
    )

    fest = datetime.now(UTC)
    gesehen = {
        hole(store, campaign, gruppen, grenzen, wuerfel=None, jetzt=fest).wartet_noch
        for _ in range(8)
    }

    assert len(gesehen) == 1                     # bei gleichem Zeitpunkt immer dieselbe Zahl


# --- Die Vorbereitungsknoepfe --------------------------------------------

def _client(bestand, config):
    pytest.importorskip("fastapi", reason="nur mit dem optionalen web-Zusatz")
    from fastapi.testclient import TestClient

    from fbgroups.marketing.web import create_app

    return TestClient(create_app(config=config, db_path=bestand))


def test_die_werkbank_erscheint_nur_bei_leerer_warteschlange(store, campaign, gruppen) -> None:
    """Sonst laege sie unter einem Beitrag, der gerade geschrieben werden soll."""
    from fbgroups.marketing.arbeitsseite import render_sperre

    leer = render_sperre(Sperre(Grund.FERTIG), KAMPAGNE)
    wartend = render_sperre(Sperre(Grund.WARTEZEIT, wartet_noch=30), KAMPAGNE)

    assert "Warteschlange fuellen" in leer
    assert "Warteschlange fuellen" not in wartend


def test_text_und_freigabe_und_einreihen_ueber_den_dienst(bestand: Path, config) -> None:
    """Die ganze Kette per Knopf - ohne ein einziges Terminal."""
    with MarketingStore(bestand) as store:
        store.setze_kampagne_zurueck(KAMPAGNE)
        for gid in GRUPPEN:                       # Text entfernen, wie nach einem Reset
            store.set_post_text(KAMPAGNE, gid, "", TextQuelle.VORLAGE)
            store.set_job_status(KAMPAGNE, gid, JobStatus.DRAFT, erzwingen=True)

    client = _client(bestand, config)

    text = client.post(f"/kampagnen/{KAMPAGNE}/vorbereiten", json={"schritt": "text"})
    assert text.status_code == 200
    assert text.json()["betroffen"] == len(GRUPPEN)

    frei = client.post(f"/kampagnen/{KAMPAGNE}/vorbereiten", json={"schritt": "approve"})
    assert frei.json()["betroffen"] == len(GRUPPEN)

    reihe = client.post(f"/kampagnen/{KAMPAGNE}/vorbereiten", json={"schritt": "enqueue"})
    assert reihe.json()["eingereiht"] == len(GRUPPEN)


def test_freigeben_ohne_text_meldet_den_grund(bestand: Path, config) -> None:
    """"0 betroffen" allein liesse offen, woran es lag."""
    with MarketingStore(bestand) as store:
        for gid in GRUPPEN:
            store.set_post_text(KAMPAGNE, gid, "", TextQuelle.VORLAGE)
            store.set_job_status(KAMPAGNE, gid, JobStatus.DRAFT, erzwingen=True)

    antwort = _client(bestand, config).post(
        f"/kampagnen/{KAMPAGNE}/vorbereiten", json={"schritt": "approve"}
    )

    assert antwort.json()["betroffen"] == 0
    assert "ohne Text" in antwort.json()["hinweis"]


def test_die_werkbank_ist_von_aussen_nicht_bedienbar(bestand: Path, config) -> None:
    """Sie schreibt - wie jeder andere schreibende Weg hinter ``_nur_lokal``."""
    pytest.importorskip("fastapi", reason="nur mit dem optionalen web-Zusatz")
    from fastapi.testclient import TestClient

    from fbgroups.marketing.web import create_app

    fremd = TestClient(
        create_app(config=config, db_path=bestand), client=("203.0.113.7", 44321)
    )

    assert fremd.post(
        f"/kampagnen/{KAMPAGNE}/vorbereiten", json={"schritt": "enqueue"}
    ).status_code == 404


def test_ein_unbekannter_schritt_wird_abgewiesen(bestand: Path, config) -> None:
    antwort = _client(bestand, config).post(
        f"/kampagnen/{KAMPAGNE}/vorbereiten", json={"schritt": "veroeffentlichen"}
    )

    assert antwort.status_code == 422
