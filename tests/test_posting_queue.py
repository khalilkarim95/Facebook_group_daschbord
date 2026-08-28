"""Tests fuer die Beitrags-Warteschlange - Zustaende, Freigabe, Protokoll.

Der Kern dieser Erweiterung ist nicht, dass etwas veroeffentlicht wird,
sondern dass **nichts** veroeffentlicht wird, was kein Mensch freigegeben hat.
Die Zustandsmaschine ist die Stelle, an der das entschieden wird; sie wird
deshalb hier fuer sich geprueft, ohne Claude, ohne Browser, ohne Facebook.

Ebenso wichtig: Der bestehende Bestand darf davon nichts merken.
``post_status``, ``campaign queue``, ``retry`` und die Uebersicht lesen
weiter, was sie immer gelesen haben - dafuer sorgt die Ableitung in
``set_job_status``, und dafuer gibt es hier eigene Tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fbgroups.marketing.models import (
    Campaign,
    CampaignGroup,
    JobStatus,
    PostStatus,
    PostVersuch,
    QueueZustand,
    TextQuelle,
)
from fbgroups.marketing.queue import (
    UEBERGAENGE,
    UngueltigerUebergang,
    pruefe_uebergang,
    uebergang_erlaubt,
)
from fbgroups.marketing.store import MarketingStore
from fbgroups.models import Group
from fbgroups.storage import SqliteStore

GID = "482910573829104"
GID_B = "739201847362915"
CODE = "FB-SYR-KLN-002"
CODE_B = "FB-SYR-BER-001"


@pytest.fixture()
def bestand(tmp_path: Path) -> Path:
    """Zwei Gruppen in einer Kampagne, jede mit ihrem eigenen Code."""
    pfad = tmp_path / "groups.sqlite"
    with SqliteStore(pfad) as store:
        store.upsert_groups(
            [
                Group(
                    group_id=GID,
                    url_canonical=f"https://www.facebook.com/groups/{GID}",
                    name="Syrer in Koeln",
                ),
                Group(
                    group_id=GID_B,
                    url_canonical=f"https://www.facebook.com/groups/{GID_B}",
                    name="Syrer in Berlin",
                ),
            ]
        )
    with MarketingStore(pfad) as store:
        store.save_campaign(
            Campaign(
                campaign_id="batreeq",
                name="Batreeq Syrian Germany",
                landing_page="https://b-tarikak.de/",
                message_template="Hallo! {link}",
            )
        )
        store.add_link(CampaignGroup(campaign_id="batreeq", group_id=GID, tracking_code=CODE))
        store.add_link(
            CampaignGroup(campaign_id="batreeq", group_id=GID_B, tracking_code=CODE_B)
        )
    return pfad


def bis_freigegeben(store: MarketingStore, group_id: str = GID) -> None:
    """Bringt einen Job bis ``approved`` - der uebliche Vorlauf."""
    store.set_post_text("batreeq", group_id, "Ein Text", TextQuelle.KI)
    store.set_job_status("batreeq", group_id, JobStatus.AI_GENERATED)
    store.set_job_status("batreeq", group_id, JobStatus.PENDING_REVIEW)
    store.set_job_status("batreeq", group_id, JobStatus.APPROVED, akteur="karim")


# --- Die Zustandsmaschine fuer sich ------------------------------------

def test_jeder_zustand_hat_einen_eintrag() -> None:
    """Ein Zustand ohne Zeile in der Tabelle waere eine Sackgasse."""
    assert set(UEBERGAENGE) == set(JobStatus)


def test_der_weg_durch_die_freigabe_ist_offen() -> None:
    weg = [
        JobStatus.DRAFT,
        JobStatus.AI_GENERATED,
        JobStatus.PENDING_REVIEW,
        JobStatus.APPROVED,
        JobStatus.QUEUED,
        JobStatus.PROCESSING,
        JobStatus.PUBLISHED,
    ]
    for von, nach in zip(weg, weg[1:], strict=False):
        assert uebergang_erlaubt(von, nach), f"{von} -> {nach}"


def test_kein_sprung_an_der_freigabe_vorbei() -> None:
    """Der eigentliche Zweck: Von der KI direkt in die Warteschlange - nein."""
    assert not uebergang_erlaubt(JobStatus.AI_GENERATED, JobStatus.QUEUED)
    assert not uebergang_erlaubt(JobStatus.DRAFT, JobStatus.PUBLISHED)
    assert not uebergang_erlaubt(JobStatus.PENDING_REVIEW, JobStatus.QUEUED)


def test_ohne_text_keine_freigabe() -> None:
    """Ein leerer Beitrag in einer Gruppe waere nicht zurueckzunehmen."""
    with pytest.raises(UngueltigerUebergang, match="Beitragstext"):
        pruefe_uebergang(JobStatus.DRAFT, JobStatus.PENDING_REVIEW, hat_text=False)

    pruefe_uebergang(JobStatus.DRAFT, JobStatus.PENDING_REVIEW, hat_text=True)


def test_fehlermeldung_nennt_die_moeglichen_wege() -> None:
    """Bei neun Zustaenden ist "geht nicht" allein keine Hilfe."""
    with pytest.raises(UngueltigerUebergang, match="approved"):
        pruefe_uebergang(JobStatus.PENDING_REVIEW, JobStatus.PUBLISHED, hat_text=True)


def test_neue_zuordnung_beginnt_als_entwurf(bestand: Path) -> None:
    with MarketingStore(bestand) as store:
        link = store.link_for("batreeq", GID)

    assert link is not None
    assert link.job_status is JobStatus.DRAFT
    assert link.post_text == ""


def test_text_ablegen_gibt_noch_nicht_frei(bestand: Path) -> None:
    """Schreiben und Freigeben sind zwei Handlungen."""
    with MarketingStore(bestand) as store:
        link = store.set_post_text("batreeq", GID, "Ein Text", TextQuelle.KI)

    assert link.post_text == "Ein Text"
    assert link.job_status is JobStatus.DRAFT
    assert link.freigegeben_am is None


def test_freigabe_haelt_fest_wer_sie_erteilt_hat(bestand: Path) -> None:
    with MarketingStore(bestand) as store:
        bis_freigegeben(store)
        link = store.link_for("batreeq", GID)

    assert link is not None
    assert link.job_status is JobStatus.APPROVED
    assert link.freigegeben_von == "karim"
    assert link.freigegeben_am is not None


def test_zurueckgenommene_freigabe_loescht_ihren_zeitpunkt(bestand: Path) -> None:
    """Sonst stuende "freigegeben am ..." an einem Job, der auf Pruefung wartet."""
    with MarketingStore(bestand) as store:
        bis_freigegeben(store)
        link = store.set_job_status("batreeq", GID, JobStatus.PENDING_REVIEW)

    assert link.freigegeben_am is None
    assert link.freigegeben_von == ""


def test_unerlaubter_sprung_wird_abgewiesen(bestand: Path) -> None:
    with MarketingStore(bestand) as store, pytest.raises(UngueltigerUebergang):
        store.set_job_status("batreeq", GID, JobStatus.PUBLISHED)


def test_erzwingen_loest_einen_verwaisten_job(bestand: Path) -> None:
    """Der eine Fall, in dem ein Mensch die Regel ueberstimmen darf."""
    with MarketingStore(bestand) as store:
        bis_freigegeben(store)
        store.set_job_status("batreeq", GID, JobStatus.QUEUED)
        store.set_job_status("batreeq", GID, JobStatus.PROCESSING)

        link = store.set_job_status(
            "batreeq", GID, JobStatus.DRAFT, erzwingen=True
        )

    assert link.job_status is JobStatus.DRAFT


# --- Der bestehende Bestand darf nichts merken -------------------------

def test_post_status_folgt_dem_job_status(bestand: Path) -> None:
    """Jeder aeltere Leser sieht weiter, was er immer sah."""
    erwartet = {
        JobStatus.DRAFT: PostStatus.OFFEN,
        JobStatus.AI_GENERATED: PostStatus.OFFEN,
        JobStatus.PENDING_REVIEW: PostStatus.OFFEN,
        JobStatus.APPROVED: PostStatus.OFFEN,
        JobStatus.QUEUED: PostStatus.OFFEN,
        JobStatus.PROCESSING: PostStatus.OFFEN,
        JobStatus.PUBLISHED: PostStatus.VEROEFFENTLICHT,
    }
    with MarketingStore(bestand) as store:
        store.set_post_text("batreeq", GID, "Ein Text", TextQuelle.KI)
        for job_status, post_status in erwartet.items():
            link = store.set_job_status("batreeq", GID, job_status, erzwingen=True)
            assert link.post_status is post_status, job_status


def test_offene_links_zeigt_einen_entwurf_weiterhin_als_offen(bestand: Path) -> None:
    """``campaign queue`` fragt nach post_status - und muss ihn finden."""
    with MarketingStore(bestand) as store:
        bis_freigegeben(store)
        offen = {link.group_id for link in store.offene_links("batreeq")}

    assert GID in offen


def test_veroeffentlicht_setzt_posted_at_nur_beim_ersten_mal(bestand: Path) -> None:
    """Die Klicks gehen auf den Beitrag zurueck, der zuerst stand."""
    with MarketingStore(bestand) as store:
        bis_freigegeben(store)
        store.set_job_status("batreeq", GID, JobStatus.QUEUED)
        store.set_job_status("batreeq", GID, JobStatus.PROCESSING)
        erst = store.set_job_status("batreeq", GID, JobStatus.PUBLISHED)

        # Spaeter ein zweiter Beitrag in derselben Gruppe.
        store.set_job_status("batreeq", GID, JobStatus.DRAFT)
        store.set_job_status("batreeq", GID, JobStatus.PENDING_REVIEW)
        store.set_job_status("batreeq", GID, JobStatus.APPROVED)
        store.set_job_status("batreeq", GID, JobStatus.QUEUED)
        store.set_job_status("batreeq", GID, JobStatus.PROCESSING)
        zweit = store.set_job_status("batreeq", GID, JobStatus.PUBLISHED)

    assert zweit.posted_at == erst.posted_at
    assert zweit.post_attempts == 2


def test_erfolg_loescht_den_alten_fehlergrund(bestand: Path) -> None:
    with MarketingStore(bestand) as store:
        bis_freigegeben(store)
        store.set_job_status("batreeq", GID, JobStatus.QUEUED)
        store.set_job_status("batreeq", GID, JobStatus.PROCESSING)
        store.set_job_status(
            "batreeq", GID, JobStatus.FAILED, fehler="Gruppe erlaubt keine Links"
        )
        store.set_job_status("batreeq", GID, JobStatus.QUEUED)
        store.set_job_status("batreeq", GID, JobStatus.PROCESSING)
        link = store.set_job_status("batreeq", GID, JobStatus.PUBLISHED)

    assert link.post_error == ""
    assert link.post_attempts == 2


def test_tracking_code_bleibt_ueber_alle_zustaende_unberuehrt(bestand: Path) -> None:
    """Er steht moeglicherweise schon in einem veroeffentlichten Beitrag."""
    with MarketingStore(bestand) as store:
        bis_freigegeben(store)
        store.set_job_status("batreeq", GID, JobStatus.QUEUED)
        store.set_job_status("batreeq", GID, JobStatus.CANCELLED)
        link = store.link_for("batreeq", GID)

    assert link is not None
    assert link.tracking_code == CODE


# --- PAUSE / RESUME / STOP ---------------------------------------------

def test_die_warteschlange_laeuft_ohne_zutun(bestand: Path) -> None:
    with MarketingStore(bestand) as store:
        assert store.queue_zustand("batreeq") is QueueZustand.LAUFEND


def test_pause_haelt_die_ausgabe_an(bestand: Path) -> None:
    with MarketingStore(bestand) as store:
        bis_freigegeben(store)
        store.set_job_status("batreeq", GID, JobStatus.QUEUED)

        assert store.naechster_job("batreeq") is not None
        store.set_queue_zustand("batreeq", QueueZustand.PAUSIERT)
        assert store.naechster_job("batreeq") is None

        # ... und laesst den Job in der Warteschlange stehen.
        link = store.link_for("batreeq", GID)
        assert link is not None
        assert link.job_status is JobStatus.QUEUED


def test_resume_gibt_die_warteschlange_wieder_frei(bestand: Path) -> None:
    with MarketingStore(bestand) as store:
        bis_freigegeben(store)
        store.set_job_status("batreeq", GID, JobStatus.QUEUED)
        store.set_queue_zustand("batreeq", QueueZustand.PAUSIERT)
        store.set_queue_zustand("batreeq", QueueZustand.LAUFEND)

        job = store.naechster_job("batreeq")

    assert job is not None
    assert job.group_id == GID


def test_stop_raeumt_die_warteschlange(bestand: Path) -> None:
    """Unterschied zu PAUSE: Was noch nicht angefangen wurde, geht zurueck."""
    with MarketingStore(bestand) as store:
        bis_freigegeben(store)
        bis_freigegeben(store, GID_B)
        store.set_job_status("batreeq", GID, JobStatus.QUEUED)
        store.set_job_status("batreeq", GID_B, JobStatus.QUEUED)

        zurueck = store.set_queue_zustand("batreeq", QueueZustand.GESTOPPT)

        assert zurueck == 2
        for gid in (GID, GID_B):
            link = store.link_for("batreeq", gid)
            assert link is not None
            assert link.job_status is JobStatus.APPROVED   # Freigabe bleibt


def test_stop_laesst_einen_laufenden_beitrag_in_ruhe(bestand: Path) -> None:
    """Dort ist moeglicherweise gerade ein Beitrag unterwegs.

    Ihn aus der Buchfuehrung zu nehmen, waehrend er in der Gruppe landet, waere
    schlimmer als ein Job zu viel in der Liste: Der Beitrag stuende dann in
    Facebook und nirgends im Protokoll.
    """
    with MarketingStore(bestand) as store:
        bis_freigegeben(store)
        store.set_job_status("batreeq", GID, JobStatus.QUEUED)
        store.set_job_status("batreeq", GID, JobStatus.PROCESSING)

        store.set_queue_zustand("batreeq", QueueZustand.GESTOPPT)
        link = store.link_for("batreeq", GID)

    assert link is not None
    assert link.job_status is JobStatus.PROCESSING


def test_unbekannter_zustand_gilt_als_gestoppt(bestand: Path) -> None:
    """Im Zweifel wird nicht gepostet."""
    with MarketingStore(bestand) as store:
        store.set_meta("queue_zustand:batreeq", "irgendwas")
        assert store.queue_zustand("batreeq") is QueueZustand.GESTOPPT


def test_eine_pausierte_kampagne_haelt_keine_andere_an(bestand: Path) -> None:
    with MarketingStore(bestand) as store:
        store.save_campaign(Campaign(campaign_id="zweite", name="Zweite"))
        store.set_queue_zustand("batreeq", QueueZustand.PAUSIERT)

        assert store.queue_zustand("zweite") is QueueZustand.LAUFEND


# --- Versuchsprotokoll --------------------------------------------------

def test_jeder_versuch_bekommt_seine_eigene_zeile(bestand: Path) -> None:
    """Dreimal derselbe Fehler ist etwas anderes als drei verschiedene."""
    with MarketingStore(bestand) as store:
        for grund in ("Netz weg", "Gruppe erlaubt keine Links", ""):
            versuch = store.beginne_versuch(
                PostVersuch(
                    campaign_id="batreeq", group_id=GID, tracking_code=CODE,
                    browser_session="standard", ausgeloest_von="worker",
                )
            )
            store.beende_versuch(versuch, erfolg=not grund, fehler=grund)

        versuche = store.versuche_for("batreeq", GID)

    assert len(versuche) == 3
    assert [v.erfolg for v in versuche] == [False, False, True]
    assert versuche[1].fehler == "Gruppe erlaubt keine Links"


def test_versuch_wird_vor_dem_absetzen_geschrieben(bestand: Path) -> None:
    """Ein Absturz mittendrin muss eine Spur hinterlassen."""
    with MarketingStore(bestand) as store:
        store.beginne_versuch(
            PostVersuch(campaign_id="batreeq", group_id=GID, tracking_code=CODE)
        )
        offen = store.offene_versuche("batreeq")

    assert len(offen) == 1
    assert offen[0].beendet_am is None


def test_das_protokoll_speichert_kein_geheimnis(bestand: Path) -> None:
    """``browser_session`` ist ein Name, kein Zugang."""
    with MarketingStore(bestand) as store:
        spalten = {
            zeile["name"] for zeile in store.conn.execute("PRAGMA table_info(post_versuche)")
        }

    verboten = {"passwort", "password", "cookie", "cookies", "token", "credentials"}
    assert not (spalten & verboten)
    assert "browser_session" in spalten


# --- Migration des vorhandenen Bestands ---------------------------------

def test_migration_leitet_den_job_aus_dem_ergebnis_ab(bestand: Path) -> None:
    """210 veroeffentlichte Beitraege duerfen nicht als Entwurf wiederkehren.

    Der Schritt setzt ``job_status`` aus dem vorhandenen ``post_status``.
    Andersherum - alles auf ``draft`` - entstuende der Eindruck, die ganze
    bisherige Arbeit stuende noch aus, und die Arbeitsliste haette 210
    erledigte Aufgaben wieder aufgemacht.
    """
    import sqlite3

    with MarketingStore(bestand) as store:
        store.set_post_status("batreeq", GID, PostStatus.VEROEFFENTLICHT)
        store.set_post_status("batreeq", GID_B, PostStatus.FEHLGESCHLAGEN, "Fehler")

    # Die Datei auf den Stand vor der Warteschlange zuruecksetzen.
    conn = sqlite3.connect(bestand)
    conn.executescript(
        """
        DROP TABLE post_versuche;
        UPDATE campaign_groups SET job_status = 'draft';
        PRAGMA user_version = 10;
        """
    )
    conn.commit()
    conn.close()

    with SqliteStore(bestand) as store:
        pass
    with MarketingStore(bestand) as store:
        veroeffentlicht = store.link_for("batreeq", GID)
        gescheitert = store.link_for("batreeq", GID_B)
        tabellen = {
            row["name"]
            for row in store.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }

    assert veroeffentlicht is not None
    assert gescheitert is not None
    assert veroeffentlicht.job_status is JobStatus.PUBLISHED
    assert gescheitert.job_status is JobStatus.FAILED
    # Die Tracking-Codes bleiben unberuehrt - sie stehen in Beitraegen.
    assert veroeffentlicht.tracking_code == CODE
    assert gescheitert.tracking_code == CODE_B
    assert "post_versuche" in tabellen
    # ``post_entwuerfe`` legt der Schritt nicht mehr an: Die KI-Schicht ist
    # entfernt, und niemand schreibt oder liest die Tabelle noch.
    assert "post_entwuerfe" not in tabellen


def test_die_beiden_achsen_koennen_nicht_auseinanderlaufen(bestand: Path) -> None:
    """Beide Schreibwege halten job_status und post_status deckungsgleich.

    Genau hier ist beim Bau eine Regression entstanden: ``set_post_status``
    (der aeltere Weg - ``campaign posted``, der Knopf in der Uebersicht)
    schrieb nur seine eigene Achse. Danach stand "fehlgeschlagen" neben einem
    job_status von "draft", und ``campaign retry`` fand den Beitrag nicht
    mehr - fuer die eine Liste gescheitert, fuer die andere nie begonnen.
    """
    from fbgroups.marketing.models import POST_STATUS_ZU_JOB

    with MarketingStore(bestand) as store:
        bis_freigegeben(store)

        for post_status in PostStatus:
            link = store.set_post_status("batreeq", GID, post_status, "Grund")
            assert link is not None
            # Die abgeleitete Richtung muss zurueckfuehren, wo sie hergekommen ist.
            assert POST_STATUS_ZU_JOB[link.job_status] is post_status, post_status


# --- Die Warteschlange ueber die Uebersicht steuern ----------------------

def test_queue_laesst_sich_ueber_den_dienst_anhalten(bestand: Path, config) -> None:
    """Wer einen Lauf anhalten will, sitzt selten vor dem Fenster, in dem er startete.

    Der Arbeiter liest den Zustand vor jedem Beitrag frisch aus der Datenbank -
    deshalb wirkt dieser Weg auch mitten in einem laufenden Arbeiter.
    """
    pytest.importorskip("fastapi", reason="nur mit dem optionalen web-Zusatz")
    from fastapi.testclient import TestClient

    from fbgroups.marketing.web import create_app

    client = TestClient(create_app(config=config, db_path=bestand))

    antwort = client.post("/kampagnen/batreeq/queue", json={"zustand": "pausiert"})

    assert antwort.status_code == 200
    assert antwort.json()["zustand"] == "pausiert"
    with MarketingStore(bestand) as store:
        assert store.queue_zustand("batreeq") is QueueZustand.PAUSIERT


def test_stopp_meldet_wie_viele_zurueckgestellt_wurden(bestand: Path, config) -> None:
    """"Gestoppt" allein liesse offen, ob 3 oder 300 Beitraege betroffen waren."""
    pytest.importorskip("fastapi", reason="nur mit dem optionalen web-Zusatz")
    from fastapi.testclient import TestClient

    from fbgroups.marketing.web import create_app

    with MarketingStore(bestand) as store:
        bis_freigegeben(store, GID)
        store.set_job_status("batreeq", GID, JobStatus.QUEUED)

    client = TestClient(create_app(config=config, db_path=bestand))
    antwort = client.post("/kampagnen/batreeq/queue", json={"zustand": "gestoppt"})

    assert antwort.json()["zurueckgestellt"] == 1
    assert antwort.json()["eingereiht"] == 0


def test_queue_steuern_ist_von_aussen_nicht_moeglich(bestand: Path, config) -> None:
    """Ein schreibender Weg wie jeder andere - er steht hinter ``_nur_lokal``."""
    pytest.importorskip("fastapi", reason="nur mit dem optionalen web-Zusatz")
    from fastapi.testclient import TestClient

    from fbgroups.marketing.web import create_app

    fremd = TestClient(
        create_app(config=config, db_path=bestand), client=("203.0.113.7", 44321)
    )

    assert fremd.post(
        "/kampagnen/batreeq/queue", json={"zustand": "gestoppt"}
    ).status_code == 404


def test_unbekannte_kampagne_ergibt_404(bestand: Path, config) -> None:
    pytest.importorskip("fastapi", reason="nur mit dem optionalen web-Zusatz")
    from fastapi.testclient import TestClient

    from fbgroups.marketing.web import create_app

    client = TestClient(create_app(config=config, db_path=bestand))

    assert client.post(
        "/kampagnen/gibtesnicht/queue", json={"zustand": "pausiert"}
    ).status_code == 404


def test_ein_tippfehler_im_zustand_wird_abgewiesen(bestand: Path, config) -> None:
    """``pausiert`` und ``gestoppt`` tun Verschiedenes - dazwischen gibt es nichts."""
    pytest.importorskip("fastapi", reason="nur mit dem optionalen web-Zusatz")
    from fastapi.testclient import TestClient

    from fbgroups.marketing.web import create_app

    client = TestClient(create_app(config=config, db_path=bestand))

    assert client.post(
        "/kampagnen/batreeq/queue", json={"zustand": "angehalten"}
    ).status_code == 422
