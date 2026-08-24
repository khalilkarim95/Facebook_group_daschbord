"""Tests fuer den Publisher-Vertrag und seine Registry.

Der Sinn dieser Schicht ist, dass der Arbeiter keinen einzigen Adapter kennt.
Solange das gilt, laesst sich ein Adapter ergaenzen oder entfernen, ohne dass
Ablaufsteuerung, Kommandozeile oder Uebersicht davon etwas merken - und die
Regeln, die die Beitraege begrenzen, bleiben ohne Browser pruefbar.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from fbgroups.marketing.models import CampaignGroup
from fbgroups.marketing.veroeffentlicher import (
    AssistierterVeroeffentlicher,
    Ergebnis,
    UnbekannterVeroeffentlicher,
    Veroeffentlicher,
    baue_veroeffentlicher,
    verfuegbare,
)
from fbgroups.models import Group

LINK = CampaignGroup(
    campaign_id="batreeq",
    group_id="482910573829104",
    tracking_code="FB-SYR-KLN-002",
    tracking_url="https://b-tarikak.de/r/FB-SYR-KLN-002",
)
GRUPPE = Group(
    group_id="482910573829104",
    url_canonical="https://www.facebook.com/groups/482910573829104",
    name="Syrer in Koeln",
)


def assistiert(**kwargs) -> AssistierterVeroeffentlicher:
    """Ohne Browser und ohne Zwischenablage - im Test greift beides ins Leere."""
    kwargs.setdefault("browser", False)
    kwargs.setdefault("zwischenablage", False)
    return AssistierterVeroeffentlicher(**kwargs)


# --- Die Registry ---------------------------------------------------------

def test_assistiert_ist_eingetragen() -> None:
    assert "assistiert" in verfuegbare()
    assert verfuegbare()["assistiert"]           # mit einem Satz Erklaerung


def test_bauen_ueber_den_namen() -> None:
    """Der Arbeiter bekommt seinen Adapter ueber einen Namen, nicht ueber einen Import."""
    adapter = baue_veroeffentlicher("assistiert", frage=lambda _: "", browser=False)

    assert isinstance(adapter, AssistierterVeroeffentlicher)
    assert adapter.name == "assistiert"


def test_ein_tippfehler_nennt_die_vorhandenen() -> None:
    """Bei "Unbekannt" ist die naechste Frage immer sofort: welche denn?"""
    with pytest.raises(UnbekannterVeroeffentlicher) as fehler:
        baue_veroeffentlicher("playwriht")

    assert "assistiert" in str(fehler.value)


def test_ein_neuer_adapter_braucht_nur_eine_zeile() -> None:
    """Die Probe aufs Exempel: ohne Aenderung an Arbeiter oder Kommandozeile."""
    from fbgroups.marketing.veroeffentlicher.basis import (
        _BESCHREIBUNGEN,
        _REGISTRY,
        register_veroeffentlicher,
    )

    @register_veroeffentlicher("erfunden", "Nur fuer diesen Test.")
    class Erfunden:
        name = "erfunden"
        beschreibung = "Nur fuer diesen Test."

        def veroeffentliche(self, *, gruppe, text, link) -> Ergebnis:
            return Ergebnis(erfolg=True)

    try:
        assert "erfunden" in verfuegbare()
        adapter = baue_veroeffentlicher("erfunden")
        assert adapter.veroeffentliche(gruppe=None, text="", link=LINK).erfolg is True
    finally:
        _REGISTRY.pop("erfunden", None)
        _BESCHREIBUNGEN.pop("erfunden", None)


def test_der_assistierte_erfuellt_den_vertrag() -> None:
    """``runtime_checkable``: Wer die Namen hat, gilt als Veroeffentlicher."""
    assert isinstance(assistiert(frage=lambda _: ""), Veroeffentlicher)


# --- Der assistierte Adapter ---------------------------------------------

def test_enter_heisst_veroeffentlicht() -> None:
    ergebnis = assistiert(frage=lambda _: "").veroeffentliche(
        gruppe=GRUPPE, text="Hallo", link=LINK
    )

    assert ergebnis.erfolg is True
    assert ergebnis.abbrechen is False


def test_f_fragt_nach_dem_grund() -> None:
    """Ohne Grund ist ``retry`` blind - derselbe Fehler kaeme wieder."""
    antworten = iter(["f", "erlaubt keine Links"])

    ergebnis = assistiert(frage=lambda _: next(antworten)).veroeffentliche(
        gruppe=GRUPPE, text="Hallo", link=LINK
    )

    assert ergebnis.erfolg is False
    assert ergebnis.fehler == "erlaubt keine Links"
    assert ergebnis.uebersprungen is False


def test_u_ist_kein_fehlschlag() -> None:
    """"Passt nicht" ist ein Urteil ueber die Gruppe, kein Fehler."""
    ergebnis = assistiert(frage=lambda _: "u").veroeffentliche(
        gruppe=GRUPPE, text="Hallo", link=LINK
    )

    assert ergebnis.uebersprungen is True
    assert ergebnis.fehler == ""


def test_q_beendet_den_ganzen_lauf() -> None:
    ergebnis = assistiert(frage=lambda _: "q").veroeffentliche(
        gruppe=GRUPPE, text="Hallo", link=LINK
    )

    assert ergebnis.abbrechen is True
    assert ergebnis.fehler == ""            # kein Fehlschlag, nur Schluss


def test_ein_fehler_ohne_angabe_bleibt_nachvollziehbar() -> None:
    """Leer gelassen heisst nicht "kein Fehler" - es heisst "ohne Angabe"."""
    antworten = iter(["f", "   "])

    ergebnis = assistiert(frage=lambda _: next(antworten)).veroeffentliche(
        gruppe=GRUPPE, text="Hallo", link=LINK
    )

    assert ergebnis.fehler == "ohne Angabe"


def test_der_adapter_bekommt_den_fertigen_text() -> None:
    """Er setzt keinen Tracking-Code ein - der steht schon drin.

    Es gibt genau eine Ersetzungsstelle im Projekt (``beitrag.beitragstext``).
    Ein Adapter, der selbst ersaetze, waere die zweite - und der Unterschied
    fiele erst auf, wenn ein Beitrag mit dem falschen Code in einer Gruppe steht.
    """
    gesehen: list[str] = []

    class Merkt:
        name = "merkt"
        beschreibung = ""

        def veroeffentliche(self, *, gruppe, text, link) -> Ergebnis:
            gesehen.append(text)
            return Ergebnis(erfolg=True)

    Merkt().veroeffentliche(gruppe=GRUPPE, text="Hallo https://b-tarikak.de/r/X", link=LINK)

    assert gesehen == ["Hallo https://b-tarikak.de/r/X"]


# --- Die Architektur-Invariante ------------------------------------------

def test_kein_modul_ausserhalb_des_pakets_kennt_einen_adapter() -> None:
    """Dieselbe Invariante wie bei ``providers/`` - und aus demselben Grund.

    Nur ``veroeffentlicher/`` darf eine konkrete Umsetzung importieren. Waere
    das verletzt, haenge der Arbeiter an einer bestimmten Art zu posten, und
    die Ablaufsteuerung liesse sich nicht mehr ohne sie pruefen.
    """
    src = Path(__file__).resolve().parents[1] / "src" / "fbgroups"
    muster = re.compile(
        r"^\s*(from|import)\s+.*\bveroeffentlicher\.(assistiert|browser|playwright)\b",
        re.MULTILINE,
    )

    verstoesse = [
        py.relative_to(src).as_posix()
        for py in src.rglob("*.py")
        if py.parent.name != "veroeffentlicher" and muster.search(py.read_text(encoding="utf-8"))
    ]

    assert verstoesse == [], f"Adapterspezifischer Import ausserhalb des Pakets: {verstoesse}"


def test_der_arbeiter_kennt_keinen_adapter() -> None:
    """``worker.py`` steuert den Ablauf und weiss nicht, wie gepostet wird."""
    quelle = (
        Path(__file__).resolve().parents[1] / "src" / "fbgroups" / "marketing" / "worker.py"
    ).read_text(encoding="utf-8")

    assert "assistiert" not in quelle.lower()
    assert "webbrowser" not in quelle
    assert "zwischenablage" not in quelle.lower()


def test_kein_feld_fuer_eine_anmeldung() -> None:
    """``PostVersuch`` haelt einen Sitzungs*namen*, nie eine Anmeldung.

    Ein Adapter, der einen Browser steuerte, brauchte eine angemeldete
    Sitzung - aber gespeichert wird davon nichts. Das Modell hat schlicht kein
    Feld, in das ein Passwort, ein Cookie oder ein Token passte.
    """
    from fbgroups.marketing.models import PostVersuch

    felder = set(PostVersuch.model_fields)

    assert "browser_session" in felder
    assert not felder & {"passwort", "password", "cookie", "token", "credentials", "secret"}
