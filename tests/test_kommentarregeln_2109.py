"""Die Anweisungen vom 21.09.2026, je Regel ein Test.

Sechs Punkte, und ihr gemeinsamer Nenner ist: **Die Gruppen einer Kampagne
hat ein Mensch ausgesucht.** Was danach noch schweigt, muss einen Grund in
der Annahme haben (Links, wiederholte Ablehnung) - nicht in einer Erlaubnis,
die niemand mehr erteilen muss.
"""

from __future__ import annotations

from fbgroups.config import AppConfig
from fbgroups.marketing import automatik, lauf
from fbgroups.marketing.entscheidung import (
    LINKMODUS,
    Anspruch,
    Erlaubnis,
    Linkmodus,
    entscheide,
)
from fbgroups.marketing.inhalt import Inhaltsbefund, Relevanz, Thema
from fbgroups.marketing.lauf import Gruppenfortschritt, Kampagnenfortschritt
from fbgroups.marketing.models import PostStatus
from fbgroups.marketing.qualifikation import Qualifikation, Regelbefund

ZIEL = lauf.ZIEL_JE_GRUPPE


def _befund(relevanz: Relevanz = Relevanz.MITTEL) -> Inhaltsbefund:
    return Inhaltsbefund(thema=Thema.REISE, relevanz=relevanz, treffer=("سفر",))


def _gruppe(gid: str, *, veroeffentlicht: int = 0) -> Gruppenfortschritt:
    return Gruppenfortschritt(
        campaign_id="k1",
        group_id=gid,
        name=f"Gruppe {gid}",
        veroeffentlicht=veroeffentlicht,
        ziel=ZIEL,
        mitglied=True,
        regeln_gelesen=True,
        post_status=PostStatus.VEROEFFENTLICHT,
    )


# --- 1. Die Werbungslogik ist weg ----------------------------------------


def test_es_gibt_kein_feld_werbung_mehr() -> None:
    """Die Kette begann an diesem Feld - also gibt es das Feld nicht mehr."""
    assert not hasattr(Erlaubnis(), "werbung")


def test_ungelesene_regeln_schweigen_nicht_mehr() -> None:
    """Der haeufigste Fall im Betrieb: Die Regeln waren nie gelesen."""
    entscheidung = entscheide(
        _befund(Relevanz.HOCH),
        Erlaubnis.aus_regeln(Regelbefund(), Qualifikation.GEEIGNET),
    )
    assert LINKMODUS[entscheidung.art] is not Linkmodus.NO_LINK


def test_ein_werbeverbot_schweigt_nicht_mehr() -> None:
    """Die Regel wird gelesen und steht im Bericht - sie sperrt nur nicht."""
    entscheidung = entscheide(
        _befund(Relevanz.HOCH),
        Erlaubnis.aus_regeln(
            Regelbefund(gelesen=True, keine_werbung=True), Qualifikation.GEEIGNET
        ),
    )
    assert LINKMODUS[entscheidung.art] is not Linkmodus.NO_LINK


# --- 2. Die Schwelle folgt der Gruppe ------------------------------------


def test_mittel_genuegt_wo_mittel_verlangt_ist() -> None:
    """Der Kern der Anweisung: nicht pauschal hoch + Strecke.

    ``anlass_pflicht=False`` ist der Wert aus ``settings.yaml`` - die Vorgabe
    **im Code** bleibt die vorsichtige (siehe ``Anspruch``). Eingeschaltet
    verlangt sie neben der Schwelle noch einen erkannten Halbsatz, und genau
    das ist "pauschal mehr als die Anforderung der Gruppe".
    """
    entscheidung = entscheide(
        _befund(Relevanz.MITTEL),
        Erlaubnis.aus_regeln(Regelbefund(gelesen=True), Qualifikation.GEEIGNET),
        Anspruch(mindestrelevanz=Relevanz.MITTEL, anlass_pflicht=False),
    )
    assert LINKMODUS[entscheidung.art] is not Linkmodus.NO_LINK


