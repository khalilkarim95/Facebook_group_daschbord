"""Eine unlesbare Gruppenseite kostet einen Durchgang, nicht den ganzen Lauf.

Der Anlass steht in vier Zeilen aus ``post_versuche`` vom 21.09.2026 - und es
ist viermal **dieselbe** Gruppe mit **derselben** Fassung:

    22:43:54 | 668139805288848 | Fassung 1 | keine Beitraege zum Kommentieren
    22:54:11 | 668139805288848 | Fassung 1 | keine Beitraege zum Kommentieren
    23:06:52 | 668139805288848 | Fassung 1 | keine Beitraege zum Kommentieren
    23:22:49 | 668139805288848 | Fassung 1 | keine Beitraege zum Kommentieren

Vierzig Minuten, vier verbrauchte Kommentar-Takte, kein einziger Kommentar -
waehrend fuenfzehn andere Gruppen derselben Kampagne warteten. Der Grund war
ein falsch gewaehltes Feld: ``fetch_top_posts`` lieferte nichts, und der
Ausgang wurde als ``erschoepft`` gebucht.

``erschoepft`` heisst "diese Gruppe gibt nichts mehr her" - ein Urteil ueber
die **Gruppe**. Eine Seite, die eine Anmeldewand zeigt, sagt aber nichts ueber
die Gruppe, sondern etwas ueber **unseren Zugang**; sie gibt bei jeder Fassung
dasselbe her. Die Gruppe blieb deshalb in der Arbeitsliste und kam im Takt der
Kommentare immer wieder an die Reihe.

Richtig ist ``gruppe_beiseite``: fuer **diesen Lauf** beiseitegelegt, kein
Urteil, im naechsten Lauf wieder dabei - dieselbe Behandlung, die
``Gruppenseite nicht lesbar`` beim Regelschritt laengst bekommt. Es ist
dieselbe Verwechslung, die am 11.09.2026 45 Gruppen faelschlich als erschoepft
vermerkt hat, nur eine Ebene tiefer.
"""

from __future__ import annotations

import pytest

from fbgroups.marketing import automatik


def _keine_beitraege(_context, _url: str, _gid: str, limit: int = 10):  # noqa: ARG001
    """Eine Gruppenseite, auf der kein einziger Beitrag lesbar ist."""
    return []


def test_eine_unlesbare_gruppe_wird_beiseitegelegt_nicht_erschoepft(monkeypatch):
    """Der oertliche Lauf: ``gruppe_beiseite``, ausdruecklich **nicht** ``erschoepft``."""
    monkeypatch.setattr(
        "fbgroups.automation.actions.fetch_top_posts", _keine_beitraege, raising=False
    )

    ergebnis = automatik.browser_schritt(
        None,
        None,
        "https://www.facebook.com/groups/668139805288848",
        "668139805288848",
        "Text mit {link}",
    )

    assert ergebnis.erfolg is False
    assert ergebnis.gruppe_beiseite is True, (
        "Ohne dieses Feld bleibt die Gruppe in der Arbeitsliste und verbraucht "
        "bei jedem Durchgang einen Kommentar-Takt."
    )
    assert ergebnis.erschoepft is False, (
        "'Erschoepft' waere ein Urteil ueber die Gruppe. Eine unlesbare Seite "
        "ist eine Aussage ueber unseren Zugang."
    )


def test_der_fernbetrieb_legt_sie_genauso_beiseite(monkeypatch):
    """Zwei Wege, eine Regel - sonst arbeitet der Server anders als der Rechner."""
    monkeypatch.setattr(
        "fbgroups.automation.actions.fetch_top_posts", _keine_beitraege, raising=False
    )

    ergebnis = automatik.browser_schritt_fern(
        None,
        "https://www.facebook.com/groups/668139805288848",
        "668139805288848",
        "Text mit {link}",
        [],
        {"erlaubnis": {"kommentare": True, "regeln_gelesen": True}},
    )

    assert ergebnis.erfolg is False
    assert ergebnis.gruppe_beiseite is True
    assert ergebnis.erschoepft is False


@pytest.mark.parametrize(
    "funktion",
    [automatik.browser_schritt, automatik.browser_schritt_fern],
)
def test_der_grund_steht_im_protokoll(monkeypatch, funktion):
    """Der Fehlertext bleibt - ohne ihn sieht der Uebersprung aus wie 'kein Anlass'."""
    monkeypatch.setattr(
        "fbgroups.automation.actions.fetch_top_posts", _keine_beitraege, raising=False
    )

    if funktion is automatik.browser_schritt:
        ergebnis = funktion(None, None, "https://x/groups/1", "g1", "Text {link}")
    else:
        ergebnis = funktion(
            None,
            "https://x/groups/1",
            "g1",
            "Text {link}",
            [],
            {"erlaubnis": {"kommentare": True, "regeln_gelesen": True}},
        )

    assert "keine Beitraege" in ergebnis.fehler
