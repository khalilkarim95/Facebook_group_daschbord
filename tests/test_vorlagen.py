"""Die deterministische Textherstellung.

Geprueft wird hier vor allem, was sich still auswirken wuerde: ein
Tracking-Platzhalter, der verlorengeht, eine Vorlagenwahl, die sich zwischen
zwei Laeufen aendert, und ein Stadtname, der in einer Vorlage ohne Stadt
stehenbleibt. Alles drei faellt sonst erst auf, wenn der Beitrag in der Gruppe
steht - und dann ist er nicht mehr zurueckzuholen.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime

import pytest

from fbgroups.config import AppConfig
from fbgroups.marketing.models import Campaign, Texttyp
from fbgroups.marketing.vorlagen import (
    MIT_STADT,
    OHNE_STADT,
    PLATZHALTER_LINK,
    Personalisierung,
    UnbekannterPlatzhalter,
    VorlageFehlt,
    erzeuge,
    fuelle,
    monat_jetzt,
    personalisierung,
    pruefe,
    schluessel_fuer,
    sprache_der_kampagne,
    text_fuer_gruppe,
    vorlage_zu,
)
from fbgroups.models import Group


def gruppe(
    group_id: str = "739201847362915",
    name: str = "Syrer in Bonn",
    city: str | None = "Bonn",
    tags: list[str] | None = None,
) -> Group:
    return Group(
        group_id=group_id,
        url_canonical=f"https://www.facebook.com/groups/{group_id}",
        name=name,
        city=city,
        audience_tags=tags if tags is not None else ["syrians"],
    )


def kampagne(sprache: str = "ar", audiences: list[str] | None = None) -> Campaign:
    return Campaign(
        campaign_id="bonn-123",
        name="Syrer in Bonn",
        language=sprache,
        landing_page="https://b-tarikak.de/",
        audiences=audiences if audiences is not None else ["syrians"],
    )


# -- Was in den Text kommt --------------------------------------------------


def test_stadt_und_anrede_kommen_aus_bestand_und_vorlagen(config: AppConfig) -> None:
    """Woher die beiden Angaben stammen - seit dem 20.09.2026 verschoben.

    Vorher kam die Stadt als ``name_ar`` aus ``cities.yaml`` und die Anrede
    als ``label_ar`` aus ``audiences.yaml``. Beide Dateien sind mit der
    Entdeckungsschicht entfernt:

    * Die **Stadt** steht so im Beitrag, wie sie im Bestand steht. Einen
      arabischen Namen dafuer zu erfinden waere schlimmer als ein deutscher -
      ein falscher Ortsname faellt erst in der fremden Gruppe auf.
    * Die **Anrede** kommt aus ``anrede_allgemein`` in ``textvorlagen.yaml``.
      Der blosse Tag ("syrians") taugt nicht in einem arabischen Satz.

    Wer den arabischen Stadtnamen will, traegt ihn in den Bestand ein.
    """
    daten = personalisierung(gruppe(), kampagne("ar"), config)
    assert daten.stadt == "Bonn"
    assert daten.zielgruppe == "الأصدقاء"

    _, text = erzeuge(gruppe(), kampagne("ar"), config)
    assert "Bonn" in text

    _, text_ar = erzeuge(gruppe(city="بون"), kampagne("ar"), config)
    assert "بون" in text_ar
    assert "Bonn" not in text_ar


def test_deutsche_kampagne_bekommt_die_allgemeine_anrede(config: AppConfig) -> None:
    """Drei Beschriftungen je Zielgruppe gab es bis zum 20.09.2026.

    ``label_de``, ``label_kurz_de`` und ``label_ar`` standen in
    ``audiences.yaml``; das kurze war dafuer da, dass in einer Stadtvorlage
    nicht "Syrer in Deutschland in Bonn" stand. Mit der Datei ist die Frage
    weg - es gibt nur noch ``anrede_allgemein``, und die passt in beide Saetze.
    """
    daten = personalisierung(gruppe(), kampagne("de"), config)
    assert daten.zielgruppe == "Freunde"

    _, text = erzeuge(gruppe(), kampagne("de"), config)
    assert "Bonn" in text
    assert "Syrer in Deutschland" not in text


def test_der_tracking_platzhalter_bleibt_unersetzt(config: AppConfig) -> None:
    """``{link}`` gehoert ``beitrag.beitragstext`` - und nur ihr.

    Wuerde dieses Modul ihn aufloesen, stuende der Tracking-Code im
    gespeicherten Text und damit spaeter im Prompt eines Sprachmodells.
    """
    _, text = erzeuge(gruppe(), kampagne("ar"), config)
    assert PLATZHALTER_LINK in text


def test_fuelle_laesst_die_link_platzhalter_in_ruhe() -> None:
    daten = Personalisierung(zielgruppe="السوريين", stadt="بون")
    text = fuelle("{zielgruppe} {stadt} {link} {tracking_code} {landing_page}", daten)
    assert text == "السوريين بون {link} {tracking_code} {landing_page}"


def test_der_gruppenname_wird_ersetzt() -> None:
    daten = Personalisierung(zielgruppe="Syrer", stadt="Bonn", gruppe="Syrer in Bonn")
    assert fuelle("Hallo {gruppe}: {link}", daten) == "Hallo Syrer in Bonn: {link}"


def test_ein_erfundener_platzhalter_wird_abgewiesen() -> None:
    """Er bliebe in geschweiften Klammern im Beitrag stehen.

    Und das faellt erst auf, wenn der Beitrag in der Gruppe steht - dann ist
    er nicht mehr zurueckzuholen. Lieber kein Text als einer mit einer Luecke.
    """
    daten = Personalisierung(zielgruppe="Syrer", stadt="Bonn")
    with pytest.raises(UnbekannterPlatzhalter):
        fuelle("Hallo {beruf} in {stadt}: {link}", daten)


# -- Ziel, Gegenstand, Datum ------------------------------------------------


def test_das_ziel_steht_in_den_textvorlagen(config: AppConfig) -> None:
    """``{ziel}`` ist ein Land, keine Anrede - und kommt aus einer Quelle.

    Bis zum 20.09.2026 stand das Land je Zielgruppe in ``audiences.yaml``
    (``ziel_ar``); seither steht es einmal in ``textvorlagen.yaml`` unter
    ``ziel_allgemein`` und lautet dort "سوريا" - der Bestand dieses
    Projekts sind Syrien-Strecken.
    """
    daten = personalisierung(gruppe(), kampagne("ar"), config)
    assert daten.ziel == "سوريا"
    assert daten.ziel != daten.zielgruppe


def test_das_ziel_haengt_nicht_mehr_an_der_zielgruppe(config: AppConfig) -> None:
    """Jede Gruppe bekommt dasselbe Ziel - egal, welchen Tag sie traegt.

    Vorher trug "syrians" das Land "سوريا" und "arabs" gar keines. Diese
    Unterscheidung ist mit ``audiences.yaml`` entfallen. Der Preis ist
    benannt: Wer einen Bestand mit zwei Zielen bewirbt, braucht zwei
    Kampagnen mit eigenen Vorlagen - nicht eine Tabelle neben dem Text.
    """
    syrisch = personalisierung(
        gruppe(tags=["syrians"]), kampagne("ar", audiences=["syrians"]), config
    )
    arabisch = personalisierung(
        gruppe(tags=["arabs"]), kampagne("ar", audiences=["arabs"]), config
    )
    assert syrisch.ziel == arabisch.ziel == "سوريا"


def test_das_datum_bleibt_beim_fuellen_stehen(config: AppConfig) -> None:
    """``{datum}`` gehoert ``beitrag.mit_link``, nicht diesem Modul.

    Es traegt den laufenden Monat. Beim Erzeugen eingesetzt und gespeichert
    stuende in einem Beitrag, der drei Wochen spaeter hinausgeht, der Monat
    von damals - eine Frage nach Reisenden im letzten Monat ist falsch.
    """
    daten = personalisierung(gruppe(), kampagne("ar"), config)
    assert fuelle("Wer reist in {datum}? {link}", daten) == "Wer reist in {datum}? {link}"


def test_der_monatsname_ist_levantinisch(config: AppConfig) -> None:
    """"أيلول" und nicht "سبتمبر" - der Zielmarkt sind syrische Communities.

    ``jetzt`` statt des echten Kalenders: Ein Test, der die Uhr liest, prueft
    im August etwas anderes als im September.
    """
    september = datetime(2026, 9, 15)
    assert monat_jetzt(config, "ar", jetzt=september) == "أيلول"
    assert monat_jetzt(config, "de", jetzt=september) == "September"


# -- Die beiden Toepfe ------------------------------------------------------


def test_gruppe_ohne_stadt_bekommt_die_vorlage_ohne_stadt(config: AppConfig) -> None:
    schluessel, text = erzeuge(gruppe(city=None), kampagne("ar"), config)
    assert f"/{OHNE_STADT}/" in schluessel
    assert "{stadt}" not in text


def test_gruppe_mit_stadt_bekommt_die_vorlage_mit_stadt(config: AppConfig) -> None:
    schluessel, _ = erzeuge(gruppe(city="Bonn"), kampagne("ar"), config)
    assert f"/{MIT_STADT}/" in schluessel


def test_jede_eingetragene_stadt_zaehlt(config: AppConfig) -> None:
    """Es gibt keine Liste mehr, gegen die eine Stadt geprueft werden koennte.

    Bis zum 20.09.2026 musste ``Group.city`` in ``cities.yaml`` vorkommen,
    sonst galt die Gruppe als ohne Stadt - lieber die allgemeine Vorlage als
    ein erfundener Ortsname. Mit der Datei ist die Pruefung entfallen: Das
    Feld wird von Hand gepflegt, und was dort steht, gilt. Leer bleibt leer.
    """
    schluessel, text = erzeuge(gruppe(city="Atlantis"), kampagne("ar"), config)
    assert f"/{MIT_STADT}/" in schluessel
    assert "Atlantis" in text

    ohne, _ = erzeuge(gruppe(city="   "), kampagne("ar"), config)
    assert f"/{OHNE_STADT}/" in ohne


# -- Beitrag und Kommentar --------------------------------------------------


def test_beitrag_und_kommentar_kommen_aus_getrennten_toepfen(config: AppConfig) -> None:
    """Sie teilen sich keine Vorlage - sprachlich haben sie wenig gemeinsam."""
    post_key, post_text = erzeuge(gruppe(), kampagne("ar"), config)
    komm_key, komm_text = erzeuge(
        gruppe(), kampagne("ar"), config, texttyp=Texttyp.KOMMENTAR
    )

    assert "/post/" in post_key
    assert "/kommentar/" in komm_key
    assert post_text != komm_text
    assert PLATZHALTER_LINK in komm_text


def test_der_kommentar_eroeffnet_nicht(config: AppConfig) -> None:
    """Ein Kommentar antwortet unter einem fremden Beitrag: wenige Zeilen."""
    for sprache in ("ar", "de"):
        for stadt in ("Bonn", None):
            _, text = erzeuge(
                gruppe(city=stadt),
                kampagne(sprache),
                config,
                texttyp=Texttyp.KOMMENTAR,
            )
            zeilen = [z for z in text.splitlines() if z.strip()]
            assert len(zeilen) <= 4, (sprache, stadt, text)


def test_ein_schluessel_des_falschen_zwecks_wird_uebergangen(config: AppConfig) -> None:
    """Sonst traege der Kommentar die Beitragsvorlage - die Vermischung."""
    beitrags_key, _ = erzeuge(gruppe(), kampagne("ar"), config)
    schluessel, text = erzeuge(
        gruppe(), kampagne("ar"), config, schluessel=beitrags_key,
        texttyp=Texttyp.KOMMENTAR,
    )
    assert "/kommentar/" in schluessel
    assert PLATZHALTER_LINK in text


def test_die_eigene_vorlage_gilt_nur_fuer_den_beitrag(config: AppConfig) -> None:
    """Sie ist als Beitrag geschrieben - als Kommentar waere sie eine Behauptung."""
    eigene = kampagne("ar")
    eigene.message_template = "Eigener Beitrag fuer {zielgruppe}: {link}"

    post_key, post_text = text_fuer_gruppe(gruppe(), eigene, config)
    komm_key, komm_text = text_fuer_gruppe(
        gruppe(), eigene, config, texttyp=Texttyp.KOMMENTAR
    )

    assert post_key == "kampagne"
    assert post_text.startswith("Eigener Beitrag")
    assert "/kommentar/" in komm_key
    assert not komm_text.startswith("Eigener Beitrag")


def test_beide_zwecke_kennen_die_beiden_toepfe(config: AppConfig) -> None:
    for texttyp in Texttyp:
        mit, _ = erzeuge(gruppe(city="Bonn"), kampagne("ar"), config, texttyp=texttyp)
        ohne, text = erzeuge(gruppe(city=None), kampagne("ar"), config, texttyp=texttyp)
        assert f"/{MIT_STADT}/" in mit
        assert f"/{OHNE_STADT}/" in ohne
        assert "{stadt}" not in text


# -- Die Zielgruppe ---------------------------------------------------------


def test_die_anrede_haengt_nicht_mehr_an_den_tags(config: AppConfig) -> None:
    """Drei Faelle, eine Antwort - seit dem 20.09.2026.

    Vorher entschied eine dreistufige Ableitung, welcher Tag angesprochen
    wird: erst ein Tag, den auch die Kampagne bewirbt, sonst der erste Tag der
    Gruppe, sonst die erste Zielgruppe der Kampagne. Daraus wurde ueber
    ``audiences.yaml`` eine Anrede. Ohne die Datei gibt es nichts mehr
    abzuleiten - und einen Tag als Anrede einzusetzen ("syrians" mitten in
    einem arabischen Satz) waere schlechter als ein allgemeines Wort.
    """
    faelle = [
        gruppe(tags=["arabs", "syrians"]),
        gruppe(tags=["syrians"]),
        gruppe(tags=[]),
    ]
    for g in faelle:
        daten = personalisierung(g, kampagne("ar", audiences=["syrians"]), config)
        assert daten.zielgruppe == "الأصدقاء"


def test_im_fertigen_text_bleibt_kein_platzhalter_stehen(config: AppConfig) -> None:
    _, text = erzeuge(gruppe(tags=[]), kampagne("ar", audiences=[]), config)
    assert "{zielgruppe}" not in text
    assert "{ziel}" not in text


# -- Die Wahl der Vorlage ---------------------------------------------------


def test_dieselbe_gruppe_bekommt_immer_dieselbe_vorlage(config: AppConfig) -> None:
    erste = erzeuge(gruppe(), kampagne("ar"), config)
    zweite = erzeuge(gruppe(), kampagne("ar"), config)
    assert erste == zweite


def test_die_wahl_ueberlebt_einen_neustart() -> None:
    """Nicht das eingebaute ``hash``: das ist je Prozess gesalzen.

    Ohne diesen Test faellt der Fehler erst auf, wenn ein Neustart des
    Dienstes jeder Gruppe eine andere Vorlage gibt - unter demjenigen, der den
    Text gerade freigegeben hat.
    """

    def nummer_mit_seed(seed: str) -> str:
        ergebnis = subprocess.run(
            [
                sys.executable,
                "-c",
                "from fbgroups.marketing.vorlagen import _nummer;"
                "print(_nummer('739201847362915', 5))",
            ],
            capture_output=True,
            text=True,
            check=True,
            env={"PYTHONHASHSEED": seed, "PATH": ""},
        )
        return ergebnis.stdout.strip()

    assert nummer_mit_seed("0") == nummer_mit_seed("12345")


def test_verschiedene_gruppen_bekommen_verschiedene_vorlagen(config: AppConfig) -> None:
    """Sonst waere die ganze Datei wirkungslos - 310 gleiche Beitraege."""
    for texttyp in Texttyp:
        schluessel = {
            erzeuge(
                gruppe(group_id=f"1000000000{i:02d}"),
                kampagne("ar"),
                config,
                texttyp=texttyp,
            )[0]
            for i in range(40)
        }
        assert len(schluessel) >= 4, texttyp


def test_dieselbe_gruppe_bekommt_je_zweck_dieselbe_vorlage(config: AppConfig) -> None:
    """Zweimal gefuellt, zweimal dasselbe - fuer beide Einsatzzwecke."""
    for texttyp in Texttyp:
        erste = erzeuge(gruppe(), kampagne("ar"), config, texttyp=texttyp)
        zweite = erzeuge(gruppe(), kampagne("ar"), config, texttyp=texttyp)
        assert erste == zweite


def test_die_kennung_ueberlebt_das_umsortieren(config: AppConfig) -> None:
    """Der Grund fuer Kennungen statt laufender Nummern.

    Frueher stand im Schluessel die Position; eine in der Mitte eingefuegte
    Vorlage verschob alle folgenden, und eine Gruppe trug einen Schluessel,
    hinter dem ein anderer Text stand.
    """
    schluessel, text = erzeuge(gruppe(), kampagne("ar"), config)

    umgedreht = _config_mit_vorlagen(
        config,
        {
            "ar": {
                "post": {
                    MIT_STADT: list(
                        reversed(config.textvorlagen["vorlagen"]["ar"]["post"][MIT_STADT])
                    ),
                    OHNE_STADT: config.textvorlagen["vorlagen"]["ar"]["post"][OHNE_STADT],
                },
                "kommentar": config.textvorlagen["vorlagen"]["ar"]["kommentar"],
            }
        },
    )
    assert vorlage_zu(umgedreht, schluessel) == vorlage_zu(config, schluessel)
    assert PLATZHALTER_LINK in text


def test_ein_gespeicherter_schluessel_wird_wiederverwendet(config: AppConfig) -> None:
    """Der Text darf sich nicht aendern, nur weil neu gefuellt wird."""
    fest = f"ar/post/{MIT_STADT}/entdeckt"
    schluessel, text = erzeuge(gruppe(), kampagne("ar"), config, schluessel=fest)
    assert schluessel == fest
    assert text == fuelle(
        vorlage_zu(config, fest), personalisierung(gruppe(), kampagne("ar"), config)
    )


def test_ein_ins_leere_zeigender_schluessel_waehlt_neu(config: AppConfig) -> None:
    """Der Grund liegt dann in der Konfiguration, nicht bei dieser Gruppe."""
    verschwunden = f"ar/post/{MIT_STADT}/gibtsnicht"
    schluessel, text = erzeuge(gruppe(), kampagne("ar"), config, schluessel=verschwunden)
    assert schluessel != verschwunden
    assert PLATZHALTER_LINK in text


def test_ein_alter_schluessel_zeigt_weiter_auf_dieselbe_vorlage(
    config: AppConfig,
) -> None:
    """"ar/mit_stadt/3" stammt aus der Zeit vor den Kommentaren.

    Ihn nicht mehr zu lesen hiesse: 310 Gruppen bekommen beim naechsten
    Fuellen eine andere Vorlage, ohne dass jemand etwas geaendert hat.
    """
    # Der dreiteilige Schluessel meint die dritte Fassung des Beitragstopfes.
    # Ihre Kennung hat sich am 31.08.2026 geaendert (die Vorlagen wurden
    # ersetzt) - dass er weiterhin dorthin zeigt, ist genau die Zusage.
    alt = vorlage_zu(config, f"ar/{MIT_STADT}/3")
    neu = vorlage_zu(config, f"ar/post/{MIT_STADT}/afieh")
    assert alt == neu


def test_unbekannter_schluessel_wirft_beim_direkten_zugriff(config: AppConfig) -> None:
    with pytest.raises(VorlageFehlt):
        vorlage_zu(config, "kl/post/mit_stadt/direkt")
    with pytest.raises(VorlageFehlt):
        vorlage_zu(config, "unsinn")
    with pytest.raises(VorlageFehlt):
        vorlage_zu(config, "ar/plakat/mit_stadt/direkt")


# -- Die Sprache ------------------------------------------------------------


def test_die_kampagne_bestimmt_die_sprache(config: AppConfig) -> None:
    assert sprache_der_kampagne(kampagne("de"), config) == "de"
    assert sprache_der_kampagne(kampagne("ar"), config) == "ar"


def test_unbekannte_sprache_faellt_auf_die_vorgabe_zurueck(config: AppConfig) -> None:
    """Ein Tippfehler im Formular haelt die Textherstellung nicht an."""
    assert sprache_der_kampagne(kampagne("kl"), config) in ("ar", "de")


def test_ausgeschriebene_sprache_aus_den_einstellungen(config: AppConfig) -> None:
    """``settings.yaml`` schreibt 'arabisch', das Formular schickt 'ar'."""
    assert sprache_der_kampagne(kampagne(""), config) == "ar"


# -- Die Konfiguration selbst -----------------------------------------------


def test_die_vorlagen_des_projekts_sind_in_ordnung(config: AppConfig) -> None:
    assert pruefe(config) == []


def _alle_vorlagen(config: AppConfig):
    """(Ort, Kennung, Text) fuer jede Fassung in der Konfiguration."""
    alle = config.textvorlagen.get("vorlagen") or {}
    for sprache, zwecke in alle.items():
        for zweck, toepfe in (zwecke or {}).items():
            for topf, liste in (toepfe or {}).items():
                for eintrag in liste:
                    kennung = str(eintrag.get("id", ""))
                    yield f"{sprache}/{zweck}/{topf}/{kennung}", kennung, eintrag["text"]


def test_keine_vorlage_enthaelt_einen_ausgeschriebenen_link(config: AppConfig) -> None:
    """Ein Link neben ``{link}`` fuehrte an der Zaehlung vorbei.

    Der Beitrag saehe richtig aus, und die Gruppe bekaeme trotzdem keinen
    Klick gutgeschrieben - genau der Fehler, den niemand bemerkt.
    """
    for ort, _, text in _alle_vorlagen(config):
        assert "http://" not in text, ort
        assert "https://" not in text, ort


def test_jede_vorlage_hat_eine_kennung(config: AppConfig) -> None:
    """Ohne sie waere der gespeicherte Schluessel wieder eine Position."""
    for ort, kennung, _ in _alle_vorlagen(config):
        assert kennung, ort


def test_jede_sprache_hat_fuenf_fassungen_je_topf(config: AppConfig) -> None:
    """Weniger als eine Handvoll, und die Beitraege wiederholen sich sichtbar."""
    alle = config.textvorlagen.get("vorlagen") or {}
    for sprache, zwecke in alle.items():
        for zweck in (t.value for t in Texttyp):
            for topf in (MIT_STADT, OHNE_STADT):
                liste = ((zwecke or {}).get(zweck) or {}).get(topf) or []
                assert len(liste) >= 5, f"{sprache}/{zweck}/{topf}"


def _topfpaar(mit: list[dict], ohne: list[dict]) -> dict:
    return {MIT_STADT: mit, OHNE_STADT: ohne}


def test_pruefe_meldet_eine_vorlage_ohne_link(config: AppConfig) -> None:
    kaputt = _config_mit_vorlagen(
        config,
        {
            "ar": {
                "post": _topfpaar(
                    [{"id": "a", "text": "Hallo {zielgruppe}"}],
                    [{"id": "a", "text": "Hallo {link}"}],
                ),
                "kommentar": _topfpaar(
                    [{"id": "a", "text": "Hallo {link}"}],
                    [{"id": "a", "text": "Hallo {link}"}],
                ),
            }
        },
    )
    assert any(PLATZHALTER_LINK in b for b in pruefe(kaputt))


def test_pruefe_meldet_stadt_im_falschen_topf(config: AppConfig) -> None:
    kaputt = _config_mit_vorlagen(
        config,
        {
            "ar": {
                "post": _topfpaar(
                    [{"id": "a", "text": "{stadt} {link}"}],
                    [{"id": "a", "text": "{stadt} {link}"}],
                ),
                "kommentar": _topfpaar(
                    [{"id": "a", "text": "{link}"}],
                    [{"id": "a", "text": "{link}"}],
                ),
            }
        },
    )
    assert any(OHNE_STADT in b and "{stadt}" in b for b in pruefe(kaputt))


def test_pruefe_meldet_einen_erfundenen_platzhalter(config: AppConfig) -> None:
    kaputt = _config_mit_vorlagen(
        config,
        {
            "ar": {
                "post": _topfpaar(
                    [{"id": "a", "text": "Fuer {beruf}: {link}"}],
                    [{"id": "a", "text": "{link}"}],
                ),
                "kommentar": _topfpaar(
                    [{"id": "a", "text": "{link}"}],
                    [{"id": "a", "text": "{link}"}],
                ),
            }
        },
    )
    assert any("{beruf}" in b for b in pruefe(kaputt))


def test_pruefe_meldet_eine_doppelte_kennung(config: AppConfig) -> None:
    """Zwei Fassungen mit derselben Kennung machen den Schluessel mehrdeutig."""
    kaputt = _config_mit_vorlagen(
        config,
        {
            "ar": {
                "post": _topfpaar(
                    [{"id": "a", "text": "Eins {link}"}, {"id": "a", "text": "Zwei {link}"}],
                    [{"id": "a", "text": "{link}"}],
                ),
                "kommentar": _topfpaar(
                    [{"id": "a", "text": "{link}"}],
                    [{"id": "a", "text": "{link}"}],
                ),
            }
        },
    )
    assert any("zweimal" in b for b in pruefe(kaputt))


def test_pruefe_meldet_einen_fehlenden_kommentartopf(config: AppConfig) -> None:
    """Eine Kampagne mit Kommentaren stuende sonst ohne Text da."""
    kaputt = _config_mit_vorlagen(
        config,
        {
            "ar": {
                "post": _topfpaar(
                    [{"id": "a", "text": "{link}"}], [{"id": "a", "text": "{link}"}]
                )
            }
        },
    )
    assert any("kommentar" in b for b in pruefe(kaputt))


def test_leerer_topf_wirft_statt_einen_leeren_text_zu_liefern(config: AppConfig) -> None:
    leer = _config_mit_vorlagen(
        config, {"ar": {"post": _topfpaar([], []), "kommentar": _topfpaar([], [])}}
    )
    for texttyp in Texttyp:
        with pytest.raises(VorlageFehlt):
            schluessel_fuer(
                leer, sprache="ar", topf=MIT_STADT, group_id="1", texttyp=texttyp
            )


def _config_mit_vorlagen(config: AppConfig, vorlagen: dict) -> AppConfig:
    """Dieselbe Konfiguration, nur mit anderen Vorlagen."""
    import dataclasses

    return dataclasses.replace(
        config,
        textvorlagen={
            "vorlagen": vorlagen,
            "anrede_allgemein": config.textvorlagen.get("anrede_allgemein", {}),
        },
    )


def test_jede_kommentarfassung_traegt_genau_einen_link(config: AppConfig) -> None:
    """Der ganze Vorrat, nicht eine Fassung.

    Eine einzige Vorlage mit zwei Links oder ohne einen traefe zehn Gruppen,
    bevor es jemandem auffiele. Dass der Kommentar ueberhaupt einen Link
    traegt, ist dabei eine Entscheidung des Nutzers (11.09.2026) und keine
    technische Notwendigkeit - ``pruefe_platzhalter`` laesst einen Kommentar
    ohne Link zu, siehe ``test_ein_kommentar_ohne_link_geht_durch_die_pruefung``.
    """
    from fbgroups.marketing.vorlagen import alle_texte_fuer_gruppe

    for sprache in ("ar", "de"):
        for stadt in ("Bonn", None):
            fassungen = alle_texte_fuer_gruppe(
                gruppe(city=stadt),
                kampagne(sprache),
                config,
                texttyp=Texttyp.KOMMENTAR,
                hoechstens=10,
            )
            assert fassungen, f"{sprache}/{stadt}: kein Vorrat"
            for schluessel, text in fassungen:
                assert text.count(PLATZHALTER_LINK) == 1, schluessel


def test_ein_kommentar_ohne_link_geht_durch_die_pruefung(config: AppConfig) -> None:
    """``pruefe_platzhalter`` kennt den Unterschied - der Beitrag nicht.

    Sonst wiese der Server einen gueltigen Kommentar genau dort zurueck, wo
    ein Mensch ihn gerade von Hand geschrieben hat.
    """
    from fbgroups.marketing.vorlagen import UngueltigerText, pruefe_platzhalter

    ohne = "تطبيق اسمه بطريقك، بيلاقيلك مسافر."
    assert pruefe_platzhalter(ohne, texttyp=Texttyp.KOMMENTAR) == ohne
    with pytest.raises(UngueltigerText):
        pruefe_platzhalter(ohne, texttyp=Texttyp.POST)

    # Eine von Hand getippte Adresse bleibt auch im Kommentar ein Fehler:
    # Der Kommentar saehe richtig aus, und seine Gruppe bekaeme nie einen
    # Klick gutgeschrieben.
    with pytest.raises(UngueltigerText):
        pruefe_platzhalter("schau mal https://go.example.invalid/r/X", texttyp=Texttyp.KOMMENTAR)
