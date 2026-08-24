"""Tests fuer die Anbieterwahl und den Ollama-Adapter.

Kein laufendes Ollama noetig - und ebenso wenig ein *nicht* laufendes: Die
HTTP-Aufrufe werden mit ``respx`` abgefangen (schon Testabhaengigkeit dieses
Projekts), und der wichtigste Fall - es laeuft **nichts**, das muss ohne
Absturz und mit einer brauchbaren Auskunft enden - wird ueber die Fixture
``ohne_ollama`` hergestellt statt vorausgesetzt. Vorher stand dort die
Annahme, auf dem Rechner sei gerade kein Ollama gestartet; sobald jemand mit
der KI arbeitete, scheiterten drei Tests, ohne dass sich am Programm etwas
geaendert hatte.
"""

from __future__ import annotations

from dataclasses import replace

import httpx
import pytest
import respx

from fbgroups.config import load_config
from fbgroups.marketing.ki import (
    ANTHROPIC,
    OLLAMA,
    KINichtVerfuegbar,
    OllamaModell,
    baue_modell,
    gewaehlter_anbieter,
)
from fbgroups.marketing.ki import status as ki_status
from fbgroups.marketing.ki import teste as ki_teste
from fbgroups.marketing.ki.ollama import DEFAULT_BASE_URL, base_url, modellname

ADRESSE = "http://localhost:11434"

# Eine Adresse, an der mit Sicherheit nichts lauscht: Port 1 ist reserviert und
# wird von keinem Dienst belegt. Der Verbindungsversuch scheitert sofort - der
# Statusabruf laeuft damit nicht einmal in seine Zeitgrenze.
TOTE_ADRESSE = "http://127.0.0.1:1"


@pytest.fixture()
def cfg(config):
    """Die echte Konfiguration - sie traegt den Standard, den wir pruefen."""
    return config


@pytest.fixture(autouse=True)
def _ohne_umgebung(monkeypatch: pytest.MonkeyPatch) -> None:
    """Die Umgebung des Arbeitsrechners darf hier nicht mitentscheiden.

    Derselbe Grund wie bei ``_ohne_env_datei`` in conftest: Wer OLLAMA_MODEL
    gesetzt hat - der Normalfall nach der Einrichtung -, saehe sonst Tests
    scheitern, die ausdruecklich die Voreinstellung pruefen.
    """
    for name in ("AI_PROVIDER", "OLLAMA_BASE_URL", "OLLAMA_MODEL", "ANTHROPIC_MODEL"):
        monkeypatch.setenv(name, "")


@pytest.fixture()
def ohne_ollama(_ohne_umgebung, monkeypatch: pytest.MonkeyPatch) -> str:
    """Macht "Ollama laeuft nicht" zu einer Angabe des Tests.

    Vorher war es eine Eigenschaft des Rechners: Die Tests unten pruefen den
    Fall, dass nichts laeuft - und taten das, indem sie darauf bauten, dass
    auf diesem Rechner gerade kein Ollama gestartet ist. Wer es startet (der
    Normalfall, sobald jemand mit der KI arbeitet), sah drei Tests scheitern,
    ohne dass sich am Programm etwas geaendert haette. Genau derselbe Grund
    wie bei ``_ohne_env_datei`` in conftest.
    """
    monkeypatch.setenv("OLLAMA_BASE_URL", TOTE_ADRESSE)
    return TOTE_ADRESSE


# --- Die Wahl des Anbieters ---------------------------------------------

def test_ollama_ist_der_standard(cfg) -> None:
    """Der Kern der ganzen Umstellung: kostenlos und lokal als Voreinstellung."""
    assert gewaehlter_anbieter(cfg) == OLLAMA


def test_umgebung_schlaegt_die_datei(cfg, monkeypatch: pytest.MonkeyPatch) -> None:
    """Dieselbe Reihenfolge wie bei APP_BASE_URL."""
    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    assert gewaehlter_anbieter(cfg) == ANTHROPIC


