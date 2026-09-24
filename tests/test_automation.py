"""Tests für die Automatisierung über Playwright.

Diese Tests prüfen, ob die Automatisierung (comment_on_post) korrekt
angebunden ist, ohne tatsächlich einen Browser zu öffnen. Automatisches
Posten ist seit dem 24.09.2026 entfernt (``post_to_group`` gibt es nicht mehr).
Geprüft wird:
1. Ein Beitrag wird nicht automatisch gesetzt - der Browser startet nicht.
2. Fehlschläge werden aufgezeichnet.
3. Erfolge werden aufgezeichnet, auch wenn die Kampagne im Moment pausiert ist.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fbgroups.marketing.arbeit import stelle_texte_bereit
from fbgroups.marketing.models import (
    Campaign,
    CampaignGroup,
    QueueZustand,
    Texttyp,
    VorschlagStatus,
)
from fbgroups.marketing.store import MarketingStore
from fbgroups.models import Group
from fbgroups.storage import SqliteStore

KAMPAGNE = "batreeq"
GRUPPEN = {
    "482910573829104": ("Syrer in Koeln", "FB-SYR-KLN-002", "Köln"),
    "739201847362915": ("Syrer in Berlin", "FB-SYR-BER-001", "Berlin"),
}


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
                    city=stadt,
                    audience_tags=["syrians"],
                )
                for gid, (name, _, stadt) in GRUPPEN.items()
            ]
        )
    with MarketingStore(pfad) as store:
        store.save_campaign(
            Campaign(
                campaign_id=KAMPAGNE,
                name="Batreeq",
                language="ar",
                audiences=["syrians"],
                landing_page="https://b-tarikak.de/",
            )
        )
        for gid, (_, code, _) in GRUPPEN.items():
            store.add_link(
                CampaignGroup(
                    campaign_id=KAMPAGNE,
                    group_id=gid,
                    tracking_code=code,
                    tracking_url=f"https://b-tarikak.de/r/{code}",
                )
            )
    return pfad


def _client(bestand, config, **kwargs):
    pytest.importorskip("fastapi", reason="nur mit dem optionalen web-Zusatz")
    from fastapi.testclient import TestClient

    from fbgroups.marketing.web import create_app
    return TestClient(create_app(config=config, db_path=bestand), **kwargs)


@patch("fbgroups.automation.actions.fetch_top_posts")
@patch("fbgroups.automation.browser.get_browser_context")
def test_fehlschlag_schreibt_einen_versuch(
    mock_context, mock_post, bestand: Path, config
) -> None:
    """Wenn die Automatisierung fehlschlägt, muss das dokumentiert werden."""
    # Texte bereitlegen
    gid = next(iter(GRUPPEN))
    with MarketingStore(bestand) as store:
        kampagne = store.load_campaign(KAMPAGNE)
        with SqliteStore(bestand) as g_store:
            gruppe = next((g for g in g_store.load_groups() if g.group_id == gid), None)
        stelle_texte_bereit(store, kampagne, gruppe, config)

    # Mock einstellen: Browser wirft eine Exception
    mock_context.return_value.__enter__.return_value = MagicMock()
    mock_post.side_effect = Exception("Playwright Timeout")

    client = _client(bestand, config)
    antwort = client.post(
        f"/arbeit/{KAMPAGNE}/vorschlag/auto",
        json={"group_id": gid, "nummer": 1, "texttyp": "kommentar"},
    )
    
    assert antwort.status_code == 200
    daten = antwort.json()
    assert daten["ok"] is False
    assert "Playwright Timeout" in daten["meldung"]
    
    # Pruefen, ob post_versuche aktualisiert wurde
    with MarketingStore(bestand) as store:
        versuche = store.versuche_for(KAMPAGNE, gid)
        assert len(versuche) == 1
        assert versuche[0].erfolg is False
        assert "Playwright Timeout" in versuche[0].fehler
        
        # Und der Stand auf FEHLGESCHLAGEN ging
        vorschlag = store.vorschlag(KAMPAGNE, gid, Texttyp.KOMMENTAR, 1)
        assert vorschlag.status == VorschlagStatus.FEHLGESCHLAGEN


@patch("fbgroups.automation.browser.get_browser_context")
def test_ein_beitrag_wird_nicht_automatisch_gesetzt(
    mock_context, bestand: Path, config
) -> None:
    """Automatisches Posten ist entfernt: Der Browser startet gar nicht erst."""
    gid = next(iter(GRUPPEN))
    client = _client(bestand, config)
    antwort = client.post(
        f"/arbeit/{KAMPAGNE}/vorschlag/auto",
        json={"group_id": gid, "nummer": 1, "texttyp": "post"},
    )

    assert antwort.status_code == 200
    daten = antwort.json()
    assert daten["ok"] is False
    mock_context.assert_not_called()
    with MarketingStore(bestand) as store:
        assert store.versuche_for(KAMPAGNE, gid) == []


def test_erfolg_wird_trotz_pause_gespeichert(bestand: Path, config) -> None:
    """Wenn ein Beitrag auf FB landet, muss er gespeichert werden, auch bei Pause."""
    gid = next(iter(GRUPPEN))
    with MarketingStore(bestand) as store:
        kampagne = store.load_campaign(KAMPAGNE)
        with SqliteStore(bestand) as g_store:
            gruppe = next((g for g in g_store.load_groups() if g.group_id == gid), None)
        stelle_texte_bereit(store, kampagne, gruppe, config)

    # 1. Wir starten den Request
    # 2. Im Request wird die DB geschlossen, Playwright laeuft
    # 3. Wir simulieren, dass Playwright lange braucht und inzwischen
    # jemand die Kampagne pausiert
    
    # Da wir das nicht asynchron mit echten Thread-Pausen testen wollen,
    # setzen wir einfach vorher die Kampagne auf pausiert, aber post_to_group gibt True zurueck.
    # ABER Moment: vorschlag_auto prueft PAUSIERT vor der READ-Phase!
    # Okay, wir setzen die Kampagne auf pausiert, ABER erst *nach* dem READ-Check.
    # Da das im synchronen Code schwer zu mocken ist, uebergehen wir den READ-Check,
    # indem wir store.queue_zustand IM Mock auf PAUSIERT setzen.
    
    # Alternativer Ansatz: Wir testen nur melde_vorschlag direkt, 
    # weil der Bug ja in melde_vorschlag war.
    from fbgroups.marketing.arbeit import Ergebnis, melde_vorschlag
    
    with MarketingStore(bestand) as store:
        store.set_queue_zustand(KAMPAGNE, QueueZustand.PAUSIERT)
        link = store.link_for(KAMPAGNE, gid)
        
        # Muss erfolgreich gespeichert werden
        ergebnis = melde_vorschlag(
            store, kampagne, link, Texttyp.POST, 1, Ergebnis(erfolg=True)
        )
        
        assert not isinstance(ergebnis, Exception)
        assert ergebnis.status == VorschlagStatus.VEROEFFENTLICHT


@patch("fbgroups.automation.actions.comment_on_post")
@patch("fbgroups.automation.actions.fetch_top_posts")
@patch("fbgroups.automation.browser.get_browser_context")
def test_der_kommentar_von_hand_geht_ohne_link_und_nie_ins_leere(
    mock_context, mock_fetch, mock_comment, bestand: Path, config, monkeypatch
) -> None:
    """``POST /arbeit/{k}/vorschlag/auto`` fuer einen Kommentar (23.09.2026).

    Zwei Zusicherungen:

    * **Kein Tracking-Link.** Auch dieser Weg setzt einen Kommentar ab; er
      traegt nur die freie Adresse der Landingpage, wie jeder Kommentar seit
      dem 23.09.2026.
    * **Kein Kommentar ins Leere.** Bis dahin stand ``comment_on_post`` eine
      Einrueckung zu weit links und lief auch dann, wenn alle Beitraege schon
      kommentiert waren - mit leerer Adresse.
    """
    from fbgroups.automation.actions import Kommentarausgang
    from fbgroups.urls import tracking_adresse_im_text

    monkeypatch.setitem(config.get("kaltmodus"), "aktiv", False)
    gid = next(iter(GRUPPEN))
    with MarketingStore(bestand) as store:
        kampagne = store.load_campaign(KAMPAGNE)
        with SqliteStore(bestand) as g_store:
            gruppe = next((g for g in g_store.load_groups() if g.group_id == gid), None)
        stelle_texte_bereit(store, kampagne, gruppe, config)

    beitrag = f"https://www.facebook.com/groups/{gid}/posts/1/"
    mock_context.return_value.__enter__.return_value = MagicMock()
    mock_fetch.return_value = [{"post_url": beitrag, "interactions": 1, "comments": 0}]
    mock_comment.return_value = Kommentarausgang(True)

    client = _client(bestand, config, headers={"Origin": "http://127.0.0.1:8090"})
    erste = client.post(
        f"/arbeit/{KAMPAGNE}/vorschlag/auto",
        json={"group_id": gid, "nummer": 1, "texttyp": "kommentar"},
    )

    assert erste.json()["ok"] is True, erste.json()
    _, adresse, text = mock_comment.call_args.args
    assert adresse == beitrag
    assert "{link}" not in text
    assert tracking_adresse_im_text(text) == ""
    schluss = "الرابط المباشر للتحميل موجود في البايو (أعلى الصفحة) 👇"
    assert text.endswith(f" {schluss}"), "der Schlusssatz"
    assert "https://" not in text, "keine Adresse mehr im Kommentar"
    assert "FB-SYR" not in text

    # Derselbe Beitrag ist jetzt kommentiert - ein zweiter Aufruf setzt nichts ab.
    mock_comment.reset_mock()
    client.post(
        f"/arbeit/{KAMPAGNE}/vorschlag/auto",
        json={"group_id": gid, "nummer": 2, "texttyp": "kommentar"},
    )
    mock_comment.assert_not_called()

