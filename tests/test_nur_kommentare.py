"""Nur Kommentare, ohne Link - und die Grenzen des 23.09.2026.

Die Anweisung des Nutzers in fuenf Saetzen:

1. Kein automatisches Posten mehr - der Lauf verarbeitet nur Kommentare.
2. Bis zu 20 Kommentare je Gruppe und Tag (die Runde bleibt: einer je Runde).
3. Das Kampagnenziel folgt aus 12 Stunden und 3 Minuten Abstand: 240.
4. **Kein Tracking-Link in einem Kommentar** - weder ``/r/<code>`` noch
   ``/t/<name>``. Das Tracking ausserhalb bleibt.
5. Die bestehenden Kommentarvorlagen funktionieren weiter.

Dazu, noch am selben Tag: Am Ende jedes Kommentars steht die **freie**
Adresse der Landingpage (``marketing.kommentar_adresse``,
``https://b-tarikak.de/home``) - ohne Code, sie zaehlt nichts. Das Beispiel
des Nutzers:

    حبيت شاركها معكم، شفت تطبيق بطريقك ... من سوريا. https://b-tarikak.de/home

Seit dem 24.09.2026 steht dort statt der Adresse ein Satz
(``marketing.kommentar_schluss``), und ein Bild traegt die Adresse
(``marketing.kommentar_bild``) - die ausgeschriebene Adresse machte bei
Facebook Probleme. Der Weg ist derselbe; ``SCHLUSS`` ist, was jetzt ankommt.

Der Link wird an **drei** Stellen ferngehalten, und jede hat ihren Test:
dort, wo der Text entsteht (``beitrag.mit_link`` mit ``texttyp``), dort, wo
der Lauf ihn waehlt (``automatik.entscheide_und_kommentiere``), und
unmittelbar vor dem Absenden (``actions.comment_on_post``).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from fbgroups.automation.actions import Kommentarausgang, comment_on_post
from fbgroups.marketing import automatik, grenzen, lauf
from fbgroups.marketing.beitrag import beitragstext, mit_link, ohne_link
from fbgroups.marketing.entscheidung import Anspruch, Erlaubnis
from fbgroups.marketing.lauf import Gruppenfortschritt, Kampagnenfortschritt
from fbgroups.marketing.models import (
    Campaign,
    CampaignGroup,
    CampaignStatus,
    GroupMarketing,
    MarketingStatus,
    PostStatus,
    Texttyp,
)
from fbgroups.marketing.store import MarketingStore
from fbgroups.models import Group
from fbgroups.storage import SqliteStore
from fbgroups.urls import adresse_im_text, tracking_adresse_im_text

KAMPAGNE = "nur-kommentare"
GRUPPEN = ["g1", "g2"]
TRACKING = "https://go.b-tarikak.de/r/FB-TST-BER-001"
OEFFENTLICH = "https://b-tarikak.de/t/safar-sham-12"
APP_NAMEN = ("بطريقك", "B-Tarikak")
LANDING = "https://b-tarikak.de/home"
#: Was die Konfiguration seit dem 24.09.2026 ans Ende jedes Kommentars setzt.
SCHLUSS = "الرابط المباشر للتحميل موجود في البايو (أعلى الصفحة) 👇"


def _vorlagen() -> dict:
    with open("config/textvorlagen.yaml", encoding="utf-8") as datei:
        return yaml.safe_load(datei)


def _kommentarvorlagen() -> list[tuple[str, str]]:
    daten = _vorlagen()
    return [
        (f"{sprache}/{topf}/{v['id']}", v["text"])
        for sprache in ("ar", "de")
        for topf, liste in daten["vorlagen"][sprache]["kommentar"].items()
        for v in liste
    ]


def _zuordnung(**felder) -> CampaignGroup:
    return CampaignGroup(
        campaign_id=KAMPAGNE,
        group_id="g1",
        tracking_code="FB-TST-BER-001",
        tracking_url=TRACKING,
        public_code="safar-sham-12",
        public_url=OEFFENTLICH,
        **felder,
    )


def _ist_ohne_adresse(text: str) -> None:
    """Kein Tracking - und keine Adresse ausser der freien Landingpage."""
    assert "{link}" not in text
    assert "/r/" not in text and "/t/" not in text
    assert "FB-TST" not in text and "safar-sham-12" not in text
    assert tracking_adresse_im_text(text) == "", text
    assert adresse_im_text(text) == "", text


def _endet_mit_landing(text: str) -> None:
    assert text.endswith(f" {SCHLUSS}"), text
    assert text.count(SCHLUSS) == 1, text


# --- 1. Die Vorlagen: ohne Link und trotzdem ein ganzer Kommentar -----------

@pytest.mark.parametrize(("schluessel", "vorlage"), _kommentarvorlagen())
def test_jede_kommentarvorlage_ergibt_einen_kommentar_ohne_link(
    schluessel: str, vorlage: str
) -> None:
    """Der Link geht samt Hinfuehrung - der Satz davor nennt die App bereits."""
    text = ohne_link(vorlage)

    _ist_ohne_adresse(text)
    assert any(name in text for name in APP_NAMEN), f"{schluessel}: die App fehlt"
    assert not text.rstrip().endswith(":"), f"{schluessel}: haengt ins Leere"
    assert text.rstrip()[-1] in ".!?؟", f"{schluessel}: kein Satzende"


def test_ein_angehaengter_link_faellt_mit_seiner_zeile() -> None:
    """So haengt ``vorlagen.anlasstext`` ihn an - der Satz davor bleibt ganz."""
    assert ohne_link("فيك تنشر طلبك على بطريقك.\n{link}") == "فيك تنشر طلبك على بطريقك."


def test_mit_link_nimmt_den_link_nur_aus_dem_kommentar(config) -> None:
    """Der Kommentar verliert den Link - der Beitrag behaelt ihn.

    Im Beitrag steht seit dem 25.09.2026 die Startseite der App, nicht mehr
    die Adresse der Zuordnung (``public_url``) - auch wenn eine alte
    Zuordnung sie noch traegt.
    """
    kampagne = Campaign(campaign_id=KAMPAGNE, name="K", language="ar")
    zuordnung = _zuordnung()
    vorlage = "شفت تطبيق بطريقك. حمّله من هنا: {link}"

    kommentar = mit_link(
        kampagne, zuordnung, vorlage, config=config, texttyp=Texttyp.KOMMENTAR
    )
    beitrag = mit_link(kampagne, zuordnung, vorlage, config=config, texttyp=Texttyp.POST)

    assert kommentar == f"شفت تطبيق بطريقك. {SCHLUSS}"
    _ist_ohne_adresse(kommentar)
    assert beitrag == f"شفت تطبيق بطريقك. حمّله من هنا: {LANDING}"
    assert OEFFENTLICH not in beitrag


def test_beitragstext_zeigt_den_kommentar_ohne_link(config) -> None:
    """Auch die Anzeige (Arbeitsseite, ``campaign message``) - eine Quelle."""
    kampagne = Campaign(campaign_id=KAMPAGNE, name="K", language="ar")
    zuordnung = _zuordnung(kommentar_text="شفت بطريقك. من هنا: {link}")

    text = beitragstext(kampagne, zuordnung, Texttyp.KOMMENTAR, config=config)
    _ist_ohne_adresse(text)
    _endet_mit_landing(text)


def test_die_freie_adresse_steht_genau_so_wie_im_beispiel(config) -> None:
    """Das Beispiel des Nutzers vom 23.09.2026, aus der Vorlage ``sharakta``."""
    kampagne = Campaign(campaign_id=KAMPAGNE, name="K", language="ar")
    vorlage = (
        "حبيت شاركها معكم، شفت تطبيق بطريقك وفكرته إنه يربط اللي بده يبعت غرض "
        "صغير مع مسافر رايح أو جاي من سوريا. حمّل بطريقك أو زور موقعه من هنا: {link}"
    )

    text = mit_link(kampagne, _zuordnung(), vorlage, config=config, texttyp=Texttyp.KOMMENTAR)

    assert text == (
        "حبيت شاركها معكم، شفت تطبيق بطريقك وفكرته إنه يربط اللي بده يبعت غرض "
        f"صغير مع مسافر رايح أو جاي من سوريا. {SCHLUSS}"
    )


def test_eine_tracking_adresse_wird_nie_als_freie_angenommen() -> None:
    """Stuende ``/t/`` oder ``/r/`` in ``marketing.kommentar_adresse``, gilt sie nicht."""
    from fbgroups.marketing.beitrag import kommentar_adresse, mit_kommentaradresse

    class _Mit:
        def __init__(self, wert: str) -> None:
            self.wert = wert

        def get(self, *_pfad, default=None):
            return self.wert

    assert kommentar_adresse(_Mit(OEFFENTLICH)) == ""
    assert kommentar_adresse(_Mit(TRACKING)) == ""
    assert kommentar_adresse(_Mit(f"{LANDING}?ref=x")) == ""
    assert kommentar_adresse(_Mit(LANDING)) == LANDING
    assert mit_kommentaradresse("Text.", TRACKING) == "Text."
    assert mit_kommentaradresse(f"Text. {LANDING}", LANDING) == f"Text. {LANDING}"


# --- 2. Der Lauf: keine Adresse, auch nicht von einem alten Server ----------

class _Konfig:
    """Echte Konfiguration - auf Wunsch mit dem alten Deckel ``tracking_link``."""

    def __init__(self, pfad: Path | None = None, **ueber) -> None:
        from fbgroups.config import load_config

        self._echt = load_config()
        self._pfad = pfad
        self._ueber = ueber

    def __getattr__(self, name: str):
        return getattr(self._echt, name)

    def path(self, name: str) -> Path:
        if name == "sqlite_path" and self._pfad is not None:
            return self._pfad
        return self._echt.path(name)

    def get(self, *pfad, default=None):
        if pfad == ("marketing", "linkmodus_max") and "linkmodus_max" in self._ueber:
            return self._ueber["linkmodus_max"]
        if pfad[:2] == ("kaltmodus", "aktiv"):
            return False
        if pfad[:1] == ("delays",):
            return 0
        if pfad[:2] == ("automatik", "kommentare_zuerst") and "kommentare_zuerst" in self._ueber:
            return self._ueber["kommentare_zuerst"]
        return self._echt.get(*pfad, default=default)


class _Zaehler:
    def __init__(self) -> None:
        self.texte: list[str] = []

    def __call__(self, _context, _post_url: str, text: str) -> Kommentarausgang:
        self.texte.append(text)
        return Kommentarausgang(True)


def test_der_lauf_setzt_keine_adresse_ein_auch_nicht_mit_altem_deckel() -> None:
    """Selbst mit ``tracking_link`` und einer mitgeschickten Adresse: kein Link.

    Genau so saehe ein aelterer Server aus - er schickt ``link_url`` mit, und
    die Entscheidung erlaubt einen Link. Der Kommentar geht trotzdem ohne.
    """
    zaehler = _Zaehler()
    ergebnis = automatik.entscheide_und_kommentiere(
        None,
        _Konfig(linkmodus_max="tracking_link"),
        [
            {
                "post_url": "https://www.facebook.com/groups/g1/posts/1/",
                "text": "كيف فيني ابعت غرض صغير من ألمانيا لسوريا؟",
                "interactions": 3,
                "comments": 0,
            }
        ],
        "g1",
        "Rueckfall {link}",
        kommentieren=zaehler,
        bisherige=[],
        erlaubnis=Erlaubnis(links=True),
        anspruch=Anspruch(anlass_pflicht=False),
        link_url=TRACKING,
    )

    assert ergebnis.erfolg, ergebnis.fehler
    assert len(zaehler.texte) == 1
    _ist_ohne_adresse(zaehler.texte[0])
    assert TRACKING not in zaehler.texte[0]
    _ist_ohne_adresse(ergebnis.text)


def test_der_lauf_haengt_die_freie_adresse_einmal_an() -> None:
    """Die freie Adresse kommt als ``link_url`` vom Server - und genau einmal hinaus."""
    zaehler = _Zaehler()
    ergebnis = automatik.entscheide_und_kommentiere(
        None,
        _Konfig(),
        [
            {
                "post_url": "https://www.facebook.com/groups/g1/posts/3/",
                "text": "كيف فيني ابعت غرض صغير من ألمانيا لسوريا؟",
                "interactions": 3,
                "comments": 0,
            }
        ],
        "g1",
        f"Rueckfall. {SCHLUSS}",
        kommentieren=zaehler,
        bisherige=[],
        erlaubnis=Erlaubnis(),
        anspruch=Anspruch(anlass_pflicht=False),
        link_url=SCHLUSS,
    )

    assert ergebnis.erfolg, ergebnis.fehler
    _ist_ohne_adresse(zaehler.texte[0])
    _endet_mit_landing(zaehler.texte[0])


def test_ein_rueckfalltext_mit_adresse_geht_nicht_hinaus() -> None:
    """Ohne lesbaren Beitrag gilt der vorbereitete Text - aber nie mit Adresse."""
    zaehler = _Zaehler()
    ergebnis = automatik.entscheide_und_kommentiere(
        None,
        _Konfig(),
        [{"post_url": "https://www.facebook.com/groups/g1/posts/2/", "text": "",
          "interactions": 0, "comments": 0}],
        "g1",
        f"شفت بطريقك. من هنا: {TRACKING}",
        kommentieren=zaehler,
        bisherige=[],
        erlaubnis=Erlaubnis(),
    )

    assert zaehler.texte == [], "nichts abgesetzt"
    assert ergebnis.kein_anlass
    assert "Adresse im Kommentar" in ergebnis.fehler


def test_comment_on_post_setzt_keinen_kommentar_mit_adresse_ab() -> None:
    """Die letzte Stelle vor dem Browser - jeder Kommentar kommt hier durch.

    ``context`` ist ``None``: Geprueft wird, bevor eine Seite geoeffnet wird.
    """
    for text in (
        f"schau mal {OEFFENTLICH}",
        "go.b-tarikak.de/r/wr4s9xw",
        "Code FB-SYR-BER-010-B",
        f"{LANDING}?ref=wr4s9xw",
    ):
        ausgang = comment_on_post(None, "https://www.facebook.com/groups/g1/posts/1/", text)
        assert not ausgang.erfolg
        assert "Adresse im Kommentar" in ausgang.hinweis


# --- 3. Im Bestand: Fernbetrieb, keine Beitraege, 20 je Gruppe --------------

@pytest.fixture()
def bestand(tmp_path: Path) -> Path:
    pfad = tmp_path / "groups.sqlite"
    with SqliteStore(pfad) as store:
        store.upsert_groups(
            [
                Group(
                    group_id=gid,
                    url_canonical=f"https://www.facebook.com/groups/{gid}",
                    name=f"Gruppe {gid}",
                    city="Berlin",
                    score=float(100 - i),
                    score_max=100.0,
                )
                for i, gid in enumerate(GRUPPEN)
            ]
        )
    with MarketingStore(pfad) as store:
        store.save_campaign(
            Campaign(
                campaign_id=KAMPAGNE, name="K", language="ar", status=CampaignStatus.ACTIVE
            )
        )
        for i, gid in enumerate(GRUPPEN, start=1):
            store.save_marketing(
                GroupMarketing(group_id=gid, marketing_status=MarketingStatus.MEMBER)
            )
            store.add_link(
                CampaignGroup(
                    campaign_id=KAMPAGNE,
                    group_id=gid,
                    tracking_code=f"FB-TST-BER-{i:03d}",
                    tracking_url=f"https://go.b-tarikak.de/r/FB-TST-BER-{i:03d}",
                )
            )
    return pfad


def test_der_lauf_setzt_keinen_beitrag_ab_und_legt_keine_beitragstexte_an(
    bestand: Path,
) -> None:
    """``automatik.beitraege: false``: nur Kommentare, auch ohne ``kommentare_zuerst``.

    Ohne Kommentare zuerst stuende der Beitrag sonst vor jedem Kommentar. Und
    fuer einen Kommentarschritt entstehen keine Beitragstexte mehr.
    """
    gesehen: list[str] = []

    def ausfuehren(url, group_id, text, texttyp="kommentar", link_url=""):
        gesehen.append(texttyp)
        _ist_ohne_adresse(text)
        assert link_url == SCHLUSS
        return automatik.Schrittergebnis(
            erfolg=True, post_url=f"https://www.facebook.com/groups/{group_id}/posts/{len(gesehen)}"
        )

    automatik.fuehre_lauf_aus(
        _Konfig(bestand, kommentare_zuerst=False), ausfuehren=ausfuehren, max_schritte=6
    )

    assert gesehen == ["kommentar"] * 6
    with MarketingStore(bestand) as store:
        for gid in GRUPPEN:
            assert store.vorschlaege(KAMPAGNE, gid, Texttyp.POST) == []


def test_in_einer_gruppe_gehen_zwanzig_kommentare(bestand: Path) -> None:
    """20 je Gruppe: Ziel und Tagesmenge - nicht mehr bei zehn Schluss."""
    gesehen: list[str] = []

    def ausfuehren(url, group_id, text, texttyp="kommentar", link_url=""):
        gesehen.append(group_id)
        return automatik.Schrittergebnis(
            erfolg=True, post_url=f"https://www.facebook.com/groups/{group_id}/posts/{len(gesehen)}"
        )

    fortschritt = automatik.fuehre_lauf_aus(
        _Konfig(bestand), ausfuehren=ausfuehren, max_schritte=60
    )

    assert gesehen.count("g1") == 20
    assert gesehen.count("g2") == 20
    assert fortschritt.kommentare_veroeffentlicht == 40
    assert gesehen[:4] == ["g1", "g2", "g1", "g2"], "die Runde bleibt: einer je Runde"


# --- 4. Die Grenzen ----------------------------------------------------------

def test_die_neuen_grenzen_stehen_in_der_konfiguration(config) -> None:
    kommentar = grenzen.einstellungen(config).fuer(grenzen.Aktion.KOMMENTAR)

    assert kommentar.je_gruppe_taeglich == 20
    assert kommentar.pro_tag == 240
    # Seit dem 24.09.2026 gestreut: 3-8 Minuten statt fest 3.
    assert (kommentar.abstand_min, kommentar.abstand_max) == (3, 8)
    assert automatik.ziel_kommentare(config) == 240
    assert lauf.ZIEL_JE_GRUPPE == 20
    assert automatik.beitraege_automatisch(config) is False
    # Die Rechnung dahinter: 240 bleibt Obergrenze (12 h bei der Untergrenze
    # von 3 Min); im Mittel (5,5 Min) sind es rund 130.
    assert 12 * 60 // kommentar.abstand_min == 240
    assert 12 * 60 // ((kommentar.abstand_min + kommentar.abstand_max) / 2) == 130
    assert 240 // kommentar.je_gruppe_taeglich == 12


def _gruppe(gid: str, **felder) -> Gruppenfortschritt:
    return Gruppenfortschritt(
        campaign_id="k",
        group_id=gid,
        name=gid,
        mitglied=True,
        post_status=PostStatus.VEROEFFENTLICHT,
        **felder,
    )


def test_je_gruppe_hoechstens_zwanzig_am_tag() -> None:
    assert _gruppe("a", veroeffentlicht=0, heute_in_gruppe=19, gruppenlimit=20).kommentierbar
    assert not _gruppe(
        "a", veroeffentlicht=0, heute_in_gruppe=20, gruppenlimit=20
    ).kommentierbar


def test_die_kampagne_ist_bei_240_erreicht() -> None:
    """Das Kampagnenziel: 239 halten sie aktiv, 240 schliessen sie ab."""

    def kampagne(zusammen: int) -> Kampagnenfortschritt:
        gruppen = [_gruppe(f"g{i}", veroeffentlicht=20) for i in range(11)]
        gruppen.append(_gruppe("g11", veroeffentlicht=zusammen - 220))
        return Kampagnenfortschritt(
            campaign_id="k", name="k", gruppen=gruppen, ziel_kommentare=240
        )

    assert not kampagne(239).abgeschlossen
    assert kampagne(240).abgeschlossen


def test_ohne_beitraege_haelt_ein_offener_beitrag_nichts_auf(bestand: Path) -> None:
    """``lies_fortschritt(beitraege=False)``: Der Beitrag zaehlt nicht mehr als offen."""
    with SqliteStore(bestand) as s:
        gruppen = {g.group_id: g for g in s.load_groups()}
    with MarketingStore(bestand) as store:
        for gid in GRUPPEN:
            store.setze_erzeugten_vorschlag(
                KAMPAGNE, gid, Texttyp.POST, 1, text="Beitrag {link}", vorlage_key="p"
            )
        lauf_id = store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)
        mit = lauf.lies_fortschritt(store, lauf_id, gruppen, beitraege=True)
        ohne = lauf.lies_fortschritt(store, lauf_id, gruppen, beitraege=False)

    assert lauf.naechster_schritt(mit).texttyp is Texttyp.POST
    schritt = lauf.naechster_schritt(ohne)
    assert schritt is not None and schritt.texttyp is Texttyp.KOMMENTAR
    assert all(not g.post_offen for g in ohne.kampagnen[0].gruppen)
