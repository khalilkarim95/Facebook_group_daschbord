"""Tests fuer die Befehle der Beitrags-Warteschlange.

Gegen die echte Kommandozeile, gegen eine eigene Datenbank im tmp-Verzeichnis.
Kein Netz und kein Modell: Texte kommen aus den Vorlagen oder aus der Hand des
Menschen, und beides laeuft hier ohne jede Verbindung nach draussen.

Die Frage, um die es geht: Kommt ein Beitrag ohne Freigabe eines Menschen bis
in die Warteschlange? Die Antwort muss in jedem einzelnen Fall nein sein.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fbgroups import cli
from fbgroups.config import load_config
from fbgroups.marketing import cli as marketing_cli
from fbgroups.marketing.models import (
    Campaign,
    CampaignGroup,
    JobStatus,
    PostStatus,
    QueueZustand,
    TextQuelle,
)
from fbgroups.marketing.store import MarketingStore
from fbgroups.marketing.vorlagen import PLATZHALTER_LINK as PLATZHALTER
from fbgroups.models import Group
from fbgroups.storage import SqliteStore

runner = CliRunner()

GID_GUT = "482910573829104"     # hoher Score -> hohe Prioritaet
GID_MITTEL = "739201847362915"
GID_OHNE = "615498273610948"    # ohne Score
CODE_GUT = "FB-SYR-KLN-002"
CODE_MITTEL = "FB-SYR-BER-001"
CODE_OHNE = "FB-GEN-DE-001"


@pytest.fixture()
def projekt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Drei Gruppen mit verschiedenen Scores, eine Kampagne, drei Codes."""
    pfad = tmp_path / "data" / "groups.sqlite"
    with SqliteStore(pfad) as store:
        store.upsert_groups(
            [
                Group(
                    group_id=GID_GUT,
                    url_canonical=f"https://www.facebook.com/groups/{GID_GUT}",
                    name="Syrer in Koeln", city="Koeln", audience_tags=["syrians"],
                    score=140, score_max=175,
                ),
                Group(
                    group_id=GID_MITTEL,
                    url_canonical=f"https://www.facebook.com/groups/{GID_MITTEL}",
                    name="Syrer in Berlin", city="Berlin", audience_tags=["syrians"],
                    score=65, score_max=100,
                ),
                Group(
                    group_id=GID_OHNE,
                    url_canonical=f"https://www.facebook.com/groups/{GID_OHNE}",
                    name="Ohne Bewertung",
                ),
            ]
        )
    with MarketingStore(pfad) as store:
        store.save_campaign(
            Campaign(
                campaign_id="batreeq",
                name="Batreeq Syrian Germany",
                landing_page="https://b-tarikak.de/",
                message_template=f"Vorlage {PLATZHALTER}",
            )
        )
        for gid, code in (
            (GID_GUT, CODE_GUT), (GID_MITTEL, CODE_MITTEL), (GID_OHNE, CODE_OHNE)
        ):
            store.add_link(
                CampaignGroup(campaign_id="batreeq", group_id=gid, tracking_code=code)
            )

    echt = load_config()
    settings = {
        **echt.settings,
        "paths": {**echt.settings["paths"], "sqlite_path": "data/groups.sqlite"},
    }
    test_config = replace(echt, root=tmp_path, settings=settings)
    monkeypatch.setattr(cli, "load_config", lambda: test_config)
    monkeypatch.setattr(marketing_cli, "load_config", lambda: test_config)
    return pfad


def mit_text(pfad: Path, *group_ids: str) -> None:
    """Gibt den genannten Gruppen einen Text und den Stand ``pending_review``."""
    with MarketingStore(pfad) as store:
        for gid in group_ids:
            store.set_post_text(
                "batreeq", gid, f"Ein Text fuer {gid} {PLATZHALTER}", TextQuelle.HAND
            )
            store.set_job_status("batreeq", gid, JobStatus.PENDING_REVIEW)


