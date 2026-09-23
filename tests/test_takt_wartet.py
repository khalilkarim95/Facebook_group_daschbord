"""Der Takt haelt die Arbeit an - er beendet sie nicht.

Der Anlass vom 14.09.2026 ist eine Abschlussmeldung des Nutzers:

    Beitraege:  2 / 23      Kommentare: 31 / 2700
    Heute nicht mehr moeglich:
      beitritt:  Tagesmenge erreicht (50/50)
      kommentar: Abstandsregel - noch 2 Min

Zwei Minuten, und danach waeren 2669 Kommentare uebrig gewesen. Der Lauf
endete trotzdem: Den Warteweg gab es nur fuer die **Beitrittsanfrage**
(``wartet_auf_beitritt``, 13.09.2026). Fuer jede andere Aktion galt weiterhin
"kein Schritt = Ende".

Die Datei haelt beide Haelften der Unterscheidung fest, denn nur zusammen sind
sie richtig:

* **"noch nicht" wird abgewartet** - der eigene Takt aus ``delays``.
* **"heute nicht mehr" und "die Gegenseite bremst" werden nicht abgewartet** -
  eine erschoepfte Tagesmenge endet erst um Mitternacht, und eine Bremse zu
  verschlafen hiesse, eine Stunde zu warten und danach genau das Muster
  fortzusetzen, das zu ihr gefuehrt hat.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fbgroups.marketing import lauf
from fbgroups.marketing.grenzen import Aktion, Grenze, Lage, pruefe
from fbgroups.marketing.models import PostStatus, Texttyp

JETZT = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)


def _gruppe(gid: str = "g1", *, veroeffentlicht: int = 0, ziel: int = 10):
    """Eine Gruppe mit offenen Kommentaren - Beitrag ist bereits heraus."""
    return lauf.Gruppenfortschritt(
        campaign_id="k",
        group_id=gid,
        name=f"Gruppe {gid}",
        veroeffentlicht=veroeffentlicht,
        ziel=ziel,
        mitglied=True,
        mitgliedschaft_noetig=False,
        post_status=PostStatus.VEROEFFENTLICHT,
    )


def _fortschritt(aktionen: dict[Aktion, Lage]):
    return lauf.Lauffortschritt(
        lauf_id=1,
        status=lauf.LaufStatus.LAEUFT,
        kampagnen=[
            lauf.Kampagnenfortschritt(
                campaign_id="k", name="test_neu", gruppen=[_gruppe()]
            )
        ],
        aktionen=aktionen,
    )


def _takt(aktion: Aktion, vor_minuten: int = 6) -> Lage:
    """Die Lage, die ``pruefe`` fuer den eigenen Abstand liefert.

    ``vor_minuten`` ist, wann diese Aktion zuletzt lief - der Abstand betraegt
    mindestens acht Minuten, sie ist also noch gesperrt.
    """
    lage = pruefe(
        aktion,
        Grenze(pro_tag=100, abstand_min=8, abstand_max=20),
        heute=1,
        letzte=JETZT - timedelta(minutes=vor_minuten),
        jetzt=JETZT,
    )
    assert not lage.moeglich and lage.nur_takt, "Aufbau: es sollte der Takt sein"
    return lage


# --- Die Unterscheidung selbst ---------------------------------------------

def test_der_eigene_takt_ist_nur_eine_frage_der_zeit() -> None:
    assert _takt(Aktion.KOMMENTAR).nur_takt is True


def test_eine_erschoepfte_tagesmenge_ist_keine() -> None:
    """"Heute nicht mehr" endet erst um Mitternacht - Warten hilft nicht."""
    lage = pruefe(
        Aktion.KOMMENTAR,
        Grenze(pro_tag=5, abstand_min=0, abstand_max=0),
        heute=5,
        letzte=None,
        jetzt=JETZT,
    )

    assert lage.moeglich is False
    assert lage.nur_takt is False
    assert lage.wartezeit == "", "ohne Wartezeit kann niemand darauf warten"


def test_eine_bremse_der_gegenseite_ist_keine() -> None:
    """**Der Fall, der die erste Fassung eine Stunde schlafen liess.**

    Sie traegt eine Wartezeit und ist trotzdem keine Frage der Zeit, sondern
    eine Ansage. Sie zu verschlafen hiesse, danach genau das Muster
    fortzusetzen, das zu ihr gefuehrt hat.
    """
    lage = pruefe(
        Aktion.KOMMENTAR,
        Grenze(pro_tag=100, abstand_min=8, abstand_max=20),
        heute=1,
        letzte=None,
        gesperrt_bis=JETZT + timedelta(hours=1),
        jetzt=JETZT,
    )

    assert lage.wartet is True, "eine Wartezeit steht dran"
    assert lage.nur_takt is False, "und trotzdem wird sie nicht abgewartet"


# --- Was der Lauf daraus macht ---------------------------------------------

def test_der_lauf_wartet_den_kommentartakt_ab() -> None:
    """**Die Zusicherung dieser Datei.**

    Vor dem 14.09.2026 stand hier "" - und der Treiber beendete den Lauf mit
    2669 offenen Kommentaren.
    """
    lage = _takt(Aktion.KOMMENTAR)
    fortschritt = _fortschritt({Aktion.KOMMENTAR: lage})

    assert lauf.naechster_schritt(fortschritt) is None, "jetzt gerade geht nichts"
    # Die Wartezeit kommt aus der Lage und wird nicht zweimal gerechnet - der
    # Treiber soll die Regel nicht ein zweites Mal auslegen.
    assert fortschritt.wartet_auf_takt == lage.wartezeit
    assert fortschritt.wartet_auf_takt.startswith("noch ")


def test_nach_dem_takt_steht_der_kommentar_an() -> None:
    """Gewartet wird nur, weil danach wirklich etwas kommt."""
    frei = _fortschritt({Aktion.KOMMENTAR: Lage(Aktion.KOMMENTAR, True, rest_heute=99)})
    schritt = lauf.naechster_schritt(frei)

    assert schritt is not None
    assert schritt.texttyp is Texttyp.KOMMENTAR


def test_ohne_arbeit_wird_nicht_gewartet() -> None:
    """Eine Viertelstunde Schlaf fuer nichts waere schlimmer als das Ende.

    Der Takt steht, aber die Gruppe hat ihr Ziel erreicht - dann ist der Lauf
    wirklich durch, und ``wartet_auf_takt`` sagt das.
    """
    fertig = lauf.Lauffortschritt(
        lauf_id=1,
        status=lauf.LaufStatus.LAEUFT,
        kampagnen=[
            lauf.Kampagnenfortschritt(
                campaign_id="k",
                name="test_neu",
                gruppen=[_gruppe(veroeffentlicht=10, ziel=10)],
            )
        ],
        aktionen={Aktion.KOMMENTAR: _takt(Aktion.KOMMENTAR)},
    )

    assert fertig.wartet_auf_takt == ""


def test_eine_bremse_beendet_den_lauf_statt_ihn_schlafen_zu_legen() -> None:
    """Sonst sitzt ein Mensch eine Stunde vor dem Bildschirm.

    Dieselbe Aussage wie ``test_eine_bremse_haelt_nur_ihre_eigene_aktion_an``,
    hier auf der Ebene der Entscheidung statt des ganzen Laufs.
    """
    gebremst = pruefe(
        Aktion.KOMMENTAR,
        Grenze(pro_tag=100, abstand_min=8, abstand_max=20),
        heute=1,
        letzte=None,
        gesperrt_bis=JETZT + timedelta(hours=1),
        jetzt=JETZT,
    )

    assert _fortschritt({Aktion.KOMMENTAR: gebremst}).wartet_auf_takt == ""


def test_eine_erschoepfte_tagesmenge_beendet_den_lauf() -> None:
    """"Heute nicht mehr" heisst: morgen wieder, nicht gleich wieder."""
    erschoepft = pruefe(
        Aktion.KOMMENTAR,
        Grenze(pro_tag=5, abstand_min=0, abstand_max=0),
        heute=5,
        letzte=None,
        jetzt=JETZT,
    )

    assert _fortschritt({Aktion.KOMMENTAR: erschoepft}).wartet_auf_takt == ""


def test_der_beitrittstakt_bleibt_wie_er_war() -> None:
    """``wartet_auf_beitritt`` ist der Sonderfall und bleibt unberuehrt.

    Er beantwortet eine andere Frage - "haelt der Takt die **Reihenfolge**
    auf?" - und wird vor dem allgemeinen Weg gefragt. Zusammengelegt haette
    der Lauf die Beitrittsphase verlassen koennen, solange Anfragen offen
    stehen; die geforderte Reihenfolge waere eine Empfehlung geworden.
    """
    assert hasattr(lauf.Lauffortschritt, "wartet_auf_beitritt")
    assert hasattr(lauf.Lauffortschritt, "wartet_auf_takt")


# --- Ein Kommentar je Gruppe und Tag - und was ihn verbraucht -------------

def test_kein_fehlschlag_verbraucht_ein_tageskontingent(tmp_path) -> None:
    """**Der Grund fuer "24 Gruppen, ein Kommentar"** (14.09.2026) - erweitert.

    Damals wurde allein der **technische** Fehlschlag herausgenommen: Er ist
    nie in der Gruppe angekommen, und ihn mitzuzaehlen hiess, dass ein
    geschlossenes Browserfenster die Gruppe den ganzen Tag kostet. Die
    Ablehnung zaehlte weiter mit, mit der Begruendung, die Gruppe habe ihn
    ja gesehen.

    **Seit dem 20.09.2026 zaehlt auch sie nicht** (Regel 3/4 des Nutzers).
    Die alte Begruendung trifft auf die Moderation zu - auf einen Kommentar,
    den Facebook gar nicht erst angenommen hat, trifft sie nicht: Er stand
    dort nie. Gezaehlt wird, was wirklich in der Gruppe steht.

    Der Schutz wandert damit nur: Was die Gruppe ablehnt, beschraenkt sie
    ueber ``qualifikation.Beobachtung``; was das Konto bremst, faengt
    ``Ausgangsart.RATE_LIMIT`` mit seinem Backoff ab.
    """
    from fbgroups.marketing.ausgang import Ablehnungsgrund
    from fbgroups.marketing.models import Campaign, CampaignGroup, PostVersuch
    from fbgroups.marketing.store import MarketingStore
    from fbgroups.models import Group
    from fbgroups.storage import SqliteStore

    pfad = tmp_path / "groups.sqlite"
    heute = datetime.now(UTC).date().isoformat()

    with SqliteStore(pfad) as store:
        store.upsert_groups([
            Group(group_id=gid, url_canonical=f"https://www.facebook.com/groups/{gid}",
                  name=f"Gruppe {gid}")
            for gid in ("g-technik", "g-abgelehnt")
        ])

    with MarketingStore(pfad) as store:
        store.save_campaign(Campaign(campaign_id="k", name="k"))
        for gid in ("g-technik", "g-abgelehnt"):
            store.add_link(CampaignGroup(campaign_id="k", group_id=gid,
                                         tracking_code=f"FB-T-{gid[-3:]}"))

        # Zwei Fehlschlaege am selben Tag - einer technisch, einer von der
        # Gruppe. Nur der zweite ist eine Aussage ueber die Gruppe.
        for gid, grund in (
            ("g-technik", Ablehnungsgrund.TECHNICAL_ERROR),
            ("g-abgelehnt", Ablehnungsgrund.CONTENT_REJECTED),
        ):
            versuch = store.beginne_versuch(
                PostVersuch(campaign_id="k", group_id=gid, texttyp="kommentar", nummer=1)
            )
            store.beende_versuch(versuch, erfolg=False, fehler=str(grund.value))
            store.conn.execute(
                "UPDATE post_versuche SET grund = ? WHERE group_id = ?",
                (grund.value, gid),
            )
        store.conn.commit()

        gezaehlt = store.versuche_heute_je_gruppe(heute, "kommentar")

        # Und die Gegenprobe: Ein **erfolgreicher** Kommentar zaehlt sehr
        # wohl - sonst pruefte dieser Test nur, dass nichts gezaehlt wird.
        erfolgreich = store.beginne_versuch(
            PostVersuch(campaign_id="k", group_id="g-abgelehnt", texttyp="kommentar", nummer=2)
        )
        store.beende_versuch(erfolgreich, erfolg=True)
        nach_erfolg = store.versuche_heute_je_gruppe(heute, "kommentar")

    assert gezaehlt.get("g-technik", 0) == 0, "Technik ist kein Urteil ueber die Gruppe"
    assert gezaehlt.get("g-abgelehnt", 0) == 0, "abgelehnt heisst: stand nie in der Gruppe"
    assert nach_erfolg["g-abgelehnt"] == 1, "ein veroeffentlichter Kommentar zaehlt"
