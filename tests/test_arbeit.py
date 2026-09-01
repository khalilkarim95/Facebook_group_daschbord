"""Tests fuer die gruppenweise Arbeit und die Arbeitsseite.

Der Grund fuer ``arbeit.py`` ist unveraendert, dass Kommandozeile und
Weboberflaeche **dieselben** Regeln benutzen - eine zweite Fassung fuer den
Dienst waere eine zweite Zaehlweise fuer dieselben Beitraege.

Was sich geaendert hat, ist die Einheit. Die Datei prueft deshalb vor allem
die Zusicherungen, die es vorher gar nicht geben konnte:

* Eine Gruppe traegt mehrere Fassungen, und jede hat ihren eigenen Stand.
* Speichern trifft genau eine davon.
* Melden schaltet nichts weiter - nicht zur naechsten Gruppe, nicht zum
  naechsten Vorschlag, und die andere Spalte bleibt unberuehrt.
* Beitrag und Kommentar gehen unabhaengig voneinander hinaus.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fbgroups.config import load_config
from fbgroups.marketing.arbeit import (
    Ergebnis,
    Grund,
    Gruppenarbeit,
    Sperre,
    arbeitsreihenfolge,
    auswahlliste,
    hole_gruppenarbeit,
    melde_vorschlag,
    stelle_texte_bereit,
)
from fbgroups.marketing.models import (
    MAX_VORSCHLAEGE,
    Campaign,
    CampaignGroup,
    JobStatus,
    PostStatus,
    QueueZustand,
    TextQuelle,
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


@pytest.fixture()
def store(bestand: Path):
    with MarketingStore(bestand) as s:
        yield s


@pytest.fixture()
def campaign(store: MarketingStore) -> Campaign:
    kampagne = store.load_campaign(KAMPAGNE)
    assert kampagne is not None
    return kampagne


@pytest.fixture()
def gruppen(bestand: Path) -> dict[str, Group]:
    with SqliteStore(bestand) as s:
        return {g.group_id: g for g in s.load_groups()}


@pytest.fixture()
def gefuellt(store: MarketingStore, campaign: Campaign, gruppen, config) -> None:
    """Alle Fassungen beider Gruppen - der Zustand nach dem ersten Aufruf."""
    for gruppe in gruppen.values():
        stelle_texte_bereit(store, campaign, gruppe, config)


def hole(store, campaign, gruppen, **kwargs) -> Gruppenarbeit:
    # ``config`` geht bis in ``beitrag.mit_link`` durch - dort wird {datum}
    # aufgeloest. Die echte Projektkonfiguration, wie die ``config``-Fixture.
    stand = hole_gruppenarbeit(store, campaign, gruppen, load_config(), **kwargs)
    assert stand is not None
    return stand


# --- Die Gruppe als Einheit -----------------------------------------------

def test_eine_gruppe_traegt_mehrere_fassungen(
    store, campaign, gruppen, gefuellt
) -> None:
    """Der Kern der Umstellung: nicht ein Text je Gruppe, sondern der Topf."""
    stand = hole(store, campaign, gruppen)

    assert len(stand.posts) > 1
    assert len(stand.kommentare) > 1
    assert [f.nummer for f in stand.posts] == list(range(1, len(stand.posts) + 1))


def test_jede_fassung_ist_ein_eigener_text(store, campaign, gruppen, gefuellt) -> None:
    """Fuenfmal derselbe Text waere fuenfmal dieselbe Entscheidung."""
    stand = hole(store, campaign, gruppen)

    texte = {f.vorschlag.text for f in stand.posts}
    assert len(texte) == len(stand.posts)


def test_platz_eins_ist_die_fassung_von_frueher(
    store, campaign, gruppen, gefuellt, config
) -> None:
    """Sonst bekaemen 310 Gruppen beim Umstellen einen anderen Text.

    ``reihenfolge_fuer`` dreht den Topf um dieselbe Zahl, die vorher die eine
    Fassung bestimmt hat - deshalb steht sie jetzt auf Platz 1.
    """
    from fbgroups.marketing import vorlagen

    gid = next(iter(GRUPPEN))
    _, erwartet = vorlagen.text_fuer_gruppe(gruppen[gid], campaign, config)

    erste = store.vorschlag(KAMPAGNE, gid, Texttyp.POST, 1)
    assert erste is not None
    assert erste.text == erwartet


def test_die_wahl_bleibt_ueber_laeufe_hinweg_dieselbe(
    store, campaign, gruppen, gefuellt, config
) -> None:
    """Sonst aenderte sich der Text unter demjenigen, der ihn freigegeben hat."""
    vorher = [v.vorlage_key for v in store.vorschlaege(
        KAMPAGNE, next(iter(GRUPPEN)), Texttyp.POST)]

    for gruppe in gruppen.values():
        stelle_texte_bereit(store, campaign, gruppe, config)

    nachher = [v.vorlage_key for v in store.vorschlaege(
        KAMPAGNE, next(iter(GRUPPEN)), Texttyp.POST)]
    assert nachher == vorher


def test_der_tracking_code_steht_nur_im_angezeigten_text(
    store, campaign, gruppen, gefuellt
) -> None:
    """Gespeichert wird ``{link}``, kopiert wird der eingesetzte Link.

    Wer den Platzhalter im gespeicherten Text ersetzte, haette einen Text, den
    kein Sprachmodell und kein zweiter Leser mehr gefahrlos anfassen darf.
    """
    stand = hole(store, campaign, gruppen)

    for fassung in stand.posts:
        assert "{link}" in fassung.vorschlag.text
        assert stand.link.tracking_code not in fassung.vorschlag.text
        assert stand.link.tracking_url in fassung.angezeigt
        assert "{link}" not in fassung.angezeigt


def test_das_ansehen_faengt_keinen_versuch_an(store, campaign, gruppen, gefuellt) -> None:
    """Vorher nahm das blosse Oeffnen der Seite einen Beitrag aus der Schlange."""
    stand = hole(store, campaign, gruppen)
    hole(store, campaign, gruppen, nummer=2)

    assert store.versuche_for(KAMPAGNE, stand.link.group_id) == []
    assert store.offene_versuche(KAMPAGNE) == []


# --- Speichern trifft genau eine Fassung ----------------------------------

def test_speichern_laesst_die_nachbarn_in_ruhe(store, campaign, gruppen, gefuellt) -> None:
    """"Beim Speichern darf nur dieser konkrete Vorschlag gespeichert werden"."""
    gid = next(iter(GRUPPEN))
    vorher = {v.nummer: v.text for v in store.vorschlaege(KAMPAGNE, gid, Texttyp.POST)}

    store.setze_vorschlag_text(KAMPAGNE, gid, Texttyp.POST, 2, "Nur der zweite {link}")

    nachher = {v.nummer: v.text for v in store.vorschlaege(KAMPAGNE, gid, Texttyp.POST)}
    assert nachher[2] == "Nur der zweite {link}"
    assert {n: t for n, t in nachher.items() if n != 2} == {
        n: t for n, t in vorher.items() if n != 2
    }


def test_speichern_laesst_die_kommentare_in_ruhe(
    store, campaign, gruppen, gefuellt
) -> None:
    """Zwei Zwecke, zwei Toepfe - und zwei Spalten in der Tabelle."""
    gid = next(iter(GRUPPEN))
    vorher = [v.text for v in store.vorschlaege(KAMPAGNE, gid, Texttyp.KOMMENTAR)]

    store.setze_vorschlag_text(KAMPAGNE, gid, Texttyp.POST, 1, "Beitrag {link}")

    assert [v.text for v in store.vorschlaege(KAMPAGNE, gid, Texttyp.KOMMENTAR)] == vorher


def test_ein_gespeicherter_text_heisst_gespeichert(
    store, campaign, gruppen, gefuellt
) -> None:
    """Sonst liesse sich "aus der Vorlage gefallen" nicht von "durchgelesen"
    unterscheiden - und beim Blaettern durch fuenf ist das die Frage."""
    gid = next(iter(GRUPPEN))
    assert store.vorschlag(KAMPAGNE, gid, Texttyp.POST, 3).status is VorschlagStatus.ENTWURF

    store.setze_vorschlag_text(KAMPAGNE, gid, Texttyp.POST, 3, "Angefasst {link}")

    assert (
        store.vorschlag(KAMPAGNE, gid, Texttyp.POST, 3).status
        is VorschlagStatus.GESPEICHERT
    )


def test_das_paar_zeigt_den_zuletzt_bearbeiteten_text(
    store, campaign, gruppen, gefuellt
) -> None:
    """``campaign message`` und die Uebersicht lesen weiterhin am Paar.

    Ohne die Spiegelung stuende dort der Text von vorgestern, waehrend die
    Arbeitsseite den von heute zeigt.
    """
    gid = next(iter(GRUPPEN))
    store.setze_vorschlag_text(KAMPAGNE, gid, Texttyp.POST, 4, "Der vierte {link}")

    assert store.link_for(KAMPAGNE, gid).post_text == "Der vierte {link}"


# --- Melden: der Ausgang gehoert der Fassung ------------------------------

def test_veroeffentlicht_gilt_nur_fuer_diese_fassung(
    store, campaign, gruppen, gefuellt
) -> None:
    """Der Fall aus der Anforderung: Post 1 raus, Post 2 noch Entwurf."""
    stand = hole(store, campaign, gruppen)

    melde_vorschlag(
        store, campaign, stand.link, Texttyp.POST, 1, Ergebnis(erfolg=True)
    )

    staende = {
        v.nummer: v.status
        for v in store.vorschlaege(KAMPAGNE, stand.link.group_id, Texttyp.POST)
    }
    assert staende[1] is VorschlagStatus.VEROEFFENTLICHT
    assert staende[2] is VorschlagStatus.ENTWURF


def test_fehlgeschlagen_gilt_ebenfalls_nur_fuer_diese_fassung(
    store, campaign, gruppen, gefuellt
) -> None:
    """Nicht "Gruppe fehlgeschlagen", sondern "Fassung 2 fehlgeschlagen"."""
    stand = hole(store, campaign, gruppen)

    melde_vorschlag(
        store, campaign, stand.link, Texttyp.POST, 1, Ergebnis(erfolg=True)
    )
    melde_vorschlag(
        store, campaign, stand.link, Texttyp.POST, 2,
        Ergebnis(erfolg=False, fehler="erlaubt keine Links"),
    )

    zwei = store.vorschlag(KAMPAGNE, stand.link.group_id, Texttyp.POST, 2)
    assert zwei.status is VorschlagStatus.FEHLGESCHLAGEN
    assert zwei.fehler == "erlaubt keine Links"
    assert (
        store.vorschlag(KAMPAGNE, stand.link.group_id, Texttyp.POST, 1).status
        is VorschlagStatus.VEROEFFENTLICHT
    )


def test_beitrag_und_kommentar_gehen_unabhaengig_hinaus(
    store, campaign, gruppen, gefuellt
) -> None:
    """Das Beispiel aus der Anforderung, vollstaendig.

    Post 1 veroeffentlicht, Kommentar 1 Entwurf, Post 2 Entwurf,
    Kommentar 2 veroeffentlicht - alles gleichzeitig in einer Gruppe.
    """
    stand = hole(store, campaign, gruppen)

    melde_vorschlag(
        store, campaign, stand.link, Texttyp.POST, 1, Ergebnis(erfolg=True)
    )
    melde_vorschlag(
        store, campaign, stand.link, Texttyp.KOMMENTAR, 2, Ergebnis(erfolg=True)
    )

    posts = {v.nummer: v.status for v in store.vorschlaege(
        KAMPAGNE, stand.link.group_id, Texttyp.POST)}
    kommentare = {v.nummer: v.status for v in store.vorschlaege(
        KAMPAGNE, stand.link.group_id, Texttyp.KOMMENTAR)}

    assert posts[1] is VorschlagStatus.VEROEFFENTLICHT
    assert posts[2] is VorschlagStatus.ENTWURF
    assert kommentare[1] is VorschlagStatus.ENTWURF
    assert kommentare[2] is VorschlagStatus.VEROEFFENTLICHT


def test_melden_schaltet_nicht_zur_naechsten_gruppe(
    store, campaign, gruppen, gefuellt
) -> None:
    """Der wichtigste Test der Datei.

    Vorher war der Rueckgabewert der naechste Auftrag - wer veroeffentlichte,
    bekam damit die naechste Gruppe, ob er wollte oder nicht.
    """
    stand = hole(store, campaign, gruppen)

    ergebnis = melde_vorschlag(
        store, campaign, stand.link, Texttyp.POST, 1, Ergebnis(erfolg=True)
    )

    assert not isinstance(ergebnis, Sperre)
    assert ergebnis.group_id == stand.link.group_id
    assert ergebnis.nummer == 1
    # Und dieselbe Gruppe steht danach immer noch an derselben Stelle.
    assert hole(store, campaign, gruppen).link.group_id == stand.link.group_id


def test_der_erste_erfolg_setzt_den_zeitpunkt_und_der_zweite_nicht(
    store, campaign, gruppen, gefuellt
) -> None:
    """Dieselbe Regel wie ``posted_at`` am Paar."""
    stand = hole(store, campaign, gruppen)

    erst = melde_vorschlag(
        store, campaign, stand.link, Texttyp.POST, 1, Ergebnis(erfolg=True)
    )
    nochmal = melde_vorschlag(
        store, campaign, stand.link, Texttyp.POST, 1, Ergebnis(erfolg=True)
    )

    assert erst.veroeffentlicht_am == nochmal.veroeffentlicht_am
    assert nochmal.versuche == 2


def test_ein_erfolg_loescht_den_alten_fehlergrund(
    store, campaign, gruppen, gefuellt
) -> None:
    """Sonst stuende der Grund neben einer veroeffentlichten Fassung."""
    stand = hole(store, campaign, gruppen)

    melde_vorschlag(
        store, campaign, stand.link, Texttyp.POST, 1,
        Ergebnis(erfolg=False, fehler="Netz weg"),
    )
    danach = melde_vorschlag(
        store, campaign, stand.link, Texttyp.POST, 1, Ergebnis(erfolg=True)
    )

    assert danach.fehler == ""


# --- Der Stand des Paares wird nachgezogen --------------------------------

def test_eine_veroeffentlichte_fassung_erledigt_die_gruppe(
    store, campaign, gruppen, gefuellt
) -> None:
    """``campaign queue``, ``retry`` und die Uebersicht lesen am Paar weiter."""
    stand = hole(store, campaign, gruppen)

    melde_vorschlag(
        store, campaign, stand.link, Texttyp.POST, 1, Ergebnis(erfolg=True)
    )

    link = store.link_for(KAMPAGNE, stand.link.group_id)
    assert link.post_status is PostStatus.VEROEFFENTLICHT
    assert link.job_status is JobStatus.PUBLISHED


def test_veroeffentlicht_gewinnt_gegen_fehlgeschlagen(
    store, campaign, gruppen, gefuellt
) -> None:
    """Die Gruppe **hat** ihren Beitrag.

    Zoege eine gescheiterte zweite Fassung das Paar auf "fehlgeschlagen",
    holte ``campaign retry`` sie zurueck in eine Liste, auf der sie nichts
    mehr zu suchen hat.
    """
    stand = hole(store, campaign, gruppen)

    melde_vorschlag(
        store, campaign, stand.link, Texttyp.POST, 1, Ergebnis(erfolg=True)
    )
    melde_vorschlag(
        store, campaign, stand.link, Texttyp.POST, 2,
        Ergebnis(erfolg=False, fehler="ging nicht"),
    )

    assert (
        store.link_for(KAMPAGNE, stand.link.group_id).post_status
        is PostStatus.VEROEFFENTLICHT
    )


def test_eine_erledigte_gruppe_bleibt_in_der_liste(
    store, campaign, gruppen, gefuellt
) -> None:
    """Sie hat vier weitere Fassungen - sie darf nicht verschwinden."""
    stand = hole(store, campaign, gruppen)
    melde_vorschlag(
        store, campaign, stand.link, Texttyp.POST, 1, Ergebnis(erfolg=True)
    )

    reihe = arbeitsreihenfolge(store, KAMPAGNE, gruppen)

    assert stand.link.group_id in {link.group_id for link in reihe}


def test_ein_kommentar_zieht_den_gruppenstand_nicht_mit(
    store, campaign, gruppen, gefuellt
) -> None:
    """Der Beitrag traegt den Ablauf - ein Kommentar ist kein Beitrag."""
    stand = hole(store, campaign, gruppen)

    melde_vorschlag(
        store, campaign, stand.link, Texttyp.KOMMENTAR, 1, Ergebnis(erfolg=True)
    )

    assert store.link_for(KAMPAGNE, stand.link.group_id).post_status is PostStatus.OFFEN


# --- Die Bremsen bleiben ---------------------------------------------------

def test_es_gibt_keine_gezaehlte_tagesgrenze_mehr(
    store, campaign, gruppen, gefuellt
) -> None:
    """Das Tageslimit ist am 27.08.2026 entfernt worden.

    Es war der letzte Rest des Arbeiters. Gegen eine Schleife, die selbst
    abschickt, war es eine Bremse; gegen einen Menschen, der jeden Beitrag von
    Hand einfuegt, war es eine Sperre, die ausgerechnet den traf, der gerade
    arbeitet. Der Test haelt fest, dass keine Zahl mehr dazwischensteht -
    haengt jemand eine neue Grenze ein, faellt sie hier auf.
    """
    stand = hole(store, campaign, gruppen)

    for nummer in range(1, MAX_VORSCHLAEGE + 1):
        ergebnis = melde_vorschlag(
            store, campaign, stand.link, Texttyp.POST, nummer, Ergebnis(erfolg=True)
        )
        assert not isinstance(ergebnis, Sperre)


def test_pausiert_haelt_beide_zwecke_an(store, campaign, gruppen, gefuellt) -> None:
    """Ein Entschluss, in dieser Kampagne gerade nichts **hinauszugeben**.

    Die Sperre haelt an, was noch nicht geschehen ist. Sie gilt deshalb fuer
    einen Fehlschlag - dort ist nichts passiert, was gebucht werden muesste.
    """
    stand = hole(store, campaign, gruppen)
    store.set_queue_zustand(KAMPAGNE, QueueZustand.PAUSIERT)

    for texttyp in (Texttyp.POST, Texttyp.KOMMENTAR):
        ergebnis = melde_vorschlag(
            store, campaign, stand.link, texttyp, 1, Ergebnis(erfolg=False, fehler="abgebrochen")
        )
        assert isinstance(ergebnis, Sperre)
        assert ergebnis.grund == Grund.PAUSIERT


def test_ein_erfolg_wird_auch_pausiert_gebucht(store, campaign, gruppen, gefuellt) -> None:
    """Was in der Gruppe steht, steht dort - die Pause holt es nicht zurueck.

    Meldet jemand einen Erfolg, ist der Beitrag bereits abgesetzt. Ihn wegen
    einer Pause nicht zu buchen hiesse, die Buchfuehrung von der Wirklichkeit
    auf Facebook zu trennen: Der Tracking-Code laeuft, die Klicks kommen an,
    und die Arbeitsliste boete dieselbe Gruppe ein zweites Mal an.
    """
    stand = hole(store, campaign, gruppen)
    store.set_queue_zustand(KAMPAGNE, QueueZustand.PAUSIERT)

    ergebnis = melde_vorschlag(
        store, campaign, stand.link, Texttyp.POST, 1, Ergebnis(erfolg=True)
    )

    assert not isinstance(ergebnis, Sperre)
    assert store.versuche_for(KAMPAGNE, stand.link.group_id) != []


def test_eine_sperre_faengt_keinen_versuch_an(store, campaign, gruppen, gefuellt) -> None:
    """Nachsehen ist keine Arbeit: Ein gesperrter Fehlschlag hinterlaesst nichts."""
    stand = hole(store, campaign, gruppen)
    store.set_queue_zustand(KAMPAGNE, QueueZustand.GESTOPPT)

    melde_vorschlag(
        store, campaign, stand.link, Texttyp.POST, 1, Ergebnis(erfolg=False, fehler="abgebrochen")
    )

    assert store.versuche_for(KAMPAGNE, stand.link.group_id) == []


def test_eine_sperre_verschliesst_die_seite_nicht(
    store, campaign, gruppen, gefuellt
) -> None:
    """Eine pausierte Kampagne ist kein Grund, die Texte von morgen nicht
    vorzubereiten."""
    store.set_queue_zustand(KAMPAGNE, QueueZustand.PAUSIERT)

    stand = hole(store, campaign, gruppen)

    assert stand.posts                      # die Texte stehen weiterhin da
    assert stand.sperre() is not None       # nur der Ausgang ist zu


# --- Die Gruppen-Navigation ------------------------------------------------

def test_die_besten_gruppen_stehen_oben(bestand: Path, gruppen, config) -> None:
    """Wer abbricht, soll die wertvollsten Beitraege geschrieben haben."""
    from fbgroups.scoring import sort_by_rank

    with MarketingStore(bestand) as store:
        reihe = arbeitsreihenfolge(store, KAMPAGNE, gruppen)

    erwartet = [g.group_id for g in sort_by_rank(list(gruppen.values()))]
    assert [link.group_id for link in reihe] == erwartet


def test_die_gruppenwahl_geht_vor_die_nummer(store, campaign, gruppen, gefuellt) -> None:
    """Wer eine bestimmte Gruppe meint, meint sie auch nach einer Neubewertung."""
    gid = "739201847362915"

    stand = hole(store, campaign, gruppen, nummer=1, group_id=gid)

    assert stand.link.group_id == gid


def test_eine_ausgeschlossene_gruppe_steht_nicht_in_der_liste(
    store, campaign, gruppen, gefuellt
) -> None:
    """Sie ist bereits als "daran arbeiten wir nicht" beurteilt."""
    from fbgroups.marketing.models import GroupMarketing

    gid = next(iter(GRUPPEN))
    store.save_marketing(GroupMarketing(group_id=gid, bearbeiten=False))

    reihe = arbeitsreihenfolge(store, KAMPAGNE, gruppen)

    assert gid not in {link.group_id for link in reihe}


def test_die_auswahl_nennt_dieselben_nummern_wie_die_arbeit(
    store, campaign, gruppen, gefuellt
) -> None:
    """Sonst fuehrte ein Eintrag auf eine andere Gruppe als die daneben.

    Dieselbe Ueberlegung wie bei ``search.build_plan``: Zwei Berechnungen
    derselben Rangfolge koennen auseinanderlaufen.
    """
    reihe = arbeitsreihenfolge(store, KAMPAGNE, gruppen)
    eintraege = auswahlliste(store, KAMPAGNE, reihe, gruppen)

    for eintrag in eintraege:
        stand = hole(store, campaign, gruppen, nummer=eintrag.nummer, reihe=reihe)
        assert stand.link.group_id == eintrag.group_id


def test_die_auswahl_zeigt_wo_schon_etwas_steht(
    store, campaign, gruppen, gefuellt
) -> None:
    """Sonst blaettert man durch dreihundert Eintraege, um festzustellen,
    dass die ersten zwanzig erledigt sind."""
    reihe = arbeitsreihenfolge(store, KAMPAGNE, gruppen)
    melde_vorschlag(
        store, campaign, reihe[0], Texttyp.POST, 1, Ergebnis(erfolg=True)
    )

    eintraege = auswahlliste(store, KAMPAGNE, reihe, gruppen)

    assert eintraege[0].veroeffentlicht is True
    assert eintraege[1].veroeffentlicht is False


# --- Die Seite -------------------------------------------------------------

def test_die_seite_zeigt_zwei_spalten(store, campaign, gruppen, gefuellt) -> None:
    from fbgroups.marketing.arbeitsseite import render_gruppenarbeit

    reihe = arbeitsreihenfolge(store, KAMPAGNE, gruppen)
    stand = hole(store, campaign, gruppen, reihe=reihe)
    seite = render_gruppenarbeit(
        stand, KAMPAGNE, auswahlliste(store, KAMPAGNE, reihe, gruppen)
    )

    assert "data-spalte='post'" in seite
    assert "data-spalte='kommentar'" in seite
    assert stand.link.tracking_code in seite


def test_die_nummernleiste_zeigt_den_stand(store, campaign, gruppen, gefuellt) -> None:
    """"Die Navigation der 5 Vorschlaege sollte diesen Status sichtbar machen"."""
    from fbgroups.marketing.arbeitsseite import render_gruppenarbeit

    reihe = arbeitsreihenfolge(store, KAMPAGNE, gruppen)
    melde_vorschlag(
        store, campaign, reihe[0], Texttyp.POST, 1, Ergebnis(erfolg=True)
    )
    melde_vorschlag(
        store, campaign, reihe[0], Texttyp.POST, 2,
        Ergebnis(erfolg=False, fehler="ging nicht"),
    )
    stand = hole(store, campaign, gruppen, reihe=reihe)
    seite = render_gruppenarbeit(
        stand, KAMPAGNE, auswahlliste(store, KAMPAGNE, reihe, gruppen)
    )

    assert "data-stand='veroeffentlicht'" in seite
    assert "data-stand='fehlgeschlagen'" in seite
    assert "data-stand='entwurf'" in seite
    assert "✓" in seite
    assert "✕" in seite


def test_arabisch_laeuft_von_rechts_nach_links(store, campaign, gruppen, gefuellt) -> None:
    from fbgroups.marketing.arbeitsseite import render_gruppenarbeit

    reihe = arbeitsreihenfolge(store, KAMPAGNE, gruppen)
    stand = hole(store, campaign, gruppen, reihe=reihe)
    seite = render_gruppenarbeit(
        stand, KAMPAGNE, auswahlliste(store, KAMPAGNE, reihe, gruppen)
    )

    assert "dir='rtl'" in seite


def test_die_seite_nennt_den_grund_der_sperre() -> None:
    from fbgroups.marketing.arbeitsseite import render_sperre

    seite = render_sperre(Sperre(Grund.PAUSIERT), KAMPAGNE)

    assert "pausiert" in seite
    assert "resume" in seite


# --- Der Weg im Dienst ----------------------------------------------------

def _client(bestand, config, **kwargs):
    pytest.importorskip("fastapi", reason="nur mit dem optionalen web-Zusatz")
    from fastapi.testclient import TestClient

    from fbgroups.marketing.web import create_app

    return TestClient(create_app(config=config, db_path=bestand), **kwargs)


def test_die_arbeitsseite_ist_von_aussen_nicht_erreichbar(bestand: Path, config) -> None:
    """Von hier aus wird veroeffentlicht - die Knoepfe dafuer stehen darauf."""
    fremd = _client(bestand, config, client=("203.0.113.7", 44321))

    assert fremd.get(f"/arbeit/{KAMPAGNE}").status_code == 404


def _oeffne_alle(client) -> None:
    """Jede Gruppe einmal aufrufen - die Seite fuellt je Gruppe beim Oeffnen.

    Sie fuellt bewusst nicht den ganzen Bestand auf einmal: Bei 310 Gruppen
    waeren das 3100 Texte bei jedem ersten Aufruf, und die allermeisten
    davon sieht an diesem Tag niemand an.
    """
    for nummer in range(1, len(GRUPPEN) + 1):
        client.get(f"/arbeit/{KAMPAGNE}?gruppe={nummer}")


def test_die_arbeitsseite_bereitet_die_texte_selbst_vor(bestand: Path, config) -> None:
    """Eine Knopfreihe zu druecken, bevor ueberhaupt ein Text dasteht, ist
    kein Entschluss, sondern eine Wegstrecke."""
    antwort = _client(bestand, config).get(f"/arbeit/{KAMPAGNE}")

    assert antwort.status_code == 200
    assert "FB-SYR" in antwort.text
    with MarketingStore(bestand) as store:
        gefuellte = [
            gid for gid in GRUPPEN
            if len(store.vorschlaege(KAMPAGNE, gid, Texttyp.POST)) > 1
        ]
    # Genau die geoeffnete Gruppe, nicht der ganze Bestand.
    assert len(gefuellte) == 1
    with MarketingStore(bestand) as store:
        assert len(store.vorschlaege(KAMPAGNE, gefuellte[0], Texttyp.KOMMENTAR)) > 1


def test_veroeffentlichen_leitet_nirgendwohin(bestand: Path, config) -> None:
    """Kein 303, keine naechste Gruppe - nur der neue Stand dieser Fassung.

    Der Unterschied zu vorher in einem Test: Frueher antwortete dieser Weg
    mit einer Weiterleitung auf dieselbe Adresse, und die holte den naechsten
    Beitrag.
    """
    client = _client(bestand, config, follow_redirects=False)
    _oeffne_alle(client)
    gid = next(iter(GRUPPEN))

    antwort = client.post(
        f"/arbeit/{KAMPAGNE}/vorschlag/ergebnis",
        json={"group_id": gid, "nummer": 2, "ausgang": "veroeffentlicht"},
    )

    assert antwort.status_code == 200
    daten = antwort.json()
    assert daten["ok"] is True
    assert daten["nummer"] == 2
    assert daten["stand"] == "veroeffentlicht"
    with MarketingStore(bestand) as store:
        assert (
            store.vorschlag(KAMPAGNE, gid, Texttyp.POST, 2).status
            is VorschlagStatus.VEROEFFENTLICHT
        )
        assert (
            store.vorschlag(KAMPAGNE, gid, Texttyp.POST, 1).status
            is VorschlagStatus.ENTWURF
        )


def test_das_ergebnis_traegt_keinen_text(bestand: Path, config) -> None:
    """Ein manipuliertes Formular kann keinen fremden Text in eine Gruppe bringen."""
    client = _client(bestand, config)
    _oeffne_alle(client)
    gid = next(iter(GRUPPEN))
    with MarketingStore(bestand) as store:
        vorher = store.vorschlag(KAMPAGNE, gid, Texttyp.POST, 1).text

    client.post(
        f"/arbeit/{KAMPAGNE}/vorschlag/ergebnis",
        json={
            "group_id": gid,
            "nummer": 1,
            "ausgang": "veroeffentlicht",
            "text": "Untergeschoben {link}",
        },
    )

    with MarketingStore(bestand) as store:
        assert store.vorschlag(KAMPAGNE, gid, Texttyp.POST, 1).text == vorher


def test_ein_erfundener_ausgang_wird_abgewiesen(bestand: Path, config) -> None:
    client = _client(bestand, config)
    _oeffne_alle(client)

    antwort = client.post(
        f"/arbeit/{KAMPAGNE}/vorschlag/ergebnis",
        json={
            "group_id": next(iter(GRUPPEN)),
            "nummer": 1,
            "ausgang": "vielleicht",
        },
    )

    assert antwort.status_code == 422


def test_eine_unbekannte_zuordnung_wird_abgewiesen(bestand: Path, config) -> None:
    """Ein Vorschlag ohne Zuordnung haette keinen Tracking-Code - also keinen Link."""
    antwort = _client(bestand, config).post(
        f"/arbeit/{KAMPAGNE}/vorschlag/text",
        json={"group_id": "999999999999999", "nummer": 1, "text": "Fremd {link}"},
    )

    assert antwort.status_code == 404


def test_der_arbeiten_knopf_fehlt_im_nur_lesen_zugang(bestand: Path, config) -> None:
    """Er schreibt - von aussen fuehrte er ins Leere."""
    from fbgroups.marketing.dashboard import render, sammle_daten

    daten = sammle_daten(config, bestand)

    assert "href='/arbeit/" in render(daten, nur_lesen=False)
    assert "href='/arbeit/" not in render(daten, nur_lesen=True)


def test_ohne_zuordnungen_nennt_die_seite_den_grund(tmp_path: Path, config) -> None:
    """Der haeufigste Griff daneben bekommt Klartext."""
    pfad = tmp_path / "leer.sqlite"
    with SqliteStore(pfad):
        pass
    with MarketingStore(pfad) as store:
        store.save_campaign(Campaign(campaign_id="leer", name="Leer"))

    seite = _client(pfad, config).get("/arbeit/leer").text

    assert "keine gruppen zugeordnet" in seite.lower()


# --- Zuruecksetzen fuer Testlaeufe ---------------------------------------

def test_reset_laesst_den_tracking_code_unberuehrt(
    store, campaign, gruppen, gefuellt
) -> None:
    """Er steht moeglicherweise in einem veroeffentlichten Beitrag."""
    stand = hole(store, campaign, gruppen)
    melde_vorschlag(
        store, campaign, stand.link, Texttyp.POST, 1, Ergebnis(erfolg=True)
    )
    vorher = {
        link.group_id: link.tracking_code
        for link in store.links_for_campaign(KAMPAGNE)
    }

    store.setze_kampagne_zurueck(KAMPAGNE)

    nachher = {
        link.group_id: link.tracking_code
        for link in store.links_for_campaign(KAMPAGNE)
    }
    assert nachher == vorher


def test_reset_stellt_den_beitragsstand_auf_anfang(
    store, campaign, gruppen, gefuellt
) -> None:
    stand = hole(store, campaign, gruppen)
    melde_vorschlag(
        store, campaign, stand.link, Texttyp.POST, 1, Ergebnis(erfolg=True)
    )

    store.setze_kampagne_zurueck(KAMPAGNE)

    for link in store.links_for_campaign(KAMPAGNE):
        assert link.post_status is PostStatus.OFFEN
        assert link.posted_at is None


def test_reset_faengt_die_warteschlange_wieder_an(store, campaign, gruppen) -> None:
    """Eine gestoppte Warteschlange bliebe sonst nach dem Reset gestoppt."""
    store.set_queue_zustand(KAMPAGNE, QueueZustand.GESTOPPT)

    store.setze_kampagne_zurueck(KAMPAGNE)

    assert store.queue_zustand(KAMPAGNE) is QueueZustand.LAUFEND


def test_die_vorschau_nennt_dieselben_zahlen(store, campaign, gruppen, gefuellt) -> None:
    """``--dry-run`` und Ernstfall lesen dieselbe Zaehlung."""
    stand = hole(store, campaign, gruppen)
    melde_vorschlag(
        store, campaign, stand.link, Texttyp.POST, 1, Ergebnis(erfolg=True)
    )

    vorschau = store.zaehle_zuruecksetzbar(KAMPAGNE)
    getan = store.setze_kampagne_zurueck(KAMPAGNE, auch_ereignisse=True)

    assert getan["zuordnungen"] == vorschau["zuordnungen"] == 2
    assert getan["versuche"] == vorschau["versuche"] == 1
    assert getan["veroeffentlicht"] == vorschau["veroeffentlicht"] == 1


# --- Die Vorbereitungsknoepfe --------------------------------------------

def test_die_werkbank_erscheint_nur_ohne_zuordnungen() -> None:
    """Sonst laege sie unter Texten, die gerade geschrieben werden sollen."""
    from fbgroups.marketing.arbeitsseite import render_sperre

    leer = render_sperre(Sperre(Grund.KEINE_GRUPPEN), KAMPAGNE)
    pausiert = render_sperre(Sperre(Grund.PAUSIERT), KAMPAGNE)

    assert "Texte vorbereiten" in leer
    assert "Texte vorbereiten" not in pausiert


def test_text_und_freigabe_und_einreihen_ueber_den_dienst(
    bestand: Path, config
) -> None:
    """Die ganze Kette per Knopf - ohne ein einziges Terminal."""
    client = _client(bestand, config)

    text = client.post(f"/kampagnen/{KAMPAGNE}/vorbereiten", json={"schritt": "text"})
    assert text.status_code == 200
    # Gezaehlt werden die Fassungen, nicht die Gruppen: Seit eine Gruppe fuenf
    # davon bekommt, ist "2 Texte" bei zwei Gruppen eine andere Auskunft.
    assert text.json()["betroffen"] > len(GRUPPEN)

    frei = client.post(f"/kampagnen/{KAMPAGNE}/vorbereiten", json={"schritt": "approve"})
    assert frei.json()["betroffen"] == len(GRUPPEN)

    reihe = client.post(f"/kampagnen/{KAMPAGNE}/vorbereiten", json={"schritt": "enqueue"})
    assert reihe.json()["eingereiht"] == len(GRUPPEN)


def test_freigeben_ohne_text_meldet_den_grund(bestand: Path, config) -> None:
    """"0 betroffen" allein liesse offen, woran es lag."""
    antwort = _client(bestand, config).post(
        f"/kampagnen/{KAMPAGNE}/vorbereiten", json={"schritt": "approve"}
    )

    assert antwort.json()["betroffen"] == 0
    assert "ohne Text" in antwort.json()["hinweis"]


def test_die_werkbank_ist_von_aussen_nicht_bedienbar(bestand: Path, config) -> None:
    """Sie schreibt - wie jeder andere schreibende Weg hinter ``_nur_lokal``."""
    fremd = _client(bestand, config, client=("203.0.113.7", 44321))

    assert fremd.post(
        f"/kampagnen/{KAMPAGNE}/vorbereiten", json={"schritt": "enqueue"}
    ).status_code == 404


def test_texte_neu_erzeugen_ueberschreibt_auch_handarbeit(
    bestand: Path, config
) -> None:
    """Der Weg fuer geaenderte Vorlagen - und er verwirft Handarbeit.

    Deshalb ein eigener Schritt und keine stillere Vorgabe.
    """
    client = _client(bestand, config)
    client.post(f"/kampagnen/{KAMPAGNE}/vorbereiten", json={"schritt": "text"})
    gid = next(iter(GRUPPEN))
    with MarketingStore(bestand) as store:
        store.setze_vorschlag_text(KAMPAGNE, gid, Texttyp.POST, 2, "Von Hand {link}")

    client.post(f"/kampagnen/{KAMPAGNE}/vorbereiten", json={"schritt": "text_neu"})

    with MarketingStore(bestand) as store:
        assert store.vorschlag(KAMPAGNE, gid, Texttyp.POST, 2).text != "Von Hand {link}"


def test_texte_erzeugen_laesst_handarbeit_stehen(bestand: Path, config) -> None:
    """Handarbeit ueberlebt einen Sammelschritt - wie Notizen einen Reimport."""
    client = _client(bestand, config)
    client.post(f"/kampagnen/{KAMPAGNE}/vorbereiten", json={"schritt": "text"})
    gid = next(iter(GRUPPEN))
    with MarketingStore(bestand) as store:
        store.setze_vorschlag_text(KAMPAGNE, gid, Texttyp.POST, 2, "Von Hand {link}")

    client.post(f"/kampagnen/{KAMPAGNE}/vorbereiten", json={"schritt": "text"})

    with MarketingStore(bestand) as store:
        vorschlag = store.vorschlag(KAMPAGNE, gid, Texttyp.POST, 2)
    assert vorschlag.text == "Von Hand {link}"
    # Die Vergleichsgroesse wird trotzdem aufgefrischt.
    assert vorschlag.generated_text.strip()
    assert vorschlag.generated_text != vorschlag.text


def test_ein_unbekannter_schritt_wird_abgewiesen(bestand: Path, config) -> None:
    antwort = _client(bestand, config).post(
        f"/kampagnen/{KAMPAGNE}/vorbereiten", json={"schritt": "veroeffentlichen"}
    )

    assert antwort.status_code == 422


def test_ein_veroeffentlichter_beitrag_bleibt_unangetastet(
    bestand: Path, config
) -> None:
    """Der Text steht dort schon in der Gruppe - ihn zu aendern hiesse luegen."""
    client = _client(bestand, config)
    client.post(f"/kampagnen/{KAMPAGNE}/vorbereiten", json={"schritt": "text"})
    gid = next(iter(GRUPPEN))
    with MarketingStore(bestand) as store:
        for stand in (JobStatus.PENDING_REVIEW, JobStatus.APPROVED, JobStatus.QUEUED,
                      JobStatus.PROCESSING, JobStatus.PUBLISHED):
            store.set_job_status(KAMPAGNE, gid, stand)
        vorher = store.vorschlag(KAMPAGNE, gid, Texttyp.POST, 1).text

    client.post(f"/kampagnen/{KAMPAGNE}/vorbereiten", json={"schritt": "text_neu"})

    with MarketingStore(bestand) as store:
        assert store.vorschlag(KAMPAGNE, gid, Texttyp.POST, 1).text == vorher


# --- Die Migration ---------------------------------------------------------

def test_ein_vorhandener_text_wird_zu_fassung_eins(bestand: Path) -> None:
    """Sonst stuenden 310 Texte in der alten Spalte und die Seite zeigte leere Felder."""
    import sqlite3

    with MarketingStore(bestand) as store:
        gid = next(iter(GRUPPEN))
        store.set_post_text(KAMPAGNE, gid, "Von damals {link}", TextQuelle.HAND)
        store.set_post_status(KAMPAGNE, gid, PostStatus.VEROEFFENTLICHT)

    conn = sqlite3.connect(bestand)
    conn.executescript("DROP TABLE campaign_group_texte; PRAGMA user_version = 14;")
    conn.commit()
    conn.close()

    with SqliteStore(bestand):
        pass

    with MarketingStore(bestand) as store:
        vorschlag = store.vorschlag(KAMPAGNE, gid, Texttyp.POST, 1)
    assert vorschlag is not None
    assert vorschlag.text == "Von damals {link}"
    assert vorschlag.quelle is TextQuelle.HAND
    # Der Stand kommt aus ``post_status`` - andersherum entstuende der
    # Eindruck, ein laengst veroeffentlichter Beitrag stuende noch aus.
    assert vorschlag.status is VorschlagStatus.VEROEFFENTLICHT


def test_ohne_text_entsteht_keine_leere_fassung(bestand: Path) -> None:
    """Eine Migration, die Texte erfindet, waere eine, die Inhalte schreibt."""
    import sqlite3

    conn = sqlite3.connect(bestand)
    conn.executescript("DROP TABLE campaign_group_texte; PRAGMA user_version = 14;")
    conn.commit()
    conn.close()

    with SqliteStore(bestand):
        pass

    with MarketingStore(bestand) as store:
        assert store.vorschlaege(KAMPAGNE, next(iter(GRUPPEN))) == []


def test_der_marketing_speicher_holt_den_schritt_ebenfalls_nach(bestand: Path) -> None:
    """``GET /r/{code}`` oeffnet nur diesen Speicher - er darf nicht scheitern."""
    import sqlite3

    conn = sqlite3.connect(bestand)
    conn.executescript("DROP TABLE campaign_group_texte; PRAGMA user_version = 14;")
    conn.commit()
    conn.close()

    with MarketingStore(bestand) as store:
        assert store.vorschlaege(KAMPAGNE, next(iter(GRUPPEN))) == []

def test_der_ersatzweg_kopiert_nie_das_bearbeitungsfeld(
    store, campaign, gruppen, gefuellt, config
) -> None:
    """Der Fehler vom 31.08.2026: ``{link}`` stand woertlich in einer Gruppe.

    Der Kopierknopf hat einen Ersatzweg fuer den Fall, dass die asynchrone
    Zwischenablage nicht erlaubt ist. Er markierte dort das **Bearbeitungs-
    feld** - und darin steht der Platzhalter, weil man den Text sonst nicht
    bearbeiten koennte. Wer auf "kopieren" drueckte und Strg+C machte, setzte
    ihn in den Beitrag.

    Geprueft wird die Seite, nicht der Browser: Dass ``feld.select()`` im
    Ersatzweg nicht mehr vorkommt und stattdessen der angezeigte Text kopiert
    wird, ist die Zusage, die den Fehler ausschliesst.
    """
    from fbgroups.marketing.arbeitsseite import render_gruppenarbeit

    stand = hole(store, campaign, gruppen)
    seite = render_gruppenarbeit(stand, KAMPAGNE, [], config)

    # Der Ersatzweg baut ein Hilfsfeld aus ``fassung.angezeigt`` - dem Text
    # **mit** eingesetztem Link.
    assert "hilfsfeld.value = fassung.angezeigt" in seite
    assert "document.execCommand('copy')" in seite

    # Und er markiert nirgends mehr das Bearbeitungsfeld.
    assert "feld.focus(); feld.select();" not in seite


def test_das_bearbeitungsfeld_setzt_den_link_beim_kopieren_ein(
    store, campaign, gruppen, gefuellt, config
) -> None:
    """Zweiter Riegel: auch Strg+C aus dem Feld heraus liefert den Link.

    Im Feld **muss** ``{link}`` stehen - sonst liesse sich der Text nicht
    bearbeiten, und ein von Hand hineingeschriebener Link ergaebe einen
    Beitrag, dessen Gruppe nie einen Klick gutgeschrieben bekaeme. Der Griff
    in das Kopier-Ereignis loest ihn genau dort auf, wo er sonst hinausginge.
    """
    from fbgroups.marketing.arbeitsseite import render_gruppenarbeit

    stand = hole(store, campaign, gruppen)
    seite = render_gruppenarbeit(stand, KAMPAGNE, [], config)

    assert "linkSchutz" in seite
    assert "addEventListener('copy'" in seite
    # Das Feld selbst bleibt unveraendert - nur die Zwischenablage bekommt
    # die fertige Fassung.
    assert "e.clipboardData.setData('text/plain'" in seite