def stand(pfad: Path, group_id: str) -> JobStatus:
    with MarketingStore(pfad) as store:
        link = store.link_for("batreeq", group_id)
    assert link is not None
    return link.job_status


# --- Der Weg ohne Freigabe ist zu ---------------------------------------

def test_ohne_freigabe_kommt_nichts_in_die_warteschlange(projekt: Path) -> None:
    """Der eigentliche Zweck der ganzen Erweiterung."""
    mit_text(projekt, GID_GUT)

    ergebnis = runner.invoke(cli.app, ["campaign", "enqueue", "batreeq"])

    assert "Nichts freigegeben" in ergebnis.stdout
    assert stand(projekt, GID_GUT) is JobStatus.PENDING_REVIEW


def test_freigeben_dann_einreihen(projekt: Path) -> None:
    mit_text(projekt, GID_GUT)

    runner.invoke(cli.app, ["campaign", "approve", "batreeq", GID_GUT, "--von", "karim"])
    assert stand(projekt, GID_GUT) is JobStatus.APPROVED

    runner.invoke(cli.app, ["campaign", "enqueue", "batreeq"])
    assert stand(projekt, GID_GUT) is JobStatus.QUEUED

    with MarketingStore(projekt) as store:
        link = store.link_for("batreeq", GID_GUT)
    assert link is not None
    assert link.freigegeben_von == "karim"


def test_freigabe_ohne_text_wird_abgewiesen(projekt: Path) -> None:
    """Ein leerer Beitrag in einer Gruppe waere nicht zurueckzunehmen."""
    ergebnis = runner.invoke(cli.app, ["campaign", "approve", "batreeq", GID_GUT])

    assert "Beitragstext" in ergebnis.stdout
    assert stand(projekt, GID_GUT) is JobStatus.DRAFT


def test_sammelfreigabe_nimmt_alle_wartenden(projekt: Path) -> None:
    mit_text(projekt, GID_GUT, GID_MITTEL, GID_OHNE)

    ergebnis = runner.invoke(cli.app, ["campaign", "approve", "batreeq", "alle"])

    assert "3 freigegeben" in ergebnis.stdout
    for gid in (GID_GUT, GID_MITTEL, GID_OHNE):
        assert stand(projekt, gid) is JobStatus.APPROVED


# --- Prioritaet beim Einreihen ------------------------------------------

def test_die_besten_gruppen_kommen_zuerst(projekt: Path) -> None:
    """Wer abbricht, soll die wertvollsten Beitraege geschrieben haben."""
    mit_text(projekt, GID_OHNE, GID_MITTEL, GID_GUT)
    runner.invoke(cli.app, ["campaign", "approve", "batreeq", "alle"])

    runner.invoke(cli.app, ["campaign", "enqueue", "batreeq", "--top", "1"])

    assert stand(projekt, GID_GUT) is JobStatus.QUEUED         # 140/175 = 80 %
    assert stand(projekt, GID_MITTEL) is JobStatus.APPROVED
    assert stand(projekt, GID_OHNE) is JobStatus.APPROVED


def test_prioritaet_zaehlt_den_anteil_nicht_den_rohwert(projekt: Path) -> None:
    """130 von 175 steht ueber 65 von 100 - eine feste Schwelle verdrehte das."""
    ergebnis = runner.invoke(cli.app, ["campaign", "jobs", "batreeq"])

    zeilen = ergebnis.stdout
    assert "140/175" in zeilen
    assert "hoch" in zeilen
    assert "mittel" in zeilen


# --- PAUSE / RESUME / STOP ----------------------------------------------

