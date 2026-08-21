"""Tests fuer Redirect-Dienst, Empfehlungen, Praemien und Auswertung.

Der Dienst wird gegen eine eigene Datenbank im tmp-Verzeichnis gefahren - der
echte Bestand wird dabei nie geoeffnet.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fbgroups.marketing.analytics import funnel, kennzahlen, top_groups
from fbgroups.marketing.models import (
    Campaign,
    CampaignGroup,
    EventType,
    ReferralStatus,
    RewardStatus,
    TrackingEvent,
)
from fbgroups.marketing.referral import (
    build_referral_code,
    code_fuer_benutzer,
    lege_empfehlung_an,
    pruefe_empfehlung,
    setze_status,
)
from fbgroups.marketing.rewards import bewerte_benutzer, load_reward_rules, zaehle_empfehlungen
from fbgroups.marketing.store import MarketingStore
from fbgroups.models import Group
from fbgroups.storage import SqliteStore

fastapi = pytest.importorskip("fastapi", reason="nur mit dem optionalen web-Zusatz")
from fastapi.testclient import TestClient  # noqa: E402

REAL_ID_A = "482910573829104"
REAL_ID_B = "739201847362915"
CODE_A = "FB-SYR-BER-001"


@pytest.fixture()
def bestand(tmp_path: Path) -> Path:
    """Datenbank mit zwei Gruppen, einer Kampagne und zwei Tracking-Codes."""
    pfad = tmp_path / "groups.sqlite"
    with SqliteStore(pfad) as store:
        store.upsert_groups(
            [
                Group(
                    group_id=REAL_ID_A,
                    url_canonical=f"https://www.facebook.com/groups/{REAL_ID_A}",
                    name="Syrer in Berlin",
                ),
                Group(
                    group_id=REAL_ID_B,
                    url_canonical=f"https://www.facebook.com/groups/{REAL_ID_B}",
                    name="Araber in Hamburg",
                ),
            ]
        )
    with MarketingStore(pfad) as store:
        store.save_campaign(
            Campaign(
                campaign_id="batreeq",
                name="Batreeq Syrian Germany",
                landing_page="https://batreeq.example/start",
            )
        )
        store.add_link(
            CampaignGroup(campaign_id="batreeq", group_id=REAL_ID_A, tracking_code=CODE_A)
        )
        store.add_link(
            CampaignGroup(
                campaign_id="batreeq", group_id=REAL_ID_B, tracking_code="FB-ARA-HAM-001"
            )
        )
    return pfad


@pytest.fixture()
def client(bestand: Path, config) -> TestClient:
    from fbgroups.marketing.web import create_app

    return TestClient(create_app(config=config, db_path=bestand), follow_redirects=False)


# --- Redirect-Dienst ---------------------------------------------------

def test_klick_leitet_weiter_und_wird_gezaehlt(client: TestClient, bestand: Path) -> None:
    antwort = client.get(f"/r/{CODE_A}")

    assert antwort.status_code == 302
    assert antwort.headers["location"].startswith("https://batreeq.example/start")
    assert f"ref={CODE_A}" in antwort.headers["location"]

    with MarketingStore(bestand) as store:
        assert store.event_counts().get("click") == 1


def test_klick_kennt_gruppe_und_kampagne(client: TestClient, bestand: Path) -> None:
    """Der Sinn der ganzen Kette: vom Klick zurueck zur Facebook-Gruppe."""
    client.get(f"/r/{CODE_A}")

    with MarketingStore(bestand) as store:
        ereignis = store.conn.execute("SELECT * FROM tracking_events").fetchone()

    assert ereignis["campaign_id"] == "batreeq"
    assert ereignis["group_id"] == REAL_ID_A


def test_derselbe_besucher_zaehlt_am_selben_tag_nur_einmal(
    client: TestClient, bestand: Path
) -> None:
    for _ in range(4):
        client.get(f"/r/{CODE_A}")

    with MarketingStore(bestand) as store:
        assert store.event_counts().get("click") == 1


def test_linkvorschau_von_facebook_zaehlt_nicht_als_klick(
    client: TestClient, bestand: Path
) -> None:
    """Facebook ruft einen frisch geposteten Link selbst ab, um Titel, Bild und
    Beschreibung fuer die Vorschaukarte zu holen - das ist kein Mensch, der
    geklickt hat. Ein Livetest zeigte 25 Klicks fuer einen einzigen Menschen,
    alle mit diesem User-Agent."""
    antwort = client.get(
        f"/r/{CODE_A}",
        headers={
            "user-agent": (
                "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"
            )
        },
    )

    assert antwort.status_code == 302  # die Vorschau bekommt ihr Ziel trotzdem
    with MarketingStore(bestand) as store:
        assert store.event_counts().get("click") is None


def test_weitere_vorschau_crawler_zaehlen_auch_nicht(client: TestClient, bestand: Path) -> None:
    for user_agent in (
        "WhatsApp/2.24.1 A",
        "Mozilla/5.0 (compatible; Discordbot/2.0; +https://discordapp.com)",
        "TelegramBot (like TwitterBot)",
    ):
        client.get(f"/r/{CODE_A}", headers={"user-agent": user_agent})

    with MarketingStore(bestand) as store:
        assert store.event_counts().get("click") is None


def test_echter_klick_zaehlt_auch_nach_einer_linkvorschau(
    client: TestClient, bestand: Path
) -> None:
    """Die Vorschau darf einen echten Klick direkt danach nicht verdecken."""
    client.get(f"/r/{CODE_A}", headers={"user-agent": "facebookexternalhit/1.1"})
    client.get(
        f"/r/{CODE_A}",
        headers={"user-agent": "Mozilla/5.0 (Linux; Android 14) Chrome/128.0"},
    )

    with MarketingStore(bestand) as store:
        assert store.event_counts().get("click") == 1


def test_keine_ip_adresse_im_bestand(client: TestClient, bestand: Path) -> None:
    """Gespeichert wird ein taeglich wechselnder Pruefwert, keine Adresse."""
    client.get(f"/r/{CODE_A}", headers={"user-agent": "Testbrowser"})

    with MarketingStore(bestand) as store:
        ereignis = store.conn.execute("SELECT * FROM tracking_events").fetchone()

    inhalt = " ".join(str(wert) for wert in tuple(ereignis))
    assert "testclient" not in inhalt.lower()      # Adresse des TestClients
    assert "Testbrowser" not in inhalt
    assert len(ereignis["visitor_hash"]) == 16


def test_unbekannter_code_wird_nicht_stillschweigend_weitergeleitet(client: TestClient) -> None:
    assert client.get("/r/FB-GIBTS-NICHT-999").status_code == 404


def test_healthz(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}


# --- Meldungen der Zielanwendung ---------------------------------------

def test_registrierung_erzeugt_ereignis_und_empfehlungscode(client: TestClient) -> None:
    antwort = client.post(
        "/events",
        json={"event_type": "registration", "user_ref": "u1", "tracking_code": CODE_A},
    )

    daten = antwort.json()
    assert antwort.status_code == 200
    assert daten["gespeichert"] == "registration"
    assert daten["referral_code"].startswith("BTQ-")


def test_meldung_ordnet_der_gruppe_zu(client: TestClient, bestand: Path) -> None:
    client.post(
        "/events",
        json={"event_type": "registration", "user_ref": "u1", "tracking_code": CODE_A},
    )
    with MarketingStore(bestand) as store:
        zeilen = top_groups(store, {REAL_ID_A: "Syrer in Berlin"})

    assert zeilen[0].schluessel == REAL_ID_A
    assert zeilen[0].registrations == 1


def test_ganze_kette_vom_klick_bis_zur_conversion(client: TestClient, bestand: Path) -> None:
    client.get(f"/r/{CODE_A}")
    for event_type in ("landing_visit", "registration", "activation", "qualified", "conversion"):
        client.post(
            "/events",
            json={"event_type": event_type, "user_ref": "u1", "tracking_code": CODE_A},
        )

    with MarketingStore(bestand) as store:
        stufen = dict((t.value, anzahl) for t, anzahl, _ in funnel(store))
        zahlen = kennzahlen(store)

    assert stufen == {
        "click": 1,
        "landing_visit": 1,
        "registration": 1,
        "activation": 1,
        "qualified": 1,
        "conversion": 1,
    }
    assert zahlen["conversions"] == 1


def test_spaetere_stufen_erben_die_gruppe_des_benutzers(
    client: TestClient, bestand: Path
) -> None:
    """Die Kernfrage: Welche Gruppe bringt *qualifizierte* Benutzer?

    Die Zielanwendung kennt beim Qualifizieren nur noch ihren Benutzer, nicht
    mehr den Tracking-Code. Ohne Erbschaft stuende die Stufe ohne Gruppe da.
    """
    client.post(
        "/events",
        json={"event_type": "registration", "user_ref": "u1", "tracking_code": CODE_A},
    )
    client.post("/events", json={"event_type": "qualified", "user_ref": "u1"})

    with MarketingStore(bestand) as store:
        zeile = next(z for z in top_groups(store, {}) if z.schluessel == REAL_ID_A)

    assert zeile.qualified == 1


def test_erbschaft_nimmt_den_ersten_fund(client: TestClient, bestand: Path) -> None:
    """Gebracht hat den Menschen die erste Gruppe, nicht eine spaetere."""
    client.post(
        "/events",
        json={"event_type": "registration", "user_ref": "u1", "tracking_code": CODE_A},
    )
    client.post(
        "/events",
        json={"event_type": "activation", "user_ref": "u1", "tracking_code": "FB-ARA-HAM-001"},
    )
    client.post("/events", json={"event_type": "conversion", "user_ref": "u1"})

    with MarketingStore(bestand) as store:
        zeile = next(z for z in top_groups(store, {}) if z.schluessel == REAL_ID_A)

    assert zeile.conversions == 1


def test_conversion_rate_ohne_klicks_ist_keine_null(bestand: Path) -> None:
    """Eine Quote ohne Grundgesamtheit gibt es nicht."""
    with MarketingStore(bestand) as store:
        store.record_event(
            TrackingEvent(group_id=REAL_ID_A, event_type=EventType.REGISTRATION)
        )
        zeile = top_groups(store, {})[0]

    assert zeile.clicks == 0
    assert zeile.conversion_rate is None


# --- Empfehlungen ------------------------------------------------------

def test_selbst_empfehlung_wird_abgelehnt(bestand: Path, config) -> None:
    with MarketingStore(bestand) as store:
        code = code_fuer_benutzer(store, config, "u1")
        entscheidung = pruefe_empfehlung(store, code, "u1")

    assert not entscheidung.angenommen
    assert "Selbst-Empfehlung" in entscheidung.grund


def test_zweiter_werber_wird_abgewiesen_und_zur_pruefung_gestellt(bestand: Path, config) -> None:
    with MarketingStore(bestand) as store:
        code_a = code_fuer_benutzer(store, config, "werber-a")
        code_b = code_fuer_benutzer(store, config, "werber-b")

        referral, _ = lege_empfehlung_an(store, code_a, "neuer-nutzer")
        assert referral is not None

        _zweiter, entscheidung = lege_empfehlung_an(store, code_b, "neuer-nutzer")

    assert not entscheidung.angenommen
    assert entscheidung.status is ReferralStatus.REVIEW


def test_dieselbe_empfehlung_zweimal_bleibt_eine(bestand: Path, config) -> None:
    with MarketingStore(bestand) as store:
        code = code_fuer_benutzer(store, config, "werber")
        lege_empfehlung_an(store, code, "geworbener")
        _zweiter, entscheidung = lege_empfehlung_an(store, code, "geworbener")
        anzahl = len(store.referrals_of("werber"))

    assert not entscheidung.angenommen
    assert anzahl == 1


def test_empfehlung_erst_nach_registrierung(bestand: Path, config) -> None:
    """Ohne Benutzerkennung gibt es keine Empfehlung - die entsteht erst dort."""
    with MarketingStore(bestand) as store:
        code = code_fuer_benutzer(store, config, "werber")
        entscheidung = pruefe_empfehlung(store, code, "")

    assert not entscheidung.angenommen


def test_unbekannter_empfehlungscode(bestand: Path) -> None:
    with MarketingStore(bestand) as store:
        entscheidung = pruefe_empfehlung(store, "BTQ-XXXXXX", "u2")
    assert not entscheidung.angenommen


def test_status_faellt_nicht_zurueck(bestand: Path, config) -> None:
    """Eine nachklappernde Meldung darf niemanden zurueckstufen."""
    with MarketingStore(bestand) as store:
        code = code_fuer_benutzer(store, config, "werber")
        lege_empfehlung_an(store, code, "geworbener")
        setze_status(store, "geworbener", ReferralStatus.QUALIFIED)
        setze_status(store, "geworbener", ReferralStatus.REGISTERED)
        referral = store.referral_for_referred("geworbener")

    assert referral.status is ReferralStatus.QUALIFIED


def test_jede_ablehnung_steht_im_audit_log(bestand: Path, config) -> None:
    with MarketingStore(bestand) as store:
        code = code_fuer_benutzer(store, config, "u1")
        lege_empfehlung_an(store, code, "u1")            # Selbst-Empfehlung
        aktionen = [zeile["action"] for zeile in store.audit_log()]

    assert "referral_abgelehnt" in aktionen


def test_empfehlungscode_ist_stabil(bestand: Path, config) -> None:
    with MarketingStore(bestand) as store:
        erst = code_fuer_benutzer(store, config, "u1")
        zweit = code_fuer_benutzer(store, config, "u1")
    assert erst == zweit


def test_empfehlungscode_meidet_verwechselbare_zeichen(config) -> None:
    code = build_referral_code(config, vergeben=set())
    kern = code.split("-", 1)[1]
    assert not set(kern) & set("O0I1L")


def test_empfehlung_ueber_die_schnittstelle(client: TestClient, bestand: Path) -> None:
    werber = client.post(
        "/events", json={"event_type": "registration", "user_ref": "werber"}
    ).json()

    antwort = client.post(
        "/events",
        json={
            "event_type": "registration",
            "user_ref": "geworbener",
            "referral_code": werber["referral_code"],
            "tracking_code": CODE_A,
        },
    ).json()

    assert antwort["referral"] == "angenommen"

    with MarketingStore(bestand) as store:
        referral = store.referral_for_referred("geworbener")

    assert referral.referrer_user_ref == "werber"
    assert referral.status is ReferralStatus.REGISTERED
    assert referral.group_id == REAL_ID_A      # welche Gruppe den Werber gebracht hat


# --- Praemien ----------------------------------------------------------

def test_regeln_kommen_aus_der_konfiguration(config) -> None:
    regeln = load_reward_rules(config.root)
    assert regeln, "config/rewards.yaml sollte Regeln enthalten"
    assert regeln[0].threshold <= regeln[-1].threshold      # aufsteigend sortiert


def test_praemie_erst_ab_der_schwelle(bestand: Path, config) -> None:
    regeln = load_reward_rules(config.root)
    schwelle = min(r.threshold for r in regeln if r.active)

    with MarketingStore(bestand) as store:
        code = code_fuer_benutzer(store, config, "werber")
        for i in range(schwelle - 1):
            lege_empfehlung_an(store, code, f"geworbener-{i}")
            setze_status(store, f"geworbener-{i}", ReferralStatus.QUALIFIED)

        assert bewerte_benutzer(store, regeln, "werber") == []

        lege_empfehlung_an(store, code, "letzter")
        setze_status(store, "letzter", ReferralStatus.QUALIFIED)
        neu = bewerte_benutzer(store, regeln, "werber")

    assert len(neu) == 1
    assert neu[0].status is RewardStatus.EARNED


def test_praemie_wird_nicht_zweimal_vergeben(bestand: Path, config) -> None:
    regeln = load_reward_rules(config.root)
    schwelle = min(r.threshold for r in regeln if r.active)

    with MarketingStore(bestand) as store:
        code = code_fuer_benutzer(store, config, "werber")
        for i in range(schwelle):
            lege_empfehlung_an(store, code, f"g{i}")
            setze_status(store, f"g{i}", ReferralStatus.QUALIFIED)

        bewerte_benutzer(store, regeln, "werber")
        nochmal = bewerte_benutzer(store, regeln, "werber")
        anzahl = len(store.rewards_of("werber"))

    assert nochmal == []
    assert anzahl == 1


def test_converted_zaehlt_auch_als_qualified(bestand: Path, config) -> None:
    """Wer weiterkommt, darf seine Praemie nicht verlieren."""
    with MarketingStore(bestand) as store:
        code = code_fuer_benutzer(store, config, "werber")
        lege_empfehlung_an(store, code, "geworbener")
        setze_status(store, "geworbener", ReferralStatus.CONVERTED)
        referrals = store.referrals_of("werber")

    assert zaehle_empfehlungen(referrals, "qualified") == 1


def test_praemie_ueber_die_schnittstelle(client: TestClient, bestand: Path, config) -> None:
    """Der ganze Weg: Registrierung, Empfehlung, Qualifikation, Praemie."""
    regeln = load_reward_rules(config.root)
    schwelle = min(r.threshold for r in regeln if r.active)

    werber = client.post(
        "/events", json={"event_type": "registration", "user_ref": "werber"}
    ).json()

    for i in range(schwelle):
        client.post(
            "/events",
            json={
                "event_type": "registration",
                "user_ref": f"g{i}",
                "referral_code": werber["referral_code"],
            },
        )
        antwort = client.post(
            "/events", json={"event_type": "qualified", "user_ref": f"g{i}"}
        ).json()

    assert "rewards_neu" in antwort

    stand = client.get("/referral/werber").json()
    assert stand["referrals"]["qualified"] == schwelle
    assert len(stand["rewards"]) == 1


# --- Schutz von POST /events -------------------------------------------

def test_ohne_schluessel_bleibt_der_weg_offen(client: TestClient) -> None:
    """Ohne ``EVENTS_TOKEN`` aendert sich nichts.

    Der Entwicklungsfall soll ohne Einrichtung laufen; im Betrieb steht der Weg
    dann hinter einem Proxy, der nur den eigenen Rechner durchlaesst.
    """
    antwort = client.post("/events", json={"event_type": "registration", "user_ref": "u1"})

    assert antwort.status_code == 200


def test_mit_schluessel_wird_geprueft(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Weg schreibt Registrierungen, Empfehlungen und damit Praemien.

    Ohne Pruefung koennte jeder, der die Adresse kennt, Praemien ausloesen.
    """
    monkeypatch.setenv("EVENTS_TOKEN", "geheim")

    ohne = client.post("/events", json={"event_type": "registration", "user_ref": "u2"})
    falsch = client.post(
        "/events",
        json={"event_type": "registration", "user_ref": "u2"},
        headers={"X-Events-Token": "daneben"},
    )
    richtig = client.post(
        "/events",
        json={"event_type": "registration", "user_ref": "u2"},
        headers={"X-Events-Token": "geheim"},
    )

    assert ohne.status_code == 401
    assert falsch.status_code == 401
    assert richtig.status_code == 200


