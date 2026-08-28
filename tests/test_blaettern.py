"""Durch die **Gruppen** blaettern - und die Fassungen einer Gruppe wechseln.

Zwei Navigationen, die nichts miteinander zu tun haben, und genau das ist der
Punkt: Wer Fassung 3 ansieht, bleibt in derselben Gruppe; wer die Gruppe
wechselt, bekommt dort wieder deren eigene fuenf und fuenf.

Der wichtigste Test der Datei ist ``test_blaettern_faengt_keinen_versuch_an``.
Vorher fuehrte der Weg zur naechsten Gruppe ausschliesslich darueber, den
aktuellen Beitrag zu **melden** - und "veroeffentlicht" ist eine Aussage ueber
einen Beitrag, den es in dem Moment noch gar nicht gibt.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fbgroups.marketing.arbeit import arbeitsreihenfolge
from fbgroups.marketing.models import Campaign, CampaignGroup, Texttyp, VorschlagStatus
from fbgroups.marketing.store import MarketingStore
from fbgroups.marketing.web import create_app
from fbgroups.models import Group
from fbgroups.storage import SqliteStore

KAMPAGNE = "k"
ANZAHL = 4


@pytest.fixture()
def bestand(tmp_path: Path) -> Path:
    pfad = tmp_path / "groups.sqlite"
    gruppen = [
        Group(
            group_id=f"10000000000000{i}",
            url_canonical=f"https://www.facebook.com/groups/{i}",
            name=f"Gruppe {i}",
            city="Bonn",
            audience_tags=["syrians"],
            category="community",
            # Absteigend, damit die Rangfolge vorhersagbar ist: Gruppe 1 steht
            # oben. Sonst entschiede bei Gleichstand die Ordnung im Bestand,
            # und ein Test ueber "die naechste Gruppe" pruefte den Zufall.
            score=90.0 - i,
            score_max=100.0,
        )
        for i in range(1, ANZAHL + 1)
    ]
    with SqliteStore(pfad) as store:
        store.upsert_groups(gruppen)
    with MarketingStore(pfad) as store:
        store.save_campaign(
            Campaign(
                campaign_id=KAMPAGNE,
                name="Test",
                language="ar",
                audiences=["syrians"],
                landing_page="https://b-tarikak.de/",
                kommentare=True,
            )
        )
        for i, gruppe in enumerate(gruppen, 1):
            code = f"FB-SYR-BON-00{i}"
            store.add_link(
                CampaignGroup(
                    campaign_id=KAMPAGNE,
                    group_id=gruppe.group_id,
                    tracking_code=code,
                    tracking_url=f"https://go.b-tarikak.de/r/{code}",
                )
            )
    return pfad


@pytest.fixture()
def client(bestand: Path, config) -> TestClient:
    klient = TestClient(create_app(config=config, db_path=bestand))
    assert klient.post(
        f"/kampagnen/{KAMPAGNE}/vorbereiten", json={"schritt": "text"}
    ).status_code == 200
    return klient


def _reihe(bestand: Path) -> list[str]:
    with SqliteStore(bestand) as gruppen_store:
        gruppen = {g.group_id: g for g in gruppen_store.load_groups()}
    with MarketingStore(bestand) as store:
        return [link.group_id for link in arbeitsreihenfolge(store, KAMPAGNE, gruppen)]


# --- Blaettern -------------------------------------------------------------


def test_blaettern_faengt_keinen_versuch_an(client: TestClient, bestand: Path) -> None:
    """Die Zusicherung: Nachsehen ist keine Arbeit.

    Ohne sie stuende die Gruppe hinterher als 'processing' da, ohne dass
    jemand etwas geschrieben hat.
    """
    for nummer in range(1, ANZAHL + 1):
        assert client.get(f"/arbeit/{KAMPAGNE}?gruppe={nummer}").status_code == 200

    with MarketingStore(bestand) as store:
        assert store.offene_versuche(KAMPAGNE) == []


def test_die_seite_nennt_nummer_und_gesamtzahl(client: TestClient) -> None:
    seite = client.get(f"/arbeit/{KAMPAGNE}?gruppe=2").text
    assert "Gruppe <b>2</b>" in seite
    assert f"von {ANZAHL}" in seite


def test_jede_nummer_fuehrt_auf_ihre_gruppe(client: TestClient, bestand: Path) -> None:
    """Die Nummer in der Adresse und die in der Auswahl meinen dieselbe Gruppe."""
    reihe = _reihe(bestand)
    with MarketingStore(bestand) as store:
        codes = {
            link.group_id: link.tracking_code
            for link in store.links_for_campaign(KAMPAGNE)
        }

    for nummer, gid in enumerate(reihe, 1):
        seite = client.get(f"/arbeit/{KAMPAGNE}?gruppe={nummer}").text
        assert codes[gid] in seite


def test_hinter_das_ende_geblaettert_fuehrt_zur_ersten(bestand: Path, config) -> None:
    """Die Liste wird kuerzer, waehrend man darin liest - keine Fehlerseite."""
    klient = TestClient(
        create_app(config=config, db_path=bestand), follow_redirects=False
    )

    antwort = klient.get(f"/arbeit/{KAMPAGNE}?gruppe={ANZAHL + 5}")

    assert antwort.status_code == 303
    assert antwort.headers["location"] == f"/arbeit/{KAMPAGNE}"


def test_die_gruppenkennung_geht_vor_die_nummer(client: TestClient, bestand: Path) -> None:
    """Wer eine bestimmte Gruppe meint, meint sie auch nach einer Neubewertung."""
    gid = _reihe(bestand)[-1]
    with MarketingStore(bestand) as store:
        code = store.link_for(KAMPAGNE, gid).tracking_code

    seite = client.get(f"/arbeit/{KAMPAGNE}?gruppe=1&group_id={gid}").text

    assert code in seite


def test_die_merkmale_der_gruppe_stehen_auf_der_seite(client: TestClient) -> None:
    """Wer 300 Gruppen bearbeitet, sieht sonst immer denselben Bildschirm."""
    seite = client.get(f"/arbeit/{KAMPAGNE}").text
    assert "Bonn" in seite
    assert "Score" in seite


def test_die_auswahl_traegt_jede_gruppe(client: TestClient) -> None:
    """Namen sind das, wonach man eine Gruppe sucht - nicht Nummern."""
    seite = client.get(f"/arbeit/{KAMPAGNE}").text
    for i in range(1, ANZAHL + 1):
        assert f"Gruppe {i}" in seite


# --- Die Fassungen einer Gruppe -------------------------------------------


def test_alle_fassungen_stehen_auf_der_seite(client: TestClient, bestand: Path) -> None:
    """Blaettern zwischen ihnen ist ein Tausch im Browser und keine Anfrage.

    Deshalb kommen sie mit dem ersten Aufruf mit - sonst laedt die Seite bei
    jedem Wechsel zwischen fuenf Fassungen neu.
    """
    gid = _reihe(bestand)[0]
    seite = client.get(f"/arbeit/{KAMPAGNE}").text
    with MarketingStore(bestand) as store:
        fassungen = store.vorschlaege(KAMPAGNE, gid, Texttyp.POST)

    assert len(fassungen) > 1
    for fassung in fassungen:
        assert f"ar/post/mit_stadt/{fassung.vorlage_key.rsplit('/', 1)[-1]}" in seite


def test_die_nummernleiste_traegt_jede_fassung(client: TestClient, bestand: Path) -> None:
    gid = _reihe(bestand)[0]
    with MarketingStore(bestand) as store:
        anzahl = len(store.vorschlaege(KAMPAGNE, gid, Texttyp.POST))

    seite = client.get(f"/arbeit/{KAMPAGNE}").text

    for nummer in range(1, anzahl + 1):
        assert f"data-nummer='{nummer}'" in seite


def test_der_wechsel_der_gruppe_holt_deren_eigene_fassungen(
    client: TestClient, bestand: Path
) -> None:
    """"dort wieder deren eigene Liste von 5 Posts und 5 Kommentaren"."""
    erste = client.get(f"/arbeit/{KAMPAGNE}?gruppe=1").text
    zweite = client.get(f"/arbeit/{KAMPAGNE}?gruppe=2").text

    reihe = _reihe(bestand)
    with MarketingStore(bestand) as store:
        codes = [store.link_for(KAMPAGNE, gid).tracking_code for gid in reihe[:2]]

    assert codes[0] in erste and codes[1] not in erste
    assert codes[1] in zweite and codes[0] not in zweite


# --- Der Editor ------------------------------------------------------------


def test_der_editor_speichert_genau_eine_fassung(
    client: TestClient, bestand: Path
) -> None:
    gid = _reihe(bestand)[0]
    with MarketingStore(bestand) as store:
        vorher = {
            v.nummer: v.text for v in store.vorschlaege(KAMPAGNE, gid, Texttyp.POST)
        }

    antwort = client.post(
        f"/arbeit/{KAMPAGNE}/vorschlag/text",
        json={"group_id": gid, "nummer": 3, "text": "Von Hand {link}"},
    )

    assert antwort.json()["ok"] is True
    with MarketingStore(bestand) as store:
        nachher = {
            v.nummer: v.text for v in store.vorschlaege(KAMPAGNE, gid, Texttyp.POST)
        }
    assert nachher[3] == "Von Hand {link}"
    assert {n: t for n, t in nachher.items() if n != 3} == {
        n: t for n, t in vorher.items() if n != 3
    }


def test_der_editor_weist_einen_text_ohne_link_ab(
    client: TestClient, bestand: Path
) -> None:
    gid = _reihe(bestand)[0]
    antwort = client.post(
        f"/arbeit/{KAMPAGNE}/vorschlag/text",
        json={"group_id": gid, "nummer": 1, "text": "Ohne Platzhalter"},
    )
    assert antwort.json()["ok"] is False
    assert "{link}" in antwort.json()["meldung"]


def test_der_editor_weist_eine_ausgeschriebene_adresse_ab(
    client: TestClient, bestand: Path
) -> None:
    """Ein Beitrag, der richtig aussieht und dessen Gruppe nie einen Klick bekommt."""
    gid = _reihe(bestand)[0]
    antwort = client.post(
        f"/arbeit/{KAMPAGNE}/vorschlag/text",
        json={
            "group_id": gid,
            "nummer": 1,
            "text": "Hier: https://go.b-tarikak.de/r/FB-SYR-BON-001 und {link}",
        },
    )
    assert antwort.json()["ok"] is False
    assert "Adresse" in antwort.json()["meldung"]


def test_der_editor_weist_zwei_platzhalter_ab(client: TestClient, bestand: Path) -> None:
    gid = _reihe(bestand)[0]
    antwort = client.post(
        f"/arbeit/{KAMPAGNE}/vorschlag/text",
        json={"group_id": gid, "nummer": 1, "text": "{link} und nochmal {link}"},
    )
    assert antwort.json()["ok"] is False


def test_der_editor_zeigt_den_gespeicherten_text_mit_platzhalter(
    client: TestClient,
) -> None:
    """Nicht den angezeigten mit eingesetztem Link.

    Wer hier eine Adresse hineinschriebe, haette einen Beitrag, dessen Gruppe
    nie einen Klick gutgeschrieben bekommt.
    """
    seite = client.get(f"/arbeit/{KAMPAGNE}").text
    feld = seite.split("id='feld-post'")[1].split("</textarea>")[0]
    assert "{link}" in feld


def test_der_editor_ist_von_aussen_nicht_erreichbar(bestand: Path, config) -> None:
    fremd = TestClient(
        create_app(config=config, db_path=bestand), client=("203.0.113.7", 44321)
    )
    antwort = fremd.post(
        f"/arbeit/{KAMPAGNE}/vorschlag/text",
        json={"group_id": "100000000000001", "nummer": 1, "text": "Fremd {link}"},
    )
    assert antwort.status_code == 404


def test_blaettern_ist_von_aussen_nicht_moeglich(bestand: Path, config) -> None:
    fremd = TestClient(
        create_app(config=config, db_path=bestand), client=("203.0.113.7", 44321)
    )
    assert fremd.get(f"/arbeit/{KAMPAGNE}?gruppe=2").status_code == 404


# --- Melden bleibt stehen --------------------------------------------------


def test_nach_veroeffentlicht_bleibt_die_gruppe_dieselbe(
    client: TestClient, bestand: Path
) -> None:
    """Der Grundsatz: "Ich entscheide selbst, was ich als Naechstes mache."

    Frueher endete die Meldung in einer 303, und die holte die naechste
    Gruppe. Jetzt aendert sich genau eine Stelle - der Stand dieser Fassung.
    """
    reihe = _reihe(bestand)
    gid = reihe[0]

    antwort = client.post(
        f"/arbeit/{KAMPAGNE}/vorschlag/ergebnis",
        json={"group_id": gid, "nummer": 1, "ausgang": "veroeffentlicht"},
    )

    assert antwort.status_code == 200
    assert antwort.json()["stand"] == "veroeffentlicht"
    # Und die Reihenfolge der Gruppen ist unveraendert - die erledigte
    # verschwindet nicht, sie hat vier weitere Fassungen.
    assert _reihe(bestand) == reihe


def test_ein_fehlschlag_haelt_seinen_grund_fest(client: TestClient, bestand: Path) -> None:
    gid = _reihe(bestand)[0]

    client.post(
        f"/arbeit/{KAMPAGNE}/vorschlag/ergebnis",
        json={
            "group_id": gid,
            "nummer": 2,
            "ausgang": "fehlgeschlagen",
            "fehler": "erlaubt keine Links",
        },
    )

    with MarketingStore(bestand) as store:
        vorschlag = store.vorschlag(KAMPAGNE, gid, Texttyp.POST, 2)
    assert vorschlag.status is VorschlagStatus.FEHLGESCHLAGEN
    assert vorschlag.fehler == "erlaubt keine Links"


def test_ohne_grund_steht_wenigstens_dass_keiner_genannt_wurde(
    client: TestClient, bestand: Path
) -> None:
    """Ohne Grund ist ein Fehlschlag spaeter nicht zu deuten."""
    gid = _reihe(bestand)[0]

    client.post(
        f"/arbeit/{KAMPAGNE}/vorschlag/ergebnis",
        json={"group_id": gid, "nummer": 1, "ausgang": "fehlgeschlagen"},
    )

    with MarketingStore(bestand) as store:
        assert store.vorschlag(KAMPAGNE, gid, Texttyp.POST, 1).fehler == "ohne Angabe"


def test_ohne_zuordnungen_nennt_die_seite_den_grund(tmp_path: Path, config) -> None:
    """Der haeufigste Griff daneben bekommt Klartext."""
    pfad = tmp_path / "leer.sqlite"
    with SqliteStore(pfad):
        pass
    with MarketingStore(pfad) as store:
        store.save_campaign(Campaign(campaign_id="leer", name="Leer"))

    seite = TestClient(create_app(config=config, db_path=pfad)).get("/arbeit/leer").text

    assert "keine Gruppen zugeordnet" in seite
    assert "campaign sync" in seite