def test_pause_und_resume(projekt: Path) -> None:
    mit_text(projekt, GID_GUT)
    runner.invoke(cli.app, ["campaign", "approve", "batreeq", GID_GUT])
    runner.invoke(cli.app, ["campaign", "enqueue", "batreeq"])

    runner.invoke(cli.app, ["campaign", "pause", "batreeq"])
    with MarketingStore(projekt) as store:
        assert store.queue_zustand("batreeq") is QueueZustand.PAUSIERT
        assert store.naechster_job("batreeq") is None
    assert stand(projekt, GID_GUT) is JobStatus.QUEUED      # bleibt eingereiht

    runner.invoke(cli.app, ["campaign", "resume", "batreeq"])
    with MarketingStore(projekt) as store:
        job = store.naechster_job("batreeq")
    assert job is not None and job.group_id == GID_GUT


def test_stop_raeumt_und_meldet_es(projekt: Path) -> None:
    mit_text(projekt, GID_GUT, GID_MITTEL)
    runner.invoke(cli.app, ["campaign", "approve", "batreeq", "alle"])
    runner.invoke(cli.app, ["campaign", "enqueue", "batreeq"])

    ergebnis = runner.invoke(cli.app, ["campaign", "stop", "batreeq"])

    assert "2" in ergebnis.stdout
    assert "approved" in ergebnis.stdout
    for gid in (GID_GUT, GID_MITTEL):
        assert stand(projekt, gid) is JobStatus.APPROVED    # Freigabe bleibt


def test_einreihen_warnt_bei_angehaltener_warteschlange(projekt: Path) -> None:
    """Sonst wundert man sich, warum nichts passiert."""
    mit_text(projekt, GID_GUT)
    runner.invoke(cli.app, ["campaign", "approve", "batreeq", GID_GUT])
    runner.invoke(cli.app, ["campaign", "pause", "batreeq"])

    ergebnis = runner.invoke(cli.app, ["campaign", "enqueue", "batreeq"])

    assert "pausiert" in ergebnis.stdout


# --- Abbrechen -----------------------------------------------------------

def test_abbrechen_laesst_den_tracking_code_gueltig(projekt: Path) -> None:
    """Er steht moeglicherweise schon in einem veroeffentlichten Beitrag."""
    mit_text(projekt, GID_GUT)

    ergebnis = runner.invoke(
        cli.app, ["campaign", "cancel", "batreeq", GID_GUT, "--grund", "passt nicht"]
    )

    assert stand(projekt, GID_GUT) is JobStatus.CANCELLED
    assert "bleibt gueltig" in ergebnis.stdout
    with MarketingStore(projekt) as store:
        link = store.link_for("batreeq", GID_GUT)
    assert link is not None
    assert link.tracking_code == CODE_GUT


# --- Retry ---------------------------------------------------------------

def test_retry_setzt_beide_achsen_zurueck(projekt: Path) -> None:
    """post_status: offen neben job_status: failed waere ein halber Zustand."""
    mit_text(projekt, GID_GUT)
    with MarketingStore(projekt) as store:
        store.set_job_status("batreeq", GID_GUT, JobStatus.PENDING_REVIEW)
        store.set_job_status("batreeq", GID_GUT, JobStatus.APPROVED)
        store.set_job_status("batreeq", GID_GUT, JobStatus.QUEUED)
        store.set_job_status("batreeq", GID_GUT, JobStatus.PROCESSING)
        store.set_job_status("batreeq", GID_GUT, JobStatus.FAILED, fehler="Netz weg")

    runner.invoke(cli.app, ["campaign", "retry", "batreeq"])

    with MarketingStore(projekt) as store:
        link = store.link_for("batreeq", GID_GUT)
    assert link is not None
    assert link.job_status is JobStatus.QUEUED
    assert link.post_status is PostStatus.OFFEN
    assert link.post_error == "Netz weg"        # das Gedaechtnis bleibt


def test_retry_holt_uebersprungene_nicht_zurueck(projekt: Path) -> None:
    """Dort hat ein Mensch entschieden, dass die Gruppe nicht passt."""
    mit_text(projekt, GID_GUT)
    runner.invoke(cli.app, ["campaign", "cancel", "batreeq", GID_GUT])

    runner.invoke(cli.app, ["campaign", "retry", "batreeq"])

    assert stand(projekt, GID_GUT) is JobStatus.CANCELLED


