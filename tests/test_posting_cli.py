"""Tests fuer die Befehle der Beitrags-Warteschlange.

Gegen die echte Kommandozeile, gegen eine eigene Datenbank im tmp-Verzeichnis.
Kein Aufruf an Claude: ``campaign draft`` wird nur mit ``--dry-run`` geprueft
oder mit einem eingesetzten Modell - ein Test, der Geld kostet, wird nicht
ausgefuehrt, und ein Test, der ohne Netz scheitert, sagt nichts ueber den Code.

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
from fbgroups.marketing.ki import PLATZHALTER
from fbgroups.marketing.models import (
    Campaign,
    CampaignGroup,
    JobStatus,
    PostStatus,
    QueueZustand,
    TextQuelle,
)
from fbgroups.marketing.store import MarketingStore
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
    """Gibt den genannten Gruppen einen Text und den Stand ``ai_generated``."""
    with MarketingStore(pfad) as store:
        for gid in group_ids:
            store.set_post_text(
                "batreeq", gid, f"Ein Text fuer {gid} {PLATZHALTER}", TextQuelle.KI
            )
            store.set_job_status("batreeq", gid, JobStatus.AI_GENERATED)


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
    assert stand(projekt, GID_GUT) is JobStatus.AI_GENERATED


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


# --- draft ---------------------------------------------------------------

def test_dry_run_ruft_nichts_ab(projekt: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Nichts wird erzeugt - weder lokal Rechenzeit noch anderswo Geld.

    Bei 310 Gruppen will man die Zahl vorher kennen, und bei einem lokalen
    Modell ist die Zeit der Preis: 310 Beitraege sind Stunden.
    """
    def platzt(*args: object, **kwargs: object) -> None:
        raise AssertionError("Es haette kein Modell gebaut werden duerfen")

    monkeypatch.setattr(marketing_cli, "baue_modell", platzt)

    ergebnis = runner.invoke(cli.app, ["campaign", "draft", "batreeq", "--dry-run"])

    assert ergebnis.exit_code == 0
    assert "Wuerde erzeugen" in ergebnis.stdout
    assert "3 Gruppen" in ergebnis.stdout


def test_dry_run_zeigt_die_besten_zuerst(projekt: Path) -> None:
    ergebnis = runner.invoke(
        cli.app, ["campaign", "draft", "batreeq", "--dry-run", "--top", "1"],
        env={"COLUMNS": "200"},
    )

    assert "Syrer in Koeln" in ergebnis.stdout
    assert "Syrer in Berlin" not in ergebnis.stdout


def test_draft_ueberspringt_gruppen_mit_text(projekt: Path) -> None:
    """Ein zweiter Lauf soll nicht 310 Aufrufe wiederholen."""
    mit_text(projekt, GID_GUT)

    ergebnis = runner.invoke(
        cli.app, ["campaign", "draft", "batreeq", "--dry-run"], env={"COLUMNS": "200"}
    )

    assert "2 Gruppen" in ergebnis.stdout
    assert "Syrer in Koeln" not in ergebnis.stdout


def test_ohne_anthropic_schluessel_laeuft_der_entwurf_trotzdem(
    projekt: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Kern der Umstellung: Ein fehlender Anthropic-Schluessel blockiert nichts.

    Ohne Schluessel und ohne das Paket muss der Weg trotzdem bis zum lokalen
    Modell durchlaufen. Dass Ollama hier auch nicht laeuft, ist ein anderer
    Fehler - und er darf nicht mit einem fehlenden Schluessel verwechselt
    werden, denn der wird gar nicht mehr gebraucht.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("AI_PROVIDER", "")

    ergebnis = runner.invoke(cli.app, ["campaign", "draft", "batreeq", "--dry-run"])

    assert ergebnis.exit_code == 0
    assert "ollama" in ergebnis.stdout
    assert "ANTHROPIC_API_KEY" not in ergebnis.stdout


def test_ohne_laufendes_ollama_ein_verstaendlicher_hinweis(
    projekt: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Connection refused darf nichts zum Absturz bringen."""
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:1")   # dort horcht nichts

    ergebnis = runner.invoke(cli.app, ["campaign", "draft", "batreeq"])

    # Der Lauf endet geordnet und sagt, was zu tun ist.
    assert "nicht erreichbar" in ergebnis.stdout or "ollama" in ergebnis.stdout.lower()
    assert ergebnis.exception is None or isinstance(ergebnis.exception, SystemExit)


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
        cli.app, ["campaign", "jobs", "batreeq", "--status", "ai_generated"],
        env={"COLUMNS": "200"},
    )

    assert "Syrer in Koeln" in ergebnis.stdout
    assert "Ohne Bewertung" not in ergebnis.stdout


def test_unbekannter_stand_nennt_die_moeglichen(projekt: Path) -> None:
    ergebnis = runner.invoke(cli.app, ["campaign", "jobs", "batreeq", "--status", "quatsch"])

    assert ergebnis.exit_code == 1
    assert "pending_review" in ergebnis.stdout
