"""Ein Limit fuer Beitraege ist keines fuer Kommentare - und umgekehrt.

Der Anlass vom 15.09.2026 ist eine Abschlussmeldung, die den Verdacht nahelegt,
der Beitragstakt habe die Kommentare eingefroren:

    Beitraege:  1 / 24
    Kommentare: 9 / 240
    Heute nicht mehr moeglich:
      post: Abstandsregel - noch 137 Min
    16 Gruppe(n) nach einem Fehlschlag beiseitegelegt.

Der Verdacht war falsch - neun Kommentare gingen hinaus, **waehrend** der
Beitrag getaktet war. Die Zeile nennt, was gerade nicht geht, nicht den Grund
fuer das Ende des Laufs.

Beim Nachsehen fanden sich aber zwei echte Fehler in derselben Gegend, und
beide betreffen genau die Zusage aus Punkt 8 der Anforderung vom 12.09.2026 -
*"Ein Limit fuer eine Aktion ist keines fuer die andere"*:

1. Die **Kommentar**-Tagesmenge je Gruppe sperrte die **ganze** Gruppe, also
   auch ihren Beitrag. Bei ``je_gruppe_taeglich: 1`` nahm der erste Kommentar
   der Gruppe den Beitrag fuer denselben Tag.
2. ``wartet_auf_takt`` meldete eine Wartezeit, obwohl ein Schritt bereitlag.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from fbgroups.marketing import lauf
from fbgroups.marketing.grenzen import Aktion, Grenze, Lage, pruefe
from fbgroups.marketing.models import PostStatus, Texttyp

JETZT = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)


def _gruppe(
    gid: str = "g1",
    *,
    post_status: PostStatus = PostStatus.OFFEN,
    heute_in_gruppe: int = 0,
    gruppenlimit: int = 1,
    veroeffentlicht: int = 0,
):
    return lauf.Gruppenfortschritt(
        campaign_id="k",
        group_id=gid,
        name=gid,
        veroeffentlicht=veroeffentlicht,
        ziel=10,
        mitglied=True,
        mitgliedschaft_noetig=False,
        post_status=post_status,
        post_fassungen=frozenset({1}),
        heute_in_gruppe=heute_in_gruppe,
        gruppenlimit=gruppenlimit,
    )


def _stand(gruppen: list, aktionen: dict):
    return lauf.Lauffortschritt(
        lauf_id=1,
        status=lauf.LaufStatus.LAEUFT,
        kampagnen=[
            lauf.Kampagnenfortschritt(
                campaign_id="k", name="k", gruppen=gruppen
            )
        ],
        aktionen=aktionen,
    )


def _getaktet(aktion: Aktion, *, abstand: int, vor_minuten: int) -> Lage:
    lage = pruefe(
        aktion,
        Grenze(pro_tag=100, abstand_min=abstand, abstand_max=abstand),
        heute=1,
        letzte=JETZT - timedelta(minutes=vor_minuten),
        jetzt=JETZT,
    )
    assert lage.nur_takt, "Aufbau: es sollte der Takt sein"
    return lage


def _frei(aktion: Aktion) -> Lage:
    return Lage(aktion, True, rest_heute=99)


# --- 1. Der Beitragstakt haelt die Kommentare nicht auf --------------------

def test_bei_getaktetem_beitrag_geht_der_kommentar_sofort_hinaus() -> None:
    """**Die Frage des Nutzers, als Zusicherung.**

    Der Beitrag ist noch 96 Minuten gesperrt, der Kommentar frei - und
    ``naechster_schritt`` liefert den Kommentar. Nicht ``None``, nicht den
    Beitrag, keine Wartezeit.
    """
    stand = _stand(
        [_gruppe()],
        {
            Aktion.POST: _getaktet(Aktion.POST, abstand=120, vor_minuten=24),
            Aktion.KOMMENTAR: _frei(Aktion.KOMMENTAR),
        },
    )

    schritt = lauf.naechster_schritt(stand)

    assert schritt is not None
    assert schritt.texttyp is Texttyp.KOMMENTAR


def test_bei_getaktetem_beitrag_wird_nicht_gewartet() -> None:
    """Wer arbeiten kann, wartet nicht.

    Vorher meldete ``wartet_auf_takt`` "noch 96 Min", obwohl ein Kommentar
    bereitlag - der Treiber fragt es zwar nur bei leerem Schritt, aber eine
    Eigenschaft, die das sagt, ist fuer sich genommen falsch.
    """
    stand = _stand(
        [_gruppe()],
        {
            Aktion.POST: _getaktet(Aktion.POST, abstand=120, vor_minuten=24),
            Aktion.KOMMENTAR: _frei(Aktion.KOMMENTAR),
        },
    )

    assert stand.wartet_auf_takt == ""


def test_bei_getaktetem_kommentar_geht_der_beitrag_hinaus() -> None:
    """Die Gegenprobe - die Trennung gilt in beide Richtungen."""
    stand = _stand(
        [_gruppe()],
        {
            Aktion.POST: _frei(Aktion.POST),
            Aktion.KOMMENTAR: _getaktet(Aktion.KOMMENTAR, abstand=12, vor_minuten=2),
        },
    )

    schritt = lauf.naechster_schritt(stand)

    assert schritt is not None
    assert schritt.texttyp is Texttyp.POST


# --- 2. Die Kommentarmenge je Gruppe sperrt den Beitrag nicht -------------

def test_der_erste_kommentar_nimmt_der_gruppe_nicht_den_beitrag() -> None:
    """**Der gefundene Fehler.**

    ``limits.comments.je_gruppe_taeglich`` wird aus **Kommentar**-Versuchen
    gezaehlt. Auf die ganze Gruppe angewandt sperrte sie auch den Beitrag -
    bei ``1`` also: Der erste Kommentar kostete den Beitrag desselben Tages.
    """
    voll = _gruppe(heute_in_gruppe=1, gruppenlimit=1, post_status=PostStatus.OFFEN)

    assert voll.tageslimit_erreicht is True
    assert voll.bearbeitbar is True, "ihr Beitrag steht noch aus"

    schritt = lauf.naechster_schritt(
        _stand([voll], {Aktion.POST: _frei(Aktion.POST), Aktion.KOMMENTAR: _frei(Aktion.KOMMENTAR)})
    )
    assert schritt is not None
    assert schritt.texttyp is Texttyp.POST, "der Beitrag darf, der Kommentar nicht"


def test_ohne_offenen_beitrag_ist_die_gruppe_fuer_heute_durch() -> None:
    """Die Tagesmenge wirkt weiter - sie sperrt nur nicht mehr zu viel."""
    voll = _gruppe(
        heute_in_gruppe=1, gruppenlimit=1, post_status=PostStatus.VEROEFFENTLICHT
    )

    assert voll.tageslimit_erreicht is True
    assert voll.bearbeitbar is False


def test_eine_gruppe_an_ihrer_tagesmenge_bekommt_keinen_zweiten_kommentar() -> None:
    """Sonst waere die Regel abgeschafft statt praezisiert."""
    voll = _gruppe(heute_in_gruppe=1, gruppenlimit=1, post_status=PostStatus.OFFEN)
    stand = _stand(
        [voll],
        {
            # Beitrag gesperrt, Kommentar frei: Bliebe die Gruppe fuer den
            # Kommentar waehlbar, bekaeme sie ihren zweiten.
            Aktion.POST: _getaktet(Aktion.POST, abstand=120, vor_minuten=24),
            Aktion.KOMMENTAR: _frei(Aktion.KOMMENTAR),
        },
    )

    assert lauf.naechster_schritt(stand) is None


def test_eine_volle_gruppe_haelt_die_naechste_nicht_auf() -> None:
    """**Der Folgefehler der Korrektur - und seine Behebung.**

    Seit eine Gruppe mit erreichter Tagesmenge ``bearbeitbar`` bleibt (ihr
    Beitrag darf ja), konnte ``naechste_gruppe`` genau sie liefern. Ist ihr
    Beitrag getaktet, stuende der Lauf vor ihr still - obwohl die naechste
    Gruppe einen Kommentar haette. Deshalb waehlt der Kommentarzweig seine
    Gruppe neu (``naechste_kommentargruppe``).
    """
    stand = _stand(
        [
            _gruppe("g1", heute_in_gruppe=1, gruppenlimit=1, post_status=PostStatus.OFFEN),
            _gruppe("g2", heute_in_gruppe=0, gruppenlimit=1),
        ],
        {
            Aktion.POST: _getaktet(Aktion.POST, abstand=120, vor_minuten=24),
            Aktion.KOMMENTAR: _frei(Aktion.KOMMENTAR),
        },
    )

    schritt = lauf.naechster_schritt(stand)

    assert schritt is not None
    assert schritt.group_id == "g2"
    assert schritt.texttyp is Texttyp.KOMMENTAR


def test_die_rangfolge_bleibt_auch_bei_der_kommentargruppe() -> None:
    """Beide lesen ``arbeitsliste`` - eine zweite Rangfolge waere zwei."""
    kampagne = lauf.Kampagnenfortschritt(
        campaign_id="k",
        name="k",
        gruppen=[
            _gruppe("g1", heute_in_gruppe=1, gruppenlimit=1, post_status=PostStatus.OFFEN),
            _gruppe("g2"),
            _gruppe("g3"),
        ],
    )

    assert kampagne.naechste_gruppe is not None
    assert kampagne.naechste_gruppe.group_id == "g1"
    assert kampagne.naechste_kommentargruppe is not None
    assert kampagne.naechste_kommentargruppe.group_id == "g2", "die erste, die darf"


# --- 3. Kein Beitrag mehr heisst nicht: Kampagne fertig --------------------

def test_erschoepfte_beitraege_beenden_die_kampagne_nicht() -> None:
    """Punkt 6 der Anforderung.

    Jede Gruppe hat ihren Beitrag, aber keine ihr Kommentarziel: Die
    Kampagne ist weder ``fertig`` noch ``abgeschlossen`` - und wird damit
    nie auf ``completed`` gesetzt.
    """
    kampagne = lauf.Kampagnenfortschritt(
        campaign_id="k",
        name="k",
        gruppen=[
            _gruppe("g1", post_status=PostStatus.VEROEFFENTLICHT, veroeffentlicht=2),
            _gruppe("g2", post_status=PostStatus.VEROEFFENTLICHT, veroeffentlicht=3),
        ],
    )

    assert kampagne.fertig is False
    assert kampagne.abgeschlossen is False


def test_ein_gesperrter_beitrag_macht_keine_kampagne_fertig() -> None:
    """Der Takt ist eine Aussage ueber die Uhr, nicht ueber die Kampagne."""
    stand = _stand(
        [_gruppe(veroeffentlicht=3)],
        {
            Aktion.POST: _getaktet(Aktion.POST, abstand=240, vor_minuten=103),
            Aktion.KOMMENTAR: _getaktet(Aktion.KOMMENTAR, abstand=12, vor_minuten=2),
        },
    )

    assert stand.fertig is False
    # Und gewartet wird auf den **Kommentar**, nicht auf den Beitrag.
    assert stand.wartet_auf_takt == "noch 10 Min"


@pytest.mark.parametrize("tagesmenge", [0, 3])
def test_die_beitragsmenge_beendet_die_kampagne_nicht(tagesmenge: int) -> None:
    """Weder abgeschaltet (``daily: 0``) noch erschoepft.

    Beides sagt "heute keine Beitraege mehr" - und keines davon sagt etwas
    ueber die Kommentare oder ueber das Ende der Kampagne.
    """
    erschoepft = Lage(
        Aktion.POST, False, grund=f"post: Tagesmenge erreicht ({tagesmenge}/{tagesmenge})"
    )
    stand = _stand(
        [_gruppe()],
        {Aktion.POST: erschoepft, Aktion.KOMMENTAR: _frei(Aktion.KOMMENTAR)},
    )

    schritt = lauf.naechster_schritt(stand)

    assert schritt is not None
    assert schritt.texttyp is Texttyp.KOMMENTAR
    assert stand.fertig is False


# --- 4. Die Abschlussmeldung nennt den wahren Grund ------------------------

def test_die_meldung_nennt_die_tagesmenge_je_gruppe() -> None:
    """**Der Fehler vom 15.09.2026 - und er war einer der Meldung.**

    Die Tafel las sich so:

        Offen bei: versand / ... (1 / 10 Kommentare)
        Heute nicht mehr moeglich:
          post: Tagesmenge erreicht (3/3)

    Offene Arbeit da, Kommentare **nicht** als gesperrt gemeldet - und der
    Lauf hoerte trotzdem auf. Es sah aus, als habe der Beitragstakt die
    Kommentare mitgerissen. Tatsaechlich hatte jede verbliebene Gruppe ihren
    einen Kommentar fuer heute schon, und das steht in
    ``limits.comments.je_gruppe_taeglich`` - einer Zahl, die unter "Heute
    nicht mehr moeglich" nicht vorkommt, weil dort nur die Grenzen je
    **Aktion** stehen.
    """
    voll = [
        _gruppe(
            f"g{i}",
            heute_in_gruppe=1,
            gruppenlimit=1,
            post_status=PostStatus.VEROEFFENTLICHT,
            veroeffentlicht=1,
        )
        for i in range(8)
    ]
    stand = _stand(
        voll,
        {
            Aktion.POST: Lage(Aktion.POST, False, grund="post: Tagesmenge erreicht (3/3)"),
            Aktion.KOMMENTAR: Lage(Aktion.KOMMENTAR, True, rest_heute=91),
        },
    )

    assert stand.gruppen_am_tageslimit == 8
    text = lauf.abschlusstext(stand)

    assert "je_gruppe_taeglich" in text, "der Grund muss dastehen"
    assert "8 Gruppe(n) hatten heute schon ihren Kommentar" in text
    assert "campaign sync" in text, "und der Hebel dazu"


def test_offen_bei_verspricht_keinen_kommentar_der_nicht_geht() -> None:
    """"1 / 10 Kommentare" las sich wie liegengebliebene Arbeit.

    Seit die Tagesmenge je Gruppe nur noch den Kommentar sperrt, kann dort
    eine Gruppe stehen, in der heute keiner mehr moeglich ist - ihr Beitrag
    haelt sie in der Liste.
    """
    stand = _stand(
        [
            _gruppe(
                "g1", heute_in_gruppe=1, gruppenlimit=1,
                post_status=PostStatus.OFFEN, veroeffentlicht=1,
            )
        ],
        {
            Aktion.POST: Lage(Aktion.POST, False, grund="post: Tagesmenge erreicht (3/3)"),
            Aktion.KOMMENTAR: Lage(Aktion.KOMMENTAR, True, rest_heute=91),
        },
    )

    text = lauf.abschlusstext(stand)

    assert "Offen bei:" in text
    assert "heute kein Kommentar mehr" in text
    assert "1 / 10 Kommentare" not in text


def test_ohne_tagesmenge_steht_die_zeile_nicht_da() -> None:
    """Eine Zeile, die immer dasteht, sagt nichts mehr."""
    stand = _stand(
        [_gruppe("g1", heute_in_gruppe=0, gruppenlimit=1)],
        {Aktion.POST: _frei(Aktion.POST), Aktion.KOMMENTAR: _frei(Aktion.KOMMENTAR)},
    )

    assert stand.gruppen_am_tageslimit == 0
    assert "je_gruppe_taeglich" not in lauf.abschlusstext(stand)


def test_eine_fertige_gruppe_zaehlt_nicht_als_tagesmenge() -> None:
    """Sie ist durch, nicht gebremst - der Unterschied gehoert in die Zahl."""
    fertig = _gruppe(
        "g1", heute_in_gruppe=1, gruppenlimit=1,
        post_status=PostStatus.VEROEFFENTLICHT, veroeffentlicht=10,
    )
    kampagne = lauf.Kampagnenfortschritt(
        campaign_id="k", name="k", gruppen=[fertig]
    )

    assert fertig.fertig is True
    assert kampagne.gruppen_am_tageslimit == 0