# --- jobs -----------------------------------------------------------------

def test_jobs_zeigt_die_zaehler(projekt: Path) -> None:
    mit_text(projekt, GID_GUT, GID_MITTEL)
    runner.invoke(cli.app, ["campaign", "approve", "batreeq", "alle"])

    ergebnis = runner.invoke(cli.app, ["campaign", "jobs", "batreeq"])

    assert "approved" in ergebnis.stdout
    assert "laufend" in ergebnis.stdout


def test_jobs_filtert_nach_stand(projekt: Path) -> None:
    mit_text(projekt, GID_GUT)

    # Breite Ausgabe: Rich kuerzt sonst die Namensspalte, und der Test
    # pruefte dann die Terminalbreite statt des Filters.
    ergebnis = runner.invoke(
        cli.app, ["campaign", "jobs", "batreeq", "--status", "pending_review"],
        env={"COLUMNS": "200"},
    )

    assert "Syrer in Koeln" in ergebnis.stdout
    assert "Ohne Bewertung" not in ergebnis.stdout


def test_unbekannter_stand_nennt_die_moeglichen(projekt: Path) -> None:
    ergebnis = runner.invoke(cli.app, ["campaign", "jobs", "batreeq", "--status", "quatsch"])

    assert ergebnis.exit_code == 1
    assert "pending_review" in ergebnis.stdout


# --- Beitrag und Kommentar auf der Kommandozeile -------------------------


def test_campaign_text_schreibt_nur_beitraege(projekt: Path) -> None:
    """Ohne Angabe folgt der Befehl der Kampagne - und die fuehrt keine."""
    ergebnis = runner.invoke(
        cli.app, ["campaign", "text", "batreeq", "--aus-vorlage", "--ja"]
    )
    assert ergebnis.exit_code == 0, ergebnis.stdout

    with MarketingStore(projekt) as store:
        for link in store.links_for_campaign("batreeq"):
            assert link.post_text.strip()
            assert link.kommentar_text == ""


def test_campaign_text_mit_typ_beide(projekt: Path) -> None:
    """``--typ`` ueberstimmt die Kampagne fuer diesen einen Lauf."""
    ergebnis = runner.invoke(
        cli.app,
        ["campaign", "text", "batreeq", "--aus-vorlage", "--typ", "beide", "--ja"],
    )
    assert ergebnis.exit_code == 0, ergebnis.stdout

    with MarketingStore(projekt) as store:
        link = store.link_for("batreeq", GID_GUT)
    assert link is not None
    # Die eigene Vorlage der Kampagne gilt nur fuer den Beitrag; der Kommentar
    # kommt aus dem Vorrat und ist deshalb ein anderer Text.
    assert link.vorlage_key == "kampagne"
    assert link.kommentar_vorlage_key.startswith("ar/kommentar/")
    assert link.kommentar_text != link.post_text
    assert PLATZHALTER in link.kommentar_text


def test_campaign_text_weist_einen_erfundenen_typ_ab(projekt: Path) -> None:
    ergebnis = runner.invoke(
        cli.app,
        ["campaign", "text", "batreeq", "--aus-vorlage", "--typ", "plakat", "--ja"],
    )
    assert ergebnis.exit_code == 2
    assert "Unbekannter Texttyp" in ergebnis.stdout


def test_campaign_message_zeigt_beide_texte(projekt: Path) -> None:
    runner.invoke(
        cli.app,
        ["campaign", "text", "batreeq", "--aus-vorlage", "--typ", "beide", "--ja"],
    )
    ergebnis = runner.invoke(cli.app, ["campaign", "message", "batreeq", GID_GUT])

    assert "POST" in ergebnis.stdout
    assert "KOMMENTAR" in ergebnis.stdout
    # Der Code steht im ausgegebenen Text - hier wird er eingesetzt, sonst nie.
    assert CODE_GUT in ergebnis.stdout
