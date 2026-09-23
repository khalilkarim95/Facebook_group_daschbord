"""Was Facebook geantwortet hat - die Einstufung, die nach der Qualifikation bleibt.

Die Qualifikation ist am 23.09.2026 entfernt worden. Die Einstufung der
Antworten bleibt: Ohne sie erkennte der Lauf die Bremse der Gegenseite nicht
und liefe nach einer Sperre einfach weiter.
"""

from __future__ import annotations

from fbgroups.marketing.ausgang import Ablehnungsgrund, Ausgangsart, grund, klassifiziere


def test_ein_technischer_fehler_ist_keine_ablehnung() -> None:
    """Der Fehler, der am 11.09.2026 45 Gruppen gekostet hat.

    Ein geschlossener Browser liess zehn Fassungen dreimal scheitern; im
    Protokoll sieht das aus wie eine Ablehnung und ist eine Aussage ueber uns.
    """
    assert klassifiziere(
        "BrowserContext.new_page: Target page, context or browser has been closed"
    ) is Ausgangsart.TECHNISCH
    assert klassifiziere("Zeitablauf") is Ausgangsart.TECHNISCH
    assert klassifiziere("") is Ausgangsart.TECHNISCH
    assert klassifiziere("Dein Kommentar wurde abgelehnt") is Ausgangsart.MODERATION
    # Im Zweifel technisch: Eine geratene Ablehnung verurteilte eine Gruppe.
    assert klassifiziere("irgendein unbekannter Text") is Ausgangsart.TECHNISCH


def test_die_bremse_klingt_wie_eine_ablehnung_und_ist_keine() -> None:
    """"voruebergehend gesperrt" enthaelt "gesperrt" - und ist unsere Eile."""
    assert klassifiziere("Du wurdest vorübergehend gesperrt") is Ausgangsart.RATE_LIMIT
    assert grund("Du wurdest vorübergehend gesperrt") is (
        Ablehnungsgrund.TEMPORARY_PLATFORM_RESTRICTION
    )


def test_das_gruppenlimit_ist_weder_bremse_noch_ablehnung() -> None:
    fehler = "Du hast das Limit für freizugebende Inhalte in dieser Gruppe erreicht"

    assert klassifiziere(fehler) is Ausgangsart.GRUPPENLIMIT
    assert grund(fehler) is Ablehnungsgrund.GROUP_PENDING_LIMIT


def test_ein_abgelehnter_link_wird_benannt() -> None:
    assert grund("Abgelehnt: Link in Kommentar") is Ablehnungsgrund.LINK_REJECTED
    assert grund("", erfolg=True) is Ablehnungsgrund.OK
