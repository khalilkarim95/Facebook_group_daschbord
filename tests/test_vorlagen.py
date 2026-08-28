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


def test_stadt_und_zielgruppe_stehen_arabisch_im_text(config: AppConfig) -> None:
    _, text = erzeuge(gruppe(), kampagne("ar"), config)
    assert "بون" in text
    assert "السوريين" in text
    assert "Bonn" not in text


def test_deutsche_kampagne_bekommt_das_kurze_label(config: AppConfig) -> None:
    _, text = erzeuge(gruppe(), kampagne("de"), config)
    assert "Bonn" in text
    # Nicht "Syrer in Deutschland in Bonn": dafuer gibt es label_kurz_de.
    assert "Syrer in Deutschland" not in text
    assert "Syrer" in text


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


# -- Die beiden Toepfe ------------------------------------------------------


def test_gruppe_ohne_stadt_bekommt_die_vorlage_ohne_stadt(config: AppConfig) -> None:
    schluessel, text = erzeuge(gruppe(city=None), kampagne("ar"), config)
    assert f"/{OHNE_STADT}/" in schluessel
    assert "{stadt}" not in text


def test_gruppe_mit_stadt_bekommt_die_vorlage_mit_stadt(config: AppConfig) -> None:
    schluessel, _ = erzeuge(gruppe(city="Bonn"), kampagne("ar"), config)
    assert f"/{MIT_STADT}/" in schluessel


def test_unbekannte_stadt_gilt_als_ohne_stadt(config: AppConfig) -> None:
    """Lieber die allgemeine Vorlage als eine erfundene Stadt im Beitrag."""
    schluessel, text = erzeuge(gruppe(city="Atlantis"), kampagne("ar"), config)
    assert f"/{OHNE_STADT}/" in schluessel
    assert "Atlantis" not in text


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


def test_die_zielgruppe_der_kampagne_gewinnt_bei_mehreren_tags(config: AppConfig) -> None:
    """Eine Gruppe unter 'syrians' und 'arabs' in einer Syrer-Kampagne.

    Beide Tags sind richtig; die Kampagne entscheidet, welcher angesprochen
    wird - sonst haengt die Anrede an der Reihenfolge der Klassifikation.
    """
    _, text = erzeuge(
        gruppe(tags=["arabs", "syrians"]), kampagne("ar", audiences=["syrians"]), config
    )
    assert "السوريين" in text
    assert "العرب" not in text


def test_gruppe_ohne_tag_erbt_die_zielgruppe_der_kampagne(config: AppConfig) -> None:
    _, text = erzeuge(gruppe(tags=[]), kampagne("ar", audiences=["syrians"]), config)
    assert "السوريين" in text


def test_ohne_jede_zielgruppe_die_allgemeine_anrede(config: AppConfig) -> None:
    _, text = erzeuge(gruppe(tags=[]), kampagne("ar", audiences=[]), config)
    assert "الأصدقاء" in text
    assert "{zielgruppe}" not in text


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
    fest = f"ar/post/{MIT_STADT}/einladung"
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
    alt = vorlage_zu(config, f"ar/{MIT_STADT}/3")
    neu = vorlage_zu(config, f"ar/post/{MIT_STADT}/alltag")
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
