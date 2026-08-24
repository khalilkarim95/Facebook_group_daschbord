"""Tests fuer die Beitragsvorschlaege - anbieterunabhaengiger Teil.

Ohne Netz, ohne Ollama, ohne Schluessel: Das Modell wird durch eine eigene
Fassung ersetzt. Das ist keine Bequemlichkeit - der Teil, auf den es ankommt,
ist die **Pruefung** der Antwort, und eine Pruefung, die einen laufenden Dienst
braucht, wird nicht ausgefuehrt.

Der Schwerpunkt liegt auf einer einzigen Frage: Kann ein erfundener oder
verstellter Tracking-Link durchkommen? Ein falscher Link in einem
veroeffentlichten Beitrag laesst sich nicht zurueckholen, und die Klicks
gingen an eine fremde Gruppe. Mit einem kleinen lokalen Modell ist diese Frage
dringlicher als vorher, nicht harmloser: Es verfehlt den Platzhalter oefter.
"""

from __future__ import annotations

import pytest

from fbgroups.marketing.ki import (
    PLATZHALTER,
    Auftrag,
    KINichtVerfuegbar,
    UngueltigerVorschlag,
    Variante,
    Vorschlaege,
    auftrag_aus_gruppe,
    erzeuge_entwuerfe,
    pruefe_platzhalter,
)
from fbgroups.marketing.models import Campaign, TextQuelle
from fbgroups.models import Group

GID = "482910573829104"


class FakeModell:
    """Liefert vorgegebene Fassungen und merkt sich, was es bekommen hat.

    ``reparatur`` ist die Antwort auf das Nachfassen (erkennbar an
    ``varianten=1``). Getrennt vom ersten Satz, weil sonst nicht pruefbar
    waere, ob ueberhaupt nachgefasst wurde - eine gleiche Antwort saehe aus
    wie gar keine Reparatur.
    """

    name = "fake:1b"

    def __init__(self, *texte: str, reparatur: tuple[str, ...] | None = None) -> None:
        self.texte = texte
        self.reparatur = reparatur
        self.system: str = ""
        self.auftraege: list[str] = []

    @property
    def aufrufe(self) -> int:
        return len(self.auftraege)

    def erzeuge(self, system: str, auftrag: str, *, varianten: int) -> Vorschlaege:
        self.system = system
        self.auftraege.append(auftrag)
        texte = self.reparatur if (varianten == 1 and self.reparatur is not None) else self.texte
        return Vorschlaege(varianten=[Variante(text=t) for t in texte])


class TotesModell:
    """Antwortet auf nichts - der Fall "Dienst laeuft nicht"."""

    name = "tot:0b"

    def erzeuge(self, system: str, auftrag: str, *, varianten: int) -> Vorschlaege:
        raise KINichtVerfuegbar("Ollama ist nicht erreichbar.")


# --- Die Pruefung des Platzhalters --------------------------------------

def test_ein_platzhalter_ist_richtig() -> None:
    text = f"Hallo zusammen, schaut mal hier vorbei: {PLATZHALTER}"
    assert pruefe_platzhalter(text) == text


def test_ohne_platzhalter_haette_der_beitrag_keinen_link() -> None:
    with pytest.raises(UngueltigerVorschlag, match="Kein"):
        pruefe_platzhalter("Ein schoener Text ganz ohne Link.")


def test_zwei_platzhalter_wuerden_den_link_doppeln() -> None:
    with pytest.raises(UngueltigerVorschlag, match="2x"):
        pruefe_platzhalter(f"Hier {PLATZHALTER} und nochmal hier {PLATZHALTER}")


def test_ausgeschriebene_adresse_wird_abgewiesen() -> None:
    """Ein Link am Ersetzungsmechanismus vorbei ist der ganze Schaden."""
    for erfunden in (
        f"Schaut hier: https://b-tarikak.de/r/FB-SYR-KLN-002 {PLATZHALTER}",
        f"Mehr unter www.b-tarikak.de {PLATZHALTER}",
        f"{PLATZHALTER} oder http://example.com",
    ):
        with pytest.raises(UngueltigerVorschlag, match="Adresse"):
            pruefe_platzhalter(erfunden)


