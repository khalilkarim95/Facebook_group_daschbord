"""Die Regeln der Gruppe, in der kommentiert wird (21.09.2026).

Der Betriebsbefund war eine Runde durch sechs Gruppen, die **null**
Kommentare schrieb. Im Protokoll stand in jeder Gruppe eines von beiden:

    [Versuch 1/3] private_contact_suggestion: reise/sucht (سفر, مطار),
                  Regeln ungelesen - vorsichtig
      kein Anlass: kein vorbereiteter Text fuer keiner

Die Kette dahinter ist geschlossen und endet immer am selben Ort:

    Regeln nie gelesen
      -> Erlaubnis.aus_regeln: werbung = False   (aus nichts keine Erlaubnis)
      -> soll_app_nennen: False
      -> Entscheidung: private_contact_suggestion / helpful_reply
      -> Linkmodus.NO_LINK
      -> kein Textvorrat (absichtlich - er waere erfunden)
      -> kein Kommentar, in jeder Runde, fuer immer

Warum die Regeln nie gelesen wurden: ``regeln_offen`` bot die naechste
**Beitritts**- und die naechste **Arbeits**gruppe an - aber seit dem
15.09.2026 waehlt der Kommentarzweig seine Gruppe selbst
(``naechste_kommentargruppe``). In einer Kampagne aus lauter Mitgliedern gibt
es keine Beitrittskandidaten, und ``naechste_gruppe`` ist genau eine: Alle
uebrigen wurden kommentiert, ohne je gelesen worden zu sein.
"""

from __future__ import annotations

from fbgroups.marketing import lauf
from fbgroups.marketing.entscheidung import (
    LINKMODUS,
    Antwortart,
    Erlaubnis,
    Linkmodus,
    entscheide,
)
from fbgroups.marketing.inhalt import Inhaltsbefund, Relevanz, Thema
from fbgroups.marketing.lauf import Gruppenfortschritt, Kampagnenfortschritt
from fbgroups.marketing.models import PostStatus
from fbgroups.marketing.qualifikation import Qualifikation, Regelbefund

ZIEL = lauf.ZIEL_JE_GRUPPE


def _gruppe(
    gid: str,
    *,
    regeln_gelesen: bool = False,
    heute_in_gruppe: int = 0,
) -> Gruppenfortschritt:
    return Gruppenfortschritt(
        campaign_id="k1",
        group_id=gid,
        name=f"Gruppe {gid}",
        veroeffentlicht=0,
        ziel=ZIEL,
        mitglied=True,
        regeln_noetig=True,
        regeln_gelesen=regeln_gelesen,
        # Der Beitrag steht, damit allein der Kommentarweg uebrigbleibt -
        # genau die Lage im Protokoll.
        post_status=PostStatus.VEROEFFENTLICHT,
        note="A++",
        heute_in_gruppe=heute_in_gruppe,
        gruppenlimit=1,
    )


def _kampagne(*gruppen: Gruppenfortschritt) -> Kampagnenfortschritt:
    return Kampagnenfortschritt(campaign_id="k1", name="K1", gruppen=list(gruppen))


# --- Die Ursache: welche Gruppen ueberhaupt gelesen werden ----------------


def test_die_kommentargruppe_steht_in_den_offenen_regeln() -> None:
    """Der eigentliche Fehler - und er ist eine einzige fehlende Zeile.

    Die erste Gruppe hat ihre Tagesmenge erreicht; kommentiert wird deshalb
    in der zweiten (``naechste_kommentargruppe``). Genau deren Regeln
    fehlten in ``regeln_offen``.
    """
    voll = _gruppe("1", heute_in_gruppe=1)
    dran = _gruppe("2")
    kampagne = _kampagne(voll, dran)

    assert kampagne.naechste_kommentargruppe is dran
    assert dran.group_id in {g.group_id for g in kampagne.regeln_offen}


def test_eine_gelesene_gruppe_kommt_nicht_noch_einmal() -> None:
    """Gelesen ist gelesen - sonst laese der Lauf jede Runde dieselbe Seite."""
    kampagne = _kampagne(_gruppe("1", regeln_gelesen=True))
    assert kampagne.regeln_offen == []


def test_der_regelschritt_kommt_vor_dem_kommentar() -> None:
    """Die Reihenfolge ist der Zweck: erst nachsehen, dann schreiben."""
    kampagne = _kampagne(_gruppe("1", heute_in_gruppe=1), _gruppe("2"))
    fortschritt = lauf.Lauffortschritt(
        lauf_id=1, status=lauf.LaufStatus.LAEUFT, kampagnen=[kampagne]
    )
    schritt = lauf.naechster_schritt(fortschritt)

    assert schritt is not None
    assert schritt.art is lauf.Schrittart.REGELN
    assert schritt.group_id == "2"


# --- Die Folge: was ungelesene Regeln anrichten ---------------------------


def _befund() -> Inhaltsbefund:
    """Ein Beitrag, der ohne jeden Zweifel zum Angebot gehoert."""
    return Inhaltsbefund(
        thema=Thema.REISE, relevanz=Relevanz.HOCH, treffer=("سفر", "وزن")
    )


def test_ohne_gelesene_regeln_bleibt_die_app_ungenannt() -> None:
    """Die Kette aus dem Protokoll, in einer Zeile nachgestellt."""
    entscheidung = entscheide(
        _befund(), Erlaubnis.aus_regeln(Regelbefund(), Qualifikation.GEEIGNET)
    )
    assert entscheidung.art is not Antwortart.CONTEXTUAL_APP_MENTION
    assert LINKMODUS[entscheidung.art] is Linkmodus.NO_LINK


def test_mit_gelesenen_regeln_wird_die_app_genannt() -> None:
    """Dieselbe Lage, nur einmal nachgesehen - und der Kommentar geht hinaus.

    Verbietet die gelesene Regel nichts, traegt die Antwort sogar den Link
    (``DIRECT_APP_RECOMMENDATION``). Geprueft wird hier aber nur, worauf es
    ankommt: Die App wird genannt, es gibt also einen Textvorrat.
    """
    entscheidung = entscheide(
        _befund(),
        Erlaubnis.aus_regeln(Regelbefund(gelesen=True), Qualifikation.GEEIGNET),
    )
    assert entscheidung.art in (
        Antwortart.CONTEXTUAL_APP_MENTION,
        Antwortart.DIRECT_APP_RECOMMENDATION,
    )
    assert LINKMODUS[entscheidung.art] is not Linkmodus.NO_LINK


def test_ein_werbeverbot_bindet_auch_nach_dem_lesen() -> None:
    """Die Regel der Gruppe bleibt bindend - sie wird gelesen, nicht umgangen.

    Das ist die Kehrseite der Reparatur: Wo die Gruppe Werbung verbietet,
    bleibt es bei ``NO_LINK`` und damit dabei, dass nichts hinausgeht. Der
    Unterschied ist, dass es jetzt **auf einer gelesenen Regel** beruht und
    nicht darauf, dass niemand nachgesehen hat.
    """
    verbietet = Regelbefund(gelesen=True, keine_werbung=True)
    entscheidung = entscheide(
        _befund(), Erlaubnis.aus_regeln(verbietet, Qualifikation.GEEIGNET)
    )
    assert LINKMODUS[entscheidung.art] is Linkmodus.NO_LINK