def test_unbekannter_anbieter_faellt_auf_ollama_zurueck(
    cfg, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ein Tippfehler darf niemanden bei einem kostenpflichtigen Dienst abliefern."""
    monkeypatch.setenv("AI_PROVIDER", "antropic")     # verschrieben
    assert gewaehlter_anbieter(cfg) == OLLAMA


def test_der_standard_baut_ein_ollama_modell(cfg) -> None:
    modell = baue_modell(cfg)
    assert isinstance(modell, OllamaModell)


def test_kein_stiller_wechsel_auf_anthropic(cfg, ohne_ollama) -> None:
    """Ollama laeuft nicht - trotzdem wird nichts Kostenpflichtiges gebaut.

    Ein Ausweichen waere bequem und falsch: Es verwandelte einen
    abgeschalteten Rechner in eine Rechnung.
    """
    modell = baue_modell(cfg)
    assert isinstance(modell, OllamaModell)
    assert ki_status(cfg).anbieter == OLLAMA


# --- Adresse und Modell --------------------------------------------------

def test_adresse_und_modell_kommen_aus_der_konfiguration(cfg) -> None:
    assert base_url(cfg) == DEFAULT_BASE_URL
    assert modellname(cfg)          # irgendein Modell ist eingestellt


def test_umgebung_schlaegt_die_datei_auch_hier(cfg, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://192.168.1.50:11434/")
    monkeypatch.setenv("OLLAMA_MODEL", "irgendein:7b")

    assert base_url(cfg) == "http://192.168.1.50:11434"    # ohne Schraegstrich
    assert modellname(cfg) == "irgendein:7b"


def test_keine_adresse_steht_fest_im_programm(cfg) -> None:
    """Die Adresse ist konfigurierbar - sonst waere ein anderer Rechner unmoeglich."""
    modell = OllamaModell(adresse="http://anderer-rechner:11434", modell="x:1b")
    assert modell.adresse == "http://anderer-rechner:11434"


# --- Status, wenn nichts laeuft ------------------------------------------

def test_ohne_ollama_kein_absturz_sondern_eine_anleitung(cfg, ohne_ollama) -> None:
    """Die Uebersicht ruft das bei jedem Seitenaufruf - es darf nie werfen."""
    stand = ki_status(cfg)

    assert stand.erreichbar is False
    assert "nicht erreichbar" in stand.meldung
    # Die drei Schritte aus der Aufgabenstellung stehen wirklich drin.
    assert "ollama serve" in stand.meldung
    assert "OLLAMA_BASE_URL" in stand.meldung
    assert "ollama pull" in stand.meldung


def test_test_ohne_ollama_meldet_sauber(cfg, ohne_ollama) -> None:
    geklappt, text = ki_teste(cfg)

    assert geklappt is False
    assert "nicht erreichbar" in text


# --- Status mit laufendem Ollama (abgefangen) ---------------------------

@respx.mock
def test_verbunden_mit_vorhandenem_modell() -> None:
    respx.get(f"{ADRESSE}/api/tags").mock(
        return_value=httpx.Response(200, json={"models": [{"name": "qwen3.5:4b"}]})
    )
    stand = OllamaModell(adresse=ADRESSE, modell="qwen3.5:4b").status()

    assert stand.erreichbar is True
    assert stand.modell_vorhanden is True
    assert stand.meldung == ""
    assert stand.verfuegbare_modelle == ["qwen3.5:4b"]


@respx.mock
def test_verbunden_aber_modell_fehlt() -> None:
    """Der haeufigste Fehler nach der Einrichtung - und "verbunden" allein
    waere hier eine irrefuehrende Auskunft."""
    respx.get(f"{ADRESSE}/api/tags").mock(
        return_value=httpx.Response(200, json={"models": [{"name": "llama3:8b"}]})
    )
    stand = OllamaModell(adresse=ADRESSE, modell="qwen3.5:4b").status()

    assert stand.erreichbar is True
    assert stand.modell_vorhanden is False
    assert "ollama pull qwen3.5:4b" in stand.meldung


@respx.mock
def test_die_tag_variante_zaehlt_nicht_als_anderes_modell() -> None:
    """'qwen3.5:4b' und 'qwen3.5:4b-instruct-q4_K_M' sind dasselbe Modell."""
    respx.get(f"{ADRESSE}/api/tags").mock(
        return_value=httpx.Response(200, json={"models": [{"name": "qwen3.5:4b-q4_K_M"}]})
    )
    assert OllamaModell(adresse=ADRESSE, modell="qwen3.5:4b").status().modell_vorhanden


# --- Erzeugen ------------------------------------------------------------

@respx.mock
def test_eine_anfrage_je_fassung() -> None:
    """Bewusst nicht drei Fassungen in einem JSON: Ein kleines Modell haelt
    kein Schema ein, und dann waere nicht eine unbrauchbar, sondern alle."""
    weg = respx.post(f"{ADRESSE}/api/generate").mock(
        return_value=httpx.Response(200, json={"response": "Ein Text {link}"})
    )
    antwort = OllamaModell(adresse=ADRESSE, modell="x:1b").erzeuge(
        "system", "auftrag", varianten=3
    )

    assert len(antwort.varianten) == 3
    assert weg.call_count == 3


@respx.mock
def test_spaetere_anfragen_kennen_die_frueheren_fassungen() -> None:
    """Ohne das schriebe ein Modell dreimal nahezu dasselbe."""
    respx.post(f"{ADRESSE}/api/generate").mock(
        return_value=httpx.Response(200, json={"response": "Immer derselbe Text {link}"})
    )
    OllamaModell(adresse=ADRESSE, modell="x:1b").erzeuge("system", "auftrag", varianten=2)

    zweite = respx.calls[1].request.content.decode()
    assert "schon geschrieben" in zweite


@respx.mock
def test_fehlendes_modell_wird_von_nicht_laufendem_dienst_unterschieden() -> None:
    """Zwei verschiedene Fehler mit zwei verschiedenen Loesungen."""
    respx.post(f"{ADRESSE}/api/generate").mock(return_value=httpx.Response(404))

    with pytest.raises(KINichtVerfuegbar, match="ollama pull"):
        OllamaModell(adresse=ADRESSE, modell="fehlt:1b").erzeuge("s", "a", varianten=1)


@respx.mock
def test_verbindungsfehler_gibt_die_drei_schritte() -> None:
    respx.post(f"{ADRESSE}/api/generate").mock(side_effect=httpx.ConnectError("refused"))

    with pytest.raises(KINichtVerfuegbar) as fehler:
        OllamaModell(adresse=ADRESSE, modell="x:1b").erzeuge("s", "a", varianten=1)

    assert "ollama serve" in str(fehler.value)


@respx.mock
def test_der_selbsttest_fragt_auf_arabisch() -> None:
    """Er beantwortet in einem Zug: laeuft der Dienst, liegt das Modell vor,
    und kommt arabische Schrift sauber heraus."""
    weg = respx.post(f"{ADRESSE}/api/generate").mock(
        return_value=httpx.Response(200, json={"response": "مرحباً، هذا اختبار."})
    )
    geklappt, text = OllamaModell(adresse=ADRESSE, modell="x:1b").teste()

    assert geklappt is True
    assert "مرحبا" in text
    assert "Arabisch" in weg.calls[0].request.content.decode()


# --- Anthropic bleibt wirklich optional ---------------------------------

def test_ohne_anthropic_paket_bleibt_der_rest_benutzbar(
    cfg, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kein Weg im System darf an einem fehlenden Schluessel haengen."""
    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")

    stand = ki_status(cfg)
    assert stand.erreichbar is False
    assert stand.anbieter == ANTHROPIC
    # Und die Meldung sagt, dass es auch ohne geht.
    assert "ollama" in stand.meldung.lower()


def test_anthropic_wird_nur_auf_ausdrueckliche_wahl_gebaut(cfg) -> None:
    """Ohne AI_PROVIDER=anthropic wird das Paket nicht einmal importiert."""
    modell = baue_modell(cfg)
    assert type(modell).__module__.endswith("ollama")


def test_die_konfiguration_nennt_ollama_als_standard() -> None:
    """Die Voreinstellung steht in der Datei, nicht nur im Programm."""
    echt = load_config()
    assert echt.get("marketing", "posting", "ki", "anbieter") == "ollama"
    # Und es gibt keinen Zwang zu einem Schluessel.
    assert replace(echt).get("marketing", "posting", "ki", "ollama", "base_url")


# --- Der Weg im Dienst ---------------------------------------------------

def test_der_testweg_ist_nicht_von_aussen_ausloesbar(tmp_path, config) -> None:
    """Er erzeugt wirklich etwas - bei Ollama Rechenzeit, bei Anthropic Geld.

    Ein Weg, den jeder von aussen ausloesen koennte, waere bei einem lokalen
    Modell eine Einladung, den Rechner lahmzulegen. Er steht deshalb hinter
    derselben Pruefung wie jeder andere schreibende Weg.
    """
    pytest.importorskip("fastapi", reason="nur mit dem optionalen web-Zusatz")
    from fastapi.testclient import TestClient

    from fbgroups.marketing.web import create_app
    from fbgroups.storage import SqliteStore

    pfad = tmp_path / "groups.sqlite"
    SqliteStore(pfad).close()
    # Fremde Absenderadresse - dieselbe Art, wie die Uebersicht geprueft wird.
    fremd = TestClient(
        create_app(config=config, db_path=pfad), client=("203.0.113.7", 44321)
    )

    assert fremd.post("/ki/test").status_code == 404
    # Der eigentliche Zweck des Dienstes bleibt von aussen erreichbar.
    assert fremd.get("/healthz").status_code == 200


def test_der_testweg_antwortet_auch_ohne_ollama_mit_200(tmp_path, config, ohne_ollama) -> None:
    """Der Aufruf hat seine Auskunft gegeben - auch wenn sie "laeuft nicht" lautet.

    Ein 500 saehe aus wie ein Fehler des Dienstes und nicht wie ein
    abgeschaltetes Ollama, und das Dashboard zeigte einen Netzwerkfehler statt
    der Anleitung.
    """
    pytest.importorskip("fastapi", reason="nur mit dem optionalen web-Zusatz")
    from fastapi.testclient import TestClient

    from fbgroups.marketing.web import create_app
    from fbgroups.storage import SqliteStore

    pfad = tmp_path / "groups.sqlite"
    SqliteStore(pfad).close()
    client = TestClient(create_app(config=config, db_path=pfad))

    antwort = client.post("/ki/test")

    assert antwort.status_code == 200
    daten = antwort.json()
    assert daten["ok"] is False
    assert "ollama" in daten["text"].lower()


def test_die_uebersicht_baut_auch_ohne_ollama(tmp_path, config, ohne_ollama) -> None:
    """Die KI ist ein Aufsatz, keine Voraussetzung."""
    from fbgroups.marketing.dashboard import render, sammle_daten
    from fbgroups.storage import SqliteStore

    pfad = tmp_path / "groups.sqlite"
    SqliteStore(pfad).close()

    daten = sammle_daten(config, pfad)
    seite = render(daten)

    assert daten["ki"]["erreichbar"] is False
    assert "ki-karte" in seite
    assert "Ollama testen" in seite       # der Knopf ist da, der Dienst nicht
