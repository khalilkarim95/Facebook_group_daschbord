"""Lesbare Adressen im Beitrag: b-tarikak.de/t/safar-sham-12 (23.09.2026).

Der Wunsch des Nutzers: Im Beitrag soll kein Code stehen, der wie einer
aussieht. Der lesbare Name ist ein **Deckname** wie der Kurzcode - gezaehlt
und ausgewertet wird weiter unter dem inneren Tracking-Code.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fbgroups.marketing.kurzcode import WOERTER, lesbarer_code
from fbgroups.marketing.models import Campaign, CampaignGroup, EventType, Texttyp
from fbgroups.marketing.store import MarketingStore
from fbgroups.models import Group
from fbgroups.storage import SqliteStore

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

KAMPAGNE = "batreeq"
BASIS = "https://b-tarikak.de/t"


def _bestand(tmp_path: Path, gruppen: tuple[str, ...], *, basis: str = BASIS) -> Path:
    pfad = tmp_path / "groups.sqlite"
    with SqliteStore(pfad) as s:
        s.upsert_groups(
            [
                Group(group_id=g, url_canonical=f"https://www.facebook.com/groups/{g}")
                for g in gruppen
            ]
        )
    with MarketingStore(pfad) as store:
        store.merke_link_basis(basis)
        store.save_campaign(
            Campaign(
                campaign_id=KAMPAGNE,
                name="Batreeq",
                language="ar",
                landing_page="https://b-tarikak.de/home",
                ziel="landing",
            )
        )
        for i, g in enumerate(gruppen, start=1):
            code = f"FB-GEN-DE-{i:03d}"
            store.add_link(
                CampaignGroup(
                    campaign_id=KAMPAGNE,
                    group_id=g,
                    tracking_code=code,
                    tracking_url=f"https://go.b-tarikak.de/r/{code}",
                )
            )
    return pfad


def test_der_name_ist_lesbar_und_stabil() -> None:
    name = lesbarer_code("FB-GEN-DE-005", "geheim")
    erstes, zweites, zahl = name.split("-")

    assert erstes in WOERTER and zweites in WOERTER and erstes != zweites
    assert 10 <= int(zahl) <= 99
    assert lesbarer_code("FB-GEN-DE-005", "geheim") == name, "gleiche Eingabe, gleicher Name"
    assert "FB" not in name.upper().split("-")


def test_eine_neue_zuordnung_bekommt_die_lesbare_adresse(tmp_path: Path) -> None:
    pfad = _bestand(tmp_path, ("111",))
    with MarketingStore(pfad) as store:
        link = store.link_for(KAMPAGNE, "111")

    assert link.public_url == f"{BASIS}/{link.public_code}"
    assert link.public_code.count("-") == 2
    assert link.tracking_code == "FB-GEN-DE-001", "der innere Code bleibt"


def test_ohne_basis_bleibt_es_beim_kurzcode(tmp_path: Path) -> None:
    pfad = _bestand(tmp_path, ("111",), basis="")
    with MarketingStore(pfad) as store:
        link = store.link_for(KAMPAGNE, "111")

    assert "/r/" in link.public_url
    assert "-" not in link.public_code


def test_die_adresse_unter_t_zaehlt_unter_dem_inneren_code(tmp_path: Path, config) -> None:
    from fbgroups.marketing.web import create_app

    pfad = _bestand(tmp_path, ("111",))
    with MarketingStore(pfad) as store:
        name = store.link_for(KAMPAGNE, "111").public_code
    client = TestClient(create_app(config=config, db_path=pfad), follow_redirects=False)

    antwort = client.get(f"/t/{name}", headers={"user-agent": "Mozilla/5.0 (Android)"})

    assert antwort.status_code == 302
    with MarketingStore(pfad) as store:
        zeilen = store.conn.execute("SELECT tracking_code FROM tracking_events")
        codes = [z["tracking_code"] for z in zeilen]
        assert store.event_counts().get(EventType.CLICK.value) == 1
    assert codes == ["FB-GEN-DE-001"]


def test_umgestellt_wird_nur_was_noch_nicht_veroeffentlicht_ist(tmp_path: Path) -> None:
    pfad = _bestand(tmp_path, ("111", "222"), basis="")
    with MarketingStore(pfad) as store:
        alt_111 = store.link_for(KAMPAGNE, "111").public_url
        alt_222 = store.link_for(KAMPAGNE, "222").public_url
        # 222 hat einen veroeffentlichten Kommentar - seine Adresse steht in einer Gruppe.
        store.setze_erzeugten_vorschlag(
            KAMPAGNE, "222", Texttyp.KOMMENTAR, 1, text="Text\n{link}", vorlage_key="k"
        )
        store.conn.execute(
            "UPDATE campaign_group_texte SET status = 'veroeffentlicht' WHERE group_id = '222'"
        )
        store.conn.commit()

        store.merke_link_basis(BASIS)
        umgestellt = store.lesbar_machen()

        neu_111 = store.link_for(KAMPAGNE, "111").public_url
        neu_222 = store.link_for(KAMPAGNE, "222").public_url

    assert umgestellt == 1
    assert neu_111.startswith(BASIS + "/") and neu_111 != alt_111
    assert neu_222 == alt_222, "veroeffentlicht - die Adresse bleibt"