def test_abgewiesenes_ereignis_wird_nicht_gespeichert(
    client: TestClient, bestand: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ein 401 darf nichts hinterlassen - sonst zaehlte der Trichter Fremdes."""
    monkeypatch.setenv("EVENTS_TOKEN", "geheim")
    client.post("/events", json={"event_type": "conversion", "user_ref": "eindringling"})

    with MarketingStore(bestand) as store:
        treffer = store.conn.execute(
            "SELECT COUNT(*) FROM tracking_events WHERE user_ref = ?", ("eindringling",)
        ).fetchone()[0]

    assert treffer == 0


def test_empfehlungsstand_steht_hinter_demselben_schluessel(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Weg gibt Auskunft ueber Menschen - er borgt seinen Schutz nicht.

    Bisher trug ihn allein nginx, der ihn nicht nach aussen durchlaesst. Wer
    diesen Block einmal um ein ``location /`` erweitert, gaebe damit den
    Empfehlungsstand jedes Benutzers heraus, dessen Kennung jemand raet - ohne
    dass es an dieser Datei sichtbar geworden waere.
    """
    monkeypatch.setenv("EVENTS_TOKEN", "geheim")

    ohne = client.get("/referral/werber")
    richtig = client.get("/referral/werber", headers={"X-Events-Token": "geheim"})

    assert ohne.status_code == 401
    assert richtig.status_code == 200


def test_empfehlungsstand_ohne_schluessel_bleibt_offen(client: TestClient) -> None:
    """Ohne ``EVENTS_TOKEN`` aendert sich nichts - derselbe Entwicklungsfall."""
    assert client.get("/referral/werber").status_code == 200