def test_erfundener_tracking_code_wird_abgewiesen() -> None:
    """Das Modell kennt keinen Code - eines, das einen schreibt, hat ihn erfunden."""
    with pytest.raises(UngueltigerVorschlag, match="Codeaehnliche"):
        pruefe_platzhalter(f"Nutzt den Code FB-SYR-KLN-002 und den Link {PLATZHALTER}")


def test_gepruefter_text_wird_nicht_repariert() -> None:
    """Eine still geflickte Fassung saehe aus wie eine gepruefte."""
    text = f"  Ein Text mit {PLATZHALTER} und Leerzeichen  "
    assert pruefe_platzhalter(text) == text


def test_arabischer_text_geht_durch() -> None:
    """Die Projektsprache der Beitraege ist Arabisch - das darf nicht stolpern."""
    text = f"مرحباً بالجميع في كولونيا! جربوا التطبيق من هنا: {PLATZHALTER}"
    assert pruefe_platzhalter(text) == text


# --- Entwuerfe aus der Antwort ------------------------------------------

def test_gute_fassungen_werden_zu_entwuerfen() -> None:
    modell = FakeModell(
        f"Erste Fassung {PLATZHALTER}",
        f"Zweite Fassung {PLATZHALTER}",
        f"Dritte Fassung {PLATZHALTER}",
    )
    entwuerfe, verworfen = erzeuge_entwuerfe(
        modell, Auftrag(gruppenname="Syrer in Koeln"),
        campaign_id="batreeq", group_id=GID,
    )

    assert len(entwuerfe) == 3
    assert not verworfen
    assert all(e.quelle is TextQuelle.KI for e in entwuerfe)
    assert all(e.modell == "fake:1b" for e in entwuerfe)   # der echte Modellname
    assert all(e.group_id == GID for e in entwuerfe)


def test_gute_fassungen_loesen_kein_nachfassen_aus() -> None:
    modell = FakeModell(f"A {PLATZHALTER}", f"B {PLATZHALTER}")
    erzeuge_entwuerfe(
        modell, Auftrag(gruppenname="Syrer in Koeln"),
        campaign_id="batreeq", group_id=GID,
    )

    assert modell.aufrufe == 1


# --- Der eine Reparaturversuch ------------------------------------------

def test_eine_schlechte_fassung_wird_einmal_nachgefragt() -> None:
    """Ein kleines Modell verfehlt den Platzhalter oefter - einmal nachfassen."""
    modell = FakeModell(
        f"Gut {PLATZHALTER}",
        "Ohne Platzhalter",
        reparatur=(f"Jetzt richtig {PLATZHALTER}",),
    )
    entwuerfe, verworfen = erzeuge_entwuerfe(
        modell, Auftrag(gruppenname="Syrer in Koeln"),
        campaign_id="batreeq", group_id=GID,
    )

    assert len(entwuerfe) == 2
    assert not verworfen
    assert entwuerfe[1].text == f"Jetzt richtig {PLATZHALTER}"
    assert modell.aufrufe == 2        # der erste Satz und genau ein Nachfassen


def test_das_nachfassen_nennt_den_grund() -> None:
    """Ohne den Grund waere es dieselbe Frage - und dieselbe falsche Antwort."""
    modell = FakeModell("Ohne Platzhalter", reparatur=(f"Richtig {PLATZHALTER}",))
    erzeuge_entwuerfe(
        modell, Auftrag(gruppenname="Syrer in Koeln"),
        campaign_id="batreeq", group_id=GID,
    )

    nachfrage = modell.auftraege[1]
    assert "unbrauchbar" in nachfrage
    assert PLATZHALTER in nachfrage


def test_hoechstens_ein_reparaturversuch() -> None:
    """Sonst wuerde daraus eine Schleife, deren Dauer niemand ueberblickt."""
    modell = FakeModell("Immer falsch", reparatur=("Wieder falsch",))
    entwuerfe, verworfen = erzeuge_entwuerfe(
        modell, Auftrag(gruppenname="Syrer in Koeln"),
        campaign_id="batreeq", group_id=GID,
    )

    assert entwuerfe == []
    assert len(verworfen) == 1
    assert modell.aufrufe == 2        # nicht drei, nicht zehn


