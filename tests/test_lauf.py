"""Die Kommentarautomatik: Reihenfolge, Rotation, Fortsetzen und Abschluss.

Der grosse Teil ist offline und ohne Datenbank - ``lauf.py`` rechnet ueber
uebergebene Werte, so wie ``kaltmodus.py``. Erst die letzten Tests fassen
einen echten Speicher an; sie pruefen genau das, was sich nicht rechnen
laesst: dass der Fortschritt einen Abbruch ueberlebt.

Die wichtigste Zusage steht in
``test_eine_fertige_gruppe_macht_die_kampagne_nicht_fertig``: Eine Kampagne
gilt erst als abgeschlossen, wenn **jede** Gruppe durch ist. Das ist Punkt 8
der Anforderung und der Grund, warum die Bedingung an genau einer Stelle
steht.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fbgroups.marketing import lauf
from fbgroups.marketing.models import (
    Campaign,
    CampaignGroup,
    CampaignStatus,
    GroupMarketing,
    KampagnenLaufStatus,
    LaufStatus,
    MarketingStatus,
    PostStatus,
    Texttyp,
    VorschlagStatus,
)
from fbgroups.marketing.qualifikation import Regelbefund
from fbgroups.marketing.store import MarketingStore
from fbgroups.models import Group
from fbgroups.storage import SqliteStore


def _gruppe(
    gid: str,
    veroeffentlicht: int = 0,
    *,
    erschoepft: bool = False,
    gescheitert: frozenset[int] = frozenset(),
    ziel: int = lauf.ZIEL_JE_GRUPPE,
    mitglied: bool = True,
    post_status: PostStatus = PostStatus.OFFEN,
    post_fassungen: frozenset[int] = frozenset(),
) -> lauf.Gruppenfortschritt:
    # ``mitglied=True`` als Vorgabe: Diese Tests pruefen Reihenfolge und
    # Rotation. Die Mitgliedschaft hat ihre eigenen Tests weiter unten - sie
    # ueberall mitzudenken machte jeden Test um eine Aussage unschaerfer.
    #
    # ``regeln_gelesen=True`` aus demselben Grund (13.09.2026): Seit der
    # Regelschritt vor der Arbeit steht, bekaeme eine Gruppe mit ungelesenen
    # Regeln ihn und nicht den Textschritt. Das ist richtig so und hat seinen
    # eigenen Test in ``test_zielprioritaet.py``.
    return lauf.Gruppenfortschritt(
        campaign_id="k",
        group_id=gid,
        name=f"Gruppe {gid}",
        veroeffentlicht=veroeffentlicht,
        ziel=ziel,
        erschoepft=erschoepft,
        gescheiterte_fassungen=gescheitert,
        mitglied=mitglied,
        regeln_gelesen=True,
        post_status=post_status,
        post_fassungen=post_fassungen,
    )


def _kampagne(cid: str, gruppen: list[lauf.Gruppenfortschritt]) -> lauf.Kampagnenfortschritt:
    return lauf.Kampagnenfortschritt(campaign_id=cid, name=f"Kampagne {cid}", gruppen=gruppen)


def _lauf(kampagnen: list[lauf.Kampagnenfortschritt]) -> lauf.Lauffortschritt:
    return lauf.Lauffortschritt(lauf_id=1, status=LaufStatus.LAEUFT, kampagnen=kampagnen)


# --- Szenario D: die Vorlagenrotation --------------------------------------
def test_die_fassungen_kommen_der_reihe_nach() -> None:
    """1-2-3-4-5, und danach ist Schluss.

    Es ist keine Rotation mit Zeiger, sondern "die kleinste, die noch nicht
    heraus ist". Dieselbe Folge, aber ohne gespeicherten Stand, der von der
    Wirklichkeit abweichen koennte.
    """
    folge = [lauf.naechste_nummer(set(range(1, i + 1))) for i in range(0, 11)]
    assert folge == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, None]

    # Und die Vorlage dahinter dreht sich: 1-5, dann wieder 1-5.
    assert [lauf.vorlage_zu_nummer(n) for n in range(1, 11)] == [1, 2, 3, 4, 5, 1, 2, 3, 4, 5]


def test_eine_aufgegebene_fassung_wird_uebersprungen() -> None:
    """Sonst haengt der Lauf an derselben Fassung fest, bis jemand eingreift."""
    assert lauf.naechste_nummer({1}, {2}) == 3


def test_keine_fassung_geht_zweimal_hinaus() -> None:
    """Fuenf Fassungen, fuenf Kommentare - kein Text doppelt in derselben Gruppe.

    Zwei wortgleiche Kommentare untereinander sind das deutlichste Zeichen
    einer Maschine, das man hinterlassen kann.
    """
    heraus: set[int] = set()
    for _ in range(lauf.ZIEL_JE_GRUPPE):
        nummer = lauf.naechste_nummer(heraus)
        assert nummer is not None
        assert nummer not in heraus
        heraus.add(nummer)
    assert heraus == set(range(1, lauf.ZIEL_JE_GRUPPE + 1))
    assert lauf.naechste_nummer(heraus) is None


# --- Szenario A und E: der Abschluss ---------------------------------------
def test_drei_volle_gruppen_schliessen_die_kampagne_ab() -> None:
    """Szenario A: 3 Gruppen a 5 Kommentare = 15, dann fertig."""
    voll = lauf.ZIEL_JE_GRUPPE
    kampagne = _kampagne("k", [_gruppe(str(i), veroeffentlicht=voll) for i in range(3)])
    assert kampagne.kommentare_veroeffentlicht == 3 * voll
    assert kampagne.kommentare_ziel == 3 * voll
    assert kampagne.gruppen_fertig == 3
    assert kampagne.fertig


def test_eine_fertige_gruppe_macht_die_kampagne_nicht_fertig() -> None:
    """Punkt 8: Gruppe 1 fertig heisst **nicht** Kampagne fertig."""
    kampagne = _kampagne(
        "k", [_gruppe("a", veroeffentlicht=lauf.ZIEL_JE_GRUPPE), _gruppe("b", veroeffentlicht=0)]
    )
    assert not kampagne.fertig
    assert kampagne.gruppen_fertig == 1


def test_eine_fast_volle_gruppe_zaehlt_nicht_als_fertig() -> None:
    kampagne = _kampagne("k", [_gruppe("a", veroeffentlicht=lauf.ZIEL_JE_GRUPPE - 1)])
    assert not kampagne.fertig


def test_eine_kampagne_ohne_gruppen_ist_nicht_erfolgreich() -> None:
    """``all()`` ueber eine leere Liste ist wahr - hier waere das ein Unfall.

    Eine gerade angelegte Kampagne ohne Zuordnungen waere sonst im selben
    Augenblick "erfolgreich abgeschlossen", in dem sie entsteht. Genau diese
    Art falscher Erfolgsmeldung soll der Lauf nie geben.
    """
    assert not _kampagne("leer", []).fertig
    assert not _lauf([]).fertig


def test_der_lauf_ist_erst_mit_der_letzten_kampagne_fertig() -> None:
    """Szenario E."""
    voll = _kampagne("eins", [_gruppe("a", veroeffentlicht=lauf.ZIEL_JE_GRUPPE)])
    offen = _kampagne("zwei", [_gruppe("b", veroeffentlicht=2)])
    assert not _lauf([voll, offen]).fertig
    assert _lauf([voll]).fertig


# --- Szenario B: die Reihenfolge -------------------------------------------
def test_erst_wenn_eine_kampagne_durch_ist_kommt_die_naechste() -> None:
    """Sequentiell, nicht parallel - Punkt 13."""
    eins = _kampagne("eins", [_gruppe("a", veroeffentlicht=2)])
    zwei = _kampagne("zwei", [_gruppe("b")])
    schritt = lauf.naechster_schritt(_lauf([eins, zwei]))
    assert schritt is not None
    assert schritt.campaign_id == "eins"
    assert schritt.group_id == "a"


def test_nach_der_ersten_gruppe_kommt_die_zweite_derselben_kampagne() -> None:
    kampagne = _kampagne(
        "k", [_gruppe("a", veroeffentlicht=lauf.ZIEL_JE_GRUPPE), _gruppe("b", veroeffentlicht=1)]
    )
    schritt = lauf.naechster_schritt(_lauf([kampagne]))
    assert schritt is not None
    assert schritt.group_id == "b"
    assert schritt.nummer == 2


def test_ein_fertiger_lauf_hat_keinen_naechsten_schritt() -> None:
    kampagne = _kampagne("k", [_gruppe("a", veroeffentlicht=lauf.ZIEL_JE_GRUPPE)])
    assert lauf.naechster_schritt(_lauf([kampagne])) is None


# --- Szenario C: fortsetzen statt neu beginnen -----------------------------
def test_der_schritt_zaehlt_beim_stand_weiter_nicht_bei_eins() -> None:
    """Szenario C: nach dem Neustart geht es bei Kommentar 3 weiter, nicht bei 1."""
    kampagne = _kampagne("k", [_gruppe("a", veroeffentlicht=2)])
    schritt = lauf.naechster_schritt(_lauf([kampagne]))
    assert schritt is not None
    assert schritt.nummer == 3
    assert schritt.kommentar_nr == 3


# --- Mitgliedschaft: die Vorbedingung fuer alles Uebrige -------------------
def test_ohne_mitgliedschaft_wird_nichts_versucht() -> None:
    """Facebook laesst Nichtmitglieder nicht schreiben.

    Am 31.08.2026 waren von 36 zugeordneten Gruppen **null** auf ``mitglied``.
    Ohne diese Regel haette der erste Lauf 180 Versuche gemacht, die alle
    fehlschlagen mussten - und genau diese Folge aus einem Konto ist das
    Muster, das zur Sperre fuehrt.
    """
    kampagne = _kampagne("k", [_gruppe("a", mitglied=False)])
    assert lauf.naechster_schritt(_lauf([kampagne])) is None
    assert kampagne.gruppen_wartend == 1


def test_eine_wartende_gruppe_ist_nicht_fertig() -> None:
    """Blockiert ist nicht erledigt.

    Sonst meldete eine Kampagne "erfolgreich abgeschlossen", in der kein
    einziger Kommentar steht - die Arbeit ist nicht getan, sie ist noch nicht
    moeglich.
    """
    gruppe = _gruppe("a", mitglied=False)
    assert gruppe.wartet
    assert not gruppe.fertig
    assert not gruppe.bearbeitbar
    assert not _kampagne("k", [gruppe]).fertig


def test_ohne_pflicht_wird_auch_ohne_mitgliedschaft_kommentiert() -> None:
    """Der Schalter aus ``settings.yaml`` hebt die Vorbedingung auf.

    Vom Nutzer am 01.09.2026 entschieden: In "Betaraqiq-Test Syrer in Berlin"
    standen drei veroeffentlichte Kommentare, waehrend der Stand auf
    ``beitritt_angefragt`` stand. Der Vermerk sagt also etwas ueber unseren
    Arbeitsstand und nicht darueber, ob Facebook dort schreiben laesst.
    """
    gruppe = lauf.Gruppenfortschritt(
        campaign_id="k",
        group_id="ohne",
        name="Gruppe ohne",
        veroeffentlicht=0,
        mitglied=False,
        mitgliedschaft_noetig=False,
    )
    assert gruppe.bearbeitbar
    assert not gruppe.wartet

    kampagne = _kampagne("k", [gruppe])
    assert kampagne.gruppen_wartend == 0
    schritt = lauf.naechster_schritt(_lauf([kampagne]))
    assert schritt is not None
    assert schritt.group_id == "ohne"


def test_der_schalter_kommt_aus_der_konfiguration(config) -> None:
    """``automatik.mitgliedschaft_pflicht`` - und die Vorgabe im Code ist wahr.

    Die beiden widersprechen sich nicht: Der Code behaelt den Schutz fuer den
    Fall, dass niemand etwas gesagt hat; gesagt wird es in der Konfiguration.
    """
    from fbgroups.marketing import automatik

    assert automatik.mitgliedschaft_pflicht(config) is False
    config.settings.pop("automatik", None)
    assert automatik.mitgliedschaft_pflicht(config) is True


def test_mitgliedslose_gruppen_werden_uebersprungen_nicht_blockiert() -> None:
    """Die naechste bearbeitbare Gruppe kommt dran, nicht die naechste ueberhaupt."""
    kampagne = _kampagne(
        "k", [_gruppe("ohne", mitglied=False), _gruppe("mit", mitglied=True)]
    )
    schritt = lauf.naechster_schritt(_lauf([kampagne]))
    assert schritt is not None
    assert schritt.group_id == "mit"


def test_die_meldung_nennt_die_wartenden_gruppen() -> None:
    """Der haeufigste Grund fuer einen kurzen Lauf - er muss dastehen.

    Ohne diesen Satz sucht man den Fehler in der Technik statt im Konto.
    """
    kampagne = _kampagne("k", [_gruppe("a", mitglied=False)])
    text = lauf.abschlusstext(_lauf([kampagne]))
    assert "ohne Mitgliedschaft" in text
    assert "marketing set" in text
    assert "erfolgreich abgeschlossen" not in text


def test_eine_offene_beitrittsanfrage_ist_keine_mitgliedschaft(bestand: Path) -> None:
    """``beitritt_angefragt`` zaehlt nicht - Facebook laesst oft wochenlang offen.

    Wer sie mitzaehlte, liefe genau in die Fehlversuche, die diese Regel
    verhindern soll.
    """
    with SqliteStore(bestand) as s:
        gruppen = {g.group_id: g for g in s.load_groups()}

    with MarketingStore(bestand) as store:
        store.save_marketing(
            GroupMarketing(group_id="111", marketing_status=MarketingStatus.JOIN_REQUESTED)
        )
        lauf_id = store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)
        stand = lauf.lies_fortschritt(store, lauf_id, gruppen)

    erste = next(g for g in stand.kampagnen[0].gruppen if g.group_id == "111")
    assert not erste.mitglied
    assert erste.wartet


# --- Der dritte Ausgang ----------------------------------------------------
def test_eine_erschoepfte_gruppe_haelt_die_kampagne_nicht_auf() -> None:
    """Ohne diesen Ausgang haengt die Kampagne fuer immer bei 2 von 5."""
    kampagne = _kampagne("k", [_gruppe("a", veroeffentlicht=2, erschoepft=True)])
    assert kampagne.fertig
    assert kampagne.gruppen_erschoepft == 1
    assert kampagne.gruppen_voll == 0


def test_erschoepft_ist_kein_erfolg() -> None:
    """Erledigt und erfolgreich sind zwei Aussagen - die Meldung nennt beide."""
    voll = lauf.ZIEL_JE_GRUPPE
    kampagne = _kampagne(
        "k", [_gruppe("a", veroeffentlicht=voll), _gruppe("b", veroeffentlicht=1, erschoepft=True)]
    )
    text = lauf.abschlusstext(_lauf([kampagne]))
    assert "erfolgreich abgeschlossen" in text
    assert "erschoepft" in text
    # Die belegte Zahl, nicht das Ziel: 11 von 20, nicht 20 von 20.
    assert f"{voll + 1} / {2 * voll}" in text


def test_alle_fassungen_verbraucht_heisst_erschoepft() -> None:
    """Wenn keine Fassung mehr offen ist, das Ziel aber nicht erreicht wurde."""
    rest = frozenset(range(3, lauf.ZIEL_JE_GRUPPE + 1))
    gruppe = _gruppe("a", veroeffentlicht=2, gescheitert=rest)
    assert lauf.gruppe_ist_erschoepft(gruppe)


def test_eine_volle_gruppe_ist_nicht_erschoepft() -> None:
    assert not lauf.gruppe_ist_erschoepft(
        _gruppe("a", veroeffentlicht=lauf.ZIEL_JE_GRUPPE)
    )


# --- Die Meldungen ---------------------------------------------------------
def test_die_abschlussmeldung_behauptet_keinen_erfolg_bei_resten() -> None:
    """Punkt 8: kein falscher Erfolg."""
    voll = lauf.ZIEL_JE_GRUPPE
    kampagne = _kampagne("k", [_gruppe("a", veroeffentlicht=voll), _gruppe("b", veroeffentlicht=3)])
    text = lauf.abschlusstext(_lauf([kampagne]))
    assert "NICHT vollstaendig" in text
    assert "erfolgreich abgeschlossen" not in text
    assert f"{voll + 3} / {2 * voll}" in text


def test_der_fortschrittstext_nennt_die_laufende_gruppe() -> None:
    voll = lauf.ZIEL_JE_GRUPPE
    kampagne = _kampagne("k", [_gruppe("a", veroeffentlicht=voll), _gruppe("b", veroeffentlicht=3)])
    text = lauf.fortschrittstext(_lauf([kampagne]))
    assert "Gruppe b" in text
    assert f"3 / {voll}" in text


# --- Der Ablauf: Beitritt, Bewertung, beste Gruppen, dann Arbeit -----------
#
# Die Reihenfolge ist ab dem 12.09.2026 verbindlich und steht an genau einer
# Stelle (``lauf.naechster_schritt``). Diese Tests halten sie fest - jeder
# einzelne von ihnen wuerde fehlschlagen, wenn die Reihenfolge wieder zu einer
# Empfehlung wuerde, die der naechste Sonderfall aushebelt.


def _wartende(
    gid: str,
    *,
    angefragt: bool = False,
    pflicht: bool = True,
    regeln_gelesen: bool = True,
) -> lauf.Gruppenfortschritt:
    """Eine Gruppe, in der wir noch nicht Mitglied sind.

    ``pflicht`` ist die Vorgabe im Code (``automatik.mitgliedschaft_pflicht``):
    Dann wird dort nichts versucht, und die einzige Arbeit, die es in dieser
    Gruppe gibt, ist die Beitrittsanfrage.

    ``regeln_gelesen=True`` als Vorgabe, obwohl der Code ``False`` setzt: Seit
    dem 13.09.2026 steht vor der Beitrittsanfrage der Regelschritt
    (``Schrittart.REGELN``), und diese Tests fragen nach der Reihenfolge
    **danach**. Wer sie ungelesen laesst, bekommt statt der Anfrage den
    Regelschritt - das ist richtig so und hat seinen eigenen Test.
    """
    from fbgroups.marketing.qualifikation import Qualifikation

    return lauf.Gruppenfortschritt(
        campaign_id="k",
        group_id=gid,
        name=f"Gruppe {gid}",
        veroeffentlicht=0,
        mitglied=False,
        mitgliedschaft_noetig=pflicht,
        beitritt_noetig=not angefragt,
        regeln_gelesen=regeln_gelesen,
        post_fassungen=frozenset({1}),
        qualifikation=(
            Qualifikation.BEITRITT_ANGEFRAGT if angefragt else Qualifikation.BEITRITT_NOETIG
        ),
    )


def _mit_kontingent(
    kampagnen: list[lauf.Kampagnenfortschritt], kontingent: int = 50, wartezeit: str = ""
) -> lauf.Lauffortschritt:
    """Ein Lauf, in dem nur die **Beitrittsanfragen** begrenzt sind.

    Beitrag und Kommentar bleiben offen: Diese Tests fragen nach der
    Reihenfolge, nicht nach der Tagesmenge. Deren eigene Tests stehen in
    ``test_grenzen.py``.
    """
    from fbgroups.marketing.grenzen import Aktion, Lage

    return lauf.Lauffortschritt(
        lauf_id=1,
        status=LaufStatus.LAEUFT,
        kampagnen=kampagnen,
        aktionen={
            Aktion.BEITRITT: Lage(
                Aktion.BEITRITT,
                moeglich=kontingent > 0 and not wartezeit,
                wartezeit=wartezeit if kontingent > 0 else "",
                rest_heute=kontingent,
            ),
            Aktion.POST: Lage(Aktion.POST, True, rest_heute=99),
            Aktion.KOMMENTAR: Lage(Aktion.KOMMENTAR, True, rest_heute=99),
        },
    )


def test_die_beitrittsanfragen_kommen_vor_der_arbeit() -> None:
    """Schritt 2 vor Schritt 5 - auch wenn in derselben Kampagne Arbeit liegt.

    Die Gruppe, in der gepostet werden koennte, steht in der Liste vorn. Sie
    kommt trotzdem nicht zuerst dran: In dieser Kampagne ist noch eine
    Beitrittsanfrage offen, und die geht voraus.
    """
    kampagne = lauf.Kampagnenfortschritt(
        campaign_id="k",
        name="Kampagne k",
        gruppen=[
            _gruppe("111", post_fassungen=frozenset({1})),
            # Ohne Mitgliedschaftspflicht koennte hier sogar gearbeitet
            # werden - die offene Anfrage geht trotzdem vor.
            _wartende("222", pflicht=False),
        ],
    )
    schritt = lauf.naechster_schritt(_mit_kontingent([kampagne]))

    assert schritt is not None
    assert schritt.art is lauf.Schrittart.BEITRITT
    assert schritt.group_id == "222"


def test_ohne_tagesmenge_geht_es_ohne_beitritt_weiter() -> None:
    """Ein erschoepftes Kontingent haelt den Lauf nicht an.

    Fuenfzig Anfragen am Tag sind die Grenze, nicht das Ende der Arbeit: Was
    heute nicht mehr hinausgeht, darf die Beitraege in den Gruppen, in denen
    wir laengst Mitglied sind, nicht aufhalten.
    """
    kampagne = lauf.Kampagnenfortschritt(
        campaign_id="k",
        name="Kampagne k",
        gruppen=[_gruppe("111", post_fassungen=frozenset({1})), _wartende("222")],
    )
    schritt = lauf.naechster_schritt(_mit_kontingent([kampagne], kontingent=0))

    assert schritt is not None
    assert schritt.art is lauf.Schrittart.TEXT
    assert schritt.group_id == "111"


def test_der_takt_haelt_die_anfrage_an_aber_nicht_die_arbeit() -> None:
    """Der Mindestabstand sagt "noch nicht" - **der Anfrage**, nicht dem Lauf.

    Bis zum 13.09.2026 hiess dieser Test
    ``test_der_takt_haelt_den_lauf_an_statt_die_arbeit_vorzuziehen`` und
    verlangte ``None``: Der Treiber sollte schlafen, damit die Reihenfolge
    (erst Beitritt, dann Arbeit) keine blosse Empfehlung wird, die jeder
    Mindestabstand aushebelt.

    Das Argument stimmt fuer kleine Abstaende und kippt bei grossen. Im
    Betrieb stand "Beitrittstakt: noch 31 Min" auf dem Schirm; bei 50 offenen
    Anfragen ergab das 23,8 Stunden Schlaf und **einen** Kommentar am Tag.
    Damit war zweierlei gebrochen: Punkt 16 der Anforderung ("eine Gruppe, die
    auf Aufnahme wartet, darf die anderen nicht aufhalten") und der Grundsatz,
    fuer den es ``grenzen.py`` gibt ("wer wegen einer gebremsten Aktion alles
    anhaelt, verliert die Arbeit dort, wo nichts dagegen spricht").

    **Die Reihenfolge selbst bleibt** - sie hat ihren eigenen Test
    (``test_die_beitrittsanfragen_kommen_vor_der_arbeit``): Laesst der Takt
    die Anfrage zu, geht sie vor jeder Arbeit hinaus. Geprueft wird hier nur,
    dass der Lauf waehrend des Abstands nicht stillsteht.
    """
    kampagne = lauf.Kampagnenfortschritt(
        campaign_id="k",
        name="Kampagne k",
        gruppen=[_gruppe("111", post_fassungen=frozenset({1})), _wartende("222")],
    )
    fortschritt = _mit_kontingent([kampagne], wartezeit="noch 2 Min")

    schritt = lauf.naechster_schritt(fortschritt)
    assert schritt is not None, "der Lauf darf waehrend des Beitrittstakts nicht stehen"
    assert schritt.art is lauf.Schrittart.TEXT
    assert schritt.group_id == "111"

    # Die Wartezeit steht weiterhin da - sie gilt der **Anfrage**. Der Treiber
    # schlaeft nur, wenn es sonst nichts zu tun gibt; das prueft
    # ``test_ohne_arbeit_wartet_der_lauf_weiterhin_auf_den_takt``.
    assert fortschritt.wartet_auf_beitritt == "noch 2 Min"


def test_die_neubewertung_kommt_vor_der_arbeit() -> None:
    """Schritt 3: Erst bewerten, dann entscheiden, wo gearbeitet wird.

    Ohne diesen Schritt arbeitete der Lauf nach den Zahlen von vorgestern -
    und die Rangfolge, nach der die besten Gruppen zuerst drankommen, waere
    eine Rangfolge von damals.
    """
    kampagne = lauf.Kampagnenfortschritt(
        campaign_id="k", name="Kampagne k", gruppen=[_gruppe("111")], bewertet=False
    )
    schritt = lauf.naechster_schritt(_mit_kontingent([kampagne]))

    assert schritt is not None
    assert schritt.art is lauf.Schrittart.BEWERTEN
    assert schritt.campaign_id == "k"
    assert schritt.group_id == "", "die Bewertung gilt der Kampagne, nicht einer Gruppe"


def test_erst_beitritt_dann_bewertung_dann_arbeit() -> None:
    """Die drei Abschnitte in ihrer Reihenfolge, an einer Kampagne durchgespielt."""
    gruppen = [_gruppe("111", post_fassungen=frozenset({1})), _wartende("222")]
    unbewertet = lauf.Kampagnenfortschritt(
        campaign_id="k", name="Kampagne k", gruppen=gruppen, bewertet=False
    )
    assert unbewertet.phase(beitritt_frei=True) is lauf.Phase.BEITRITT
    assert unbewertet.phase(beitritt_frei=False) is lauf.Phase.BEWERTEN

    bewertet = lauf.Kampagnenfortschritt(
        campaign_id="k", name="Kampagne k", gruppen=gruppen, bewertet=True
    )
    assert bewertet.phase(beitritt_frei=False) is lauf.Phase.ARBEIT


def test_gruppen_die_beides_nehmen_kommen_zuerst() -> None:
    """Schritt 4: Beitrag **und** Kommentare schlaegt "nur eines von beiden".

    Die linkscheue Gruppe steht in der Liste vorn - sie hat den besseren
    Score. Gearbeitet wird trotzdem zuerst dort, wo beides moeglich ist: Eine
    Gruppe, die nur die Haelfte nimmt, ist der schlechtere Platz, auch wenn
    sie thematisch besser passt.
    """
    from fbgroups.marketing.qualifikation import Qualifikation

    halb = lauf.Gruppenfortschritt(
        campaign_id="k",
        group_id="111",
        name="nur Kommentare",
        veroeffentlicht=0,
        post_fassungen=frozenset({1}),
        mitglied=True,
        qualifikation=Qualifikation.OHNE_BEITRAEGE,
    )
    ganz = lauf.Gruppenfortschritt(
        campaign_id="k",
        group_id="222",
        name="beides",
        veroeffentlicht=0,
        post_fassungen=frozenset({1}),
        mitglied=True,
        qualifikation=Qualifikation.GEEIGNET,
    )
    kampagne = lauf.Kampagnenfortschritt(
        campaign_id="k", name="Kampagne k", gruppen=[halb, ganz]
    )

    assert halb.vorrang == 1
    assert ganz.vorrang == 0
    assert [g.group_id for g in kampagne.arbeitsliste] == ["222", "111"]
    assert kampagne.naechste_gruppe is not None
    assert kampagne.naechste_gruppe.group_id == "222"


def test_die_reihenfolge_innerhalb_einer_klasse_bleibt_der_score() -> None:
    """Stabil sortiert: Der Vorrang ordnet die Klassen, nicht die Gruppen darin.

    Die Liste kommt score-sortiert herein (``sort_by_rank``). Wuerde hier neu
    geordnet, gaebe es zwei Rangfolgen - und die Anzeige zeigte eine andere
    Gruppe als die, an der gearbeitet wird.
    """
    erste = _gruppe("111", post_fassungen=frozenset({1}))
    zweite = _gruppe("222", post_fassungen=frozenset({1}))
    kampagne = _kampagne("k", [erste, zweite])

    assert [g.group_id for g in kampagne.arbeitsliste] == ["111", "222"]


def test_eine_gesperrte_gruppe_gilt_nicht_als_ungeeignet() -> None:
    """Uebersprungen heisst nicht aussortiert - sie bleibt im Bestand.

    "Gibt nichts mehr her" waere ein Urteil ueber die Gruppe; hier liegt eine
    Entscheidung von uns vor, und sie kann sich aendern: durch eine Aufnahme,
    einen gelesenen Regelsatz, einen umgelegten Schalter.
    """
    from fbgroups.marketing.qualifikation import Qualifikation

    gesperrt = lauf.Gruppenfortschritt(
        campaign_id="k",
        group_id="111",
        name="ungeeignet",
        veroeffentlicht=0,
        mitglied=True,
        qualifikation=Qualifikation.UNGEEIGNET,
    )
    kampagne = _kampagne("k", [gesperrt, _gruppe("222")])

    assert gesperrt.gesperrt is True
    assert gesperrt.vorrang == 2
    assert lauf.gruppe_ist_erschoepft(gesperrt) is False
    assert gesperrt.erschoepft is False
    # Sie steht weiter in der Liste - nur eben hinten.
    assert [g.group_id for g in kampagne.arbeitsliste] == ["222", "111"]
    schritt = lauf.naechster_schritt(_mit_kontingent([kampagne], kontingent=0))
    assert schritt is not None
    assert schritt.group_id == "222"


def test_die_regeln_der_gruppe_binden_ohne_schalter() -> None:
    """Keine Links, wo Links verboten sind - auch ohne ``qualifikation.pflicht``.

    Der Schalter entscheidet ueber die **Beitrittsstufen**, nicht ueber die
    Regeln der Gruppe. Ein Schalter, der Letztere aufhoebe, waere ein
    Schalter zum Regelbruch.
    """
    from fbgroups.marketing.qualifikation import Qualifikation

    linkscheu = lauf.Gruppenfortschritt(
        campaign_id="k",
        group_id="111",
        name="ohne Links",
        veroeffentlicht=0,
        mitglied=True,
        qualifikation_pflicht=False,
        qualifikation=Qualifikation.OHNE_LINKS,
        fassungen_mit_link=frozenset({1, 2}),
        post_fassungen=frozenset({1}),
    )

    assert linkscheu.erlaubt(Texttyp.KOMMENTAR, 1) is False, "Fassung 1 traegt einen Link"
    assert linkscheu.erlaubt(Texttyp.KOMMENTAR, 3) is True, "diese nicht"
    assert linkscheu.erlaubt(Texttyp.POST, 1) is False, "ein Beitrag traegt immer einen"


def test_ohne_schalter_haelt_die_beitrittsstufe_nicht_auf() -> None:
    """Die andere Haelfte derselben Trennung.

    Ob "kein Mitglied" sperrt, ist eine Frage ueber **unseren** Stand - und
    der Nutzer hat sie am 01.09.2026 beantwortet: Der Vermerk beschreibt
    unsere Buchfuehrung, nicht das, was Facebook zulaesst.
    """
    from fbgroups.marketing.qualifikation import Qualifikation

    offen = lauf.Gruppenfortschritt(
        campaign_id="k",
        group_id="111",
        name="angefragt",
        veroeffentlicht=0,
        mitglied=False,
        mitgliedschaft_noetig=False,
        qualifikation_pflicht=False,
        qualifikation=Qualifikation.BEITRITT_ANGEFRAGT,
    )
    assert offen.erlaubt(Texttyp.KOMMENTAR, 1) is True

    gesperrt = lauf.Gruppenfortschritt(**{**offen.__dict__, "qualifikation_pflicht": True})
    assert gesperrt.erlaubt(Texttyp.KOMMENTAR, 1) is False


def test_erst_wenn_die_kampagne_durch_ist_kommt_die_naechste() -> None:
    """Schritt 6, und die Beitrittsanfrage zaehlt dabei als Arbeit.

    Eine Kampagne, in der noch keine einzige Mitgliedschaft besteht, gaebe
    sonst nichts her und wuerde uebersprungen - ausgerechnet die, fuer die es
    den Beitrittsschritt gibt.
    """
    erste = lauf.Kampagnenfortschritt(
        campaign_id="a", name="Kampagne a", gruppen=[_wartende("111")]
    )
    zweite = _kampagne("b", [_gruppe("222")])

    fortschritt = _mit_kontingent([erste, zweite])
    schritt = lauf.naechster_schritt(fortschritt)
    assert schritt is not None
    assert schritt.art is lauf.Schrittart.BEITRITT
    assert schritt.campaign_id == "a"

    # Ohne Kontingent gibt die erste Kampagne nichts mehr her - dann die zweite.
    ohne = lauf.naechster_schritt(_mit_kontingent([erste, zweite], kontingent=0))
    assert ohne is not None
    assert ohne.campaign_id == "b"


# --- Mit echtem Speicher: ueberlebt der Stand einen Abbruch? ---------------
KAMPAGNE = "lauf-test"
# Namen, die ihre eigene Einstufung tragen (13.09.2026). "Gruppe Eins" und
# "Gruppe Zwei" standen hier jahrelang - bis die Neubewertung mitten im Lauf
# die Klassifikation neu rechnete: Aus einem Namen ohne Zielgruppe und ohne
# Stadt wird die Zielprioritaet D, und in D wird nicht gearbeitet. Der Test
# scheiterte also zu Recht, nur eben an seinem eigenen Kunstnamen. Echte
# Gruppennamen tragen ihre Merkmale mit sich; die hier auch.
#
# Die **alphabetische Reihenfolge** bleibt dieselbe wie vorher (Berlin vor
# Hamburg, wie Eins vor Zwei): ``scoring._rangfolge`` bricht den Gleichstand
# am Namen, und mehrere Tests lesen "die erste Gruppe".
GRUPPEN = {"111": "Syrer in Berlin", "222": "Syrer in Hamburg"}


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
                    city="Berlin",
                    audience_tags=["syrians"],
                    score=50.0,
                    score_max=100.0,
                )
                for gid, name in GRUPPEN.items()
            ]
        )
    with MarketingStore(pfad) as store:
        store.save_campaign(
            Campaign(
                campaign_id=KAMPAGNE,
                name="Lauftest",
                language="ar",
                audiences=["syrians"],
                # Aktiv, weil ein Lauf ausschliesslich aktive Kampagnen
                # einfriert - ein Entwurf ist nicht in Betrieb.
                status=CampaignStatus.ACTIVE,
            )
        )
        # Ohne Mitgliedschaft versucht die Automatik in einer Gruppe nichts -
        # Facebook laesst Nichtmitglieder nicht schreiben.
        #
        # Und die Regeln gelten als gelesen: Seit dem 13.09.2026 steht der
        # Regelschritt vor der Arbeit, und zwar auch fuer Bestandsmitglieder.
        # Eine Gruppe mit ungelesenen Regeln bekaeme ihn statt des
        # Textschritts - richtig so, aber nicht die Frage dieser Tests.
        for gid in GRUPPEN:
            store.save_marketing(
                GroupMarketing(group_id=gid, marketing_status=MarketingStatus.MEMBER)
            )
            store.merke_regeln(gid, Regelbefund(gelesen=True))
        for i, gid in enumerate(GRUPPEN, start=1):
            store.add_link(
                CampaignGroup(
                    campaign_id=KAMPAGNE,
                    group_id=gid,
                    tracking_code=f"FB-TST-BER-{i:03d}",
                    tracking_url=f"https://example.invalid/r/FB-TST-BER-{i:03d}",
                )
            )
    return pfad


def test_der_fortschritt_wird_gelesen_nicht_gefuehrt(bestand: Path) -> None:
    """Der Kern: Der Stand steht in den Fassungen, nicht in einem Zaehler.

    Genau deshalb ueberlebt er einen Abbruch ohne Aufraeumarbeit - es wurde
    nie etwas anderes behauptet, als was in der Tabelle steht.
    """
    with SqliteStore(bestand) as s:
        gruppen = {g.group_id: g for g in s.load_groups()}

    with MarketingStore(bestand) as store:
        lauf_id = store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)
        # Die Neubewertung steht vor der Arbeit und ist hier nicht die Frage;
        # dieser Test prueft den Fortschritt. Ohne den Vermerk waere der
        # naechste Schritt die Bewertung - das prueft
        # ``test_die_neubewertung_kommt_vor_der_arbeit``.
        store.merke_bewertung(lauf_id, KAMPAGNE)
        # Und aus demselben Grund gelten die Regeln als gelesen: Seit dem
        # 13.09.2026 steht der Regelschritt noch davor. Sein eigener Test ist
        # ``test_die_regeln_werden_vor_der_anfrage_gelesen``.
        for gid in GRUPPEN:
            store.merke_regeln(gid, Regelbefund(gelesen=True))

        # Nichts getan: alles offen.
        stand = lauf.lies_fortschritt(store, lauf_id, gruppen)
        assert stand.kommentare_veroeffentlicht == 0
        assert stand.kommentare_ziel == 2 * lauf.ZIEL_JE_GRUPPE
        assert not stand.fertig

        # Zwei Fassungen einer Gruppe veroeffentlichen - ohne den Lauf
        # anzufassen. Der Fortschritt muss das trotzdem sehen.
        for nummer in (1, 2):
            store.setze_erzeugten_vorschlag(
                KAMPAGNE, "111", Texttyp.KOMMENTAR, nummer, text="Text", vorlage_key="k"
            )
            store.setze_vorschlag_stand(
                KAMPAGNE, "111", Texttyp.KOMMENTAR, nummer, VorschlagStatus.VEROEFFENTLICHT
            )

        stand = lauf.lies_fortschritt(store, lauf_id, gruppen)
        assert stand.kommentare_veroeffentlicht == 2

        # Und der naechste Schritt ist Fassung 3 - nicht 1.
        schritt = lauf.naechster_schritt(stand)
        assert schritt is not None
        assert schritt.group_id == "111"
        assert schritt.nummer == 3


def test_ein_neuer_lauf_friert_die_kampagnenliste_ein(bestand: Path) -> None:
    """Punkt 16: Eine spaeter aktivierte Kampagne greift nicht in den Lauf ein."""
    with MarketingStore(bestand) as store:
        lauf_id = store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)

        store.save_campaign(
            Campaign(campaign_id="spaeter", name="Spaeter", language="ar", audiences=["syrians"])
        )

        zeilen = store.lauf_kampagnen(lauf_id)
        assert [z["campaign_id"] for z in zeilen] == [KAMPAGNE]


def test_ein_offener_lauf_wird_fortgesetzt_nicht_neu_begonnen(bestand: Path) -> None:
    """Punkt 17: Der zweite Start nimmt den ersten Lauf wieder auf."""
    from fbgroups.marketing import automatik

    with MarketingStore(bestand) as store:
        erste, neu1 = automatik.hole_oder_starte_lauf(store, ziel_je_gruppe=5)
        zweite, neu2 = automatik.hole_oder_starte_lauf(store, ziel_je_gruppe=5)

    assert neu1 is True
    assert neu2 is False
    assert erste == zweite


def test_erschoepfung_ueberlebt_den_neustart(bestand: Path) -> None:
    """Sie ist ein Urteil nach dem Versuch - deshalb gespeichert, nicht abgeleitet."""
    with SqliteStore(bestand) as s:
        gruppen = {g.group_id: g for g in s.load_groups()}

    with MarketingStore(bestand) as store:
        lauf_id = store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)
        store.merke_bewertung(lauf_id, KAMPAGNE)  # die Bewertung ist hier nicht die Frage
        store.setze_kommentar_erschoepft(KAMPAGNE, "111", "nur 2 Beitraege vorhanden")

    with MarketingStore(bestand) as store:
        stand = lauf.lies_fortschritt(store, lauf_id, gruppen)
        erste = next(g for g in stand.kampagnen[0].gruppen if g.group_id == "111")
        assert erste.erschoepft
        assert "2 Beitraege" in erste.erschoepft_grund
        # Und sie haelt den Lauf nicht mehr auf:
        schritt = lauf.naechster_schritt(stand)
        assert schritt is not None
        assert schritt.group_id == "222"


def test_der_lauf_wird_erst_am_ende_fertig_gemeldet(bestand: Path) -> None:
    """Szenario E mit echtem Speicher - beide Gruppen, dann erst 'fertig'."""
    with SqliteStore(bestand) as s:
        gruppen = {g.group_id: g for g in s.load_groups()}

    with MarketingStore(bestand) as store:
        lauf_id = store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)

        for gid in GRUPPEN:
            for nummer in range(1, lauf.ZIEL_JE_GRUPPE + 1):
                store.setze_erzeugten_vorschlag(
                    KAMPAGNE, gid, Texttyp.KOMMENTAR, nummer, text="Text", vorlage_key="k"
                )
            if gid == "111":  # nur die erste Gruppe fertig machen
                for nummer in range(1, lauf.ZIEL_JE_GRUPPE + 1):
                    store.setze_vorschlag_stand(
                        KAMPAGNE, gid, Texttyp.KOMMENTAR, nummer, VorschlagStatus.VEROEFFENTLICHT
                    )

        stand = lauf.lies_fortschritt(store, lauf_id, gruppen)
        assert stand.gruppen_fertig == 1
        assert not stand.fertig, "eine fertige Gruppe ist keine fertige Kampagne"

        for nummer in range(1, lauf.ZIEL_JE_GRUPPE + 1):
            store.setze_vorschlag_stand(
                KAMPAGNE, "222", Texttyp.KOMMENTAR, nummer, VorschlagStatus.VEROEFFENTLICHT
            )

        stand = lauf.lies_fortschritt(store, lauf_id, gruppen)
        assert stand.fertig
        assert stand.kommentare_veroeffentlicht == 2 * lauf.ZIEL_JE_GRUPPE
        assert "erfolgreich abgeschlossen" in lauf.abschlusstext(stand)


def test_der_lauf_status_kommt_aus_der_datenbank(bestand: Path) -> None:
    with SqliteStore(bestand) as s:
        gruppen = {g.group_id: g for g in s.load_groups()}
    with MarketingStore(bestand) as store:
        lauf_id = store.starte_lauf([KAMPAGNE], ziel_je_gruppe=5)
        assert lauf.lies_fortschritt(store, lauf_id, gruppen).status is LaufStatus.LAEUFT
        store.setze_lauf_status(lauf_id, LaufStatus.FERTIG.value)
        assert lauf.lies_fortschritt(store, lauf_id, gruppen).status is LaufStatus.FERTIG
        assert store.lauf(lauf_id)["beendet_am"] is not None


# --- Die Schleife selbst: Szenario A und B ohne Browser -------------------
class _Konfig:
    """Die echte Projektkonfiguration, nur mit umgebogenem Datenbankpfad.

    Ein duennerer Stub waere verlockend, ginge aber am Zweck vorbei: Der
    Treiber reicht ``config`` bis in ``beitrag.mit_link`` durch, wo ``{datum}``
    aus ``textvorlagen`` aufgeloest wird. Mit einem Stub prueft der Test dann
    eine Konfiguration, die es nicht gibt.

    Der Kaltmodus ist hier aus, weil er eine andere Frage beantwortet (in
    welchem Takt?) als dieser Test (in welcher Reihenfolge?). Seine eigenen
    Tests stehen in ``test_kaltmodus.py``.

    Aus demselben Grund sind die **Grenzen je Aktion** hier weit gestellt:
    ``limits`` und ``delays`` beantworten "wie viel und wie schnell", diese
    Tests fragen "in welcher Reihenfolge". Wer beides in einem Test prueft,
    erfaehrt beim naechsten Fehlschlag nicht, welche der beiden Fragen falsch
    beantwortet wurde. Ihre eigenen Tests stehen in ``test_grenzen.py``.
    """

    def __init__(self, pfad: Path) -> None:
        from fbgroups.config import load_config

        self._echt = load_config()
        self._pfad = pfad

    def __getattr__(self, name: str):
        return getattr(self._echt, name)

    def path(self, name: str) -> Path:
        return self._pfad if name == "sqlite_path" else self._echt.path(name)

    def get(self, *pfad, default=None):
        if pfad[:2] == ("kaltmodus", "aktiv"):
            return False
        if pfad[:1] == ("limits",) and pfad[-1:] == ("daily",):
            return 1000
        if pfad[-1:] == ("je_gruppe_taeglich",):
            # 0 heisst **ohne Schranke** (anders als bei ``daily``, wo 0 "gar
            # nicht" heisst). Dieselbe Begruendung wie fuer die weiten
            # Tagesmengen daneben: Diese Tests fragen nach der Reihenfolge,
            # nicht nach dem Takt. Die Schranke hat ihren Test in
            # ``test_grenzen.py``.
            return 0
        if pfad[:1] == ("delays",):
            return 0
        if pfad[:2] == ("beitritt", "mindestabstand_minuten"):
            return 0
        return self._echt.get(*pfad, default=default)


def _texte_anlegen(store: MarketingStore, campaign_id: str, gruppen: list[str]) -> None:
    """Fuenf Kommentarfassungen je Gruppe - mit ``{link}`` wie jede echte Vorlage.

    Der Platzhalter gehoert dazu und ist nicht Beiwerk: ``beitrag.mit_link``
    setzt dort den Tracking-Link ein, und ein Text ohne ihn ergaebe einen
    Kommentar, dessen Gruppe nie einen Klick gutgeschrieben bekaeme.
    """
    for gid in gruppen:
        for nummer in range(1, lauf.ZIEL_JE_GRUPPE + 1):
            store.setze_erzeugten_vorschlag(
                campaign_id,
                gid,
                Texttyp.KOMMENTAR,
                nummer,
                text=f"Text {nummer}\n{{link}}",
                vorlage_key="k",
            )


def test_die_schleife_arbeitet_alle_gruppen_ab(bestand: Path) -> None:
    """Szenario A/B: ein Start, danach laeuft alles allein durch.

    ``ausfuehren`` zaehlt, statt zu kommentieren - damit prueft dieser Test
    genau die Schleife und nicht den Browser.
    """
    from fbgroups.marketing import automatik

    with MarketingStore(bestand) as store:
        _texte_anlegen(store, KAMPAGNE, list(GRUPPEN))
        store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)

    gesehen: list[str] = []

    def ausfuehren(
        url: str,
        group_id: str,
        text: str,
        texttyp: str = "kommentar",
        link_url: str = "",
    ) -> automatik.Schrittergebnis:
        gesehen.append(group_id)
        return automatik.Schrittergebnis(erfolg=True, post_url=f"p{len(gesehen)}")

    fortschritt = automatik.fuehre_lauf_aus(_Konfig(bestand), ausfuehren=ausfuehren)

    voll = lauf.ZIEL_JE_GRUPPE
    assert len(gesehen) == 2 * voll, "zwei Gruppen a zehn Kommentare"
    assert fortschritt.fertig
    assert fortschritt.kommentare_veroeffentlicht == 2 * voll
    # Erst die eine Gruppe ganz, dann die andere - nicht abwechselnd.
    assert gesehen[:voll] == [gesehen[0]] * voll
    assert gesehen[voll:] == [gesehen[voll]] * voll
    assert gesehen[0] != gesehen[voll]


def _beitragstexte_anlegen(store: MarketingStore, campaign_id: str, gruppen: list[str]) -> None:
    """Je Gruppe **eine** Beitragsfassung - der Beitrag steht einmal, nicht zehnmal."""
    for gid in gruppen:
        store.setze_erzeugten_vorschlag(
            campaign_id,
            gid,
            Texttyp.POST,
            1,
            text="Beitrag fuer diese Gruppe {link}",
            vorlage_key="p",
        )


def test_in_jeder_gruppe_zuerst_der_beitrag_dann_die_kommentare(bestand: Path) -> None:
    """Der ganze Weg einmal durch: Beitrag, zehn Kommentare, naechste Gruppe."""
    from fbgroups.marketing import automatik

    with MarketingStore(bestand) as store:
        _texte_anlegen(store, KAMPAGNE, list(GRUPPEN))
        _beitragstexte_anlegen(store, KAMPAGNE, list(GRUPPEN))
        store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)

    gesehen: list[tuple[str, str]] = []

    def ausfuehren(
        url: str,
        group_id: str,
        text: str,
        texttyp: str = "kommentar",
        link_url: str = "",
    ) -> automatik.Schrittergebnis:
        gesehen.append((group_id, texttyp))
        # Ein abgesetzter Beitrag meldet keine Adresse zurueck - genau wie
        # ``browser_schritt_post``.
        post_url = "" if texttyp == "post" else f"p{len(gesehen)}"
        return automatik.Schrittergebnis(erfolg=True, post_url=post_url)

    fortschritt = automatik.fuehre_lauf_aus(_Konfig(bestand), ausfuehren=ausfuehren)

    voll = lauf.ZIEL_JE_GRUPPE
    assert len(gesehen) == 2 * (voll + 1), "je Gruppe ein Beitrag und zehn Kommentare"
    erste = gesehen[0][0]
    assert [zweck for gid, zweck in gesehen if gid == erste] == ["post"] + ["kommentar"] * voll
    assert fortschritt.fertig
    assert fortschritt.beitraege_veroeffentlicht == 2
    assert fortschritt.kommentare_veroeffentlicht == 2 * voll


def test_ein_gescheiterter_beitrag_haelt_den_lauf_nicht_auf(bestand: Path) -> None:
    """Geht der Beitrag nicht, wird kommentiert - und zwar sofort.

    Genau ein Anlauf je Gruppe: Ein zweiter waere eine Wiederholung dessen,
    was gerade nachweislich nicht ging, aus demselben Konto.
    """
    from fbgroups.marketing import automatik

    with MarketingStore(bestand) as store:
        _texte_anlegen(store, KAMPAGNE, list(GRUPPEN))
        _beitragstexte_anlegen(store, KAMPAGNE, list(GRUPPEN))
        store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)

    gesehen: list[tuple[str, str]] = []

    def nur_beitrag_scheitert(
        url: str,
        group_id: str,
        text: str,
        texttyp: str = "kommentar",
        link_url: str = "",
    ) -> automatik.Schrittergebnis:
        gesehen.append((group_id, texttyp))
        if texttyp == "post":
            return automatik.Schrittergebnis(erfolg=False, fehler="Gruppe erlaubt keine Links")
        return automatik.Schrittergebnis(erfolg=True, post_url=f"p{len(gesehen)}")

    fortschritt = automatik.fuehre_lauf_aus(
        _Konfig(bestand), ausfuehren=nur_beitrag_scheitert
    )

    voll = lauf.ZIEL_JE_GRUPPE
    beitraege = [g for g, zweck in gesehen if zweck == "post"]
    assert sorted(beitraege) == sorted(GRUPPEN), "je Gruppe genau ein Anlauf"
    assert sum(1 for _, zweck in gesehen if zweck == "kommentar") == 2 * voll
    assert fortschritt.beitraege_veroeffentlicht == 0
    assert fortschritt.kommentare_veroeffentlicht == 2 * voll
    assert fortschritt.fertig, "ein gescheiterter Beitrag laesst die Gruppe nicht offen"


def test_die_schleife_setzt_nach_einem_abbruch_fort(bestand: Path) -> None:
    """Szenario C: Nach sechs Kommentaren abbrechen, dann weiter - nicht von vorn."""
    from fbgroups.marketing import automatik

    with MarketingStore(bestand) as store:
        _texte_anlegen(store, KAMPAGNE, list(GRUPPEN))
        store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)

    def erfolgreich(
        url: str,
        group_id: str,
        text: str,
        texttyp: str = "kommentar",
        link_url: str = "",
    ) -> automatik.Schrittergebnis:
        return automatik.Schrittergebnis(erfolg=True, post_url="p")

    erster = automatik.fuehre_lauf_aus(_Konfig(bestand), ausfuehren=erfolgreich, max_schritte=6)
    assert erster.kommentare_veroeffentlicht == 6
    assert not erster.fertig
    rest = 2 * lauf.ZIEL_JE_GRUPPE - 6

    weitere: list[int] = []

    def zaehlend(
        url: str,
        group_id: str,
        text: str,
        texttyp: str = "kommentar",
        link_url: str = "",
    ) -> automatik.Schrittergebnis:
        weitere.append(1)
        return automatik.Schrittergebnis(erfolg=True, post_url="p")

    zweiter = automatik.fuehre_lauf_aus(_Konfig(bestand), ausfuehren=zaehlend)

    assert len(weitere) == rest, "nur die restlichen, nicht wieder alle"
    assert zweiter.fertig
    assert zweiter.kommentare_veroeffentlicht == 2 * lauf.ZIEL_JE_GRUPPE


def test_dauerhafte_fehlschlaege_erschoepfen_die_gruppe(bestand: Path) -> None:
    """Punkt 4: Ein Fehler darf die Kampagne nicht faelschlich erfolgreich machen.

    Und er darf sie auch nicht ewig aufhalten: Nach ``MAX_VERSUCHE_JE_FASSUNG``
    gilt die Fassung als aufgegeben, nach allen Fassungen die Gruppe als
    erschoepft - erledigt, aber ausdruecklich nicht erfolgreich.
    """
    from fbgroups.marketing import automatik

    with MarketingStore(bestand) as store:
        _texte_anlegen(store, KAMPAGNE, list(GRUPPEN))
        store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)

    def scheitert(
        url: str,
        group_id: str,
        text: str,
        texttyp: str = "kommentar",
        link_url: str = "",
    ) -> automatik.Schrittergebnis:
        return automatik.Schrittergebnis(erfolg=False, fehler="Kommentare abgeschaltet")

    fortschritt = automatik.fuehre_lauf_aus(
        _Konfig(bestand), ausfuehren=scheitert, max_schritte=60
    )

    assert fortschritt.kommentare_veroeffentlicht == 0
    text = lauf.abschlusstext(fortschritt)
    assert f"0 / {2 * lauf.ZIEL_JE_GRUPPE}" in text
    assert "erfolgreich abgeschlossen" not in text or "erschoepft" in text


def test_ein_erschoepfter_schritt_haelt_die_gruppe_nicht_fest(bestand: Path) -> None:
    """Meldet der Schritt 'keine Beitraege mehr', geht es zur naechsten Gruppe."""
    from fbgroups.marketing import automatik

    with MarketingStore(bestand) as store:
        _texte_anlegen(store, KAMPAGNE, list(GRUPPEN))
        store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)

    gesehen: list[str] = []

    def leer(
        url: str,
        group_id: str,
        text: str,
        texttyp: str = "kommentar",
        link_url: str = "",
    ) -> automatik.Schrittergebnis:
        gesehen.append(group_id)
        return automatik.Schrittergebnis(
            erfolg=False, fehler="keine Beitraege zum Kommentieren gefunden", erschoepft=True
        )

    automatik.fuehre_lauf_aus(_Konfig(bestand), ausfuehren=leer, max_schritte=20)

    # Je Gruppe genau ein Anlauf: Danach steht sie als erschoepft fest.
    assert sorted(set(gesehen)) == sorted(GRUPPEN)
    assert len(gesehen) == 2


# --- Erst der Beitrag, dann die Kommentare --------------------------------
def test_erst_der_beitrag_dann_die_kommentare() -> None:
    """In einer Gruppe steht der eigene Beitrag vor dem ersten Kommentar.

    Die Reihenfolge ist der Zweck der Sache: Der Beitrag ist der Anlass, der
    Kommentar haengt an einem fremden.
    """
    gruppe = _gruppe("g1", post_fassungen=frozenset({1, 2, 3}))
    schritt = lauf.naechster_schritt(_lauf([_kampagne("k", [gruppe])]))

    assert schritt is not None
    assert schritt.texttyp is Texttyp.POST
    assert schritt.nummer == 1, "die kleinste vorhandene Fassung"
    assert schritt.kommentar_ziel == 1, "vom Beitrag gibt es einen, nicht zehn"


def test_nach_dem_beitrag_kommen_die_kommentare() -> None:
    """Steht der Beitrag, geht es mit Kommentar 1 weiter."""
    gruppe = _gruppe(
        "g1", post_status=PostStatus.VEROEFFENTLICHT, post_fassungen=frozenset({1})
    )
    schritt = lauf.naechster_schritt(_lauf([_kampagne("k", [gruppe])]))

    assert schritt is not None
    assert schritt.texttyp is Texttyp.KOMMENTAR
    assert schritt.nummer == 1


def test_ein_gescheiterter_beitrag_haelt_die_kommentare_nicht_auf() -> None:
    """Punkt aus der Anforderung: geht der Beitrag nicht, wird kommentiert.

    Ein Fehlschlag beim Beitrag ist ein Urteil ueber das Beitragsformular
    dieser Gruppe, keines ueber die Gruppe - die zehn Kommentare haengen
    nicht daran.
    """
    gruppe = _gruppe(
        "g1", post_status=PostStatus.FEHLGESCHLAGEN, post_fassungen=frozenset({1})
    )
    schritt = lauf.naechster_schritt(_lauf([_kampagne("k", [gruppe])]))

    assert schritt is not None
    assert schritt.texttyp is Texttyp.KOMMENTAR
    assert not gruppe.post_offen


def test_ohne_beitragstext_gibt_es_keinen_beitragsschritt() -> None:
    """Kein Text, kein Versuch - ein Schritt ohne Text koennte nur scheitern."""
    gruppe = _gruppe("g1")
    schritt = lauf.naechster_schritt(_lauf([_kampagne("k", [gruppe])]))

    assert schritt is not None
    assert schritt.texttyp is Texttyp.KOMMENTAR
    assert not gruppe.post_offen


def test_eine_gruppe_ist_erst_mit_beitrag_fertig() -> None:
    """Zehn Kommentare und kein Beitrag heisst nicht "abgearbeitet"."""
    gruppe = _gruppe(
        "g1", veroeffentlicht=lauf.ZIEL_JE_GRUPPE, post_fassungen=frozenset({1})
    )

    assert gruppe.voll, "die Kommentare sind heraus"
    assert not gruppe.fertig, "der Beitrag fehlt noch"
    assert gruppe.bearbeitbar


def test_eine_leere_kampagne_haelt_den_lauf_nicht_auf() -> None:
    """Eine Kampagne ohne Gruppen ist erledigt, aber nicht erfolgreich.

    Der Fall vom 10.09.2026: Die Kampagne des Laufs war geloescht worden,
    ihre Zuordnungen gingen per CASCADE mit, und der Lauf konnte deshalb nie
    fertig werden - er wurde bei jedem Start wieder aufgenommen und meldete
    "0 / 1", ohne etwas zu tun.
    """
    stand = _lauf([_kampagne("weg", [])])

    assert stand.kampagnen[0].leer
    assert not stand.kampagnen[0].fertig, "leer ist kein Erfolg"
    assert stand.fertig, "der Lauf ist trotzdem durch"
    assert "ohne zugeordnete Gruppen" in lauf.abschlusstext(stand)


def test_ohne_aktive_kampagne_entsteht_kein_lauf(bestand: Path) -> None:
    """Es wird nichts eingefroren, was nie fertig werden kann."""
    from fbgroups.marketing import automatik

    with MarketingStore(bestand) as store:
        for kampagne in store.load_campaigns():
            kampagne.status = CampaignStatus.PAUSED
            store.save_campaign(kampagne)

        lauf_id, neu = automatik.hole_oder_starte_lauf(
            store, ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE
        )

        assert (lauf_id, neu) == (0, False)
        assert store.offener_lauf() is None, "kein Lauf angelegt"


def test_ein_lauf_ohne_kampagnen_wird_geschlossen(bestand: Path) -> None:
    """Der leere Lauf vom 10.09.2026 laesst sich beenden.

    Entstehen kann er nicht mehr - aber die auf dem Server vorhandenen
    muessen aus dem Weg, sonst holt ``offener_lauf`` bei jedem Start
    denselben zurueck.
    """
    from fbgroups.marketing import automatik

    with SqliteStore(bestand) as s:
        gruppen = {g.group_id: g for g in s.load_groups()}

    with MarketingStore(bestand) as store:
        lauf_id = store.starte_lauf([], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)
        stand = lauf.lies_fortschritt(store, lauf_id, gruppen)

        assert not stand.fertig, "leer ist kein Erfolg"
        assert lauf.abschlusstext(stand) == lauf.OHNE_INHALT

        automatik._stand_fortschreiben(store, lauf_id, stand)

        assert store.lauf(lauf_id)["status"] == LaufStatus.FERTIG.value
        assert store.offener_lauf() is None, "er wird nicht wieder aufgenommen"


def test_die_schleife_stellt_die_anfrage_vor_dem_ersten_kommentar(bestand: Path) -> None:
    """Der Ablauf im Speicher: erst die Beitrittsanfrage, dann die Arbeit.

    Gruppe zwei ist nicht Mitglied und hat nie eine Anfrage bekommen. Der
    Lauf schickt sie los, **bevor** er in Gruppe eins den ersten Kommentar
    setzt - und traegt sie danach als angefragt ein.
    """
    from fbgroups.marketing import automatik

    with MarketingStore(bestand) as store:
        _texte_anlegen(store, KAMPAGNE, list(GRUPPEN))
        # Gruppe zwei zurueck auf Anfang: kein Mitglied, keine Anfrage.
        store.save_marketing(
            GroupMarketing(group_id="222", marketing_status=MarketingStatus.NOT_CONTACTED)
        )
        # Seit dem 13.09.2026 steht der Regelschritt vor der Anfrage. Diese
        # Tests fragen nach der **Anfrage**; die Regeln gelten hier deshalb
        # als gelesen. Dass sie vorher drankommen, hat seinen eigenen Test
        # (``test_die_regeln_werden_vor_der_anfrage_gelesen``).
        store.merke_regeln("222", Regelbefund(gelesen=True))
        store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)

    ablauf: list[str] = []

    def beitreten(url: str) -> tuple[str, str]:
        ablauf.append(f"beitritt:{url.rsplit('/', 1)[-1]}")
        return "angefragt", ""

    def ausfuehren(
        url: str,
        group_id: str,
        text: str,
        texttyp: str = "kommentar",
        link_url: str = "",
    ) -> automatik.Schrittergebnis:
        ablauf.append(f"{texttyp}:{group_id}")
        return automatik.Schrittergebnis(erfolg=True, post_url=f"p{len(ablauf)}")

    automatik.fuehre_lauf_aus(
        _Konfig(bestand),
        ausfuehren=ausfuehren,
        beitreten=beitreten,
        # Ohne Grenze liefe der Test durch zwanzig Kommentare; die Frage ist
        # die Reihenfolge der ersten Schritte.
        max_schritte=3,
        warte=lambda _s: None,
    )

    assert ablauf[0] == "beitritt:222", "die Anfrage steht vor jedem Kommentar"
    assert all(not s.startswith("beitritt") for s in ablauf[1:]), "und sie geht nur einmal raus"

    with MarketingStore(bestand) as store:
        stand = store.load_marketing("222")
        assert stand is not None
        assert stand.marketing_status is MarketingStatus.JOIN_REQUESTED
        assert stand.join_requested_at


def test_eine_gescheiterte_anfrage_haelt_den_lauf_nicht_auf(bestand: Path) -> None:
    """Sie hinterlaesst nichts im Bestand - und trotzdem kommt sie nicht wieder.

    ``beitritt_angefragt`` zu setzen waere die Behauptung, es sei etwas
    abgeschickt worden. Ohne Vermerk boete der naechste Durchgang dieselbe
    Gruppe erneut an, und der Lauf drehte sich um sie: Deshalb wird sie fuer
    **diesen** Lauf beiseitegelegt.
    """
    from fbgroups.marketing import automatik

    with MarketingStore(bestand) as store:
        _texte_anlegen(store, KAMPAGNE, list(GRUPPEN))
        store.save_marketing(
            GroupMarketing(group_id="222", marketing_status=MarketingStatus.NOT_CONTACTED)
        )
        # Seit dem 13.09.2026 steht der Regelschritt vor der Anfrage. Diese
        # Tests fragen nach der **Anfrage**; die Regeln gelten hier deshalb
        # als gelesen. Dass sie vorher drankommen, hat seinen eigenen Test
        # (``test_die_regeln_werden_vor_der_anfrage_gelesen``).
        store.merke_regeln("222", Regelbefund(gelesen=True))
        lauf_id = store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)

    versuche: list[str] = []

    def beitreten(url: str) -> tuple[str, str]:
        versuche.append(url)
        raise RuntimeError("Browserfenster zu")

    def ausfuehren(
        url: str,
        group_id: str,
        text: str,
        texttyp: str = "kommentar",
        link_url: str = "",
    ) -> automatik.Schrittergebnis:
        return automatik.Schrittergebnis(erfolg=True, post_url="p")

    fortschritt = automatik.fuehre_lauf_aus(
        _Konfig(bestand),
        ausfuehren=ausfuehren,
        beitreten=beitreten,
        warte=lambda _s: None,
    )

    assert len(versuche) == 1, "genau ein Anlauf, nicht bei jedem Durchgang einer"
    assert fortschritt.kommentare_veroeffentlicht > 0, "die andere Gruppe wurde bearbeitet"

    with MarketingStore(bestand) as store:
        stand = store.load_marketing("222")
        assert stand is None or stand.marketing_status is MarketingStatus.NOT_CONTACTED
        assert (KAMPAGNE, "222") in store.uebersprungene_gruppen(lauf_id)


# --- Fehlerisolierung: ein Fehler kostet eine Gruppe, nicht den Lauf -------
#
# Die Zusage vom 12.09.2026. Sie ist nicht dieselbe wie "ein Fehlschlag wird
# gebucht": Gebucht wird ein *Ausgang* - der Kommentar ging nicht durch. Hier
# geht es um das, was **daneben** schiefgeht: ein abgestuerzter Browser, eine
# Vorlage, die wirft, eine Zuordnung, die verschwunden ist. Frueher endete
# damit der ganze Lauf, und die uebrigen dreihundert Gruppen kamen nie dran.


def _zweite_kampagne(pfad: Path, campaign_id: str, gruppen: list[str]) -> None:
    """Eine zweite aktive Kampagne ueber dieselben Gruppen."""
    with MarketingStore(pfad) as store:
        store.save_campaign(
            Campaign(
                campaign_id=campaign_id,
                name=f"Kampagne {campaign_id}",
                language="ar",
                audiences=["syrians"],
                status=CampaignStatus.ACTIVE,
            )
        )
        for i, gid in enumerate(gruppen, start=1):
            store.add_link(
                CampaignGroup(
                    campaign_id=campaign_id,
                    group_id=gid,
                    tracking_code=f"FB-ZWO-BER-{i:03d}",
                    tracking_url=f"https://example.invalid/r/FB-ZWO-BER-{i:03d}",
                )
            )
        _texte_anlegen(store, campaign_id, gruppen)


def test_ein_fehler_bei_einer_gruppe_haelt_den_lauf_nicht_auf(bestand: Path) -> None:
    """Gruppe zwei wirft - Gruppe eins wird trotzdem fertig.

    Geworfen wird hier **nicht** in ``ausfuehren`` (das faengt der Aufrufer
    ab und meldet einen Ausgang), sondern beim Vorbereiten des Textes: der
    Fall, den frueher niemand abfing.
    """
    from fbgroups.marketing import automatik
    from fbgroups.marketing.beitrag import mit_link as echtes_mit_link

    with MarketingStore(bestand) as store:
        _texte_anlegen(store, KAMPAGNE, list(GRUPPEN))
        store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)

    kaputt = "222"

    def mit_link(campaign, link, text, **rest):  # noqa: ANN001, ANN202
        if link.group_id == kaputt:
            raise RuntimeError("Vorlage kaputt")
        return echtes_mit_link(campaign, link, text, **rest)

    gesehen: list[str] = []

    def ausfuehren(
        url: str,
        group_id: str,
        text: str,
        texttyp: str = "kommentar",
        link_url: str = "",
    ) -> automatik.Schrittergebnis:
        gesehen.append(group_id)
        return automatik.Schrittergebnis(erfolg=True, post_url=f"p{len(gesehen)}")

    import fbgroups.marketing.beitrag as beitrag_modul

    echte = beitrag_modul.mit_link
    beitrag_modul.mit_link = mit_link
    try:
        fortschritt = automatik.fuehre_lauf_aus(_Konfig(bestand), ausfuehren=ausfuehren)
    finally:
        beitrag_modul.mit_link = echte

    assert gesehen, "die heile Gruppe wurde bearbeitet"
    assert kaputt not in gesehen, "die kaputte nicht"
    assert len(gesehen) == lauf.ZIEL_JE_GRUPPE, "die heile ganz, nicht nur einmal"
    # Die kaputte Gruppe ist uebersprungen - und ausdruecklich nicht erschoepft.
    stand = {g.group_id: g for k in fortschritt.kampagnen for g in k.gruppen}
    assert stand[kaputt].uebersprungen is True
    assert "Vorlage kaputt" in stand[kaputt].uebersprungen_grund
    assert stand[kaputt].erschoepft is False


def test_der_uebersprungene_vermerk_gilt_nur_fuer_diesen_lauf(bestand: Path) -> None:
    """Ein Fehler von heute ist kein Urteil ueber morgen.

    Der Unterschied zu ``kommentar_erschoepft``: Jenes sagt "diese Gruppe gibt
    nichts mehr her" und bleibt stehen. Dieser Vermerk haengt an der
    ``lauf_id`` - im naechsten Lauf ist die Gruppe wieder dabei.
    """
    with MarketingStore(bestand) as store:
        erster = store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)
        store.ueberspringe_gruppe(erster, KAMPAGNE, "111", "Browser abgestuerzt")

        assert store.uebersprungene_gruppen(erster) == {
            (KAMPAGNE, "111"): "Browser abgestuerzt"
        }
        # Der erste Grund bleibt stehen - was danach kommt, sind meist Folgen.
        store.ueberspringe_gruppe(erster, KAMPAGNE, "111", "und noch etwas")
        assert store.uebersprungene_gruppen(erster)[(KAMPAGNE, "111")] == "Browser abgestuerzt"

        store.setze_lauf_status(erster, LaufStatus.FERTIG.value)
        zweiter = store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)
        assert store.uebersprungene_gruppen(zweiter) == {}


def test_ein_fehler_bei_einer_kampagne_haelt_die_naechste_nicht_auf(bestand: Path) -> None:
    """Punkt 4 der Fehlerisolierung: auch eine ganze Kampagne darf ausfallen.

    Gelesen wird je Kampagne (``_lies_kampagne``); wirft eine, kommt sie leer
    und gescheitert in die Liste, und der Lauf arbeitet die naechste ab. Ohne
    diese Trennung nahm eine kaputte Zuordnung dreihundert Beitraege in
    anderen Kampagnen mit.
    """
    _zweite_kampagne(bestand, "zweite", list(GRUPPEN))
    with MarketingStore(bestand) as store:
        _texte_anlegen(store, KAMPAGNE, list(GRUPPEN))
        lauf_id = store.starte_lauf(
            [KAMPAGNE, "zweite"], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE
        )
        store.merke_bewertung(lauf_id, KAMPAGNE)
        store.merke_bewertung(lauf_id, "zweite")

    with SqliteStore(bestand) as s:
        gruppen = {g.group_id: g for g in s.load_groups()}

    class _Kaputt(MarketingStore):
        """Ein Speicher, dem die erste Kampagne unter der Hand wegbricht."""

        def kommentarstand(self, campaign_id: str) -> dict[str, int]:
            if campaign_id == KAMPAGNE:
                raise RuntimeError("Tabelle weg")
            return super().kommentarstand(campaign_id)

    with _Kaputt(bestand) as store:
        fortschritt = lauf.lies_fortschritt(store, lauf_id, gruppen)

    erste, zweite = fortschritt.kampagnen
    assert erste.campaign_id == KAMPAGNE
    assert erste.status is KampagnenLaufStatus.GESCHEITERT
    assert erste.leer is True, "leer haelt den Lauf nicht auf"
    assert zweite.campaign_id == "zweite"
    assert zweite.gruppen_gesamt == len(GRUPPEN)

    # Und gearbeitet wird in der zweiten.
    schritt = lauf.naechster_schritt(fortschritt)
    assert schritt is not None
    assert schritt.campaign_id == "zweite"


def test_ein_haengender_schritt_wird_beiseitegelegt(bestand: Path) -> None:
    """Die Notbremse: Derselbe Schritt viermal, dann ist die Gruppe dran gewesen.

    Jeder bekannte Weg schreibt seinen Ausgang - aber "jeder bekannte Weg" ist
    genau die Annahme, die eine Endlosschleife widerlegt. Ein Lauf, der nichts
    mehr tut und trotzdem nicht aufhoert, ist schlimmer als eine Gruppe
    weniger.
    """
    from fbgroups.marketing import automatik

    schritt = lauf.Schritt(campaign_id="k", group_id="111", nummer=1)
    waechter = automatik._Schleifenwaechter()

    assert [waechter.haengt(schritt) for _ in range(5)] == [
        False,
        False,
        False,
        True,
        True,
    ]
    # Ein anderer Schritt setzt die Zaehlung zurueck - er ist ja Fortschritt.
    anderer = lauf.Schritt(campaign_id="k", group_id="222", nummer=1)
    assert waechter.haengt(anderer) is False


def test_ein_offener_lauf_uebersieht_eine_neue_kampagne(bestand: Path) -> None:
    """Die eingefrorene Liste ist eine Zusage - und genau deshalb ein Hindernis.

    Wer eine Kampagne anlegt, waehrend ein Lauf offen ist, wartet sonst
    vergeblich: Sie kommt nie dran, und von aussen sieht das aus, als taete
    die Automatik nichts.
    """
    from fbgroups.marketing import automatik

    with MarketingStore(bestand) as store:
        erster = store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)
        _zweite_kampagne(bestand, "spaeter", list(GRUPPEN))

        lauf_id, neu = automatik.hole_oder_starte_lauf(
            store, ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE
        )
        assert (lauf_id, neu) == (erster, False)
        assert [z["campaign_id"] for z in store.lauf_kampagnen(lauf_id)] == [KAMPAGNE]


def test_neu_friert_eine_frische_kampagnenliste_ein(bestand: Path) -> None:
    """Der Ausweg, und er ist ein eigener Handgriff.

    Verloren geht dabei **nur** die Liste: Der Fortschritt steht in den
    Fassungen und wird gelesen, nicht gefuehrt - der neue Lauf sieht dieselben
    veroeffentlichten Kommentare wie der alte.
    """
    from fbgroups.marketing import automatik

    with MarketingStore(bestand) as store:
        erster = store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)
        _zweite_kampagne(bestand, "spaeter", list(GRUPPEN))

        lauf_id, neu = automatik.hole_oder_starte_lauf(
            store, ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE, neu=True
        )

        assert neu is True
        assert lauf_id != erster
        assert [z["campaign_id"] for z in store.lauf_kampagnen(lauf_id)] == [
            KAMPAGNE,
            "spaeter",
        ]
        # Der alte Lauf ist abgeschlossen und wird nicht wieder angeboten.
        assert store.lauf(erster)["status"] == LaufStatus.FERTIG.value
        offen = store.offener_lauf()
        assert offen is not None
        assert int(offen["lauf_id"]) == lauf_id


def test_neu_wirkt_auch_ueber_den_server(bestand: Path) -> None:
    """Derselbe Handgriff im Fernbetrieb - sonst gaebe es ihn nur oertlich.

    Der Bestand liegt auf dem Server; ein Ausweg, den man nur auf dem
    Arbeitsrechner haette, waere dort wirkungslos.
    """
    with MarketingStore(bestand) as store:
        _texte_anlegen(store, KAMPAGNE, list(GRUPPEN))
        erster = store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)
    _zweite_kampagne(bestand, "spaeter", list(GRUPPEN))

    client = _fern_client(bestand)
    client.post("/automatik/naechster", json={"kampagnen": [], "neu": True})

    with MarketingStore(bestand) as store:
        assert store.lauf(erster)["status"] == LaufStatus.FERTIG.value
        offen = store.offener_lauf()
        assert offen is not None
        assert [z["campaign_id"] for z in store.lauf_kampagnen(int(offen["lauf_id"]))] == [
            KAMPAGNE,
            "spaeter",
        ]


# --- Der tote Browser: ein Fehler des Rechners, keiner der Gruppen ---------
#
# Am 11.09.2026 hat ein geschlossenes Fenster 45 Gruppen als erschoepft
# vermerkt, am 12.09.2026 noch einmal 78 - vier Kampagnen standen auf "fertig"
# mit null Kommentaren. Beides sind Urteile, die niemand gefaellt hat.


def test_ein_technischer_fehlschlag_erschoepft_keine_gruppe(bestand: Path) -> None:
    """Der Zaehler unterscheidet jetzt, woran ein Versuch gescheitert ist.

    Zehn Fassungen, jede dreimal am geschlossenen Browser gescheitert - und
    trotzdem ist keine aufgegeben. Dieselbe Trennung, die
    ``qualifikation.Beobachtung`` schon macht; sie fehlte nur hier.
    """
    with MarketingStore(bestand) as store:
        _texte_anlegen(store, KAMPAGNE, list(GRUPPEN))
        for nummer in range(1, lauf.ZIEL_JE_GRUPPE + 1):
            for _ in range(lauf.MAX_VERSUCHE_JE_FASSUNG):
                store.setze_vorschlag_stand(
                    KAMPAGNE,
                    "111",
                    Texttyp.KOMMENTAR,
                    nummer,
                    VorschlagStatus.FEHLGESCHLAGEN,
                    fehler=(
                        "BrowserContext.new_page: Target page, context or browser "
                        "has been closed"
                    ),
                )

        aufgegeben = store.gescheiterte_kommentarfassungen(
            KAMPAGNE, lauf.MAX_VERSUCHE_JE_FASSUNG
        )

    assert aufgegeben.get("111", set()) == set(), "die Technik verurteilt keine Gruppe"


def test_eine_ablehnung_der_gruppe_zaehlt_weiterhin(bestand: Path) -> None:
    """Die andere Haelfte: Was die Gruppe sagt, bleibt ein Urteil.

    Ohne diesen Fall waere die Aenderung eine Abschaltung der Grenze - und
    der Lauf liefe ewig gegen eine Gruppe, die ihn nicht will.
    """
    with MarketingStore(bestand) as store:
        _texte_anlegen(store, KAMPAGNE, list(GRUPPEN))
        for _ in range(lauf.MAX_VERSUCHE_JE_FASSUNG):
            store.setze_vorschlag_stand(
                KAMPAGNE,
                "111",
                Texttyp.KOMMENTAR,
                1,
                VorschlagStatus.FEHLGESCHLAGEN,
                fehler="Der Kommentar wurde abgelehnt: Spam",
            )

        aufgegeben = store.gescheiterte_kommentarfassungen(
            KAMPAGNE, lauf.MAX_VERSUCHE_JE_FASSUNG
        )

    assert aufgegeben.get("111", set()) == {1}


def test_bei_totem_browser_hoert_der_lauf_auf(bestand: Path) -> None:
    """Ein geschlossenes Fenster beendet den Lauf - **beim ersten Mal**.

    Das ist kein Fall fuer die Fehlerisolierung je Gruppe: Es liegt nicht an
    dieser Gruppe und nicht an der naechsten. Ohne diese Grenze arbeitete
    sich der Lauf durch 121 Gruppen und vermerkte ueberall dasselbe.

    **Nicht mehr erst nach fuenf** (15.09.2026): Ein Sitzungsfehler ist beim
    ersten Mal so eindeutig wie beim fuenften, und vier weitere Anlaeufe
    kosten vier Gruppen einen Vermerk. Gezaehlt wird nur noch, was **nicht**
    die Sitzung betrifft - "kein Kommentarfeld" etwa, und dort liegt die
    Grenze weit hinten (``_Technikwaechter.GRENZE``).
    """
    from fbgroups.marketing import automatik

    with MarketingStore(bestand) as store:
        _texte_anlegen(store, KAMPAGNE, list(GRUPPEN))
        _beitragstexte_anlegen(store, KAMPAGNE, list(GRUPPEN))
        store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)

    versuche: list[str] = []

    def toter_browser(
        url: str,
        group_id: str,
        text: str,
        texttyp: str = "kommentar",
        link_url: str = "",
    ) -> automatik.Schrittergebnis:
        versuche.append(group_id)
        return automatik.Schrittergebnis(
            erfolg=False,
            fehler="BrowserContext.new_page: Target page, context or browser has been closed",
        )

    fortschritt = automatik.fuehre_lauf_aus(_Konfig(bestand), ausfuehren=toter_browser)

    assert len(versuche) == 1, "ein totes Fenster erkennt man beim ersten Mal"
    assert not fortschritt.fertig, "ein Abbruch ist kein Abschluss"
    # Und nichts davon steht als Urteil an einer Gruppe.
    stand = {g.group_id: g for k in fortschritt.kampagnen for g in k.gruppen}
    assert all(not g.erschoepft for g in stand.values())


def test_ein_erfolg_setzt_den_technikzaehler_zurueck() -> None:
    """Sonst beendete ein Aussetzer alle paar Schritte den Lauf."""
    from fbgroups.marketing import automatik

    waechter = automatik._Technikwaechter()
    technisch = automatik.Schrittergebnis(erfolg=False, fehler="Timeout beim Laden")
    assert [waechter.melde(technisch) for _ in range(4)] == [False, False, False, False]

    assert waechter.melde(automatik.Schrittergebnis(erfolg=True)) is False
    assert waechter.folge == 0
    # Eine Ablehnung der Gruppe setzt ebenfalls zurueck - der Browser lebt ja.
    waechter.melde(technisch)
    assert (
        waechter.melde(automatik.Schrittergebnis(erfolg=False, fehler="abgelehnt: Spam"))
        is False
    )
    assert waechter.folge == 0


# --- Die Grenzen im laufenden Betrieb -------------------------------------
#
# ``test_grenzen.py`` prueft die Rechnung. Hier geht es um die Wirkung: Greift
# eine Tagesmenge wirklich in den Lauf ein, und haelt eine Bremse tatsaechlich
# nur ihre eigene Aktion an?


class _MitGrenzen(_Konfig):
    """Wie ``_Konfig``, aber mit echten Tagesmengen - je Aktion einstellbar."""

    def __init__(self, pfad: Path, **grenzen: int) -> None:
        super().__init__(pfad)
        self._grenzen = grenzen

    def get(self, *pfad, default=None):  # noqa: ANN002, ANN003, ANN201
        if pfad[:1] == ("limits",) and pfad[-1:] == ("daily",):
            return self._grenzen.get(pfad[1], 1000)
        return super().get(*pfad, default=default)


def test_die_tagesmenge_begrenzt_den_lauf(bestand: Path) -> None:
    """Sechs Kommentare am Tag heissen sechs Kommentare - nicht zwanzig.

    Die Zahl ist ein Planungswert dieses Programms und keine Zusage von
    Facebook; was sie hier beweist, ist nur, dass sie ueberhaupt eingreift.
    """
    from fbgroups.marketing import automatik

    with MarketingStore(bestand) as store:
        _texte_anlegen(store, KAMPAGNE, list(GRUPPEN))
        store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)

    gesehen: list[str] = []

    def ausfuehren(
        url: str,
        group_id: str,
        text: str,
        texttyp: str = "kommentar",
        link_url: str = "",
    ) -> automatik.Schrittergebnis:
        gesehen.append(texttyp)
        return automatik.Schrittergebnis(erfolg=True, post_url=f"p{len(gesehen)}")

    automatik.fuehre_lauf_aus(
        _MitGrenzen(bestand, comments=6, posts=0), ausfuehren=ausfuehren
    )

    assert gesehen.count("kommentar") == 6
    assert "post" not in gesehen, "posts: 0 schaltet die Aktion ab"


def test_eine_bremse_haelt_nur_ihre_eigene_aktion_an(bestand: Path) -> None:
    """Der Kern von Punkt 7 und 8, im laufenden Betrieb.

    Der erste Kommentar laeuft in eine Bremse. Danach ruhen die Kommentare -
    der **Beitrag** derselben Gruppe geht trotzdem hinaus. Frueher haette
    derselbe Fehlschlag entweder nichts bewirkt (und der Lauf haette weiter
    gegen die Bremse gearbeitet) oder alles angehalten.
    """
    from fbgroups.marketing import automatik
    from fbgroups.marketing.grenzen import Aktion

    with MarketingStore(bestand) as store:
        _texte_anlegen(store, KAMPAGNE, list(GRUPPEN))
        _beitragstexte_anlegen(store, KAMPAGNE, list(GRUPPEN))
        store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)
        # Kommentare sind bereits gebremst, Beitraege nicht.
        automatik.merke_bremse(store, Aktion.KOMMENTAR.value)

    gesehen: list[str] = []

    def ausfuehren(
        url: str,
        group_id: str,
        text: str,
        texttyp: str = "kommentar",
        link_url: str = "",
    ) -> automatik.Schrittergebnis:
        gesehen.append(texttyp)
        return automatik.Schrittergebnis(erfolg=True, post_url=f"p{len(gesehen)}")

    automatik.fuehre_lauf_aus(_Konfig(bestand), ausfuehren=ausfuehren)

    assert "post" in gesehen, "der Beitrag laeuft weiter"
    assert "kommentar" not in gesehen, "die Kommentare ruhen"


def test_eine_bremse_wird_gebucht_und_verdoppelt_sich(bestand: Path) -> None:
    """Und sie ueberlebt den Neustart - sonst begaenne der Backoff von vorn."""
    from fbgroups.marketing import automatik
    from fbgroups.marketing.grenzen import Aktion

    with MarketingStore(bestand) as store:
        _texte_anlegen(store, KAMPAGNE, list(GRUPPEN))
        store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)

    def gebremst(
        url: str,
        group_id: str,
        text: str,
        texttyp: str = "kommentar",
        link_url: str = "",
    ) -> automatik.Schrittergebnis:
        return automatik.Schrittergebnis(
            erfolg=False, fehler="Du hast diese Funktion zu oft verwendet"
        )

    automatik.fuehre_lauf_aus(_Konfig(bestand), ausfuehren=gebremst, max_schritte=1)

    with MarketingStore(bestand) as store:
        bis, stufe = store.sperre(Aktion.KOMMENTAR.value)
        assert bis is not None
        assert stufe == 1
        # Eine zweite Bremsung verdoppelt die Ruhezeit.
        assert automatik.merke_bremse(store, Aktion.KOMMENTAR.value) == 2


def test_ein_erfolg_hebt_die_bremse_auf(bestand: Path) -> None:
    """Der Backoff soll die naechste Bremsung messen, nicht die letzte Woche."""
    from fbgroups.marketing import automatik
    from fbgroups.marketing.grenzen import Aktion

    with MarketingStore(bestand) as store:
        _texte_anlegen(store, KAMPAGNE, list(GRUPPEN))
        store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)
        automatik.merke_bremse(store, Aktion.KOMMENTAR.value)

    def erfolgreich(
        url: str,
        group_id: str,
        text: str,
        texttyp: str = "kommentar",
        link_url: str = "",
    ) -> automatik.Schrittergebnis:
        return automatik.Schrittergebnis(erfolg=True, post_url="p")

    # Die Sperre von Hand aufheben, damit ueberhaupt ein Schritt entsteht -
    # geprueft wird, was der Erfolg danach mit der Stufe macht.
    with MarketingStore(bestand) as store:
        store.loesche_sperre(Aktion.KOMMENTAR.value)
        automatik.merke_bremse(store, Aktion.KOMMENTAR.value)
        store.set_meta(f"sperre:{Aktion.KOMMENTAR.value}:bis", "")

    automatik.fuehre_lauf_aus(_Konfig(bestand), ausfuehren=erfolgreich, max_schritte=1)

    with MarketingStore(bestand) as store:
        _bis, stufe = store.sperre(Aktion.KOMMENTAR.value)
    assert stufe == 0


# --- Punkt 12: "gerade geht nichts" ist nicht "fertig" ---------------------


def test_eine_erschoepfte_kampagne_gilt_nicht_als_abgeschlossen() -> None:
    """Der Unterschied, der am 12.09.2026 im Bild stand.

    Vier Kampagnen meldeten "FERTIG", 78 von 78 Gruppen durch - und **null**
    veroeffentlichte Kommentare. Fertig war daran nur der Lauf.
    """
    erschoepft = _gruppe("111", 0, erschoepft=True)
    kampagne = _kampagne("k", [erschoepft])

    assert kampagne.fertig, "der Lauf versucht hier nichts mehr"
    assert not kampagne.abgeschlossen, "erreicht ist damit nichts"
    assert (
        kampagne.lauf_status() is not KampagnenLaufStatus.ERSCHOEPFT_VORERST
        or not kampagne.abgeschlossen
    )


def test_eine_volle_kampagne_ist_abgeschlossen() -> None:
    """Und nur sie darf dauerhaft auf ``completed`` gehen."""
    voll = _gruppe("111", lauf.ZIEL_JE_GRUPPE)
    kampagne = _kampagne("k", [voll])

    assert kampagne.abgeschlossen
    assert kampagne.lauf_status() is KampagnenLaufStatus.FERTIG


def test_wartende_gruppen_ergeben_wartet_auf_beitritt() -> None:
    """Eine Kampagne, die noch nicht angefangen hat, ist nicht am Ende."""
    kampagne = lauf.Kampagnenfortschritt(
        campaign_id="k", name="Kampagne k", gruppen=[_wartende("111")]
    )

    assert kampagne.lauf_status() is KampagnenLaufStatus.WARTET_AUF_BEITRITT
    assert not kampagne.abgeschlossen


def test_eine_bremse_ergibt_vorerst_gebremst() -> None:
    """Eine Aussage ueber **uns** und den heutigen Tag, nicht ueber die Kampagne."""
    kampagne = lauf.Kampagnenfortschritt(
        campaign_id="k",
        name="Kampagne k",
        gruppen=[_gruppe("111", 0, erschoepft=True)],
    )

    assert (
        kampagne.lauf_status(gebremst=True) is KampagnenLaufStatus.VORERST_GEBREMST
    )


def test_eine_erschoepfte_kampagne_wird_nicht_dauerhaft_completed(bestand: Path) -> None:
    """Sonst verschwaende ein einziger schlechter Tag die Kampagne fuer immer.

    In denselben Gruppen stehen morgen neue Beitraege. Wer die Kampagne
    trotzdem beenden will, tut das von Hand - das ist eine Entscheidung und
    keine Ableitung.
    """
    from fbgroups.marketing import automatik

    with MarketingStore(bestand) as store:
        _texte_anlegen(store, KAMPAGNE, list(GRUPPEN))
        lauf_id = store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)
        store.merke_bewertung(lauf_id, KAMPAGNE)
        for gid in GRUPPEN:
            store.setze_kommentar_erschoepft(KAMPAGNE, gid, "nur 0 von 10 moeglich")

    with SqliteStore(bestand) as s:
        gruppen = {g.group_id: g for g in s.load_groups()}

    with MarketingStore(bestand) as store:
        fortschritt = lauf.lies_fortschritt(store, lauf_id, gruppen)
        automatik._stand_fortschreiben(store, lauf_id, fortschritt)
        kampagne = store.load_campaign(KAMPAGNE)
        zeile = store.lauf_kampagnen(lauf_id)[0]

    assert kampagne is not None
    assert kampagne.status is CampaignStatus.ACTIVE, "nicht completed"
    assert zeile["status"] == KampagnenLaufStatus.ERSCHOEPFT_VORERST.value


# --- Die Weboberflaeche: Zahlen, keine Knoepfe ----------------------------
def test_ein_leerer_lauf_haelt_den_fernbetrieb_nicht_auf(bestand: Path) -> None:
    """Der leere Lauf wird geschlossen, und der Treiber macht sofort weiter."""
    from fastapi.testclient import TestClient

    from fbgroups.marketing.web import create_app

    with MarketingStore(bestand) as store:
        store.starte_lauf([], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)

    klient = TestClient(create_app(db_path=bestand))
    antwort = klient.post("/automatik/naechster", json={"kampagnen": []})

    assert antwort.status_code == 200
    assert antwort.json()["weiter"] is True, "der naechste Aufruf friert eine neue Liste ein"

    with MarketingStore(bestand) as store:
        assert store.offener_lauf() is None


def test_der_stand_ist_ohne_lauf_untaetig(bestand: Path) -> None:
    from fastapi.testclient import TestClient

    from fbgroups.marketing.web import create_app

    antwort = TestClient(create_app(db_path=bestand)).get("/automatik")
    assert antwort.status_code == 200
    assert antwort.json()["status"] == "untaetig"


def test_der_stand_nennt_die_zahlen_des_laufs(bestand: Path) -> None:
    """Punkt 9: Die Zahl steht vorn - 'laeuft' allein beantwortet nichts."""
    from fastapi.testclient import TestClient

    from fbgroups.marketing.web import create_app

    with MarketingStore(bestand) as store:
        store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)

    daten = TestClient(create_app(db_path=bestand)).get("/automatik").json()
    assert daten["status"] == LaufStatus.LAEUFT.value
    assert daten["kommentare"] == {"fertig": 0, "gesamt": 2 * lauf.ZIEL_JE_GRUPPE}
    assert daten["gruppen"] == {"fertig": 0, "gesamt": 2}
    assert daten["fertig"] is False


def test_der_stand_startet_nichts(bestand: Path) -> None:
    """Es gibt bewusst keinen Startweg im Dienst.

    Ein Lauf braucht einen sichtbaren Browser mit angemeldeter Sitzung. Auf
    dem Server gibt es beides nicht - ein Startknopf dort waere ein Knopf, der
    zuverlaessig fehlschlaegt und dabei einen Fehlversuch protokolliert.
    """
    from fastapi.testclient import TestClient

    client = TestClient(create_app_fuer_test(bestand))
    assert client.post("/automatik/start").status_code in (404, 405)


def test_der_automatikknopf_fehlt_ohne_facebook_sitzung() -> None:
    """Ein Knopf, der zuverlaessig scheitert, ist schlimmer als kein Knopf.

    Er laesst den **Dienst** einen sichtbaren Browser oeffnen. Auf dem Server
    gibt es weder $DISPLAY noch eine angemeldete Sitzung; dort endete jeder
    Druck in "Executable doesn't exist" - und hinterliess einen Fehlversuch im
    Protokoll und eine Kaltmodus-Sperre. Der Weg
    ``POST /arbeit/{k}/vorschlag/auto`` bleibt trotzdem bestehen: Was fehlt,
    ist der Knopf, nicht der Weg.
    """
    from fbgroups.marketing.arbeitsseite import _knopfreihe

    ohne = _knopfreihe("post", "Beitrag", "https://example.invalid/g/1", automatik_moeglich=False)
    mit = _knopfreihe("post", "Beitrag", "https://example.invalid/g/1", automatik_moeglich=True)

    assert "Automatisch posten" not in ohne
    assert "Automatisch posten" in mit
    # Die uebrigen drei Knoepfe stehen in beiden Faellen - sie brauchen
    # keinen Browser auf dem Server, sondern den im Kopf des Menschen.
    for html in (ohne, mit):
        assert "speichern-post" in html
        assert "kopieren-post" in html
        assert "Gruppe bei Facebook oeffnen" in html


def create_app_fuer_test(pfad: Path):
    from fbgroups.marketing.web import create_app

    return create_app(db_path=pfad)


# --- Fernbetrieb: eine Wahrheit, und sie liegt auf dem Server -------------
def _fern_client(bestand: Path):
    """Ein Client, der wie ein Aufruf durch den SSH-Tunnel aussieht.

    ``_nur_lokal`` prueft Absenderadresse **und** Herkunft der Seite; die
    Testclient-Adresse ist bereits oertlich, der Ursprung muss gesetzt werden.
    """
    from fastapi.testclient import TestClient

    from fbgroups.marketing.web import create_app

    # Der Ursprung muss eine oertliche Adresse sein - genau das ist die zweite
    # Pruefung in ``_nur_lokal``. Im Betrieb liefert der SSH-Tunnel sie
    # (``http://127.0.0.1:8090``); ``testserver`` waere eine fremde Seite.
    # Mit derselben weit gestellten Konfiguration wie der oertliche Lauf:
    # Diese Tests fragen nach dem Weg (welcher Schritt kommt als naechster?),
    # nicht nach der Tagesmenge. Sonst endete jeder von ihnen nach dem ersten
    # Kommentar an der Abstandsregel - und pruefte damit ``grenzen.py``
    # zum zweiten Mal, statt den Fernbetrieb zum ersten.
    return TestClient(
        create_app(config=_Konfig(bestand), db_path=bestand),
        headers={"Origin": "http://127.0.0.1:8090"},
    )


def _hole_schritt(client, versuche: int = 6) -> dict:
    """Fragt so lange nach, bis ein Schritt kommt - genau wie der Treiber.

    Der Server beantwortet nicht jeden Aufruf mit einer Handlung fuer den
    Browser: Die Neubewertung einer Kampagne fuehrt er selbst aus und meldet
    ``weiter``. Ein Test, der einmal fragt und einen Schritt erwartet, prueft
    deshalb eine Schnittstelle, die es nicht gibt - ``fuehre_lauf_fern_aus``
    fragt in derselben Schleife weiter.
    """
    for _ in range(versuche):
        daten = client.post("/automatik/naechster", json={}).json()
        if daten.get("schritt") is not None:
            return daten
        assert daten.get("weiter"), f"kein Schritt und kein weiter: {daten}"
    raise AssertionError("auch nach mehreren Aufrufen kam kein Schritt")


def test_der_server_gibt_den_naechsten_schritt_heraus(bestand: Path) -> None:
    """Der Weg, der die zweite Datenbank ueberfluessig macht.

    Er liefert den **fertigen** Text mit eingesetztem Tracking-Link: Der
    Arbeitsrechner baut ihn nie selbst, also kann er ihn auch nicht anders
    bauen als der Server.
    """
    with MarketingStore(bestand) as store:
        _texte_anlegen(store, KAMPAGNE, list(GRUPPEN))

    daten = _hole_schritt(_fern_client(bestand))

    assert daten["weiter"] is True
    schritt = daten["schritt"]
    assert schritt["nummer"] == 1
    assert schritt["group_id"] in GRUPPEN
    assert schritt["gruppen_url"].startswith("https://www.facebook.com/groups/")
    assert "{link}" not in schritt["text"], "der Link muss eingesetzt sein"
    # Eingesetzt ist die **oeffentliche** Adresse. Der Tracking-Code steht
    # nicht darin: Er nennt Kanal, Zielgruppe, Stadt und laufende Nummer, und
    # das ist unsere Buchhaltung und keine Auskunft fuer einen Leser.
    assert "FB-TST-BER" not in schritt["text"]
    with MarketingStore(bestand) as store:
        link = store.link_for(KAMPAGNE, schritt["group_id"])
        assert link is not None
        assert link.url_fuer("browser") in schritt["text"]


def test_die_meldung_bucht_auf_dem_server(bestand: Path) -> None:
    """Nach der Meldung steht die Fassung dort als veroeffentlicht - nicht nur hier."""
    with MarketingStore(bestand) as store:
        _texte_anlegen(store, KAMPAGNE, list(GRUPPEN))

    client = _fern_client(bestand)
    schritt = _hole_schritt(client)["schritt"]

    antwort = client.post(
        "/automatik/ergebnis",
        json={
            "campaign_id": schritt["campaign_id"],
            "group_id": schritt["group_id"],
            "nummer": schritt["nummer"],
            "erfolg": True,
            "post_url": "https://www.facebook.com/groups/1/posts/9",
        },
    )
    assert antwort.status_code == 200
    assert antwort.json()["ok"] is True

    with MarketingStore(bestand) as store:
        v = store.vorschlag(
            schritt["campaign_id"], schritt["group_id"], Texttyp.KOMMENTAR, schritt["nummer"]
        )
        assert v is not None
        assert v.status is VorschlagStatus.VEROEFFENTLICHT


def test_der_naechste_schritt_zaehlt_nach_der_meldung_weiter(bestand: Path) -> None:
    """Fassung 1 gemeldet, also kommt Fassung 2 - der Stand lebt auf dem Server."""
    with MarketingStore(bestand) as store:
        _texte_anlegen(store, KAMPAGNE, list(GRUPPEN))

    client = _fern_client(bestand)
    erster = _hole_schritt(client)["schritt"]
    client.post(
        "/automatik/ergebnis",
        json={
            "campaign_id": erster["campaign_id"],
            "group_id": erster["group_id"],
            "nummer": erster["nummer"],
            "erfolg": True,
        },
    )
    zweiter = _hole_schritt(client)["schritt"]

    assert zweiter["group_id"] == erster["group_id"]
    assert zweiter["nummer"] == 2


def test_der_server_gibt_die_beitrittsanfrage_zuerst_heraus(bestand: Path) -> None:
    """Auch im Fernbetrieb bestimmt der Server die Reihenfolge.

    Der Arbeitsrechner sieht nur ``art`` und tut, was dort steht - er kann
    die Reihenfolge deshalb nicht anders auslegen als der oertliche Lauf.
    Frueher gab es hier nur Kommentarschritte, und die Beitrittsanfragen
    liefen in einem eigenen Befehl daneben.
    """
    with MarketingStore(bestand) as store:
        _texte_anlegen(store, KAMPAGNE, list(GRUPPEN))
        store.save_marketing(
            GroupMarketing(group_id="222", marketing_status=MarketingStatus.NOT_CONTACTED)
        )
        # Seit dem 13.09.2026 steht der Regelschritt vor der Anfrage. Diese
        # Tests fragen nach der **Anfrage**; die Regeln gelten hier deshalb
        # als gelesen. Dass sie vorher drankommen, hat seinen eigenen Test
        # (``test_die_regeln_werden_vor_der_anfrage_gelesen``).
        store.merke_regeln("222", Regelbefund(gelesen=True))

    daten = _hole_schritt(_fern_client(bestand))
    schritt = daten["schritt"]

    assert schritt["art"] == "beitritt"
    assert schritt["group_id"] == "222"
    assert schritt["gruppen_url"].startswith("https://www.facebook.com/groups/")
    assert schritt["text"] == "", "eine Beitrittsanfrage traegt keinen Text"


def test_eine_gescheiterte_anfrage_wird_auf_dem_server_uebersprungen(bestand: Path) -> None:
    """Nichts vermerkt, aber nicht noch einmal angeboten.

    Der Server haelt den Stand - also muss er auch wissen, dass diese Gruppe
    in diesem Lauf schon einmal drankam. Sonst boete er sie im naechsten
    Durchgang wieder an, und der Lauf drehte sich um sie.
    """
    with MarketingStore(bestand) as store:
        _texte_anlegen(store, KAMPAGNE, list(GRUPPEN))
        store.save_marketing(
            GroupMarketing(group_id="222", marketing_status=MarketingStatus.NOT_CONTACTED)
        )
        # Seit dem 13.09.2026 steht der Regelschritt vor der Anfrage. Diese
        # Tests fragen nach der **Anfrage**; die Regeln gelten hier deshalb
        # als gelesen. Dass sie vorher drankommen, hat seinen eigenen Test
        # (``test_die_regeln_werden_vor_der_anfrage_gelesen``).
        store.merke_regeln("222", Regelbefund(gelesen=True))

    client = _fern_client(bestand)
    schritt = _hole_schritt(client)["schritt"]
    assert schritt["art"] == "beitritt"

    antwort = client.post(
        "/automatik/beitritt/ergebnis",
        json={
            "group_id": schritt["group_id"],
            "campaign_id": schritt["campaign_id"],
            "ausgang": "fehler",
            "bemerkung": "Browserfenster zu",
        },
    )
    assert antwort.status_code == 200
    assert antwort.json()["vermerkt"] is False, "es ist nichts abgeschickt worden"

    # Der naechste Schritt ist ein Kommentar, keine zweite Anfrage.
    zweiter = _hole_schritt(client)["schritt"]
    assert zweiter["art"] == "text"

    with MarketingStore(bestand) as store:
        stand = store.load_marketing("222")
        assert stand is None or stand.marketing_status is MarketingStatus.NOT_CONTACTED
        offen = store.offener_lauf()
        assert offen is not None
        assert (KAMPAGNE, "222") in store.uebersprungene_gruppen(int(offen["lauf_id"]))


def test_die_meldung_traegt_keinen_text(bestand: Path) -> None:
    """Der Text geht nur hinaus, nie zurueck.

    Dieselbe Zusage wie beim Ergebnisformular der Arbeitsseite: Ein
    manipulierter Aufruf kann keinen anderen Text in einen Kommentar bringen
    als den, den der Server vorbereitet hat.
    """
    from fbgroups.marketing.web import AutomatikErgebnis

    assert "text" not in AutomatikErgebnis.model_fields


def test_die_fernwege_sind_von_aussen_nicht_erreichbar(bestand: Path) -> None:
    """Wie jeder schreibende Weg hinter ``_nur_lokal`` - 404, nicht 403.

    Wer den Dienst oeffentlich stellt, soll nicht nebenbei verraten, dass es
    hier eine Automatik gibt.
    """
    from fastapi.testclient import TestClient

    from fbgroups.marketing.web import create_app

    # Ohne Origin-Kopf gilt der Aufruf als fremde Seite.
    client = TestClient(create_app(db_path=bestand), headers={"Origin": "https://boese.invalid"})
    assert client.post("/automatik/naechster", json={}).status_code == 404
    assert (
        client.post(
            "/automatik/ergebnis",
            json={"campaign_id": KAMPAGNE, "group_id": "111", "nummer": 1, "erfolg": True},
        ).status_code
        == 404
    )


def test_ein_lauf_laesst_sich_auf_eine_kampagne_einschraenken(bestand: Path) -> None:
    """Der erste Ernstfall: an **einer** Kampagne sehen, nicht an allen aktiven.

    Ohne diese Einschraenkung fror der erste Versuch alle aktiven Kampagnen
    ein - beim Nutzer waeren das fuenf gewesen, also sofort echte Gruppen
    statt der Testgruppe.
    """
    from fbgroups.marketing import automatik

    with MarketingStore(bestand) as store:
        store.save_campaign(
            Campaign(
                campaign_id="zweite",
                name="Zweite",
                language="ar",
                audiences=["syrians"],
                status=CampaignStatus.ACTIVE,
            )
        )
        assert sorted(automatik.aktive_kampagnen(store)) == sorted([KAMPAGNE, "zweite"])

        lauf_id, neu = automatik.hole_oder_starte_lauf(
            store, ziel_je_gruppe=5, nur=[KAMPAGNE]
        )
        assert neu is True
        assert [z["campaign_id"] for z in store.lauf_kampagnen(lauf_id)] == [KAMPAGNE]


def test_die_einschraenkung_ueberstimmt_pausiert_nicht(bestand: Path) -> None:
    """Sonst waere "pausiert" eine Beschriftung ohne Wirkung."""
    from fbgroups.marketing import automatik

    with MarketingStore(bestand) as store:
        kampagne = store.load_campaign(KAMPAGNE)
        kampagne.status = CampaignStatus.PAUSED
        store.save_campaign(kampagne)

        lauf_id, _ = automatik.hole_oder_starte_lauf(store, ziel_je_gruppe=5, nur=[KAMPAGNE])
        assert store.lauf_kampagnen(lauf_id) == []


def test_die_einschraenkung_gilt_nur_beim_anlegen(bestand: Path) -> None:
    """Punkt 16: Ein offener Lauf behaelt seine eingefrorene Liste."""
    from fbgroups.marketing import automatik

    with MarketingStore(bestand) as store:
        erste, _ = automatik.hole_oder_starte_lauf(store, ziel_je_gruppe=5)
        zweite, neu = automatik.hole_oder_starte_lauf(
            store, ziel_je_gruppe=5, nur=["gibtesnicht"]
        )

    assert zweite == erste
    assert neu is False


def test_kampagnen_im_lauf_behalten_ihre_reihenfolge(bestand: Path) -> None:
    """Ohne feste Position koennte ein fortgesetzter Lauf anders waehlen."""
    with MarketingStore(bestand) as store:
        store.save_campaign(
            Campaign(campaign_id="zweite", name="Zweite", language="ar", audiences=["syrians"])
        )
        lauf_id = store.starte_lauf(["zweite", KAMPAGNE], ziel_je_gruppe=5)
        zeilen = store.lauf_kampagnen(lauf_id)

    assert [z["campaign_id"] for z in zeilen] == ["zweite", KAMPAGNE]
    assert [z["position"] for z in zeilen] == [1, 2]
    assert all(z["status"] == KampagnenLaufStatus.WARTET.value for z in zeilen)

# --- Zwei Ziele: Browser und Store, beide getrackt ------------------------
def test_die_ziele_wechseln_je_fassung() -> None:
    """1 Browser, 2 Store, 3 Browser ... - Punkt "Link-Rotation".

    Eine Rechnung, kein gespeicherter Zeiger: Dieselbe Fassung ergibt nach
    einem Abbruch wieder dasselbe Ziel.
    """
    folge = [lauf.ziel_zu_nummer(n) for n in range(1, 11)]
    assert folge == ["browser", "store"] * 5


def test_der_browsercode_leitet_sich_vom_storecode_ab(bestand: Path) -> None:
    """``FB-TST-BER-001`` → ``FB-TST-BER-001-B``.

    Man sieht der Kennung an, zu welchem Paar sie gehoert - bei einem Klick im
    Protokoll ist das die erste Frage. Und die Nummernreihe des
    ``CodeAllocator`` bleibt unberuehrt.
    """
    with MarketingStore(bestand) as store:
        code = store.vergib_browsercode(KAMPAGNE, "111", "https://go.example.invalid")
        link = store.link_for(KAMPAGNE, "111")

    assert code == link.tracking_code + "-B"
    assert link.tracking_url_browser.endswith("/r/" + code)


def test_ein_vergebener_browsercode_wird_nie_ersetzt(bestand: Path) -> None:
    """Er steht moeglicherweise schon in einem veroeffentlichten Beitrag."""
    with MarketingStore(bestand) as store:
        erst = store.vergib_browsercode(KAMPAGNE, "111", "https://go.example.invalid")
        nochmal = store.vergib_browsercode(KAMPAGNE, "111", "https://ganz.anders.invalid")

    assert erst == nochmal


def test_beide_codes_fuehren_zum_selben_paar(bestand: Path) -> None:
    """Sonst antwortete die Weiterleitung auf jeden Browser-Code mit 404.

    Der Klick waere verloren - bei einem Code, der bereits in einem Beitrag
    steht.
    """
    with MarketingStore(bestand) as store:
        browser = store.vergib_browsercode(KAMPAGNE, "111", "https://go.example.invalid")
        link = store.link_for(KAMPAGNE, "111")

        ueber_store = store.resolve_code(link.tracking_code)
        ueber_browser = store.resolve_code(browser)

        assert ueber_store is not None
        assert ueber_browser is not None
        assert ueber_store.group_id == ueber_browser.group_id == "111"

        # Und das Ziel haengt am Code, nicht an der Kampagne.
        assert store.ziel_des_codes(link.tracking_code) == "store"
        assert store.ziel_des_codes(browser) == "browser"
        assert store.ziel_des_codes("gibtesnicht") == ""


def test_der_bestehende_code_bleibt_ein_storecode(bestand: Path) -> None:
    """Die wichtigste Zusage dieser Aenderung.

    ``tracking_code`` steht in veroeffentlichten Beitraegen. Sein Ziel
    nachtraeglich umzustellen aenderte, wohin alte Beitraege fuehren - ohne
    dass jemand sie angefasst haette.
    """
    with MarketingStore(bestand) as store:
        vorher = store.link_for(KAMPAGNE, "111").tracking_code
        store.vergib_browsercode(KAMPAGNE, "111", "https://go.example.invalid")
        nachher = store.link_for(KAMPAGNE, "111")

    assert nachher.tracking_code == vorher
    assert store_ziel(bestand, vorher) == "store"


def store_ziel(pfad: Path, code: str) -> str:
    with MarketingStore(pfad) as store:
        return store.ziel_des_codes(code)


def test_der_text_traegt_den_code_des_gewaehlten_ziels(bestand: Path) -> None:
    """``mit_link`` setzt den Code ein, der zum Ziel gehoert."""
    from fbgroups.config import load_config
    from fbgroups.marketing.beitrag import mit_link

    cfg = load_config()
    with MarketingStore(bestand) as store:
        browser = store.vergib_browsercode(KAMPAGNE, "111", "https://go.example.invalid")
        campaign = store.load_campaign(KAMPAGNE)
        link = store.link_for(KAMPAGNE, "111")

    im_browser = mit_link(campaign, link, "x {link}", config=cfg, ziel="browser")
    im_store = mit_link(campaign, link, "x {link}", config=cfg, ziel="store")

    # In den Text geht die kurze Adresse, je Ziel eine eigene. Der innere
    # Code steht in keiner von beiden - er bleibt in der Datenbank.
    assert link.url_fuer("browser") in im_browser
    assert link.url_fuer("store") in im_store
    assert browser not in im_browser
    assert link.tracking_code not in im_store
    assert im_browser != im_store


def test_ohne_browsercode_faellt_es_auf_den_storecode_zurueck(bestand: Path) -> None:
    """Ein Beitrag ohne Link waere schlimmer als einer mit dem anderen Ziel."""
    with MarketingStore(bestand) as store:
        link = store.link_for(KAMPAGNE, "111")

    assert link.tracking_code_browser == ""
    assert link.code_fuer("browser") == link.tracking_code
    # Ohne Browser-Code faellt auch die oeffentliche Adresse auf die des
    # Store-Codes zurueck - dieselbe Ueberlegung eine Ebene tiefer.
    assert link.url_fuer("browser") == link.url_fuer("store") == link.public_url
    assert link.interne_url_fuer("browser") == link.tracking_url

# --- Anreicherung: Zahlen vom Browser auf den Server ----------------------
def test_der_server_nimmt_mitgliederzahl_und_aktivitaet_entgegen(bestand: Path) -> None:
    """Der Weg, der die zweite Datenbank vermeidet.

    ``enrich --browser`` laeuft auf dem Arbeitsrechner - dort steht der
    angemeldete Browser. Gebucht wird auf dem Server, wo auch die Klicks
    gezaehlt werden; sonst zeigte das Dashboard weiter "unknown".
    """
    client = _fern_client(bestand)
    antwort = client.post(
        "/automatik/anreichern/ergebnis",
        json={
            "group_id": "111",
            "erreichbar": True,
            "member_count": 12400,
            "privacy_hint": "public",
            "posts_per_day": 3.5,
            "activity_factor": 0.8,
        },
    )
    assert antwort.status_code == 200
    assert antwort.json()["member_count"] == 12400

    with SqliteStore(bestand) as gruppen_store:
        g = next(x for x in gruppen_store.load_groups() if x.group_id == "111")

    assert g.member_count == 12400
    assert g.member_count_source is not None
    assert g.activity_factor == 0.8
    assert g.activity_source is not None
    assert g.member_count_checked_at is not None


def test_eine_fehlende_zahl_loescht_keine_vorhandene(bestand: Path) -> None:
    """Ein Anmeldefenster ist kein Beleg dafuer, dass eine Gruppe geschrumpft ist.

    Der Zeitpunkt der Pruefung wird trotzdem gesetzt - sonst liefe derselbe
    erfolglose Abruf bei jedem Lauf erneut.
    """
    client = _fern_client(bestand)
    client.post(
        "/automatik/anreichern/ergebnis",
        json={"group_id": "111", "erreichbar": True, "member_count": 500},
    )
    client.post(
        "/automatik/anreichern/ergebnis",
        json={"group_id": "111", "erreichbar": False},
    )

    with SqliteStore(bestand) as gruppen_store:
        g = next(x for x in gruppen_store.load_groups() if x.group_id == "111")

    assert g.member_count == 500, "die Zahl von vorhin steht noch"
    assert g.last_checked_at is not None


def test_die_naechsten_gruppen_sind_die_ohne_zahlen(bestand: Path) -> None:
    daten = _fern_client(bestand).post("/automatik/anreichern/naechste", json={}).json()
    assert sorted(g["group_id"] for g in daten["gruppen"]) == ["111", "222"]
    assert daten["offen"] == 2


def test_die_anreicherungswege_sind_von_aussen_nicht_erreichbar(bestand: Path) -> None:
    from fastapi.testclient import TestClient

    from fbgroups.marketing.web import create_app

    fremd = TestClient(
        create_app(db_path=bestand), headers={"Origin": "https://boese.invalid"}
    )
    assert fremd.post("/automatik/anreichern/naechste", json={}).status_code == 404
    assert (
        fremd.post(
            "/automatik/anreichern/ergebnis", json={"group_id": "111"}
        ).status_code
        == 404
    )


def test_die_meldung_hat_kein_feld_fuer_texte() -> None:
    """Die harte Projektgrenze gilt auch hier.

    Keine Beitragsinhalte, keine Namen von Menschen - ein Feld, das es nicht
    gibt, kann auch nicht gefuellt werden.
    """
    from fbgroups.marketing.web import AnreicherungErgebnis

    felder = set(AnreicherungErgebnis.model_fields)
    assert not felder & {"text", "beitrag", "autor", "author", "name", "mitglieder_namen"}


def test_ein_lauf_erzeugt_fehlende_texte_statt_die_gruppe_zu_erschoepfen(
    bestand: Path,
) -> None:
    """Der Lauf bereitet selbst vor - wie "Arbeiten" auf der Arbeitsseite.

    Am 11.09.2026 endete ein Lauf mit "45 Gruppe(n) erschoepft" und vier
    Kommentaren: Fuer 44 der 45 Gruppen war nie ``campaign text`` gelaufen,
    und der Lauf erklaerte jede davon fuer erschoepft - ein Urteil ueber die
    **Gruppe**, wo die Luecke bei **uns** lag. Und weil der Vermerk
    gespeichert wird, blieb sie danach dauerhaft uebersprungen.

    Hier wird deshalb ausdruecklich **kein** Text angelegt: Der Lauf muss ihn
    selbst nachziehen.
    """
    from fbgroups.marketing import automatik

    with MarketingStore(bestand) as store:
        store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)

    gesehen: list[tuple[str, str]] = []

    def ausfuehren(
        url: str,
        group_id: str,
        text: str,
        texttyp: str = "kommentar",
        link_url: str = "",
    ) -> automatik.Schrittergebnis:
        gesehen.append((group_id, texttyp))
        return automatik.Schrittergebnis(erfolg=True, post_url=f"p{len(gesehen)}")

    fortschritt = automatik.fuehre_lauf_aus(_Konfig(bestand), ausfuehren=ausfuehren)

    assert fortschritt.fertig
    # Je Gruppe ein Beitrag und zehn Kommentare - nichts davon lag vorher vor.
    assert len(gesehen) == len(GRUPPEN) * (1 + lauf.ZIEL_JE_GRUPPE)
    assert fortschritt.kommentare_veroeffentlicht == len(GRUPPEN) * lauf.ZIEL_JE_GRUPPE
    assert fortschritt.beitraege_veroeffentlicht == len(GRUPPEN)
    erschoepft = sum(k.gruppen_erschoepft for k in fortschritt.kampagnen)
    assert erschoepft == 0, "eine Gruppe ohne Text ist das Gegenteil einer erschoepften"


def test_ein_alter_vermerk_ohne_text_haelt_die_gruppe_nicht_fuer_immer_zu(
    bestand: Path,
) -> None:
    """``kein Kommentartext vorhanden`` war nie ein Urteil ueber die Gruppe.

    Die Zeile bleibt im Bestand stehen - verworfen wird beim Lesen, wie bei
    ``drop_shared_snippets``. Ein Grund, der **nach** dem Nachziehen entsteht
    (``KEINE_VORLAGE``), zaehlt dagegen weiter: Sonst liefe der Lauf im Kreis.
    """
    erste, zweite = list(GRUPPEN)
    with MarketingStore(bestand) as store:
        _texte_anlegen(store, KAMPAGNE, [erste, zweite])
        lauf_id = store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)
        store.setze_kommentar_erschoepft(KAMPAGNE, erste, lauf.OHNE_KOMMENTARTEXT)
        store.setze_kommentar_erschoepft(KAMPAGNE, zweite, lauf.KEINE_VORLAGE)

        with SqliteStore(bestand) as bestand_store:
            gruppen = {g.group_id: g for g in bestand_store.load_groups()}
        fortschritt = lauf.lies_fortschritt(
            store, lauf_id, gruppen, mitgliedschaft_pflicht=False
        )

    stand = {g.group_id: g for k in fortschritt.kampagnen for g in k.gruppen}
    assert stand[erste].erschoepft is False, "der alte Grund wird uebergangen"
    assert stand[zweite].erschoepft is True, "eine fehlende Vorlage bleibt ein Ende"


def test_ein_beitrag_ohne_text_gilt_nicht_als_gescheiterter_beitrag(
    bestand: Path,
) -> None:
    """``kein Beitragstext vorhanden`` ist kein Ausgang, sondern ein Ausfall.

    ``fehlgeschlagen`` heisst sonst: In dieser Gruppe ging der Beitrag nicht,
    und ein zweiter Anlauf wiederholte nur das. Hier wurde nie einer versucht.
    """
    erste = next(iter(GRUPPEN))
    with MarketingStore(bestand) as store:
        _texte_anlegen(store, KAMPAGNE, [erste])
        _beitragstexte_anlegen(store, KAMPAGNE, [erste])
        lauf_id = store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)
        store.set_post_status(
            KAMPAGNE, erste, PostStatus.FEHLGESCHLAGEN, lauf.OHNE_BEITRAGSTEXT
        )

        with SqliteStore(bestand) as bestand_store:
            gruppen = {g.group_id: g for g in bestand_store.load_groups()}
        fortschritt = lauf.lies_fortschritt(
            store, lauf_id, gruppen, mitgliedschaft_pflicht=False
        )

    stand = {g.group_id: g for k in fortschritt.kampagnen for g in k.gruppen}
    assert stand[erste].post_offen is True


def test_ein_abgestuerzter_browser_macht_die_gruppe_nicht_dauerhaft_zu(
    bestand: Path,
) -> None:
    """``campaign retry --kommentare`` gegen den Fall vom 11.09.2026.

    Ein geschlossener Browser liess jede der zehn Fassungen dreimal
    scheitern (*"BrowserContext.new_page: Target page ... has been closed"*).
    Danach galten alle zehn als aufgegeben und die Gruppe als erschoepft -
    wegen eines Fensters, das jemand zugemacht hat. ``campaign reset`` waere
    die einzige Antwort gewesen und haette die veroeffentlichten Fassungen
    der Nachbargruppe gleich mit zurueckgenommen.
    """
    erste, zweite = list(GRUPPEN)
    with MarketingStore(bestand) as store:
        _texte_anlegen(store, KAMPAGNE, [erste, zweite])
        lauf_id = store.starte_lauf([KAMPAGNE], ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)

        # Gruppe eins: alle zehn Fassungen dreimal gescheitert, dann erschoepft.
        for nummer in range(1, lauf.ZIEL_JE_GRUPPE + 1):
            for _ in range(lauf.MAX_VERSUCHE_JE_FASSUNG):
                store.setze_vorschlag_stand(
                    KAMPAGNE, erste, Texttyp.KOMMENTAR, nummer,
                    VorschlagStatus.FEHLGESCHLAGEN, fehler="browser zu",
                )
        store.setze_kommentar_erschoepft(
            KAMPAGNE, erste, "nur 0 von 10 Kommentaren moeglich"
        )
        # Gruppe zwei: eine Fassung steht in der Gruppe. Sie darf das bleiben.
        store.setze_vorschlag_stand(
            KAMPAGNE, zweite, Texttyp.KOMMENTAR, 1, VorschlagStatus.VEROEFFENTLICHT
        )

        fassungen, gruppen = store.gescheiterte_fassungen_zuruecksetzen(KAMPAGNE)
        assert fassungen == lauf.ZIEL_JE_GRUPPE
        assert gruppen == 1

        with SqliteStore(bestand) as bestand_store:
            alle = {g.group_id: g for g in bestand_store.load_groups()}
        fortschritt = lauf.lies_fortschritt(
            store, lauf_id, alle, mitgliedschaft_pflicht=False
        )
        veroeffentlicht = store.vorschlag(KAMPAGNE, zweite, Texttyp.KOMMENTAR, 1)

    stand = {g.group_id: g for k in fortschritt.kampagnen for g in k.gruppen}
    assert stand[erste].erschoepft is False
    assert stand[erste].gescheiterte_fassungen == frozenset()
    # Was in der Gruppe steht, bleibt dort - der Unterschied zu ``reset``.
    assert veroeffentlicht is not None
    assert veroeffentlicht.status is VorschlagStatus.VEROEFFENTLICHT
    assert stand[zweite].veroeffentlicht == 1
