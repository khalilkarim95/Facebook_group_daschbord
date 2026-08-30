"""Tests für die Automatisierung über Playwright.

Diese Tests prüfen, ob die Automatisierung (post_to_group, comment_on_post)
korrekt angebunden ist, ohne tatsächlich einen Browser zu öffnen.
Geprüft wird:
1. Der Tageslimit-Zähler greift.
2. Fehlschläge werden aufgezeichnet.
3. Erfolge werden aufgezeichnet, auch wenn die Kampagne im Moment pausiert ist.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from fbgroups.marketing.store import MarketingStore
from fbgroups.marketing.models import Campaign, CampaignGroup, QueueZustand, VorschlagStatus, Texttyp
from fbgroups.storage import SqliteStore
from fbgroups.models import Group
from fbgroups.marketing.arbeit import stelle_texte_bereit

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


@patch("fbgroups.automation.actions.post_to_group")
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
        json={"group_id": gid, "nummer": 1, "texttyp": "post"},
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
        vorschlag = store.vorschlag(KAMPAGNE, gid, Texttyp.POST, 1)
        assert vorschlag.status == VorschlagStatus.FEHLGESCHLAGEN


@patch("fbgroups.automation.actions.post_to_group")
@patch("fbgroups.automation.browser.get_browser_context")
def test_tageslimit_blockiert_die_ausfuehrung(
    mock_context, mock_post, bestand: Path, config, monkeypatch
) -> None:
    """Wenn Kaltmodus zuschlägt, wird Playwright gar nicht erst gestartet."""
    # Kaltmodus auf 0 setzen
    monkeypatch.setitem(config.get("kaltmodus"), "aktiv", True)
    monkeypatch.setitem(config.get("kaltmodus"), "beitraege_pro_tag", 0)

    gid = next(iter(GRUPPEN))
    with MarketingStore(bestand) as store:
        kampagne = store.load_campaign(KAMPAGNE)
        with SqliteStore(bestand) as g_store:
            gruppe = next((g for g in g_store.load_groups() if g.group_id == gid), None)
        stelle_texte_bereit(store, kampagne, gruppe, config)

    client = _client(bestand, config)
    antwort = client.post(
        f"/arbeit/{KAMPAGNE}/vorschlag/auto",
        json={"group_id": gid, "nummer": 1, "texttyp": "post"},
    )
    
    assert antwort.status_code == 200
    daten = antwort.json()
    assert daten["ok"] is False
    assert "Tageslimit" in daten["meldung"]
    
    mock_post.assert_not_called()
    mock_context.assert_not_called()


@patch("fbgroups.automation.actions.post_to_group")
@patch("fbgroups.automation.browser.get_browser_context")
def test_erfolg_wird_trotz_pause_gespeichert(
    mock_context, mock_post, bestand: Path, config
) -> None:
    """Wenn ein Beitrag auf FB landet, muss er gespeichert werden, auch bei Pause."""
    gid = next(iter(GRUPPEN))
    with MarketingStore(bestand) as store:
        kampagne = store.load_campaign(KAMPAGNE)
        with SqliteStore(bestand) as g_store:
            gruppe = next((g for g in g_store.load_groups() if g.group_id == gid), None)
        stelle_texte_bereit(store, kampagne, gruppe, config)

    mock_context.return_value.__enter__.return_value = MagicMock()
    mock_post.return_value = True

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
    
    # Alternativer Ansatz: Wir testen nur melde_vorschlag direkt, weil der Bug ja in melde_vorschlag war.
    from fbgroups.marketing.arbeit import melde_vorschlag, Ergebnis
    
    with MarketingStore(bestand) as store:
        store.set_queue_zustand(KAMPAGNE, QueueZustand.PAUSIERT)
        link = store.link_for(KAMPAGNE, gid)
        
        # Muss erfolgreich gespeichert werden
        ergebnis = melde_vorschlag(
            store, kampagne, link, Texttyp.POST, 1, Ergebnis(erfolg=True)
        )
        
        assert not isinstance(ergebnis, Exception)
        assert ergebnis.status == VorschlagStatus.VEROEFFENTLICHT