def test_auch_die_reparatur_wird_geprueft() -> None:
    """Sonst waere das Nachfassen eine Hintertuer an der Pruefung vorbei."""
    modell = FakeModell(
        "Ohne Platzhalter",
        reparatur=(f"https://erfunden.de {PLATZHALTER}",),   # neuer Fehler
    )
    entwuerfe, verworfen = erzeuge_entwuerfe(
        modell, Auftrag(gruppenname="Syrer in Koeln"),
        campaign_id="batreeq", group_id=GID,
    )

    assert entwuerfe == []
    assert len(verworfen) == 1


def test_ein_ausfall_beim_nachfassen_verwirft_nur() -> None:
    """Faellt der Dienst zwischendurch aus, ist das kein Absturz."""

    class HalbTot(FakeModell):
        def erzeuge(self, system: str, auftrag: str, *, varianten: int) -> Vorschlaege:
            if varianten == 1:
                raise KINichtVerfuegbar("weg")
            return super().erzeuge(system, auftrag, varianten=varianten)

    entwuerfe, verworfen = erzeuge_entwuerfe(
        HalbTot("Ohne Platzhalter"), Auftrag(gruppenname="Syrer in Koeln"),
        campaign_id="batreeq", group_id=GID,
    )

    assert entwuerfe == []
    assert len(verworfen) == 1


def test_ein_toter_dienst_meldet_sich_als_solcher() -> None:
    with pytest.raises(KINichtVerfuegbar):
        erzeuge_entwuerfe(
            TotesModell(), Auftrag(gruppenname="Syrer in Koeln"),
            campaign_id="batreeq", group_id=GID,
        )


# --- Was das Modell ueberhaupt zu sehen bekommt -------------------------

def test_kein_tracking_code_im_prompt() -> None:
    """Die Kernentscheidung: Was das Modell nie sieht, kann es nicht verfaelschen."""
    modell = FakeModell(f"Text {PLATZHALTER}")
    auftrag = Auftrag(
        gruppenname="Syrer in Koeln",
        stadt="Koeln",
        kampagne="Batreeq Syrian Germany",
        landing_page="https://b-tarikak.de/",
    )
    erzeuge_entwuerfe(modell, auftrag, campaign_id="batreeq", group_id=GID)

    gesamt = modell.system + " ".join(modell.auftraege)
    assert "FB-SYR" not in gesamt
    assert "/r/" not in gesamt
    assert "FB-" not in gesamt


def test_leere_angaben_stehen_nicht_im_prompt() -> None:
    """Was leer ist, faellt heraus - statt als Vermutung hineinzugehen.

    Bei einem kleinen Modell ist das wichtiger als bei einem grossen: Es
    fuellt Luecken bereitwillig mit Erfundenem ueber die Gruppe.
    """
    modell = FakeModell(f"Text {PLATZHALTER}")
    erzeuge_entwuerfe(
        modell, Auftrag(gruppenname="Syrer in Koeln"),
        campaign_id="batreeq", group_id=GID,
    )

    assert "Stadt:" not in modell.auftraege[0]
    assert "Kampagne:" not in modell.auftraege[0]
    assert "Mitglieder:" not in modell.auftraege[0]
    assert "Gruppenname: Syrer in Koeln" in modell.auftraege[0]


def test_mitgliederzahl_geht_mit_wenn_sie_bekannt_ist() -> None:
    modell = FakeModell(f"Text {PLATZHALTER}")
    erzeuge_entwuerfe(
        modell, Auftrag(gruppenname="Syrer in Stuttgart", mitglieder=18500),
        campaign_id="batreeq", group_id=GID,
    )

    assert "Mitglieder: 18.500" in modell.auftraege[0]


def test_bisherige_texte_werden_zur_abgrenzung_mitgegeben() -> None:
    """Sonst schreibt die zweite Runde dasselbe wie die erste."""
    modell = FakeModell(f"Neu {PLATZHALTER}")
    erzeuge_entwuerfe(
        modell,
        Auftrag(gruppenname="Syrer in Koeln", bisherige_texte=["Alte Fassung"]),
        campaign_id="batreeq", group_id=GID,
    )

    assert "Alte Fassung" in modell.auftraege[0]