def test_hoch_verlangt_weiterhin_hoch() -> None:
    """Die Anforderung der Gruppe gilt - in beide Richtungen."""
    entscheidung = entscheide(
        _befund(Relevanz.MITTEL),
        Erlaubnis.aus_regeln(Regelbefund(gelesen=True), Qualifikation.GEEIGNET),
        Anspruch(mindestrelevanz=Relevanz.HOCH, anlass_pflicht=False),
    )
    assert "zu schwach" in entscheidung.grund


def test_ohne_bezug_wird_nicht_kommentiert() -> None:
    """Punkt 3: keine beliebigen voellig irrelevanten Beitraege."""
    entscheidung = entscheide(
        Inhaltsbefund(thema=Thema.WOHNUNG, relevanz=Relevanz.KEINE),
        Erlaubnis.aus_regeln(Regelbefund(gelesen=True), Qualifikation.GEEIGNET),
        Anspruch(mindestrelevanz=Relevanz.MITTEL, anlass_pflicht=False),
    )
    assert "kein Bezug" in entscheidung.grund


def test_niedrig_ist_die_schwaechste_schwelle_und_heisst_mittel(
    config: AppConfig,
) -> None:
    """"niedrig" ist erlaubt - darunter liegt nur "kein Zusammenhang"."""

    class _Niedrig:
        def get(self, *_pfad, default=None):  # noqa: ANN002, ANN003
            return "niedrig"

    # Seit dem 23.09.2026 eine Schwelle fuer alle Gruppen
    # (``marketing.mindestrelevanz``) statt einer je Zielklasse.
    assert automatik.mindestrelevanz(_Niedrig()) is Relevanz.MITTEL


# --- 5. Das Kampagnenziel -------------------------------------------------


def test_die_kampagne_bleibt_bis_zu_hundert_kommentaren_aktiv() -> None:
    """Neunundneunzig sind nicht hundert."""
    kampagne = Kampagnenfortschritt(
        campaign_id="k1",
        name="K1",
        gruppen=[_gruppe(str(i), veroeffentlicht=ZIEL) for i in range(9)]
        + [_gruppe("9", veroeffentlicht=9)],
        ziel_kommentare=100,
    )
    assert kampagne.kommentare_veroeffentlicht == 99
    assert not kampagne.abgeschlossen


def test_bei_hundert_kommentaren_ist_die_kampagne_erreicht() -> None:
    kampagne = Kampagnenfortschritt(
        campaign_id="k1",
        name="K1",
        gruppen=[_gruppe(str(i), veroeffentlicht=ZIEL) for i in range(10)],
        ziel_kommentare=100,
    )
    assert kampagne.kommentare_veroeffentlicht == 100
    assert kampagne.abgeschlossen


def test_ohne_ziel_gilt_die_alte_bedingung() -> None:
    """``0`` heisst: kein eigenes Ziel - jede Gruppe muss voll sein."""
    kampagne = Kampagnenfortschritt(
        campaign_id="k1",
        name="K1",
        gruppen=[_gruppe("1", veroeffentlicht=ZIEL), _gruppe("2", veroeffentlicht=1)],
        ziel_kommentare=0,
    )
    assert not kampagne.abgeschlossen


def test_eine_volle_kampagne_endet_auch_unter_hundert() -> None:
    """Die Garantie des Abschlusses: Der Vorrat kann vor der Hundert enden."""
    kampagne = Kampagnenfortschritt(
        campaign_id="k1",
        name="K1",
        gruppen=[_gruppe(str(i), veroeffentlicht=ZIEL) for i in range(3)],
        ziel_kommentare=100,
    )
    assert kampagne.kommentare_veroeffentlicht == 30
    assert kampagne.abgeschlossen


def test_das_ziel_steht_in_der_konfiguration(config: AppConfig) -> None:
    from fbgroups.marketing import automatik

    assert automatik.ziel_kommentare(config) == 100