def test_auftrag_uebernimmt_nur_vorhandene_felder(config) -> None:
    group = Group(
        group_id=GID,
        url_canonical=f"https://www.facebook.com/groups/{GID}",
        name="Syrer in Koeln",
        city="Koeln",
        audience_tags=["syrians"],
    )
    campaign = Campaign(
        campaign_id="batreeq",
        name="Batreeq Syrian Germany",
        landing_page="https://b-tarikak.de/",
    )

    auftrag = auftrag_aus_gruppe(group, campaign, config)

    assert auftrag.gruppenname == "Syrer in Koeln"
    assert auftrag.stadt == "Koeln"
    assert auftrag.kampagne == "Batreeq Syrian Germany"
    assert auftrag.kategorie == ""          # nicht erfunden
    assert auftrag.beschreibung == ""
    assert auftrag.mitglieder is None


def test_der_systemtext_verbietet_ausgeschriebene_links() -> None:
    """Die Regel steht im Prompt UND wird geprueft - Guertel und Hosentraeger."""
    from fbgroups.marketing.ki import SYSTEM_PROMPT

    assert PLATZHALTER in SYSTEM_PROMPT
    assert "Adresse" in SYSTEM_PROMPT
    # Und im Systemtext selbst steht kein Beispielcode, den man abschreiben
    # koennte - genau das hat dieser Test beim ersten Lauf gefunden.
    assert "FB-" not in SYSTEM_PROMPT


# --- Der Weg vom Entwurf zum fertigen Beitrag ---------------------------

def test_der_link_der_gruppe_ersetzt_den_platzhalter() -> None:
    """Die ganze Kette: Das Modell schreibt {link}, die Zuordnung setzt IHREN Code ein."""
    from fbgroups.marketing.beitrag import beitragstext
    from fbgroups.marketing.models import CampaignGroup

    campaign = Campaign(campaign_id="batreeq", name="Batreeq",
                        message_template="Vorlage {link}")
    link = CampaignGroup(
        campaign_id="batreeq",
        group_id=GID,
        tracking_code="FB-SYR-KLN-002",
        tracking_url="https://go.b-tarikak.de/r/FB-SYR-KLN-002",
        post_text=f"Hallo Koeln! Schaut hier: {PLATZHALTER}",
    )

    fertig = beitragstext(campaign, link)

    assert fertig == "Hallo Koeln! Schaut hier: https://go.b-tarikak.de/r/FB-SYR-KLN-002"
    assert PLATZHALTER not in fertig


def test_zwei_gruppen_bekommen_zwei_verschiedene_links() -> None:
    """Derselbe KI-Text, zwei Gruppen - und keine bekommt den Code der anderen."""
    from fbgroups.marketing.beitrag import beitragstext
    from fbgroups.marketing.models import CampaignGroup

    campaign = Campaign(campaign_id="batreeq", name="Batreeq")
    text = f"Derselbe Text {PLATZHALTER}"
    koeln = CampaignGroup(
        campaign_id="batreeq", group_id=GID, tracking_code="FB-SYR-KLN-002",
        tracking_url="https://go.b-tarikak.de/r/FB-SYR-KLN-002", post_text=text,
    )
    berlin = CampaignGroup(
        campaign_id="batreeq", group_id="739201847362915",
        tracking_code="FB-SYR-BER-001",
        tracking_url="https://go.b-tarikak.de/r/FB-SYR-BER-001", post_text=text,
    )

    assert "FB-SYR-KLN-002" in beitragstext(campaign, koeln)
    assert "FB-SYR-BER-001" not in beitragstext(campaign, koeln)
    assert "FB-SYR-BER-001" in beitragstext(campaign, berlin)


def test_ohne_eigenen_text_gilt_weiter_die_vorlage() -> None:
    """Bestehende Kampagnen ohne KI-Text arbeiten unveraendert weiter."""
    from fbgroups.marketing.beitrag import beitragstext
    from fbgroups.marketing.models import CampaignGroup

    campaign = Campaign(campaign_id="batreeq", name="Batreeq",
                        message_template="Alte Vorlage {link}")
    link = CampaignGroup(
        campaign_id="batreeq", group_id=GID, tracking_code="FB-SYR-KLN-002",
        tracking_url="https://go.b-tarikak.de/r/FB-SYR-KLN-002",
    )

    assert beitragstext(campaign, link).startswith("Alte Vorlage ")
