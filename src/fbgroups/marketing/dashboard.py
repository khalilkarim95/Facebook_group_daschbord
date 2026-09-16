"""Lesende Oberflaeche auf den Bestand - die Seite unter ``/``.

Drei Entscheidungen, die man dem Code sonst nicht ansieht:

- **Aenderbar ist allein der Arbeitsstand** (``POST /stand`` in ``web.py``),
  und zwar ueber denselben Store wie ``fbgroups marketing set`` - dieselbe
  Tabelle, dieselben Werte, dasselbe Pruefprotokoll. Genau dieses eine Feld
  kann das Programm naemlich nicht selbst ermitteln: Facebook meldet nicht,
  dass jemand eine Beitrittsanfrage gestellt hat, und facebook.com wird nicht
  aufgerufen. Es kann nur von dem Menschen kommen, der die Anfrage geschickt
  hat - hier mit einem Klick statt mit einem Befehl.

  Alles Uebrige bleibt lesend: Bewertung, Klassifikation und Trefferdaten
  entstehen aus der Suche und sind reproduzierbar. Sie hier ueberschreiben zu
  koennen hiesse, zwei Wahrheiten ueber denselben Bestand zu fuehren.
- **Nur ueber localhost erreichbar** (siehe ``web.dashboard``). Der Dienst ist
  dafuer gedacht, oeffentlich zu stehen: Die Tracking-Links in den Beitraegen
  zeigen auf ihn. Die Arbeitsliste - welche Gruppen angesprochen werden sollen,
  wer schon kontaktiert wurde - gehoert aber nicht ins offene Netz. Es gibt
  keine Anmeldung, also gibt es auch nichts, was sie schuetzen wuerde.
- **Keine Datei von aussen.** Kein CDN, keine Schriftart, kein Skript von
  Dritten. Die Seite muss ohne Internet funktionieren, und ein Abruf bei einem
  fremden Dienst verriete, woran hier gearbeitet wird.

Gefiltert wird im Browser ueber die eingebetteten Daten, nicht ueber weitere
Wege am Dienst. Der Bestand ist dreistellig - das laedt in einem Zug, und es
entsteht keine zweite Schnittstelle, die jemand ungewollt oeffentlich stellt.
"""

from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from fbgroups.config import AppConfig
from fbgroups.marketing import qualifikation, zielgruppe
from fbgroups.marketing.analytics import funnel, kennzahlen
from fbgroups.marketing.models import CampaignStatus, MarketingStatus
from fbgroups.marketing.qualifikation import GRUND_BESCHRIFTUNG, Ablehnungsgrund
from fbgroups.marketing.resonanz import resonanz_je_gruppe
from fbgroups.marketing.selection import Auswahl, auswahl_der_kampagne, passt
from fbgroups.marketing.store import MarketingStore
from fbgroups.models import Group
from fbgroups.scoring import Resonanz
from fbgroups.storage.sqlite_store import SqliteStore

# Klartext fuer die Statusnamen. Die englischen Kennungen stehen in der
# Datenbank; auf der Seite haben sie nichts verloren.
# Welche Staende als Mitgliedschaft gelten - dieselbe Menge wie in
# ``lauf.lies_fortschritt``. Wer die Zusammenarbeit angebahnt oder
# abgeschlossen hat, ist erst recht drin; ``beitritt_angefragt`` zaehlt
# ausdruecklich nicht: Eine offene Anfrage ist keine Mitgliedschaft.
_MITGLIEDSCHAFT = frozenset(
    {
        MarketingStatus.MEMBER,
        MarketingStatus.CONTACTED,
        MarketingStatus.INTERESTED,
        MarketingStatus.APPROVED,
        MarketingStatus.ACTIVE,
    }
)


def _regeln_von(stand) -> qualifikation.Regelbefund:  # noqa: ANN001 - GroupMarketing | None
    """Der Regelbefund aus dem Arbeitsstand - ``gelesen`` traegt die Aussage.

    Ohne Eintrag ``gelesen=False``, und das ist nicht dasselbe wie "nichts
    verboten": Die Abwesenheit einer Regel waere sonst eine Erlaubnis, die
    niemand erteilt hat.
    """
    if stand is None:
        return qualifikation.Regelbefund()
    return qualifikation.Regelbefund(
        gelesen=stand.regeln_gelesen_am is not None,
        keine_links=stand.regel_keine_links,
        keine_werbung=stand.regel_keine_werbung,
        freigabe_noetig=stand.regel_freigabe_noetig,
        neue_ohne_links=stand.regel_neue_ohne_links,
    )


_STATUS_LABEL = {
    "not_contacted": "nichts getan",
    "beitritt_angefragt": "Beitritt angefragt",
    "mitglied": "Mitglied",
    "beitritt_abgelehnt": "Beitritt abgelehnt",
    "contacted": "Leitung angesprochen",
    "interested": "interessiert",
    "approved": "zugesagt",
    "rejected": "abgelehnt",
    "active": "aktiv",
    "inactive": "inaktiv",
}

def status_label(wert: str) -> str:
    """Klartext zu einer Statuskennung - eine Quelle fuer Seite und Schnittstelle."""
    return _STATUS_LABEL.get(wert, wert)


def regel_kurzfassung(auswahl: Auswahl) -> str:
    """Die Auswahlregel in einer Zeile - gezaehlt, nicht aufgezaehlt.

    ``Auswahl.beschreibung`` nennt jede Kennung einzeln. Das ist auf der
    Kommandozeile richtig und in einer Tabellenzelle unbrauchbar: Bei allen 14
    Zielgruppen und 25 Staedten schob die Aufzaehlung die Spalte auf eine
    Bildschirmhoehe auseinander und drueckte den Rest der Zeile zusammen.

    Die vollstaendige Fassung bleibt erreichbar - sie steht im ``title`` der
    Zeile (Mauszeiger) und im Pruefprotokoll. Wer sie bearbeiten will, sieht
    ohnehin die angehakten Eintraege im Formular.
    """
    if auswahl.ohne_einschraenkung:
        return "alle Gruppen im Bestand"

    teile: list[str] = []
    for menge, eins, viele in (
        (auswahl.audiences, "1 Zielgruppe", "Zielgruppen"),
        (auswahl.cities, "1 Stadt", "Städte"),
        (auswahl.categories, "1 Kategorie", "Kategorien"),
        (auswahl.statuses, "1 Status", "Status"),
    ):
        if menge:
            teile.append(eins if len(menge) == 1 else f"{len(menge)} {viele}")
    if auswahl.min_score is not None:
        teile.append(f"Score ab {auswahl.min_score:g}")
    teile.append("auch ohne Score" if auswahl.include_unscored else "nur bewertete")
    return " · ".join(teile)

# Welche Ereignisstufen je Gruppe und Kampagne in der Tabelle stehen. Die
# Reihenfolge ist die des Trichters; die Namen sind die Werte aus EventType,
# damit Tabelle und counts_by nicht auseinanderlaufen koennen.
EREIGNISFELDER: tuple[str, ...] = (
    "click", "registration", "download", "activation", "qualified", "conversion",
)

_EREIGNIS_LABEL = {
    "click": "Klicks",
    "landing_visit": "Landungen",
    "registration": "Registrierungen",
    # Ohne Eintrag faellt ereignis_label auf den Rohwert zurueck - im Trichter
    # standen "store_visit" und "download" englisch zwischen deutschen Stufen.
    "store_visit": "Store-Besuche",
    "download": "Downloads",
    "activation": "Aktivierungen",
    "qualified": "qualifiziert",
    "conversion": "Abschluesse",
}

def ereignis_label(wert: str) -> str:
    return _EREIGNIS_LABEL.get(wert, wert)

# Reihenfolge der Dringlichkeit: Was oben steht, gewinnt. Ein offener Beitrag
# neben einem veroeffentlichten heisst "noch offen" - die Gruppe hat noch
# Arbeit, auch wenn ein Teil erledigt ist.
_BEITRAG_RANG: tuple[str, ...] = (
    "offen",
    "fehlgeschlagen",
    "uebersprungen",
    "veroeffentlicht",
)


def _beitrag_gesamtstand(beitraege: list[dict[str, Any]]) -> str:
    """Der dringlichste Stand aller Zuordnungen dieser Gruppe.

    Ohne Zuordnung gibt es keinen Beitrag zu schreiben - dann steht hier
    ``ohne``, und die Gruppe faellt aus jedem Beitragsfilter heraus, statt als
    faelschlich "offen" die Liste zu fuellen.
    """
    if not beitraege:
        return "ohne"
    staende = {b["status"] for b in beitraege}
    return next((stand for stand in _BEITRAG_RANG if stand in staende), "ohne")


#: Die Einstufungen, die es gibt - einmal gerechnet statt in jeder Zeile.
_GRUENDE = {g.value for g in Ablehnungsgrund}


def _antworttext(gruende: dict[str, int]) -> str:
    """"Link abgelehnt 2, angenommen 8" - haeufigstes zuerst.

    Eine Zeichenkette und kein Wortverzeichnis, weil sie im Titel der Zelle
    steht: Ein Mensch liest dort einen Satz, keine Tabelle. Die Zahlen selbst
    stehen daneben in ``facebook_gruende``, falls jemand sie auswertet.

    Eine Einstufung, die es nicht (mehr) gibt, wird uebergangen - eine
    aeltere Fassung des Programms koennte einen Wert geschrieben haben, den
    diese nicht kennt, und daran soll die Uebersicht nicht scheitern.
    """
    return ", ".join(
        f"{GRUND_BESCHRIFTUNG[Ablehnungsgrund(schluessel)]} {anzahl}"
        for schluessel, anzahl in sorted(gruende.items(), key=lambda kv: -kv[1])
        if schluessel in _GRUENDE
    )


def _gruppe_als_zeile(
    group: Group,
    config: AppConfig,
    marketing_status: str,
    codes: list[str],
    anfragen: int,
    ereignisse: dict[str, int] | None = None,
    bearbeiten: bool = True,
    ausschlussgrund: str = "",
    passt_zu: list[dict[str, str]] | None = None,
    beitraege: list[dict[str, Any]] | None = None,
    resonanz: Resonanz | None = None,
    qualifikation: str = "unbekannt",
    qualifikation_label: str = "",
    qualifikation_grund: str = "",
    regeln: str = "",
    zielprioritaet: str = "",
    zielprioritaet_label: str = "",
    zielprioritaet_grund: str = "",
    zielregion: str = "",
    zielregion_label: str = "",
    gruende: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Eine Tabellenzeile - bereits mit den Bezeichnungen der Konfiguration.

    Die Uebersetzung passiert hier und nicht im Browser: Die Bezeichnungen
    stehen in ``config/*.yaml`` und sind die fachliche Wahrheit des Projekts.
    Eine zweite Liste im JavaScript wuerde beim naechsten neuen Stadtnamen
    auseinanderlaufen.
    """
    kategorie = next(
        (c.label_de for c in config.categories if c.id == group.category),
        group.category or "",
    )
    zielgruppen = [
        config.audiences[tag].label_de if tag in config.audiences else tag
        for tag in group.audience_tags
    ]

    return {
        "id": group.group_id,
        "name": group.name or "(ohne Namen)",
        "url": group.url_canonical,
        "score": group.score,
        "score_max": group.score_max,
        "grund": group.score_reason,
        # Wie belastbar die Grundlage ist - NEBEN dem Score, nie darin.
        # Ein maessiger Score aus belegten Zahlen und ein guter aus duennen
        # Hinweisen sind zwei Aussagen; verrechnet waeren beide unlesbar.
        "konfidenz": group.data_confidence,
        # Die Rohzahlen hinter den Punkten. Sie stehen hier, damit die Zeile
        # ihre Bewertung belegen kann statt sie zu behaupten - "22/25" ist
        # eine Note, "42.300 Mitglieder (facebook)" ist der Grund dafuer.
        "mitglieder": group.member_count,
        "mitglieder_quelle": (
            group.member_count_source.value if group.member_count_source else None
        ),
        "posts_pro_tag": group.posts_per_day,
        "aktivitaet_quelle": (
            group.activity_source.value if group.activity_source else None
        ),
        "letzter_beitrag": (
            group.last_post_at.isoformat() if group.last_post_at else None
        ),
        "stadt": group.city or "",
        "zielgruppen": zielgruppen,
        "kategorie": kategorie,
        "status": group.status.value,
        "marketing": marketing_status,
        "marketing_label": _STATUS_LABEL.get(marketing_status, marketing_status),
        # Die dritte Achse neben "wo stehen wir?" und "arbeiten wir daran?":
        # **darf** hier ueberhaupt etwas stehen. Sie wird bei jedem Aufruf aus
        # Mitgliedschaft, gelesenen Gruppenregeln und dem Versuchsprotokoll
        # gerechnet (``qualifikation.beurteile``) und ist deshalb nie
        # veraltet. Der Grund faehrt mit: Eine Einstufung, deren Begruendung
        # man nachschlagen muss, wird geglaubt statt nachgeschlagen.
        "qualifikation": qualifikation,
        "qualifikation_label": qualifikation_label or qualifikation,
        "qualifikation_grund": qualifikation_grund,
        # Was auf der Gruppenseite an Regeln stand - leer heisst "nichts
        # verbietendes gefunden", nicht "nicht nachgesehen"; das sagt die
        # Stufe selbst (``in Bewertung``).
        "regeln": regeln,
        # Die vierte Achse (13.09.2026): **gehoert** diese Gruppe zum
        # Zielmarkt? Neben "wo stehen wir?" (marketing), "arbeiten wir
        # daran?" (bearbeiten) und "darf hier etwas stehen?"
        # (qualifikation) - vier Fragen, vier Spalten. Sie wird bei jedem
        # Aufruf gerechnet (``zielgruppe.aus_group``) und ist deshalb nie
        # veraltet; der Grund faehrt mit, wie ueberall.
        "zielprioritaet": zielprioritaet,
        "zielprioritaet_label": zielprioritaet_label or zielprioritaet,
        "zielprioritaet_grund": zielprioritaet_grund,
        # Das Land steht neben der Klasse und nicht darin: "worum geht es
        # dort?" und "wo ist das?" sind zwei Fragen. In der Zelle stehen sie
        # trotzdem zusammen ("A·DE") - es ist eine Rangfolge, und getrennt
        # muesste man sie im Kopf wieder zusammensetzen.
        "zielregion": zielregion,
        "zielregion_label": zielregion_label or zielregion,
        # Was Facebook auf unsere Versuche geantwortet hat - gezaehlt je
        # Einstufung ("link_rejected": 2). Der Fehlertext selbst steht
        # weiterhin im Protokoll; hier steht, was sich ueber 314 Gruppen
        # ueberblicken laesst.
        "facebook_gruende": gruende or {},
        "facebook_antwort": _antworttext(gruende or {}),
        # Eigene Achse neben dem Kooperationsweg: "wo stehen wir?" und
        # "arbeiten wir ueberhaupt daran?" sind zwei Fragen. Siehe
        # GroupMarketing.bearbeiten.
        "bearbeiten": bearbeiten,
        "ausschlussgrund": ausschlussgrund,
        "codes": codes,
        # Je Zuordnung ein Eintrag: Kampagne, Code, Stand des Beitrags. Der
        # fertige Text stand hier einmal mit - fuer einen Kopierknopf in der
        # Zelle. Er ist weg; wer den Text braucht, arbeitet unter
        # /arbeit/{kampagne}, und dort steht er samt Merkmalen der Gruppe.
        "beitraege": beitraege or [],
        # Die Kampagnennamen als eine Zeichenkette - allein zum Sortieren und
        # Suchen. Die Zellen selbst zeichnen aus ``beitraege``; ein zweites
        # Feld mit denselben Angaben koennte davon abweichen.
        "kampagnen_text": ", ".join(
            sorted({b["kampagne_name"] for b in (beitraege or [])})
        ),
        # Kampagnen, deren Auswahlregel diese Gruppe erfasst, ohne dass sie
        # zugeordnet waere. Das ist die Frage "wohin gehoert sie?" - die
        # Zuordnung beantwortet nur "wo steht sie schon?". Ohne diese Angabe
        # muesste man die Regel jeder Kampagne im Kopf nachrechnen.
        "passt_zu": passt_zu or [],
        # Der schlechteste Stand aller Zuordnungen - danach wird gefiltert.
        # "Diese Gruppe ist erledigt" darf erst gelten, wenn kein Beitrag mehr
        # aussteht; sonst verschwindet eine offene Aufgabe aus dem Filter.
        "beitrag_status": _beitrag_gesamtstand(beitraege or []),
        # Die gemessenen Grundlagen des Scores - dieselben Zahlen, aus denen
        # scoring._resonanz_faktoren rechnet. Sie stehen hier, damit die Zeile
        # die Bewertung belegen kann statt sie nur zu behaupten.
        "resonanz": (
            None
            if resonanz is None
            else {
                "beitraege": resonanz.beitraege,
                "klicks": resonanz.klicks,
                "registrierungen": resonanz.registrierungen,
                "quote": (
                    round(resonanz.registrierungen / resonanz.klicks * 100, 1)
                    if resonanz.klicks
                    else None
                ),
                "letzte_regung": (
                    resonanz.letzte_regung.isoformat() if resonanz.letzte_regung else None
                ),
                "erster_beitrag_am": (
                    resonanz.erster_beitrag_am.isoformat()
                    if resonanz.erster_beitrag_am
                    else None
                ),
            }
        ),
        # Die Einzelteile des Scores. Bisher stand nur der Satz in
        # score_reason; fuer eine Tabelle braucht es die Zahlen selbst.
        "punkte": group.score_breakdown.model_dump(),
        # Verschiedene Anfragen, die diese Gruppe gefunden haben - nicht
        # times_seen. Siehe SqliteStore.count_distinct_sources: times_seen
        # waechst mit jedem Lauf weiter und misst damit das Alter des
        # Datensatzes statt der Auffindbarkeit der Gruppe.
        "anfragen": anfragen,
        # "mitglieder" steht weiter oben bei den Belegen des Scores - dort, wo
        # auch die Herkunft der Zahl steht. Zweimal dasselbe Feld waere zwei
        # Wahrheiten ueber dieselbe Zahl.
        "beschreibung": group.description_snippet or "",
        # Die Trichterzahlen dieser Gruppe. Sie stehen in derselben Zeile wie
        # Score und Stand, damit sich die Frage "welche Gruppe bringt
        # tatsaechlich Leute?" mit denselben Filtern beantworten laesst wie
        # alles andere - eine zweite Bestenliste daneben waere ein zweiter
        # Satz Zahlen, den man getrennt filtern muesste.
        **{feld: (ereignisse or {}).get(feld, 0) for feld in EREIGNISFELDER},
    }

def sammle_daten(config: AppConfig, db_path: Path) -> dict[str, Any]:
    """Traegt alles zusammen, was die Seite zeigt - in einem Rutsch.

    Beide Speicher lesen dieselbe Datei; getrennte Verbindungen, weil die
    Marketing-Tabellen ein eigenes Modul haben.
    """
    with SqliteStore(db_path) as store:
        groups = store.load_groups()
        anfragen_je_gruppe = store.count_distinct_sources()

    with MarketingStore(db_path) as mstore:
        marketing = mstore.load_all_marketing()
        campaigns = mstore.load_campaigns()
        zahlen = kennzahlen(mstore)
        klicks_je_kampagne = mstore.counts_by("campaign_id")
        klicks_je_gruppe = mstore.counts_by("group_id")
        trichter = [
            {
                "stufe": event_type.value,
                "label": ereignis_label(event_type.value),
                "anzahl": anzahl,
                "anteil": anteil,
            }
            for event_type, anzahl, anteil in funnel(mstore)
        ]
        # Die Grundlage der Qualifikation - in **einem** Zugriff fuer den
        # ganzen Bestand. Je Zeile einzeln waeren das bei 314 Gruppen 314
        # Abfragen fuer dieselbe Tabelle.
        beobachtet = mstore.beobachtungen()
        links = {c.campaign_id: mstore.links_for_campaign(c.campaign_id) for c in campaigns}
        beitrag_zaehler = {c.campaign_id: mstore.post_counts(c.campaign_id) for c in campaigns}
        # Dieselbe Funktion, die auch 'fbgroups rescore' benutzt - Anzeige und
        # Bewertung koennen damit nicht auseinanderlaufen.
        resonanz_je_id = resonanz_je_gruppe(mstore)

    codes_je_gruppe: dict[str, list[str]] = {}
    beitraege_je_gruppe: dict[str, list[dict[str, Any]]] = {}
    kampagne_nach_id = {c.campaign_id: c for c in campaigns}
    for campaign_id, campaign_links in links.items():
        campaign = kampagne_nach_id.get(campaign_id)
        for link in campaign_links:
            codes_je_gruppe.setdefault(link.group_id, []).append(link.tracking_code)
            beitraege_je_gruppe.setdefault(link.group_id, []).append(
                {
                    # Schmal mit Absicht: Was hier steht, steht 310mal im
                    # Dokument. Der fertige Beitragstext stand einmal darin,
                    # damit ein Knopf in dieser Zelle ihn ohne zweiten Aufruf
                    # kopieren konnte - den Knopf gibt es nicht mehr,
                    # gearbeitet wird unter /arbeit/{{kampagne}}.
                    "kampagne": campaign_id,
                    "kampagne_name": campaign.name if campaign else campaign_id,
                    "code": link.tracking_code,
                    "status": link.post_status.value,
                    "fehler": link.post_error,
                }
            )

    # counts_by liefert {(schluessel, event_type): anzahl} - hier einmal nach
    # Gruppe umgedreht, statt fuer jede der 310 Zeilen erneut zu suchen.
    ereignisse_je_gruppe: dict[str, dict[str, int]] = {}
    for (group_id, event_type), anzahl in klicks_je_gruppe.items():
        ereignisse_je_gruppe.setdefault(group_id, {})[event_type] = anzahl

    # Welche Kampagnenregel welche Gruppe erfasst. Einmal je Kampagne ueber den
    # Bestand statt je Zeile ueber die Kampagnen: Bei 310 Gruppen und zehn
    # Kampagnen ist das derselbe Aufwand, aber ``auswahl_der_kampagne`` wird
    # zehnmal aufgerufen statt dreitausendmal.
    passt_je_gruppe: dict[str, list[dict[str, str]]] = {}
    for c in campaigns:
        if c.status is not CampaignStatus.ACTIVE:
            # Eine pausierte oder beendete Kampagne sucht keine Gruppen mehr.
            # Sie hier anzubieten hiesse, zu einer Zuordnung zu raten, die
            # `campaign sync` selbst nicht mehr vornaehme.
            continue
        regel = auswahl_der_kampagne(c, config)
        schon_drin = {link.group_id for link in links.get(c.campaign_id, [])}
        for g in groups:
            if g.group_id in schon_drin or not passt(g, regel):
                continue
            passt_je_gruppe.setdefault(g.group_id, []).append(
                {"id": c.campaign_id, "name": c.name}
            )

    # Die Qualifikation je Gruppe - gerechnet, nicht gelesen. Es gibt keine
    # gespeicherte Einstufung, die veralten koennte; sie entsteht hier aus
    # Mitgliedschaft, gelesenen Gruppenregeln und dem Versuchsprotokoll.
    urteile = {
        g.group_id: qualifikation.beurteile(
            mitglied=bool(
                g.group_id in marketing
                and marketing[g.group_id].marketing_status in _MITGLIEDSCHAFT
            ),
            beitritt_angefragt=bool(
                g.group_id in marketing
                and marketing[g.group_id].marketing_status
                is MarketingStatus.JOIN_REQUESTED
            ),
            regeln=_regeln_von(marketing.get(g.group_id)),
            beobachtung=beobachtet.get(g.group_id),
        )
        for g in groups
    }

    # Die Zielprioritaet je Gruppe - ebenfalls gerechnet und nicht gelesen.
    # Sie beantwortet die vierte Frage der Uebersicht: Gehoert diese Gruppe
    # ueberhaupt zum Zielmarkt der Kampagne (Reise und Versand nach Syrien)?
    zielregeln = zielgruppe.regeln_aus_config(config)
    zielbefunde = {g.group_id: zielgruppe.aus_group(g, zielregeln) for g in groups}

    # Was Facebook geantwortet hat - je Gruppe gezaehlt. Aus dem
    # Versuchsprotokoll, das es ohnehin gibt; ein Zaehler daneben waere eine
    # zweite Wahrheit ueber dieselben Versuche.
    with MarketingStore(db_path) as store:
        gruende_je_gruppe = store.gruende_je_gruppe()

    zeilen = [
        _gruppe_als_zeile(
            g,
            config,
            marketing[g.group_id].marketing_status.value
            if g.group_id in marketing
            else "not_contacted",
            codes_je_gruppe.get(g.group_id, []),
            anfragen_je_gruppe.get(g.group_id, 0),
            ereignisse_je_gruppe.get(g.group_id),
            bearbeiten=marketing[g.group_id].bearbeiten if g.group_id in marketing else True,
            ausschlussgrund=(
                marketing[g.group_id].ausschlussgrund if g.group_id in marketing else ""
            ),
            beitraege=beitraege_je_gruppe.get(g.group_id, []),
            resonanz=resonanz_je_id.get(g.group_id),
            passt_zu=passt_je_gruppe.get(g.group_id, []),
            qualifikation=urteile[g.group_id].qualifikation.value,
            qualifikation_label=urteile[g.group_id].beschriftung,
            qualifikation_grund=urteile[g.group_id].grund,
            regeln=_regeln_von(marketing.get(g.group_id)).zusammenfassung()
            if g.group_id in marketing
            and marketing[g.group_id].regeln_gelesen_am is not None
            else "",
            zielprioritaet=zielbefunde[g.group_id].prioritaet.value,
            zielprioritaet_label=zielbefunde[g.group_id].beschriftung,
            zielprioritaet_grund=zielbefunde[g.group_id].grund,
            zielregion=zielbefunde[g.group_id].region.value,
            zielregion_label=zielbefunde[g.group_id].region_beschriftung,
            gruende=gruende_je_gruppe.get(g.group_id, {}),
        )
        for g in groups
    ]

    # Die Verteilung ueber die Prioritaetsklassen - aus **denselben**
    # Befunden wie die Zeilen. Ein zweiter Lauf ueber die Gruppen koennte
    # davon abweichen, und dann naennte die Kachel eine andere Zahl als die
    # gefilterte Tabelle darunter.
    ziel_zaehler: dict[str, int] = {}
    for befund in zielbefunde.values():
        ziel_zaehler[befund.prioritaet.value] = ziel_zaehler.get(befund.prioritaet.value, 0) + 1
        # Die A-Gruppen noch einmal nach Land. Das ist die Frage, wegen der
        # es die zweite Achse gibt: "20 A-Gruppen" beantwortet nicht, ob
        # genug davon in Deutschland stehen - und genau dort wird zuerst
        # gearbeitet.
        if befund.prioritaet.value == "a":
            schluessel = f"a_{befund.region.value}"
            ziel_zaehler[schluessel] = ziel_zaehler.get(schluessel, 0) + 1

    # Auswahllisten fuer das Kampagnenformular. Aus der Konfiguration, nicht
    # aus dem Bestand: Eine Kampagne darf eine Zielgruppe bewerben, zu der noch
    # keine Gruppe gefunden wurde - das ist der Normalfall beim Anlegen.
    auswahl = {
        "zielgruppen": [
            {"id": a.id, "label": a.label_de} for a in config.audiences.values()
        ],
        "staedte": [{"id": c.id, "label": c.name_de} for c in config.cities.values()],
        "kampagnen_status": [s.value for s in CampaignStatus],
    }

    bewertet = [z for z in zeilen if z["score"] is not None]
    schnitt = round(sum(z["score"] for z in bewertet) / len(bewertet), 1) if bewertet else None

    kampagnen = []
    for c in campaigns:
        klicks = klicks_je_kampagne.get((c.campaign_id, "click"), 0)
        abschluesse = klicks_je_kampagne.get((c.campaign_id, "conversion"), 0)
        # Die Auswahlregel gehoert sichtbar an die Kampagne. Eine Regel, die
        # man nicht nachlesen kann, aendert niemand gern - und "leer heisst
        # keine Einschraenkung" sieht man einem leeren Feld nicht an.
        # Gelesen wird sie ueber selection.auswahl_der_kampagne, damit Anzeige
        # und Ausfuehrung nicht auseinanderlaufen koennen.
        regel = auswahl_der_kampagne(c, config)
        kampagnen.append(
            {
                "id": c.campaign_id,
                "name": c.name,
                "status": c.status.value,
                "regel": {
                    "audiences": list(c.target_audiences),
                    "cities": list(c.target_cities),
                    "categories": list(c.target_categories),
                    "statuses": list(c.target_statuses),
                    "min_score": c.target_min_score,
                    "include_unscored": c.target_include_unscored,
                    "auto_assign": c.auto_assign,
                    "beschreibung": regel.beschreibung(),
                    "kurz": regel_kurzfassung(regel),
                    # Wie viele Gruppen die Regel heute trifft - zugeordnete
                    # eingeschlossen. Ohne diese Zahl bleibt jede Aenderung
                    # eine Vermutung bis zum naechsten "Zuordnen".
                    "passend": sum(1 for g in groups if passt(g, regel)),
                    "bestand": len(groups),
                },
                "gruppen": len(links.get(c.campaign_id, [])),
                # Wie weit die Kampagne beim Veroeffentlichen ist - in
                # derselben Zeile wie ihre Trichterzahlen. Ohne Beitrag gibt es
                # keinen Klick; die beiden Zahlen nebeneinander zeigen sofort,
                # ob eine schwache Kampagne schwach wirkt oder noch gar nicht
                # gepostet wurde.
                "beitraege": beitrag_zaehler.get(c.campaign_id, {}),
                "klicks": klicks,
                "registrierungen": klicks_je_kampagne.get((c.campaign_id, "registration"), 0),
                "qualifiziert": klicks_je_kampagne.get((c.campaign_id, "qualified"), 0),
                "abschluesse": abschluesse,
                # Ohne Klicks gibt es keine Quote - nicht 0,0 %. Eine Quote
                # ohne Grundgesamtheit waere eine Aussage, die niemand belegen
                # kann. Dieselbe Regel wie in analytics.Zeile.conversion_rate.
                "quote": round(abschluesse / klicks * 100, 1) if klicks else None,
            }
        )

    return {
        "gruppen": zeilen,
        "kampagnen": kampagnen,
        "auswahl": auswahl,
        "trichter": trichter,
        # Die Verteilung ueber die Prioritaetsklassen. C und D stehen
        # zusammen, weil sie dasselbe bedeuten: nicht der Ort, an dem
        # gearbeitet wird. Sie zu trennen brauchte eine vierte Kachel fuer
        # eine Unterscheidung, die man in der Spalte nachsieht.
        "ziel_a": ziel_zaehler.get("a", 0),
        "ziel_a_de": ziel_zaehler.get("a_de", 0),
        "ziel_a_eu": ziel_zaehler.get("a_eu", 0),
        "ziel_b": ziel_zaehler.get("b", 0),
        "ziel_cd": ziel_zaehler.get("c", 0) + ziel_zaehler.get("d", 0),
        "kennzahlen": {
            "gesamt": len(zeilen),
            "bewertet": len(bewertet),
            "schnitt": schnitt,
            "bestwert": max((z["score"] for z in bewertet), default=None),
            "tracking_links": sum(len(v) for v in links.values()),
            "beitraege_offen": sum(
                1 for z in zeilen if z["beitrag_status"] in ("offen", "fehlgeschlagen")
            ),
            # Zaehlt Beitraege, nicht Gruppen: Eine Gruppe in zwei Kampagnen
            # traegt zwei Beitraege, und beide sind Arbeit.
            "beitraege_veroeffentlicht": sum(
                1
                for z in zeilen
                for b in z["beitraege"]
                if b["status"] == "veroeffentlicht"
            ),
            **zahlen,
        },
        "erzeugt_am": datetime.now().strftime("%d.%m.%Y %H:%M"),
    }

def _kachel(wert: str, label: str) -> str:
    return f'<div class="kachel"><b>{html.escape(wert)}</b><span>{html.escape(label)}</span></div>'

def _riegel(x: float, y: float, breite: float, hoehe: float, r: float = 4) -> str:
    """Liegender Balken - gerundet am rechten Ende, eckig an der Grundlinie."""
    if breite <= 0:
        return ""
    r = min(r, hoehe / 2, breite)
    return (
        f"M{x},{y} L{x + breite - r},{y} Q{x + breite},{y} {x + breite},{y + r} "
        f"L{x + breite},{y + hoehe - r} Q{x + breite},{y + hoehe} "
        f"{x + breite - r},{y + hoehe} L{x},{y + hoehe} Z"
    )


def _diagramme(gruppen: list[dict[str, Any]]) -> str:
    """Ein kleines Balkendiagramm ueber der Tabelle: worauf der Score beruht.

    Es beantwortet die Frage, die die Kacheln offenlassen. Am 01.09.2026 ist
    das die eigentliche Auskunft: Mitgliederzahl und Aktivitaet tragen
    zusammen die Haelfte des Scores und stehen bei 0 %. Als Zahl in einer
    Kachel liest man darueber hinweg; als leerer Balken neben drei vollen
    nicht.

    Eine Reihe, also keine Legende - die Ueberschrift sagt, was gezeigt wird.
    Beschriftet wird direkt am Balkenende.

    Eine Verteilung der Punkte stand hier bis zum 01.09.2026 daneben; der
    Nutzer hat sie verworfen. Sie beantwortete eine Frage, die die Tabelle
    schon beantwortet - dort steht jede Gruppe mit ihrem Score, sortierbar.
    """
    # --- Abdeckung: worauf der Score beruht
    gesamt = len(gruppen) or 1

    def hat_aktivitaet(g: dict[str, Any]) -> bool:
        return (
            g.get("aktivitaet_quelle") is not None
            or g.get("posts_pro_tag") is not None
            or (g.get("punkte") or {}).get("activity", 0) > 0
        )

    felder = [
        ("Mitgliederzahl", sum(1 for g in gruppen if g.get("mitglieder") is not None)),
        ("Aktivität", sum(1 for g in gruppen if hat_aktivitaet(g))),
        ("Stadt", sum(1 for g in gruppen if g.get("stadt"))),
        ("Zielgruppe", sum(1 for g in gruppen if g.get("zielgruppen"))),
        ("Kategorie", sum(1 for g in gruppen if g.get("kategorie"))),
    ]
    spur_x, spur_breite, hoehe = 96.0, 176.0, 14.0
    riegel = []
    for i, (name, anzahl) in enumerate(felder):
        y = 12 + i * 20
        anteil = anzahl / gesamt
        teile = [
            f"<g><title>{html.escape(name)}: {anzahl} von {len(gruppen)} Gruppen</title>",
            f'<text class="viz-achse viz-links" x="{spur_x - 8}" y="{y + 11}">'
            f"{html.escape(name)}</text>",
            f'<path class="viz-spur" d="{_riegel(spur_x, y, spur_breite, hoehe)}"/>',
        ]
        if anteil > 0:
            teile.append(
                f'<path class="viz-mark" '
                f'd="{_riegel(spur_x, y, spur_breite * anteil, hoehe)}"/>'
            )
        teile.append(
            f'<text class="viz-wert viz-rechts" x="{spur_x + spur_breite + 8}" y="{y + 11}">'
            # "0 %" fuer einen Wert, den es gibt, waere eine falsche Auskunft:
            # Bei einer von 314 Gruppen rundet der Prozentwert auf null.
            f"{'&lt;1' if 0 < anteil < 0.01 else round(anteil * 100)} %</text></g>"
        )
        riegel.append("".join(teile))

    return (
        '<figure class="viz-karte">\n'
        f"  <figcaption>Datenabdeckung <span>{len(gruppen)} Gruppen</span></figcaption>\n"
        '  <svg viewBox="0 0 340 116" role="img"\n'
        '       aria-label="Anteil der Gruppen mit belegter Angabe je Merkmal">\n'
        f"    {''.join(riegel)}\n"
        "  </svg>\n"
        "</figure>"
    )


def render(daten: dict[str, Any], *, nur_lesen: bool = False) -> str:
    """Baut die vollstaendige Seite.

    Die Daten stehen als JSON im Dokument; gefiltert und sortiert wird im
    Browser. ``</`` wird dabei maskiert - ohne das beendete ein Gruppenname mit
    dieser Zeichenfolge das Skript und die Seite bliebe leer.

    ``nur_lesen`` baut dieselben Zahlen ohne die Bedienelemente, die schreiben.
    Das ist **keine** Absicherung - die schreibenden Wege pruefen selbst, siehe
    ``web._nur_lokal`` - sondern Aufrichtigkeit: Ein Knopf, dessen Weg mit 404
    antwortet, sieht aus wie ein Fehler der Seite. Gezeigt wird stattdessen,
    wo die Aenderung hingehoert.
    """
    k = daten["kennzahlen"]
    daten = {**daten, "staende": [
        {"wert": s.value, "label": status_label(s.value)} for s in MarketingStatus
    ]}
    nutzlast = json.dumps(daten, ensure_ascii=False).replace("</", "<\\/")

    diagramme = _diagramme(daten.get("gruppen") or [])

    kacheln = "".join(
        [
            _kachel(str(k["gesamt"]), "Gruppen"),
            _kachel(str(k["bewertet"]), "bewertet"),
            _kachel(
                f"{k['schnitt']:.1f}".replace(".", ",") if k["schnitt"] is not None else "–",
                "Durchschnitt",
            ),
            _kachel(
                f"{k['bestwert']:.1f}".replace(".", ",") if k["bestwert"] is not None else "–",
                "Bestwert",
            ),
            # Die Prioritaetsverteilung (13.09.2026). Sie beantwortet die
            # Frage, wegen der es die Einstufung gibt: Haben wir ueberhaupt
            # genug Gruppen im Zielmarkt, oder arbeitet die Kampagne
            # hauptsaechlich in Gemeinschaftsgruppen? "20 A-Gruppen" ist eine
            # Auskunft, "314 Gruppen" ist keine.
            # Seit dem 14.09.2026 mit dem Land daneben. "20 A-Gruppen"
            # beantwortet nicht, ob genug davon in Deutschland stehen - und
            # dort wird zuerst gearbeitet. Die dritte Zahl (A ohne genanntes
            # Land) steht nicht als eigene Kachel: Sie ist der Rest der
            # ersten und liesse sich nur zusammen mit den anderen beiden
            # lesen; wer sie braucht, filtert danach.
            _kachel(
                f"{daten['ziel_a_de']} / {daten['ziel_a_eu']}",
                "A – Deutschland / Europa",
            ),
            _kachel(str(daten["ziel_a"]), "A – gesamt"),
            _kachel(str(daten["ziel_b"]), "B – Gemeinschaft"),
            _kachel(str(daten["ziel_cd"]), "C/D – nachrangig"),
            _kachel(str(k["tracking_links"]), "Tracking-Links"),
            _kachel(str(k["beitraege_veroeffentlicht"]), "Beiträge"),
            _kachel(str(k["beitraege_offen"]), "offen"),
            # Klicks, Registrierungen, Downloads und "qualifiziert" stehen
            # seit dem 01.09.2026 nur noch im Trichter darunter. Zweimal
            # dieselbe Zahl heisst zwei Stellen, an denen sie stimmen muss -
            # und die Kachel nannte sie ohne ihre Stufe davor und dahinter.
            _kachel(str(k["referrals"]), "Empfehlungen"),
            _kachel(str(k["rewards"]), "Prämien"),
        ]
    )

    quote = lambda w: "–" if w is None else f"{w:.1f}".replace(".", ",") + " %"  # noqa: E731

    # Vergebene Links ohne einen einzigen Klick sind der haeufigste stille
    # Fehler: Die Codes stehen, aber unter der Basis-URL antwortet der Dienst
    # nicht - dann liefert der Server die Startseite der App statt der
    # Weiterleitung, und niemand merkt es, weil die Seite ja erscheint.
    warnung = ""
    if k["tracking_links"] and not k["clicks"]:
        warnung = (
            '<div class="warnung"><b>Noch kein einziger Klick.</b> '
            + str(k["tracking_links"])
            + " Tracking-Links sind vergeben. Prüfe, ob unter der Basis-URL wirklich "
            "dieser Dienst antwortet – <code>/healthz</code> muss dort den Status als "
            "JSON liefern. Kommt HTML zurück, zeigt die Domain auf eine andere "
            "Anwendung, und jeder Klick geht verloren, ohne dass es auffällt: "
            "Der Besucher sieht ja eine Seite.</div>"
        )

    def status_auswahl(aktuell: str) -> str:
        """Die vier Kampagnenzustaende, der aktuelle vorgewaehlt."""
        return "".join(
            f"<option value='{s.value}'"
            f"{' selected' if s.value == aktuell else ''}>{s.value}</option>"
            for s in CampaignStatus
        )

    kampagnen_zeilen = "".join(
        f"<tr><td>{html.escape(c['name'])}"
        f"<span class='regel-text' data-id=\"{html.escape(c['id'])}\" "
        f"title=\"{html.escape(c['regel']['beschreibung'])}\">"
        f"trifft {c['regel']['passend']} von {c['regel']['bestand']}"
        f" · {html.escape(c['regel']['kurz'])}"
        f"{' · nimmt neue Funde automatisch auf' if c['regel']['auto_assign'] else ''}"
        f"</span>"
        f"<span class='k-kennung'><code>{html.escape(c['id'])}</code></span>"
        # Kein Haken fuer Kommentare mehr: Beitrag **und** Kommentar
        # entstehen fuer jede Kampagne. Der Schalter war eine Frage, die in
        # jeder Zeile stand und in keiner beantwortet werden musste.
        f"</td>"
        f"<td><select class='k-status' data-id=\"{html.escape(c['id'])}\" "
        f"data-vorher=\"{html.escape(c['status'])}\">"
        f"{status_auswahl(c['status'])}"
        f"</select></td>"
        f"<td class='zahl'>{c['gruppen']}</td>"
        f"<td class='zahl'>{c['klicks']}</td>"
        f"<td class='zahl'>{c['registrierungen']}</td>"
        f"<td class='zahl'>{c['qualifiziert']}</td>"
        f"<td class='zahl'>{c['abschluesse']}</td>"
        f"<td class='zahl'>{quote(c['quote'])}</td>"
        f"<td class='knopfzelle'>"
        # Der Weg zur Arbeitsseite. Er schreibt (beginnt einen Versuch) und
        # erscheint deshalb nur im bedienbaren Zugang - von aussen fuehrte er
        # ins Leere, und ein Knopf, der 404 antwortet, sieht aus wie ein Fehler.
        + (
            ""
            if nur_lesen
            else f"<a class='k-arbeit' href='/arbeit/{html.escape(c['id'])}'>Arbeiten</a>"
        )
        + f"<button class='k-regel' data-id=\"{html.escape(c['id'])}\">Regel</button>"
        f"<button class='k-sync' data-id=\"{html.escape(c['id'])}\">Zuordnen</button>"
        f"<button class='k-weg' data-id=\"{html.escape(c['id'])}\" "
        f"data-name=\"{html.escape(c['name'])}\" title='Kampagne loeschen'>&times;</button>"
        f"</td>"
        f"</tr>"
        for c in daten["kampagnen"]
    ) or "<tr><td colspan='10' class='leer'>Noch keine Kampagne angelegt.</td></tr>"

    # Der Trichter als Balken: Die Anteile beziehen sich auf die Klicks, damit
    # zwei Auswertungen vergleichbar bleiben (siehe analytics.funnel).
    trichter_zeilen = "".join(
        f"<tr><td>{html.escape(s['label'])}</td>"
        f"<td class='zahl'>{s['anzahl']}</td>"
        f"<td class='zahl'>{quote(s['anteil'])}</td>"
        f"<td class='balkenzelle'><span class='balken' style='width:"
        f"{min(s['anteil'] or 0, 100):.1f}%'></span></td></tr>"
        for s in daten["trichter"]
    )

    # Ausgeblendet wird per CSS, nicht entfernt: Das Skript sucht mehrere
    # dieser Knoepfe beim Start ueber getElementById und liefe sonst in einen
    # Fehler, der die ganze Seite leer liesse.
    koerper_klasse = ' class="nur-lesen"' if nur_lesen else ""
    nur_lesen_js = "true" if nur_lesen else "false"
    hinweis = (
        "Nur-Lesen-Ansicht: Zahlen ja, Änderungen nein. Der Stand wird über den "
        "SSH-Zugang gepflegt – dort steht ein Mensch vor den Knöpfen, die "
        "Tracking-Codes vergeben."
        if nur_lesen
        else "Den Stand kannst du in der Tabelle direkt ändern – er wird sofort "
        "gespeichert. Alles andere entsteht aus der Suche und ist hier nicht "
        "änderbar. Facebook meldet nichts von selbst: Was du dort tust, trägst "
        "du hier nach."
    )
    fusszeile = (
        "Kein Zugriff auf facebook.com. Diese Ansicht zeigt nur an; geändert "
        "wird über den SSH-Zugang."
        if nur_lesen
        else "Kein Zugriff auf facebook.com. Diese Seite ist nur über localhost "
        "erreichbar – der Dienst selbst beantwortet <code>/r/{code}</code> "
        "weiterhin für alle."
    )

    return f"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Batraqiq – Gruppenübersicht</title>
<style>
  :root {{
    --bg: #f6f7f9; --karte: #fff; --text: #1a1c1f; --leise: #6b7280;
    --rand: #e3e6ea; --akzent: #2563eb; --gut: #15803d; --mittel: #b45309;
    --viz: #2a78d6;
    --b-offen-bg: #fef3c7;  --b-offen-fg: #92400e;
    --b-gut-bg: #dcfce7;    --b-gut-fg: #166534;
    --b-fehler-bg: #fee2e2; --b-fehler-fg: #991b1b;
    --b-aus-bg: #e5e7eb;    --b-aus-fg: #4b5563;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #16181d; --karte: #1e2127; --text: #e8eaed; --leise: #9aa1ab;
      --rand: #2c313a; --akzent: #60a5fa; --gut: #4ade80; --mittel: #fbbf24;
      --viz: #3987e5;
      --b-offen-bg: #3b2f14;  --b-offen-fg: #fcd34d;
      --b-gut-bg: #14321f;    --b-gut-fg: #6ee7a8;
      --b-fehler-bg: #3b1c1c; --b-fehler-fg: #fca5a5;
      --b-aus-bg: #272b33;    --b-aus-fg: #9aa1ab;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 24px; background: var(--bg); color: var(--text);
    font: 15px/1.5 "Segoe UI", system-ui, sans-serif;
  }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }}
  .hinweis {{ color: var(--leise); font-size: 13px; margin: 0 0 20px; }}
  .kacheln {{ display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 20px; }}
  /* Diagramme: eine Reihe je Bild, deshalb keine Legende. Die Markenfarbe ist
     geprueft (Helligkeitsband, Chroma, Kontrast gegen die Kartenflaeche) und
     steht bewusst nicht auf --akzent: dessen Dunkelwert liegt ausserhalb des
     Bandes und traegt als Flaeche zu wenig Gewicht. */
  /* Die obere Reihe: Datenabdeckung und Trichter nebeneinander. Beide sind
     Auskunft ueber den ganzen Bestand und gehoeren deshalb vor die Tabelle -
     der Trichter stand bis zum 01.09.2026 unter dreihundert Zeilen. */
  .oben {{
    display: flex; flex-wrap: wrap; gap: 12px;
    align-items: flex-start; margin-bottom: 20px;
  }}
  .oben > .trichter {{ flex: 1 1 420px; min-width: 320px; }}
  .oben h2 {{ margin: 0 0 8px; }}
  .oben table {{ font-size: 13px; }}
  .oben th, .oben td {{ padding: 6px 10px; }}
  .viz-karte {{
    flex: 1 1 320px; max-width: 440px; margin: 0;
    background: var(--karte); border: 1px solid var(--rand);
    border-radius: 10px; padding: 10px 12px 4px;
  }}
  .viz-karte figcaption {{
    font-size: 12px; font-weight: 600; color: var(--text); margin-bottom: 2px;
  }}
  .viz-karte figcaption span {{ font-weight: 400; color: var(--leise); }}
  .viz-karte svg {{ width: 100%; height: auto; display: block; }}
  .viz-mark {{ fill: var(--viz); }}
  .viz-spur {{ fill: var(--rand); }}
  .viz-linie {{ stroke: var(--rand); stroke-width: 1; }}
  /* Text traegt Textfarben, nie die Datenfarbe. */
  .viz-wert {{ fill: var(--text); font-size: 10px; font-weight: 600; text-anchor: middle; }}
  .viz-achse {{ fill: var(--leise); font-size: 10px; text-anchor: middle; }}
  .viz-links {{ text-anchor: end; }}
  .viz-rechts {{ text-anchor: start; }}
  .kachel {{
    background: var(--karte); border: 1px solid var(--rand); border-radius: 10px;
    padding: 12px 18px; min-width: 108px;
  }}
  .kachel b {{ display: block; font-size: 22px; }}
  .kachel span {{ color: var(--leise); font-size: 12px; }}
  .blaettern {{
    display:flex; gap:.75rem; align-items:center; justify-content:center;
    margin:.75rem 0 1.5rem; color:#8b929c; font-size:.85rem;
  }}
  .blaettern button {{ min-width:2.5rem; }}
  .blaettern button[disabled] {{ opacity:.35; cursor:default; }}
  .blaettern b {{ color:#e6e8eb; }}
  .s-groesse {{ display:flex; gap:.4rem; align-items:center; }}
  .s-groesse select {{ padding:.3rem .5rem; }}
  .filter {{
    display: flex; flex-wrap: wrap; gap: 10px; align-items: center;
    background: var(--karte); border: 1px solid var(--rand);
    border-radius: 10px; padding: 12px; margin-bottom: 16px;
  }}
  select, input[type=search] {{
    background: var(--bg); color: var(--text); border: 1px solid var(--rand);
    border-radius: 7px; padding: 7px 10px; font: inherit; font-size: 14px;
  }}
  input[type=search] {{ flex: 1; min-width: 200px; }}
  label.schalter {{
    color: var(--leise); font-size: 13px;
    display: flex; gap: 6px; align-items: center;
  }}
  table {{
    width: 100%; border-collapse: collapse; background: var(--karte);
    border: 1px solid var(--rand); border-radius: 10px; overflow: hidden;
  }}
  th, td {{ padding: 9px 12px; text-align: start; border-bottom: 1px solid var(--rand); }}
  th {{
    font-size: 12px; text-transform: uppercase; letter-spacing: .04em;
    color: var(--leise); cursor: pointer; user-select: none; white-space: nowrap;
  }}
  tbody tr:last-child td {{ border-bottom: 0; }}
  tbody tr:hover {{ background: color-mix(in srgb, var(--akzent) 7%, transparent); }}
  td.zahl, th.zahl {{ text-align: end; font-variant-numeric: tabular-nums; }}
  /* Die Score-Zelle: Zahl, Hoechstwert, fuenf Balken.
     Die Balken stehen fuer die fuenf Bestandteile in fester Reihenfolge -
     dieselbe Farbe bedeutet in jeder Zeile denselben Bestandteil, sonst
     waere die Spalte beim Ueberfliegen wertlos. */
  .scorezelle {{ white-space: nowrap; line-height: 1.15; }}
  .von {{ font-size: 10px; color: var(--leise); margin-left: 2px; }}
  .balken {{ display: flex; gap: 2px; margin-top: 3px; justify-content: flex-end; }}
  .balken i {{
    display: block; width: 9px; height: 4px; border-radius: 1px;
    background: var(--rand); position: relative; overflow: hidden;
  }}
  /* Der gefuellte Anteil als zweite Ebene - so bleibt die Gesamtbreite
     sichtbar. Ein Balken, der nur so lang ist wie sein Wert, sagt nicht,
     wovon er ein Teil ist. */
  .balken i::after {{
    content: ""; position: absolute; inset: 0 auto 0 0; width: var(--anteil);
  }}
  .b-members::after         {{ background: #60a5fa; }}
  .b-activity::after        {{ background: #f97316; }}
  .b-location::after        {{ background: #34d399; }}
  .b-category::after        {{ background: #a78bfa; }}
  .b-target_audience::after {{ background: #fbbf24; }}
  .punkte {{ font-weight: 700; font-variant-numeric: tabular-nums; }}
  .punkte.hoch {{ color: var(--gut); }}
  .punkte.mittel {{ color: var(--mittel); }}
  .punkte.keine {{ color: var(--leise); font-weight: 400; }}
  .name a {{ color: inherit; text-decoration: none; }}
  .name a:hover {{ color: var(--akzent); text-decoration: underline; }}
  .grund {{ display: block; color: var(--leise); font-size: 12px; margin-top: 2px; }}
  code {{
    background: var(--bg); border: 1px solid var(--rand); border-radius: 5px;
    padding: 1px 5px; font-size: 12px;
  }}
  /* Die Begruendung neben dem Zaehler: leiser als er, aber lesbar - sie
     erklaert eine Zahl, die sonst nach einem Fehler aussieht. */
  .marke-grund {{
    font-size: 12px; color: #9ca3af; margin-left: 10px;
    display: inline-flex; align-items: center; gap: 8px;
  }}
  .marke-grund button.mini {{
    cursor: pointer; padding: 2px 8px; border-radius: 5px; font-size: 12px;
  }}
  .marke {{
    font-size: 12px; border: 1px solid var(--rand); border-radius: 20px;
    padding: 2px 9px; color: var(--leise); white-space: nowrap;
  }}
  select.stand {{ font-size: 12px; padding: 4px 7px; max-width: 190px; }}
  select.stand:hover {{ border-color: var(--akzent); }}
  select.stand.gespeichert {{ border-color: var(--gut); color: var(--gut); }}
  select.stand.fehler {{ border-color: #dc2626; color: #dc2626; }}
  .leer {{ text-align: center; color: var(--leise); padding: 28px; }}
  h2 {{ font-size: 15px; margin: 28px 0 10px; }}
  footer {{ color: var(--leise); font-size: 12px; margin-top: 28px; }}
  .tabelle-rahmen {{ overflow-x: auto; }}
  /* Die Kampagnen stehen fuer sich und nicht neben dem Trichter: Bei zehn
     Kampagnen lief die Tabelle in der halben Breite ueber, und ausgerechnet
     die Knopfspalte verschwand im waagerechten Bildlauf - man sah die
     Kampagne, konnte sie aber nicht bedienen. */
  .kampagnen-block {{ margin-top: 8px; }}
  .kampagnen-block table {{ width: 100%; }}
  /* Und selbst wenn sie doch einmal ueberlaeuft, bleiben die Knoepfe stehen. */
  .kampagnen-block td.knopfzelle,
  .kampagnen-block th.aktionen-kopf {{
    position: sticky; right: 0; background: var(--karte);
    box-shadow: -8px 0 8px -8px rgba(0, 0, 0, .6);
  }}
  /* Die Kennung steht jetzt unter dem Namen statt in einer eigenen Spalte:
     Man braucht sie fuer Befehle auf der Kommandozeile, aber nicht als
     Sortierkriterium - und eine Spalte weniger ist eine Spalte, die nicht
     ueberlaeuft. */
  .k-kennung {{ display: block; margin-top: 3px; }}
  .kampagnen-zelle {{ min-width: 150px; }}
  .k-marke {{
    display: inline-block; background: var(--bg); border: 1px solid var(--rand);
    border-radius: 5px; padding: 1px 6px; margin: 0 3px 3px 0; font-size: 11px;
  }}
  .k-zuordnen {{
    display: block; font-size: 11px; padding: 2px 4px; margin-top: 2px;
    background: transparent; color: var(--leise);
    border: 1px dashed var(--rand); border-radius: 5px;
  }}
  .k-zuordnen:hover {{ color: var(--text); border-style: solid; }}
  /* Vorschlag statt Zustand: Er sieht wie ein Knopf aus, weil er einer ist -
     die Marke daneben ist eine Feststellung und darf nicht anklickbar wirken. */
  .k-vorschlag {{
    display: inline-block; cursor: pointer; font-size: 11px;
    padding: 1px 6px; margin: 0 3px 3px 0; border-radius: 5px;
    background: transparent; color: var(--gut);
    border: 1px dashed var(--gut);
  }}
  .k-vorschlag:hover {{ background: var(--gut); color: var(--bg); border-style: solid; }}
  .k-offen {{
    display: inline-block; font-size: 11px; padding: 1px 6px;
    margin: 0 3px 3px 0; border-radius: 5px; color: var(--leise);
    border: 1px dashed var(--rand);
  }}
  .k-kennung code {{ font-size: 11px; }}
  .balkenzelle {{ width: 40%; min-width: 120px; }}
  .balken {{
    display: block; height: 10px; border-radius: 5px;
    background: var(--akzent); min-width: 2px;
  }}
  .spalten {{ display: flex; flex-wrap: wrap; gap: 20px; align-items: flex-start; }}
  .spalten > section {{ flex: 1; min-width: 320px; }}
  h2 {{ font-size: 16px; margin: 24px 0 8px; }}
  .warnung {{
    background: var(--karte); border: 1px solid var(--mittel);
    border-left: 4px solid var(--mittel); border-radius: 8px;
    padding: 10px 14px; margin-bottom: 16px; font-size: 13px;
  }}
  /* Sammelleiste: erscheint erst, wenn etwas ausgewaehlt ist - sonst stuende
     dauerhaft eine Schaltflaeche da, die nichts tut. */
  .sammel {{
    display: flex; gap: 10px; align-items: center; flex-wrap: wrap;
    background: var(--karte); border: 1px solid var(--mittel);
    border-radius: 8px; padding: 10px 14px; margin-bottom: 12px; font-size: 13px;
  }}
  .sammel[hidden] {{ display: none; }}
  .neu-kampagne {{
    background: var(--karte); border: 1px solid var(--mittel);
    border-radius: 8px; padding: 12px 16px; margin: 12px 0 24px; font-size: 13px;
  }}
  .neu-kampagne summary {{ cursor: pointer; font-weight: 600; }}
  .formular {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 12px; margin-top: 14px;
  }}
  .formular label {{ display: flex; flex-direction: column; gap: 4px; }}
  .formular .breit {{ grid-column: 1 / -1; }}
  .formular input, .formular select, .formular textarea {{
    font: inherit; padding: 6px 8px; border-radius: 5px;
  }}
  .knopfreihe {{ display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }}
  .knopfreihe button, .k-sync {{ cursor: pointer; padding: 6px 14px; border-radius: 6px; }}
  .zart {{ color: var(--mittel); font-weight: 400; }}
  .meldung {{ font-size: 12px; margin: 2px 0 0; }}
  .meldung.gut {{ color: var(--gut); }}
  .meldung.schlecht {{ color: var(--mittel); }}
  .erklaerung {{ font-size: 12px; color: var(--leise); margin: 10px 0 0; }}
  .sammel input[type=text] {{ flex: 1; min-width: 200px; }}
  .sammel button {{ cursor: pointer; padding: 6px 12px; border-radius: 6px; }}
  th.auswahl, td.auswahl {{ width: 28px; text-align: center; }}
  /* Ausgeschlossen heisst zurueckgetreten, nicht verschwunden: Wer den Filter
     abschaltet, soll die Zeile sehen und den Grund gleich mitlesen koennen. */
  tr.ausgeschlossen {{ opacity: .5; }}
  tr.ausgeschlossen .name a {{ text-decoration: line-through; }}
  .aus-grund {{ font-size: 11px; color: var(--mittel); display: block; }}
  /* Gezaehlt statt aufgezaehlt (siehe regel_kurzfassung) - und selbst dann
     bleibt die Zeile einzeilig: Eine Regel darf die Namensspalte nicht
     auseinanderschieben. Der volle Text steht im title. */
  .regel-text {{
    font-size: 11px; color: var(--leise); display: block;
    max-width: 46ch; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }}
  .knopfzelle {{ display: flex; gap: 6px; }}
  .k-regel {{ cursor: pointer; padding: 6px 12px; border-radius: 6px; }}
  /* Der Weg zur Arbeitsseite - hervorgehoben, weil er der Einstieg in die
     eigentliche Arbeit ist und nicht eine Einstellung daneben. */
  .k-arbeit {{ padding: 6px 12px; border-radius: 6px; text-decoration: none;
               background: #1d4ed8; color: #fff; font-weight: 600;
               white-space: nowrap; }}
  .k-arbeit:hover {{ background: #2563eb; }}
  /* Zurueckhaltend, obwohl es der folgenreichste Knopf der Seite ist: Er
     loescht Tracking-Codes, die in veroeffentlichten Beitraegen stehen.
     Auffaellig gestaltet lockte er zum Ausprobieren - die Warnung steht
     stattdessen im Dialog, wo sie gelesen wird. */
  .k-weg {{ cursor: pointer; padding: 6px 10px; border-radius: 6px;
            background: transparent; color: var(--leise); border: 1px solid transparent;
            font-size: 16px; line-height: 1; }}
  .k-weg:hover {{ background: #7a2e2e; color: #fff; border-color: #993a3a; }}
  /* Beitragsspalte */
  .beitrag {{ display: flex; flex-direction: column; gap: 4px; min-width: 210px; }}
  .b-zeile {{ display: flex; align-items: center; gap: 4px; flex-wrap: wrap; }}
  .b-marke {{
    font-size: 11px; padding: 1px 7px; border-radius: 10px; white-space: nowrap;
  }}
  .b-offen           {{ background: var(--b-offen-bg);  color: var(--b-offen-fg); }}
  .b-veroeffentlicht {{ background: var(--b-gut-bg);    color: var(--b-gut-fg); }}
  .b-fehlgeschlagen  {{ background: var(--b-fehler-bg); color: var(--b-fehler-fg); }}
  .b-uebersprungen   {{ background: var(--b-aus-bg);    color: var(--b-aus-fg); }}
  .b-fehler {{ font-size: 11px; color: var(--b-fehler-fg); }}
  .b-leer {{ color: var(--leise); font-size: 12px; }}
  /* Gemessene Resonanz */
  .res {{ font-size: 12px; line-height: 1.45; white-space: nowrap; }}
  .res b {{ font-size: 13px; }}
  /* Nur-Lesen: alles weg, was einen schreibenden Weg ruft. */
  .sammel-teiler {{ color: var(--rand); padding: 0 .2rem; }}
  body.nur-lesen .sammel,
  body.nur-lesen th.auswahl, body.nur-lesen td.auswahl,
  body.nur-lesen .knopfzelle, body.nur-lesen .k-status,
  body.nur-lesen .neu-kampagne,
  /* Ein Stand, den die Automatik fuehrt - als Text, nicht als Feld. Ein
     Auswahlfeld mit zwei Werten liesse ihn beim naechsten Anfassen fallen. */
  .stand-fest {{ font-size:.85rem; opacity:.85; font-style:italic; }}
</style>
</head>
<body{koerper_klasse}>
<h1>Batraqiq – Gruppenübersicht</h1>
<p class="hinweis">
  {hinweis}
  Stand: {html.escape(daten["erzeugt_am"])}
</p>

{warnung}

<div class="kacheln">{kacheln}</div>

<div class="oben">
{diagramme}
<section class="trichter">
<h2>Trichter</h2>
<div class="tabelle-rahmen">
<table>
  <thead><tr>
    <th>Stufe</th><th class="zahl">Anzahl</th>
    <th class="zahl" title="Anteil an den Klicks – nicht an der vorigen Stufe.
So bleiben zwei Auswertungen vergleichbar.">Anteil</th><th></th>
  </tr></thead>
  <tbody>{trichter_zeilen}</tbody>
</table>
</div>
</section>
</div>

<div class="filter">
  <select id="f-stadt"><option value="">Alle Städte</option></select>
  <select id="f-zielgruppe"><option value="">Alle Zielgruppen</option></select>
  <select id="f-kategorie"><option value="">Alle Kategorien</option></select>
  <select id="f-zielprio"
          title="Gehoert die Gruppe zum Zielmarkt? A wird zuerst bearbeitet, D gar nicht.">
    <option value="">Jede Prioritaet</option>
    <option value="a">A – Reise &amp; Versand</option>
    <option value="b">B – Gemeinschaft</option>
    <option value="c">C – allgemein</option>
    <option value="d">D – ohne Bezug</option>
  </select>
  <select id="f-zielregion"
          title="Wo arbeitet die Gruppe? Gearbeitet wird Deutschland zuerst,
dann das uebrige Europa. Eine Gruppe, die nur ein Land ausserhalb Europas
nennt, faellt aus dem Zielmarkt heraus - ihre Strecke ist nicht unsere.">
    <option value="">Jedes Land</option>
    <option value="de">Deutschland</option>
    <option value="eu">übriges Europa</option>
    <option value="unbekannt">Land unbekannt</option>
    <option value="ausserhalb">außerhalb Europas</option>
  </select>
  <select id="f-marketing"><option value="">Jeder Stand</option></select>
  <select id="f-qualifikation" title="Darf hier ueberhaupt etwas stehen?">
    <option value="">Jede Stufe</option>
  </select>
  <select id="f-beitrag">
    <option value="">Jeder Beitrag</option>
    <option value="zu-tun">nur zu erledigen</option>
    <option value="offen">offen</option>
    <option value="fehlgeschlagen">fehlgeschlagen</option>
    <option value="veroeffentlicht">veröffentlicht</option>
    <option value="uebersprungen">übersprungen</option>
  </select>
  <input type="search" id="f-suche" placeholder="Name durchsuchen – auch arabisch …">
  <select id="f-mitglieder" title="Nach belegter Mitgliederzahl filtern">
    <option value="">Jede Größe</option>
    <option value="ja">Mitgliederzahl belegt</option>
    <option value="nein">Mitgliederzahl unbekannt</option>
    <option value="10000">ab 10.000</option>
    <option value="1000">ab 1.000</option>
  </select>
  <select id="f-aktivitaet" title="Nach erhobener Aktivität filtern">
    <option value="">Jede Aktivität</option>
    <option value="ja">Aktivität gemessen</option>
    <option value="nein">Aktivität unbekannt</option>
    <option value="hoch">hohe Aktivität</option>
  </select>
  <select id="f-konfidenz" title="Wie belastbar die Grundlage des Scores ist">
    <option value="">Jede Datenqualität</option>
    <option value="0.7">ab 70 %</option>
    <option value="0.4">ab 40 %</option>
    <option value="niedrig">unter 40 %</option>
  </select>
  <!-- **Beide aus** (14.09.2026). Sie standen auf "checked", und damit
       verschwieg die Uebersicht beim ersten Aufruf jede Gruppe ohne Score und
       jede ausgeschlossene: 471 von 621 - ohne dass irgendwo stand, warum.
       Der Zaehler nannte nur die Zahl, und wer eine bearbeitete Gruppe
       suchte, hielt sie fuer verschwunden.

       Eine Uebersicht, die "Gruppen" heisst, zeigt den Bestand. Was man
       ausblenden will, blendet man aus - und sieht daneben, dass man es
       getan hat (siehe "treffer-grund"). Der eigene Haken ueberlebt die
       Sitzung weiterhin (sessionStorage); anders ist allein, womit die Seite
       anfaengt. -->
  <label class="schalter"><input type="checkbox" id="f-bewertet"> nur bewertete</label>
  <label class="schalter"><input type="checkbox" id="f-bearbeitet"> nur bearbeitete</label>
  <span class="marke" id="treffer"></span>
  <span class="marke-grund" id="treffer-grund"></span>
</div>

<div class="sammel" id="sammel" hidden>
  <strong id="sammel-anzahl"></strong>
  <input type="text" id="sammel-grund" placeholder="Grund für den Ausschluss (optional)">
  <button id="sammel-aus">Ausschließen</button>
  <button id="sammel-ein">Wieder aufnehmen</button>
  <span class="hinweis">Der Tracking-Code bleibt in jedem Fall gültig.</span>
  <span class="sammel-teiler">·</span>
  <select id="sammel-kampagne" title="Die gewählten Gruppen dieser Kampagne zuordnen">
    <option value="">Kampagne wählen …</option>
  </select>
  <button id="sammel-zuordnen">Zuordnen</button>
</div>

<div class="tabelle-rahmen">
<table>
  <thead><tr>
    <th class="auswahl"><input type="checkbox" id="alle" title="Alle sichtbaren auswählen"></th>
    <th class="zahl" data-sort="score">Score</th>
    <th data-sort="name">Gruppe</th>
    <th data-sort="stadt">Stadt</th>
    <th data-sort="zielgruppen">Zielgruppe</th>
    <th data-sort="kategorie">Kategorie</th>
    <th data-sort="zielprioritaet"
        title="Gehoert diese Gruppe zum Zielmarkt? A = Reise und Versand nach
Syrien, B = syrische/arabische Gemeinschaft in Deutschland, C = allgemein,
D = ohne Bezug. Gerechnet aus Name, Beschreibung, Kategorie, Zielgruppe und
Stadt - nicht gespeichert. A wird zuerst bearbeitet, D gar nicht.">Ziel</th>
    <th data-sort="kampagnen_text"
        title="Zu welchen Kampagnen diese Gruppe gehoert. Zuordnen vergibt einen
Tracking-Code - der wird nie zurueckgenommen, er steht spaeter in
veroeffentlichten Beitraegen.">Kampagne</th>
    <th data-sort="marketing_label">Stand</th>
    <th data-sort="qualifikation_label"
        title="Darf hier ueberhaupt etwas stehen? Gerechnet aus Mitgliedschaft,
gelesenen Gruppenregeln und den bisherigen Ausgaengen – nicht gespeichert.">Darf</th>
    <th data-sort="beitrag_status"
        title="Der Beitrag dieser Kampagne in dieser Gruppe. Der Text trägt den
Tracking-Link genau dieser Gruppe – er entsteht aus der Zuordnung, nicht aus
einer Liste im Programm.">Beitrag</th>
    <th class="zahl" data-sort="click">Klicks</th>
    <th class="zahl" data-sort="registration">Registr.</th>
    <th class="zahl" data-sort="download"
        title="Der Bezug der App wurde ausgeloest. Zaehlt je Mensch einmal und
haengt an keiner anderen Stufe: Wer sich registriert und nicht herunterlaedt,
und wer herunterlaedt, ohne sich zu registrieren, sind beide gueltige
Faelle.">Downl.</th>
    <th class="zahl" data-sort="activation"
        title="Die App wurde erstmals geoeffnet - der einzige Beleg dafuer, dass
sie wirklich auf einem Geraet liegt. Nur die App selbst kann ihn liefern.">Aktiv.</th>
    <th class="zahl" data-sort="qualified">qualif.</th>
    <th class="zahl" data-sort="conversion">Absch.</th>
  </tr></thead>
  <tbody id="zeilen"></tbody>
</table>
</div>

<div class="blaettern" id="blaettern" hidden>
  <button type="button" id="s-zurueck">&larr;</button>
  <span id="s-stand"></span>
  <button type="button" id="s-weiter">&rarr;</button>
  <label class="s-groesse">Zeilen
    <select id="s-pro-seite">
      <option value="10">10</option>
      <option value="25" selected>25</option>
      <option value="50">50</option>
      <option value="100">100</option>
      <option value="0">alle</option>
    </select>
  </label>
</div>

<section class="kampagnen-block">
<h2>Kampagnen</h2>
<div class="tabelle-rahmen">
<table>
  <thead><tr>
    <th>Name</th><th>Status</th>
    <th class="zahl">Gruppen</th><th class="zahl">Klicks</th>
    <th class="zahl">Registr.</th><th class="zahl">qualif.</th>
    <th class="zahl">Absch.</th><th class="zahl">Quote</th>
    <th class="aktionen-kopf">Aktionen</th>
  </tr></thead>
  <tbody>{kampagnen_zeilen}</tbody>
</table>
</div>

<details class="neu-kampagne" id="regel-block">
  <summary>Auswahlregel einer Kampagne ändern</summary>
  <p class="erklaerung">
    Die Regel bestimmt, <strong>welche Gruppen</strong> die Kampagne erfasst –
    getrennt davon, <em>wen</em> sie laut Beschreibung bewirbt. Bei jedem Feld
    heißt <strong>leer: keine Einschränkung</strong>; eine Kampagne ohne
    Angaben erfasst also den ganzen Bestand. Speichern vergibt
    <strong>keinen</strong> Code – es rechnet nur vor, was die Regel trifft.
    Die Codes entstehen erst über „Zuordnen“, und dort wird noch einmal gefragt.
  </p>
  <div class="formular">
    <label>Kampagne
      <select id="r-kampagne"></select>
    </label>
    <label>Zielgruppen <span class="zart">(leer = alle, mehrere mit Strg)</span>
      <select id="r-zielgruppen" multiple size="8"></select>
    </label>
    <label>Städte <span class="zart">(leer = alle, mehrere mit Strg)</span>
      <select id="r-staedte" multiple size="8"></select>
    </label>
    <label>Mindestscore <span class="zart">(leer = keiner)</span>
      <input type="number" id="r-minscore" min="0" max="100" step="1" placeholder="z. B. 60">
    </label>
    <div class="breit">
      <label class="schalter">
        <input type="checkbox" id="r-unbewertete">
        auch Gruppen ohne Score – sie bekommen einen Code ohne Zielgruppe und Stadt
      </label>
      <label class="schalter">
        <input type="checkbox" id="r-auto">
        neu gefundene Gruppen automatisch übernehmen (nur bei Status „active“)
      </label>
    </div>
    <div class="breit knopfreihe">
      <button id="r-speichern">Regel speichern</button>
      <span class="zart">
        Ohne Wirkung auf bereits vergebene Codes: Passt eine Gruppe später
        nicht mehr, behält sie ihren Code – er steht möglicherweise in einem
        veröffentlichten Beitrag.
      </span>
    </div>
    <p class="meldung breit" id="r-meldung"></p>
  </div>
</details>

<details class="neu-kampagne">
  <summary>Neue Kampagne anlegen</summary>
  <div class="formular">
    <label>Name
      <input type="text" id="k-name" placeholder="z. B. Batreeq Iraqi Germany">
    </label>
    <label>Kennung <span class="zart">(leer = aus dem Namen)</span>
      <input type="text" id="k-id" placeholder="batreeq-iraqi-germany">
    </label>
    <label>Zielgruppen <span class="zart">(mehrere mit Strg, scrollbar)</span>
      <select id="k-zielgruppen" multiple size="8"></select>
    </label>
    <label>Städte <span class="zart">(leer = alle, mehrere mit Strg)</span>
      <select id="k-staedte" multiple size="8"></select>
    </label>
    <label>Sprache
      <input type="text" id="k-sprache" placeholder="ar | de">
    </label>
    <label>Landingpage
      <input type="text" id="k-landing" placeholder="https://b-tarikak.de/">
    </label>
    <div class="breit knopfreihe">
      <button id="k-anlegen">Anlegen</button>
      <span class="zart">
        Legt einen Entwurf an – <strong>ohne</strong> Tracking-Codes zu vergeben.
        Die Codes kommen erst über „Zuordnen“, und dort wird vorher gerechnet.
      </span>
    </div>
  </div>
</details>

</section>


<footer>
  {fusszeile}
</footer>

<script>
const DATEN = {nutzlast};
const NUR_LESEN = {nur_lesen_js};
const zeilen = DATEN.gruppen;
let sortSpalte = "score", sortAb = true;


// --- Blaettern ------------------------------------------------------------
//
// Bei 314 Zeilen war die Tabelle laenger als jeder Bildschirm: Wer die
// Kampagnenliste darunter erreichen wollte, scrollte an dreihundert Zeilen
// vorbei. Gefiltert und sortiert wird weiterhin ueber den **ganzen** Bestand -
// geblaettert wird erst danach. Andersherum zeigte Seite 1 die ersten
// fuenfundzwanzig Zeilen der Datei statt die fuenfundzwanzig besten.
//
// ``proSeite = 0`` heisst "alle" und ist bewusst moeglich: Wer sucht, will
// manchmal jede Zeile auf einmal sehen.
let seite = 1, proSeite = 25;

// --- Wo war ich? ---------------------------------------------------------
//
// Jede Aenderung an einer Gruppe laedt die Seite neu, und danach stand man
// wieder ganz oben mit zurueckgesetzten Filtern. Bei 314 Zeilen heisst das:
// Stadt neu waehlen, Haken neu setzen, die Zeile wiederfinden - nach jedem
// einzelnen Klick. Gefiltert wird im Browser, also weiss nur der Browser,
// wo man war; sessionStorage haelt es ueber das Neuladen hinweg.
//
// Bewusst sessionStorage und nicht localStorage: Der Stand gehoert zu dieser
// Sitzung. Wer das Fenster morgen neu oeffnet, will die Uebersicht sehen und
// nicht den Filter von gestern.
const MERKER = "fbgroups-uebersicht";
const MERK_FELDER = ["f-stadt", "f-zielgruppe", "f-kategorie", "f-marketing",
                     "f-zielprio", "f-zielregion", "f-beitrag", "f-suche", "f-mitglieder",
                     "f-aktivitaet", "f-konfidenz"];
const MERK_SCHALTER = ["f-bewertet", "f-bearbeitet"];

function standSichern() {{
  try {{
    const stand = {{werte: {{}}, schalter: {{}}, sortSpalte, sortAb,
                   seite, proSeite, y: window.scrollY}};
    MERK_FELDER.forEach((id) => {{
      const el = document.getElementById(id);
      if (el) stand.werte[id] = el.value;
    }});
    MERK_SCHALTER.forEach((id) => {{
      const el = document.getElementById(id);
      if (el) stand.schalter[id] = el.checked;
    }});
    sessionStorage.setItem(MERKER, JSON.stringify(stand));
  }} catch (_) {{
    // Privates Fenster, gesperrter Speicher: dann eben ohne. Ein Merker ist
    // eine Bequemlichkeit und darf die Seite nicht mitreissen.
  }}
}}

function standHolen() {{
  try {{
    const roh = sessionStorage.getItem(MERKER);
    if (!roh) return 0;
    const stand = JSON.parse(roh);
    MERK_FELDER.forEach((id) => {{
      const el = document.getElementById(id);
      if (el && stand.werte && stand.werte[id] !== undefined) el.value = stand.werte[id];
    }});
    MERK_SCHALTER.forEach((id) => {{
      const el = document.getElementById(id);
      if (el && stand.schalter && stand.schalter[id] !== undefined) {{
        el.checked = stand.schalter[id];
      }}
    }});
    if (stand.sortSpalte) {{ sortSpalte = stand.sortSpalte; sortAb = !!stand.sortAb; }}
    // Die Seite gehoert dazu: Wer auf Seite 7 eine Gruppe zuordnet, will
    // danach Seite 7 sehen und nicht wieder Seite 1.
    if (stand.proSeite !== undefined) {{
      proSeite = Number(stand.proSeite);
      const feld = document.getElementById("s-pro-seite");
      if (feld) feld.value = String(proSeite);
    }}
    if (stand.seite) seite = Number(stand.seite);
    return stand.y || 0;
  }} catch (_) {{
    return 0;
  }}
}}

// Auch beim gewoehnlichen Neuladen (F5) und beim Wechsel auf die
// Arbeitsseite: Wer ueber "Arbeiten" weggeht und zurueckkommt, soll seine
// Auswahl wiederfinden.
window.addEventListener("beforeunload", standSichern);
// Die Auswahl ueberlebt das Neuzeichnen: Wer 40 Zeilen angehakt hat und dann
// den Filter aendert, soll sie nicht verlieren.
const gewaehlt = new Set();

const esc = (t) => String(t ?? "").replace(/[&<>"]/g, (c) =>
  ({{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}}[c]));
const zahl = (n) => n === null || n === undefined ? "–" : n.toFixed(1).replace(".", ",");

function fuelleAuswahl(id, werte) {{
  const el = document.getElementById(id);
  [...new Set(werte.filter(Boolean))].sort((a, b) => a.localeCompare(b, "de"))
    .forEach((w) => el.add(new Option(w, w)));
}}
fuelleAuswahl("f-stadt", zeilen.map((z) => z.stadt));
fuelleAuswahl("f-zielgruppe", zeilen.flatMap((z) => z.zielgruppen));
fuelleAuswahl("f-kategorie", zeilen.map((z) => z.kategorie));
fuelleAuswahl("f-marketing", zeilen.map((z) => z.marketing_label));
fuelleAuswahl("f-qualifikation", zeilen.map((z) => z.qualifikation_label));
fuelleSammelKampagnen();

function gefiltert() {{
  const stadt = document.getElementById("f-stadt").value;
  const ziel = document.getElementById("f-zielgruppe").value;
  const kat = document.getElementById("f-kategorie").value;
  const stand = document.getElementById("f-marketing").value;
  const qual = document.getElementById("f-qualifikation").value;
  const zielprio = document.getElementById("f-zielprio").value;
  const zielregion = document.getElementById("f-zielregion").value;
  const suche = document.getElementById("f-suche").value.trim().toLowerCase();
  const nurBewertet = document.getElementById("f-bewertet").checked;
  const nurBearbeitet = document.getElementById("f-bearbeitet").checked;
  const beitrag = document.getElementById("f-beitrag").value;
  const mitglieder = document.getElementById("f-mitglieder").value;
  const aktivitaet = document.getElementById("f-aktivitaet").value;
  const konfidenz = document.getElementById("f-konfidenz").value;

  // "zu-tun" fasst zusammen, wonach man taeglich sucht: was noch aussteht.
  // Uebersprungene gehoeren nicht dazu - dort hat ein Mensch entschieden.
  const passtBeitrag = (z) =>
    !beitrag ||
    (beitrag === "zu-tun"
      ? z.beitrag_status === "offen" || z.beitrag_status === "fehlgeschlagen"
      : z.beitrag_status === beitrag);

  // Die drei Filter der Reichweite. "unbekannt" ist bei allen dreien eine
  // eigene Wahl und kein Randfall: Wer die Mitgliederzahlen nachpflegen
  // will, braucht genau die Liste der Gruppen ohne Zahl - und die ist mit
  // 313 von 313 Eintraegen der haeufigste Fall, nicht der seltenste.
  const passtMitglieder = (z) => {{
    if (!mitglieder) return true;
    if (mitglieder === "ja") return z.mitglieder !== null;
    if (mitglieder === "nein") return z.mitglieder === null;
    return z.mitglieder !== null && z.mitglieder >= Number(mitglieder);
  }};
  // "Gemessen" heisst: irgendeine Quelle hat etwas geliefert - die
  // Beitragsliste, die eigene Resonanz oder ein datierter Suchtreffer.
  // Welche es war, steht im Tooltip; zum Filtern zaehlt nur, ob ueberhaupt.
  const passtAktivitaet = (z) => {{
    if (!aktivitaet) return true;
    // Die Resonanz ist eine gemessene Quelle, steht aber nicht am Datensatz:
    // sie entsteht beim Bewerten aus den Klicks und wird nicht in
    // ``activity_source`` geschrieben. Ohne den dritten Teil galt eine Gruppe
    // mit "Aktivitaet 4.41 (resonanz)" im Score hier als unbekannt.
    const gemessen = z.aktivitaet_quelle !== null || z.posts_pro_tag !== null
                     || ((z.punkte || {{}}).activity || 0) > 0;
    if (aktivitaet === "ja") return gemessen;
    if (aktivitaet === "nein") return !gemessen;
    return (z.punkte || {{}}).activity >= 18;   // von 25
  }};
  const passtKonfidenz = (z) =>
    !konfidenz ||
    (konfidenz === "niedrig" ? z.konfidenz < 0.4 : z.konfidenz >= Number(konfidenz));

  return zeilen.filter((z) =>
    (!nurBearbeitet || z.bearbeiten) &&
    passtBeitrag(z) &&
    passtMitglieder(z) &&
    passtAktivitaet(z) &&
    passtKonfidenz(z) &&
    (!stadt || z.stadt === stadt) &&
    (!ziel || z.zielgruppen.includes(ziel)) &&
    (!kat || z.kategorie === kat) &&
    (!stand || z.marketing_label === stand) &&
    (!qual || z.qualifikation_label === qual) &&
    (!zielprio || z.zielprioritaet === zielprio) &&
    (!zielregion || z.zielregion === zielregion) &&
    (!nurBewertet || z.score !== null) &&
    (!suche || z.name.toLowerCase().includes(suche) ||
               z.beschreibung.toLowerCase().includes(suche))
  );
}}

function sortiert(liste) {{
  return [...liste].sort((a, b) => {{
    let x = a[sortSpalte], y = b[sortSpalte];
    if (Array.isArray(x)) {{ x = x.join(); y = y.join(); }}
    // Nicht bewertbare Datensaetze stehen immer hinten - sie sind kein
    // schlechtes Ergebnis, sondern ein offener Punkt.
    if (x === null) return 1;
    if (y === null) return -1;
    const v = typeof x === "number" ? x - y : String(x).localeCompare(String(y), "de");
    return sortAb ? -v : v;
  }});
}}

// Die Filter, die gerade etwas ausblenden - mit dem Wort, das im Feld steht.
// "4 von 621" sagt, DASS etwas fehlt, und verschweigt, warum: Wer einen Stand
// gewaehlt hat und danach eine bearbeitete Gruppe sucht, haelt sie fuer
// verschwunden. Die Zeile nennt deshalb den Grund und bietet den Weg zurueck.
function aktiveFilter() {{
  const felder = [
    ["f-stadt", "Stadt"], ["f-zielgruppe", "Zielgruppe"], ["f-kategorie", "Kategorie"],
    ["f-zielprio", "Ziel"], ["f-zielregion", "Land"], ["f-marketing", "Stand"],
    ["f-qualifikation", "Darf"], ["f-beitrag", "Beitrag"], ["f-mitglieder", "Groesse"],
    ["f-aktivitaet", "Aktivitaet"], ["f-konfidenz", "Datenqualitaet"],
  ];
  const aktiv = [];
  for (const [id, name] of felder) {{
    const feld = document.getElementById(id);
    if (feld && feld.value) {{
      const text = feld.options[feld.selectedIndex].textContent.trim();
      aktiv.push(name + ": " + text);
    }}
  }}
  const suche = document.getElementById("f-suche").value.trim();
  if (suche) aktiv.push('Suche: "' + suche + '"');
  if (document.getElementById("f-bewertet").checked) aktiv.push("nur bewertete");
  if (document.getElementById("f-bearbeitet").checked) aktiv.push("nur bearbeitete");
  return aktiv;
}}

function filterZuruecksetzen() {{
  for (const id of ["f-stadt", "f-zielgruppe", "f-kategorie", "f-zielprio",
                    "f-zielregion", "f-marketing", "f-qualifikation", "f-beitrag",
                    "f-mitglieder", "f-aktivitaet", "f-konfidenz"]) {{
    const feld = document.getElementById(id);
    if (feld) feld.value = "";
  }}
  document.getElementById("f-suche").value = "";
  document.getElementById("f-bewertet").checked = false;
  document.getElementById("f-bearbeitet").checked = false;
  seite = 1;
  merkeStand();
  zeichne();
}}

function zeichne() {{
  const alle = sortiert(gefiltert());
  const aktiv = aktiveFilter();
  const treffer = document.getElementById("treffer");
  treffer.textContent = alle.length + " von " + zeilen.length;
  const hinweis = document.getElementById("treffer-grund");
  if (hinweis) {{
    const versteckt = zeilen.length - alle.length;
    hinweis.innerHTML = versteckt > 0
      ? esc(versteckt + " ausgeblendet durch " + aktiv.join(" · ") + " ")
        + '<button type="button" id="filter-weg" class="mini">Filter zurücksetzen</button>'
      : "";
    const knopf = document.getElementById("filter-weg");
    if (knopf) knopf.onclick = filterZuruecksetzen;
  }}

  const seiten = proSeite > 0 ? Math.max(1, Math.ceil(alle.length / proSeite)) : 1;
  if (seite > seiten) seite = seiten;
  if (seite < 1) seite = 1;
  const liste = proSeite > 0
    ? alle.slice((seite - 1) * proSeite, seite * proSeite)
    : alle;
  zeichneBlaetterleiste(alle.length, seiten);

  document.getElementById("zeilen").innerHTML = alle.length === 0
    ? "<tr><td colspan='15' class='leer'>Keine Gruppe passt zu diesem Filter.</td></tr>"
    : liste.map((z) => {{
        const klasse = z.score === null ? "keine" : z.score >= 90 ? "hoch"
                     : z.score >= 70 ? "mittel" : "";
        const codes = z.codes.length
          ? " " + z.codes.map((c) => "<code>" + esc(c) + "</code>").join(" ") : "";
        const ausGrund = z.bearbeiten || !z.ausschlussgrund ? ""
          : `<span class="aus-grund">ausgeschlossen: ${{esc(z.ausschlussgrund)}}</span>`;
        return `<tr class="${{z.bearbeiten ? "" : "ausgeschlossen"}}">
          <td class="auswahl">
            <input type="checkbox" class="waehlen" data-id="${{esc(z.id)}}"
                   ${{gewaehlt.has(z.id) ? "checked" : ""}}>
          </td>
          <td class="zahl scorezelle" title="${{esc(punkteText(z))}}">
            <span class="punkte ${{klasse}}">${{zahl(z.score)}}</span>
            <span class="von">/ ${{zahl(z.score_max) || "–"}}</span>
            ${{scoreBalken(z)}}
          </td>
          <td class="name" dir="auto">
            <a href="${{esc(z.url)}}" target="_blank"
               rel="noopener noreferrer">${{esc(z.name)}}</a>${{codes}}${{ausGrund}}
          </td>
          <td>${{esc(z.stadt) || "–"}}</td>
          <td>${{esc(z.zielgruppen.join(", ")) || "–"}}</td>
          <td>${{esc(z.kategorie) || "–"}}</td>
          <td>${{zielZelle(z)}}</td>
          <td class="kampagnen-zelle">${{kampagnenZelle(z)}}</td>
          <td>${{standZelle(z)}}</td>
          <td>${{qualZelle(z)}}</td>
          <td>${{beitragZelle(z)}}</td>
          <td class="zahl">${{z.click}}</td>
          <td class="zahl">${{z.registration}}</td>
          <td class="zahl">${{z.download}}</td>
          <td class="zahl">${{z.activation}}</td>
          <td class="zahl">${{z.qualified}}</td>
          <td class="zahl">${{z.conversion}}</td>
        </tr>`;
      }}).join("");
}}

function zeichneBlaetterleiste(gesamt, seiten) {{
  const leiste = document.getElementById("blaettern");
  if (!leiste) return;
  // Passt alles auf eine Seite, gehoert dort auch keine Leiste hin - sie
  // waere ein Bedienelement ohne Wirkung.
  leiste.hidden = proSeite > 0 && gesamt <= proSeite;
  const von = gesamt === 0 ? 0 : (seite - 1) * (proSeite || gesamt) + 1;
  const bis = proSeite > 0 ? Math.min(gesamt, seite * proSeite) : gesamt;
  document.getElementById("s-stand").innerHTML =
    "Zeile <b>" + von + "–" + bis + "</b> von " + gesamt
    + " &middot; Seite <b>" + seite + "</b> von " + seiten;
  document.getElementById("s-zurueck").disabled = seite <= 1;
  document.getElementById("s-weiter").disabled = seite >= seiten;
}}

document.getElementById("s-zurueck").addEventListener("click", () => {{
  seite -= 1; zeichne(); window.scrollTo({{top: 0, behavior: "smooth"}});
}});
document.getElementById("s-weiter").addEventListener("click", () => {{
  seite += 1; zeichne(); window.scrollTo({{top: 0, behavior: "smooth"}});
}});
document.getElementById("s-pro-seite").addEventListener("change", (e) => {{
  proSeite = Number(e.target.value);
  seite = 1;
  zeichne();
}});

// --- Gemessene Resonanz ------------------------------------------------

// Klartext fuer die Score-Bestandteile. Dieselben Namen wie scoring._LABELS -
// eine zweite Liste liefe beim naechsten neuen Bestandteil auseinander, und
// die Zeile zeigte dann etwas anderes als der Export.
// Die fuenf Bestandteile mit Zeichen, Beschriftung und Hoechstpunktzahl.
// Die Hoechstwerte stehen hier und nicht im Programm: "22" allein ist keine
// Auskunft, "22/25" ist eine.
const BESTANDTEILE = [
  ["members",         "👥", "Mitglieder", 25],
  ["activity",        "🔥", "Aktivität",  25],
  ["location",        "📍", "Stadt",      15],
  ["category",        "🏷", "Kategorie",  20],
  ["target_audience", "🎯", "Zielgruppe", 15],
  ["name_quality",    "✍",  "Name",        0],
];
const PUNKT_LABEL = Object.fromEntries(BESTANDTEILE.map(([n, , l]) => [n, l]));
const PUNKT_MAX   = Object.fromEntries(BESTANDTEILE.map(([n, , , m]) => [n, m]));
const PUNKT_ICON  = Object.fromEntries(BESTANDTEILE.map(([n, i]) => [n, i]));

function kampagnenZelle(z) {{
  // Zwei verschiedene Aussagen, und sie duerfen nicht gleich aussehen:
  //
  //   Marke  = die Gruppe ist dieser Kampagne zugeordnet und hat ihren Code.
  //   Vorschlag = die Auswahlregel der Kampagne erfasst sie, ein Code steht
  //               aber noch aus.
  //
  // Die zweite beantwortet "wohin gehoert diese Gruppe?", die erste nur "wo
  // steht sie schon?". Ohne die Unterscheidung muesste man die Regel jeder
  // Kampagne im Kopf nachrechnen.
  const marken = (z.beitraege || []).map((b) =>
    `<span class="k-marke" title="Zugeordnet - Code ${{esc(b.code)}}">`
    + `${{esc(b.kampagne_name)}}</span>`
  ).join("");

  const passend = z.passt_zu || [];
  if (NUR_LESEN) {{
    // Im Lesezugang wird nichts vergeben, also kein Knopf - der Hinweis, dass
    // etwas aussteht, bleibt aber sichtbar.
    const offen = passend.map((k) =>
      `<span class="k-offen" title="Passt zur Regel, noch nicht zugeordnet">`
      + `${{esc(k.name)}}</span>`).join("");
    return marken + offen || '<span class="zart">–</span>';
  }}

  // Vorschlaege zuerst: Sie sind das, wonach man sucht. Ein Klick ordnet zu.
  const vorschlaege = passend.map((k) =>
    `<button type="button" class="k-vorschlag" data-gruppe="${{esc(z.id)}}"`
    + ` data-kampagne="${{esc(k.id)}}" data-name="${{esc(k.name)}}"`
    + ` title="Passt zur Auswahlregel - klicken vergibt den Tracking-Code">`
    + `+ ${{esc(k.name)}}</button>`).join("");

  // Das Auswahlfeld nennt JEDE Kampagne, in der die Gruppe noch nicht steht -
  // auch die vorgeschlagenen.
  //
  // Vorher blieben die Vorschlaege ausgespart, weil daneben schon ein Knopf
  // fuer sie stand. Das Feld verschwand damit genau dann, wenn es nur eine
  // Kampagne gibt und die Gruppe ihr bereits zugeordnet ist: Die Spalte zeigte
  // nur noch eine Marke, und die Frage "wohin gehoert diese Gruppe?" hatte in
  // der Spalte, die es dafuer gibt, keine Antwortmoeglichkeit mehr. Der Knopf
  // ist die Abkuerzung, das Feld die vollstaendige Liste - dass eine Kampagne
  // in beiden steht, ist kein Widerspruch.
  //
  // Entfernt wird hier nie: Zuordnen vergibt einen Tracking-Code, und der
  // steht spaeter in veroeffentlichten Beitraegen. Deshalb nennt das Feld nur
  // Kampagnen, die noch dazukommen KOENNEN, und heisst "+".
  const drin = new Set((z.beitraege || []).map((b) => b.kampagne));
  const waehlbar = (DATEN.kampagnen || []).filter((k) => !drin.has(k.id));

  const auswahl = waehlbar.length
    ? '<select class="k-zuordnen" data-gruppe="' + esc(z.id) + '"'
      + ' title="Gruppe einer Kampagne zuordnen - vergibt einen Tracking-Code">'
      + '<option value="">+ Kampagne …</option>'
      + waehlbar.map((k) => `<option value="${{esc(k.id)}}">${{esc(k.name)}}</option>`).join("")
      + '</select>'
    : "";

  return (marken + vorschlaege + auswahl) || '<span class="zart">–</span>';
}}

function scoreBalken(z) {{
  // Fuenf schmale Balken statt fuenf Zahlen: Bei 25 sichtbaren Zeilen sind
  // 125 Zahlen unlesbar, 125 Balkenlaengen nicht. Die Zahlen selbst stehen
  // im Tooltip - die Frage "warum 84?" stellt man einmal je Gruppe, nicht
  // beim Ueberfliegen.
  //
  // Ein Bestandteil OHNE Grundlage bekommt keinen leeren Balken, sondern gar
  // keinen: Ein leerer Balken liest sich wie "geprueft und null", und genau
  // diese Verwechslung soll die Anzeige nicht erzeugen.
  if (z.score === null) return "";
  const teile = BESTANDTEILE.filter(([name, , , max]) =>
    max > 0 && (z.punkte || {{}})[name] !== undefined && istBeurteilt(z, name));
  if (!teile.length) return "";
  return '<span class="balken">' + teile.map(([name, , label, max]) => {{
    const wert = (z.punkte || {{}})[name] || 0;
    const anteil = Math.round((wert / max) * 100);
    return `<i class="b-${{name}}" style="--anteil:${{anteil}}%"
              title="${{label}} ${{zahl(wert)}}/${{max}}"></i>`;
  }}).join("") + "</span>";
}}

function istBeurteilt(z, name) {{
  // "Bewertet mit 0" und "nicht bewertbar" sind zwei Zustaende, und die
  // Aufschluesselung kennt nur Zahlen. Unterscheiden laesst es sich an der
  // Begruendung: Was dort als "unbekannt" steht, hatte keine Grundlage.
  const label = PUNKT_LABEL[name] || name;
  return !(z.grund || "").includes(label + " unbekannt");
}}

function punkteText(z) {{
  // Die Aufschluesselung als Tooltip statt als Liste in der Zeile.
  //
  // Sie beantwortet eine Frage, die man einmal je Gruppe stellt ("warum 92?"),
  // stand aber dauerhaft in jeder der 300 Zeilen und machte die Tabelle
  // dreimal so hoch. Beim Zeigen ist sie da, beim Ueberfliegen im Weg -
  // deshalb wird sie versteckt und nicht entfernt.
  //
  // Nur die Bestandteile, die tatsaechlich Punkte gebracht haben. Ein
  // abgeschalteter oder unbekannter Bestandteil steht nicht mit "0" da - das
  // liesse sich als "geprueft und wertlos" missverstehen.
  const zeilen = BESTANDTEILE
    .filter(([name, , , max]) => max > 0 && istBeurteilt(z, name))
    .map(([name, icon, label, max]) =>
      `${{icon}} ${{label}}: ${{zahl((z.punkte || {{}})[name] || 0)}} / ${{max}}`);

  // Die Belege darunter: Wer "Mitglieder 22/25" liest, will als naechstes
  // wissen, aus welcher Zahl das entstanden ist und woher sie stammt.
  const belege = [];
  if (z.mitglieder !== null && z.mitglieder !== undefined) {{
    belege.push(`👥 ${{z.mitglieder.toLocaleString("de-DE")}} Mitglieder`
      + (z.mitglieder_quelle ? ` (${{z.mitglieder_quelle}})` : ""));
  }}
  if (z.posts_pro_tag !== null && z.posts_pro_tag !== undefined) {{
    belege.push(`🔥 ca. ${{zahl(z.posts_pro_tag)}} Beiträge/Tag`
      + (z.aktivitaet_quelle ? ` (${{z.aktivitaet_quelle}})` : ""));
  }} else if (z.aktivitaet_quelle) {{
    belege.push(`🔥 Aktivität aus ${{z.aktivitaet_quelle}}`);
  }}
  if (z.konfidenz) belege.push(`Datenqualität ${{Math.round(z.konfidenz * 100)}} %`);

  if (belege.length) zeilen.push("", ...belege);
  if (z.grund) zeilen.push("", z.grund);
  return zeilen.join("\\n");
}}

// --- Beitrag je Gruppe -------------------------------------------------
// Der fertige Text steht schon im Dokument (siehe sammle_daten): Bei 300
// Gruppen waeren es sonst 300 Anfragen an den Dienst, nur um dreimal etwas
// zu kopieren.

const BEITRAG_LABEL = {{
  offen: "offen",
  veroeffentlicht: "veröffentlicht",
  fehlgeschlagen: "fehlgeschlagen",
  uebersprungen: "übersprungen",
}};

// Die Spalte zeigt den Stand und sonst nichts. Sie hatte einmal einen Knopf
// "Text", der den fertigen Beitrag in die Zwischenablage legte - und damit
// denselben Ablauf ein zweites Mal anbot, nur schmaler und ohne die Merkmale
// der Gruppe. Zwei Wege zum selben Beitrag heissen zwei Zaehlweisen;
// gearbeitet wird unter /arbeit/{{kampagne}}.
// Die Spalte STAND - zwei Werte, nicht zehn.
//
// Von Hand gesetzt werden nur die beiden, um die es geht: Beitrittsanfrage
// oder nicht. Die uebrigen Staende der Aufzaehlung (Mitglied, abgelehnt,
// Zusammenarbeit ...) bleiben im Modell und in den Bestandsdaten - entfernt
// waeren aeltere Datensaetze nicht mehr ladbar, und "Mitglied" setzt die
// Automatik ohnehin selbst, wenn sie das Schreibfeld sieht.
//
// Steht ein solcher Wert an der Gruppe, erscheint er als **Text** statt als
// Auswahlfeld. Das ist kein Schmuck: Ein Feld mit zwei Optionen, in dem
// "Mitglied" gar nicht vorkommt, wuerde bei der naechsten Beruehrung auf den
// ersten Eintrag zurueckfallen - und damit eine erreichte Mitgliedschaft
// stillschweigend loeschen.
const STAND_VON_HAND = ["not_contacted", "beitritt_angefragt"];

// Die Farbe sagt, was der Text sagt - sie ersetzt ihn nicht. Wer nur die
// Farbe liest, liest "gut/schlecht"; die Stufe selbst steht daneben, und der
// Grund haengt als Titel daran.
const QUAL_FARBE = {{
  geeignet: "#16a34a",
  bewertung: "#ca8a04",
  ohne_links: "#ca8a04",
  ohne_kommentare: "#ca8a04",
  ohne_beitraege: "#ca8a04",
  beitritt_noetig: "#6b7280",
  beitritt_angefragt: "#6b7280",
  unbekannt: "#6b7280",
  ungeeignet: "#dc2626",
}};

const ZIEL_FARBE = {{
  a: "#16a34a",
  b: "#2563eb",
  c: "#ca8a04",
  d: "#9ca3af",
}};

// Das Land in zwei Zeichen. Nur bei den Gruppen, bei denen es die
// Reihenfolge aendert - "unbekannt" ist der Regelfall und bekommt deshalb
// kein Zeichen: Ein Vermerk, der in zwei Dritteln der Zeilen steht, sagt
// nichts mehr.
const REGION_KURZ = {{de: "DE", eu: "EU", ausserhalb: "✗"}};

function zielZelle(z) {{
  // Nur der Buchstabe, mit der Begruendung im Titel: Die Spalte steht in
  // jeder der 314 Zeilen, und "A - Reise & Versand" waere dort breiter als
  // der Gruppenname. Wer den Grund braucht, faehrt darueber.
  const kurz = (z.zielprioritaet || "").toUpperCase() || "–";
  const farbe = ZIEL_FARBE[z.zielprioritaet] || "#9ca3af";
  const land = REGION_KURZ[z.zielregion] || "";
  const titel = [z.zielprioritaet_label, z.zielregion_label,
                 z.zielprioritaet_grund, z.facebook_antwort]
    .filter(Boolean).join(" · ");
  return `<span style="color:${{farbe}};font-weight:600" title="${{esc(titel)}}">`
    + esc(kurz) + `</span>`
    + (land ? `<span style="color:#6b7280;font-size:11px"> ${{esc(land)}}</span>` : "");
}}

function qualZelle(z) {{
  const farbe = QUAL_FARBE[z.qualifikation] || "#6b7280";
  const titel = [z.qualifikation_grund, z.regeln].filter(Boolean).join(" · ");
  return `<span style="color:${{farbe}}" title="${{esc(titel)}}">`
    + esc(z.qualifikation_label) + `</span>`;
}}

function standZelle(z) {{
  if (NUR_LESEN) return esc(z.marketing_label);
  if (!STAND_VON_HAND.includes(z.marketing)) {{
    return `<span class="stand-fest" title="Wird von der Automatik gefuehrt">`
      + esc(z.marketing_label) + `</span>`;
  }}
  return `<select class="stand" data-id="${{esc(z.id)}}">`
    + STAND_VON_HAND.map((wert) => {{
        // **Dieselben Woerter wie im Filter** (14.09.2026). Hier standen
        // zwei eigene Beschriftungen fest im Programm, waehrend das
        // Filterfeld darueber dieselben beiden Werte anders nannte - zwei
        // Namen fuer ein Feld. Wer den Text der Spalte im Filter suchte,
        // fand ihn nicht, griff zum naechstbesten Eintrag, und die Gruppe
        // war weg: Es sah aus, als waere sie aus der Uebersicht
        // verschwunden. ``DATEN.staende`` kommt aus ``status_label`` -
        // derselben Quelle, aus der auch das Filterfeld gefuellt wird.
        const eintrag = DATEN.staende.find((x) => x.wert === wert);
        const label = eintrag ? eintrag.label : wert;
        return `<option value="${{wert}}"${{wert === z.marketing ? " selected" : ""}}>`
          + esc(label) + "</option>";
      }}).join("")
    + `</select>`;
}}

function beitragZelle(z) {{
  if (!z.beitraege.length) {{
    return '<span class="b-leer">kein Tracking-Link</span>';
  }}
  return '<div class="beitrag">' + z.beitraege.map((b) => {{
    const fehler = b.fehler
      ? `<span class="b-fehler" title="${{esc(b.fehler)}}">${{esc(b.fehler)}}</span>` : "";
    const wem = esc(b.kampagne_name) + " – " + esc(b.code);
    return `<div class="b-zeile">
      <span class="b-marke b-${{b.status}}" title="${{wem}}">
        ${{BEITRAG_LABEL[b.status] || b.status}}</span>
      ${{fehler}}
    </div>`;
  }}).join("") + '</div>';
}}

// --- Kampagnen ---------------------------------------------------------
// Anlegen und Zuordnen sind getrennt: Ein Tracking-Code ist endgueltig, er
// steht spaeter in veroeffentlichten Beitraegen. Deshalb rechnet "Zuordnen"
// erst und fragt dann - dieselbe Rechnung, die danach ausgefuehrt wird.
(function fuelleAuswahl() {{
  const ziel = document.getElementById("k-zielgruppen");
  const stadt = document.getElementById("k-staedte");
  if (!ziel || !DATEN.auswahl) return;
  DATEN.auswahl.zielgruppen.forEach((a) => {{
    const o = document.createElement("option");
    o.value = a.id; o.textContent = a.label + "  (" + a.id + ")";
    ziel.appendChild(o);
  }});
  DATEN.auswahl.staedte.forEach((c) => {{
    const o = document.createElement("option");
    o.value = c.id; o.textContent = c.label;
    stadt.appendChild(o);
  }});
}})();

function gewaehlteWerte(id) {{
  return [...document.getElementById(id).selectedOptions].map((o) => o.value);
}}

document.getElementById("k-anlegen")?.addEventListener("click", async (e) => {{
  const knopf = e.target;
  const name = document.getElementById("k-name").value.trim();
  if (!name) {{ alert("Die Kampagne braucht einen Namen."); return; }}

  knopf.disabled = true;
  try {{
    const antwort = await fetch("/kampagnen", {{
      method: "POST",
      headers: {{"Content-Type": "application/json"}},
      body: JSON.stringify({{
        name: name,
        campaign_id: document.getElementById("k-id").value.trim(),
        audiences: gewaehlteWerte("k-zielgruppen"),
        cities: gewaehlteWerte("k-staedte"),
        language: document.getElementById("k-sprache").value.trim(),
        landing_page: document.getElementById("k-landing").value.trim(),
        // Eigene Textvorlage und Kommentar-Haken stehen hier nicht mehr:
        // Die Vorlage galt fuer ALLE Gruppen der Kampagne und war der
        // haeufigste Griff daneben, und die Textarten sind in der
        // Kampagnenzeile umschaltbar. Beides bleibt erreichbar - ueber
        // "campaign set <kampagne> --vorlage ..." und den Haken in der Zeile.
      }}),
    }});
    const ergebnis = await antwort.json();
    if (!antwort.ok) throw new Error(ergebnis.detail || "HTTP " + antwort.status);
    alert("Angelegt: " + ergebnis.campaign_id + " (Entwurf, noch ohne Codes).");
    standSichern();
    location.reload();
  }} catch (fehler) {{
    alert("Konnte die Kampagne nicht anlegen: " + fehler.message);
  }} finally {{
    knopf.disabled = false;
  }}
}});

function meldung(id, text, gut) {{
  const el = document.getElementById(id);
  el.textContent = text;
  el.className = "meldung breit" + (gut ? " gut" : " schlecht");
}}

// --- Auswahlregel einer Kampagne --------------------------------------
// Die Regel steht in der Kampagne (target_*), nicht in der Beschreibung
// (audiences/cities): Die eine sagt, welche Gruppen einen Code bekommen, die
// andere, wen die Kampagne bewirbt. Gespeichert wird der vollstaendige Stand
// der beiden Listen - leer heisst dabei "keine Einschraenkung".
function kampagneNach(id) {{
  return DATEN.kampagnen.find((k) => k.id === id) || null;
}}

function auswahlSetzen(feldId, werte) {{
  const feld = document.getElementById(feldId);
  const gewaehlt = new Set(werte || []);
  [...feld.options].forEach((o) => {{ o.selected = gewaehlt.has(o.value); }});
}}

function regelAnzeigen(id) {{
  const kampagne = kampagneNach(id);
  if (!kampagne) return;
  document.getElementById("r-kampagne").value = id;
  auswahlSetzen("r-zielgruppen", kampagne.regel.audiences);
  auswahlSetzen("r-staedte", kampagne.regel.cities);
  document.getElementById("r-minscore").value =
    kampagne.regel.min_score === null ? "" : kampagne.regel.min_score;
  document.getElementById("r-unbewertete").checked = kampagne.regel.include_unscored;
  document.getElementById("r-auto").checked = kampagne.regel.auto_assign;
  meldung("r-meldung", "Gilt jetzt: " + kampagne.regel.kurz
    + " · trifft " + kampagne.regel.passend + " von " + kampagne.regel.bestand
    + " Gruppen.", true);
}}

(function regelFuellen() {{
  const wahl = document.getElementById("r-kampagne");
  if (!wahl || !DATEN.auswahl) return;
  DATEN.kampagnen.forEach((k) => wahl.add(new Option(k.name + "  (" + k.id + ")", k.id)));
  DATEN.auswahl.zielgruppen.forEach((a) =>
    document.getElementById("r-zielgruppen").add(new Option(a.label + "  (" + a.id + ")", a.id)));
  DATEN.auswahl.staedte.forEach((c) =>
    document.getElementById("r-staedte").add(new Option(c.label, c.id)));
  if (DATEN.kampagnen.length) regelAnzeigen(DATEN.kampagnen[0].id);
}})();

document.getElementById("r-kampagne")?.addEventListener("change", (e) =>
  regelAnzeigen(e.target.value));

document.getElementById("r-speichern")?.addEventListener("click", async (e) => {{
  const knopf = e.target;
  const id = document.getElementById("r-kampagne").value;
  if (!id) {{ meldung("r-meldung", "Keine Kampagne gewählt.", false); return; }}
  const score = document.getElementById("r-minscore").value.trim();

  knopf.disabled = true;
  try {{
    const antwort = await fetch("/kampagnen/" + encodeURIComponent(id) + "/auswahl", {{
      method: "POST",
      headers: {{"Content-Type": "application/json"}},
      body: JSON.stringify({{
        audiences: gewaehlteWerte("r-zielgruppen"),
        cities: gewaehlteWerte("r-staedte"),
        // -1 hebt den Mindestscore auf - dieselbe Vereinbarung wie auf der
        // Kommandozeile. Ein leeres Feld heisst "kein Mindestscore", nicht
        // "unveraendert lassen".
        min_score: score === "" ? -1 : Number(score),
        include_unscored: document.getElementById("r-unbewertete").checked,
        auto_assign: document.getElementById("r-auto").checked,
      }}),
    }});
    const ergebnis = await antwort.json();
    if (!antwort.ok) throw new Error(ergebnis.detail || "HTTP " + antwort.status);

    const kampagne = kampagneNach(id);
    if (kampagne) {{
      kampagne.regel = Object.assign({{}}, kampagne.regel, ergebnis.regel, {{
        beschreibung: ergebnis.beschreibung,
        kurz: ergebnis.kurz,
        passend: ergebnis.passend,
        bestand: ergebnis.bestand,
      }});
    }}
    const zeile = document.querySelector('.regel-text[data-id="' + id + '"]');
    if (zeile) {{
      zeile.textContent = "trifft " + ergebnis.passend + " von " + ergebnis.bestand
        + " · " + ergebnis.kurz
        + (ergebnis.auto_assign ? " · nimmt neue Funde automatisch auf" : "");
      zeile.title = ergebnis.beschreibung;
    }}
    meldung("r-meldung", ["Gespeichert: " + ergebnis.kurz + ".",
      "Trifft " + ergebnis.passend + " Gruppen –",
      ergebnis.bereits_zugeordnet + " davon haben schon einen Code,",
      ergebnis.neu + " bekämen bei „Zuordnen“ einen neuen.",
      ergebnis.nicht_mehr_passend
        ? ergebnis.nicht_mehr_passend + " zugeordnete Gruppen passen nicht mehr zur Regel – "
          + "ihr Code bleibt gültig."
        : ""].join(" "), true);
  }} catch (fehler) {{
    meldung("r-meldung", "Nicht gespeichert: " + fehler.message, false);
  }} finally {{
    knopf.disabled = false;
  }}
}});

document.addEventListener("click", (ereignis) => {{
  const knopf = ereignis.target;
  if (!knopf.classList.contains("k-regel")) return;
  const block = document.getElementById("regel-block");
  block.open = true;
  regelAnzeigen(knopf.dataset.id);
  block.scrollIntoView({{behavior: "smooth", block: "nearest"}});
}});

document.addEventListener("change", async (ereignis) => {{
  const feld = ereignis.target;
  if (!feld.classList.contains("k-status")) return;
  const vorher = feld.dataset.vorher || "";
  feld.disabled = true;
  try {{
    const antwort = await fetch("/kampagnen/" + encodeURIComponent(feld.dataset.id) + "/status", {{
      method: "POST",
      headers: {{"Content-Type": "application/json"}},
      body: JSON.stringify({{status: feld.value}}),
    }});
    if (!antwort.ok) throw new Error("HTTP " + antwort.status);
    feld.dataset.vorher = feld.value;
  }} catch (fehler) {{
    if (vorher) feld.value = vorher;
    alert("Konnte den Status nicht setzen: " + fehler.message);
  }} finally {{
    feld.disabled = false;
  }}
}});

document.addEventListener("click", async (ereignis) => {{
  const knopf = ereignis.target;
  if (!knopf.classList.contains("k-sync")) return;
  const id = knopf.dataset.id;
  knopf.disabled = true;
  try {{
    const plan = await (await fetch("/kampagnen/" + encodeURIComponent(id) + "/sync", {{
      method: "POST",
      headers: {{"Content-Type": "application/json"}},
      body: JSON.stringify({{dry_run: true}}),
    }})).json();

    if (!plan.neu) {{
      alert("Nichts zu tun: " + plan.bereits_zugeordnet + " Gruppen sind bereits zugeordnet.");
      return;
    }}
    const frage = plan.neu + " Gruppen bekommen einen neuen Tracking-Code.\\n"
      + "Bereits zugeordnet: " + plan.bereits_zugeordnet + "\\n"
      + (plan.beispiele.length ? "Beispiele: " + plan.beispiele.join(", ") + "\\n" : "")
      + "\\nEin vergebener Code wird nie zurueckgenommen - er steht spaeter in "
      + "veroeffentlichten Beitraegen. Ausfuehren?";
    if (!confirm(frage)) return;

    const echt = await (await fetch("/kampagnen/" + encodeURIComponent(id) + "/sync", {{
      method: "POST",
      headers: {{"Content-Type": "application/json"}},
      body: JSON.stringify({{dry_run: false}}),
    }})).json();
    alert(echt.neu + " Zuordnungen angelegt.");
    standSichern();
    location.reload();
  }} catch (fehler) {{
    alert("Zuordnen fehlgeschlagen: " + fehler.message);
  }} finally {{
    knopf.disabled = false;
  }}
}});

document.addEventListener("click", async (ereignis) => {{
  const knopf = ereignis.target;
  if (!knopf.classList.contains("k-weg")) return;
  const id = knopf.dataset.id;
  knopf.disabled = true;
  try {{
    // Erst fragen, was verlorenginge - ohne etwas zu aendern. Derselbe Weg
    // beantwortet beides; eine zweite Zaehlung koennte abweichen, und der
    // Mensch bestaetigte dann eine Zahl und bekaeme eine andere.
    const vorschau = await (await fetch(
      "/kampagnen/" + encodeURIComponent(id) + "/loeschen", {{
        method: "POST",
        headers: {{"Content-Type": "application/json"}},
        body: JSON.stringify({{bestaetigt: false}}),
      }})).json();

    let frage = 'Kampagne "' + (knopf.dataset.name || id) + '" loeschen?\\n\\n'
      + vorschau.zuordnungen + " Zuordnungen samt Tracking-Codes\\n"
      + vorschau.versuche + " Versuche\\n"
      + vorschau.ereignisse_bleiben + " Ereignisse bleiben erhalten\\n";
    if (vorschau.veroeffentlichte_codes) {{
      // Der einzige Teil, der sich nicht wiederherstellen laesst: Diese Codes
      // stehen in Beitraegen, die wirklich abgesetzt wurden.
      frage += "\\nACHTUNG: " + vorschau.veroeffentlichte_codes
        + " dieser Codes stehen in veroeffentlichten Facebook-Beitraegen.\\n"
        + "Ein Klick darauf fuehrt danach ins Leere (404), und den Beitrag\\n"
        + "kann niemand mehr zurueckholen.\\n";
    }}
    frage += "\\nDas laesst sich nicht rueckgaengig machen. Wirklich loeschen?";
    if (!confirm(frage)) return;

    await fetch("/kampagnen/" + encodeURIComponent(id) + "/loeschen", {{
      method: "POST",
      headers: {{"Content-Type": "application/json"}},
      body: JSON.stringify({{bestaetigt: true}}),
    }});
    standSichern();
    location.reload();
  }} catch (fehler) {{
    alert("Loeschen fehlgeschlagen: " + fehler.message);
  }} finally {{
    knopf.disabled = false;
  }}
}});

document.addEventListener("click", async (ereignis) => {{
  const knopf = ereignis.target;
  if (!knopf.classList.contains("k-vorschlag")) return;
  await zuordnen(knopf.dataset.gruppe, knopf.dataset.kampagne, knopf.dataset.name, knopf);
}});

document.addEventListener("change", async (ereignis) => {{
  const feld = ereignis.target;
  if (!feld.classList.contains("k-zuordnen")) return;
  const kampagne = feld.value;
  if (!kampagne) return;
  const name = feld.options[feld.selectedIndex].textContent;
  feld.value = "";
  await zuordnen(feld.dataset.gruppe, kampagne, name, feld);
}});

async function zuordnen(gruppe, kampagne, name, element) {{

  // Ein Tracking-Code wird nie zurueckgenommen - er steht spaeter in
  // veroeffentlichten Beitraegen. Deshalb wird gefragt, obwohl es nur eine
  // Gruppe ist: Der Unterschied zwischen "eine" und "vierhundert" ist die
  // Menge, nicht die Endgueltigkeit.
  if (!confirm('Gruppe der Kampagne "' + name + '" zuordnen?\\n\\n'
      + "Sie bekommt dabei einen eigenen Tracking-Code. Ein vergebener Code "
      + "wird nie zurueckgenommen.")) return;

  element.disabled = true;
  try {{
    const antwort = await fetch(
      "/gruppen/" + encodeURIComponent(gruppe) + "/kampagne", {{
        method: "POST",
        headers: {{"Content-Type": "application/json"}},
        body: JSON.stringify({{campaign_id: kampagne}}),
      }});
    const daten = await antwort.json();
    if (!antwort.ok) {{
      alert(daten.detail || ("Fehler " + antwort.status));
      return;
    }}
    standSichern();
    location.reload();
  }} catch (fehler) {{
    alert("Zuordnen fehlgeschlagen: " + fehler.message);
  }} finally {{
    element.disabled = false;
  }}
}}

function sammelLeiste() {{
  const leiste = document.getElementById("sammel");
  leiste.hidden = gewaehlt.size === 0;
  document.getElementById("sammel-anzahl").textContent =
    gewaehlt.size + (gewaehlt.size === 1 ? " Gruppe gewählt" : " Gruppen gewählt");
}}

document.getElementById("zeilen").addEventListener("change", (ereignis) => {{
  const feld = ereignis.target;
  if (!feld.classList.contains("waehlen")) return;
  if (feld.checked) gewaehlt.add(feld.dataset.id);
  else gewaehlt.delete(feld.dataset.id);
  sammelLeiste();
}});

document.getElementById("alle").addEventListener("change", (ereignis) => {{
  // Nur die sichtbaren: Ein Haken darf nicht 400 Gruppen erfassen, von denen
  // gerade 12 auf dem Bildschirm stehen.
  const sichtbar = gefiltert();
  sichtbar.forEach((z) => ereignis.target.checked
    ? gewaehlt.add(z.id) : gewaehlt.delete(z.id));
  zeichne();
  sammelLeiste();
}});

async function bearbeitenSetzen(wert) {{
  const ids = [...gewaehlt];
  if (!ids.length) return;
  const grund = document.getElementById("sammel-grund").value.trim();
  const knoepfe = document.querySelectorAll("#sammel button");
  knoepfe.forEach((k) => k.disabled = true);
  try {{
    const antwort = await fetch("/bearbeiten", {{
      method: "POST",
      headers: {{"Content-Type": "application/json"}},
      body: JSON.stringify({{group_ids: ids, bearbeiten: wert, grund: grund}}),
    }});
    if (!antwort.ok) throw new Error("HTTP " + antwort.status);
    await antwort.json();
    // Erst nach der Bestaetigung des Servers: Sonst stuende auf dem Bildschirm
    // etwas anderes als in der Datenbank, und du wuerdest danach handeln.
    zeilen.filter((z) => gewaehlt.has(z.id)).forEach((z) => {{
      z.bearbeiten = wert;
      z.ausschlussgrund = wert ? "" : grund;
    }});
    gewaehlt.clear();
    document.getElementById("sammel-grund").value = "";
    document.getElementById("alle").checked = false;
    zeichne();
    sammelLeiste();
  }} catch (fehler) {{
    alert("Konnte die Auswahl nicht speichern: " + fehler.message);
  }} finally {{
    knoepfe.forEach((k) => k.disabled = false);
  }}
}}

// Die Kampagnenliste der Sammelleiste - einmal beim Start gefuellt.
//
// Sie zeigt **alle** Kampagnen, anders als das Feld in der Kampagnenspalte:
// Dort geht es um eine Gruppe, und was schon zugeordnet ist, waere dort ein
// Angebot ohne Wirkung. Hier stehen viele Gruppen mit verschiedenen Staenden
// hinter der Auswahl - der Server ueberspringt die bereits zugeordneten und
// sagt hinterher, wie viele es waren.
function fuelleSammelKampagnen() {{
  const feld = document.getElementById("sammel-kampagne");
  feld.innerHTML = '<option value="">Kampagne wählen …</option>'
    + (DATEN.kampagnen || [])
        .map((k) => `<option value="${{esc(k.id)}}">${{esc(k.name)}}</option>`)
        .join("");
}}

async function sammelZuordnen() {{
  const feld = document.getElementById("sammel-kampagne");
  const kampagne = feld.value;
  const ids = [...gewaehlt];
  if (!ids.length) return;
  if (!kampagne) {{
    alert("Erst eine Kampagne wählen.");
    feld.focus();
    return;
  }}
  const name = feld.options[feld.selectedIndex].textContent;

  // Gefragt wird mit der **Zahl**, nicht nur mit dem Namen: Ein Tracking-Code
  // wird nie zurueckgenommen, und wie viele Codes gleich entstehen, ist die
  // Angabe, die man vorher gegenlesen will.
  if (!confirm(ids.length + (ids.length === 1 ? " Gruppe" : " Gruppen")
      + ' der Kampagne "' + name + '" zuordnen?\\n\\n'
      + "Jede bekommt einen eigenen Tracking-Code. Ein vergebener Code wird "
      + "nie zurückgenommen. Bereits zugeordnete bleiben unverändert.")) return;

  const knoepfe = document.querySelectorAll("#sammel button");
  knoepfe.forEach((k) => k.disabled = true);
  try {{
    const antwort = await fetch(
      "/kampagnen/" + encodeURIComponent(kampagne) + "/gruppen", {{
        method: "POST",
        headers: {{"Content-Type": "application/json"}},
        body: JSON.stringify({{group_ids: ids}}),
      }});
    const daten = await antwort.json();
    if (!antwort.ok) {{
      alert(daten.detail || ("Fehler " + antwort.status));
      return;
    }}
    // Was uebersprungen wurde, gehoert in die Meldung: "12 gewählt, 3 neu"
    // ohne den Rest liest sich wie ein halber Fehlschlag.
    let text = daten.neu + (daten.neu === 1 ? " Gruppe" : " Gruppen") + " zugeordnet.";
    if (daten.schon_zugeordnet) text += " " + daten.schon_zugeordnet + " waren es schon.";
    if ((daten.unbekannt || []).length) text += " Unbekannt: " + daten.unbekannt.join(", ");
    alert(text);
    standSichern();
    location.reload();
  }} catch (fehler) {{
    alert("Zuordnen fehlgeschlagen: " + fehler.message);
  }} finally {{
    knoepfe.forEach((k) => k.disabled = false);
  }}
}}

document.getElementById("sammel-zuordnen").addEventListener("click", sammelZuordnen);

document.getElementById("sammel-aus")
  .addEventListener("click", () => bearbeitenSetzen(false));
document.getElementById("sammel-ein")
  .addEventListener("click", () => bearbeitenSetzen(true));

// Der Stand kommt nur von dir: Facebook meldet nicht, dass du eine
// Beitrittsanfrage gestellt hast. Ein Klick schreibt ihn ueber denselben Weg
// wie "fbgroups marketing set" - dieselbe Tabelle, dieselben Werte.
document.getElementById("zeilen").addEventListener("change", async (ereignis) => {{
  const feld = ereignis.target;
  if (!feld.classList.contains("stand")) return;

  const zeile = zeilen.find((z) => z.id === feld.dataset.id);
  const vorher = zeile.marketing;
  feld.disabled = true;
  try {{
    const antwort = await fetch("/stand", {{
      method: "POST",
      headers: {{"Content-Type": "application/json"}},
      body: JSON.stringify({{group_id: feld.dataset.id, status: feld.value}}),
    }});
    if (!antwort.ok) throw new Error("HTTP " + antwort.status);
    const ergebnis = await antwort.json();
    zeile.marketing = ergebnis.status;
    zeile.marketing_label = ergebnis.label;
    feld.classList.add("gespeichert");
    setTimeout(() => feld.classList.remove("gespeichert"), 1200);
  }} catch (fehler) {{
    // Nicht stillschweigend weitermachen: Sonst stuende auf dem Bildschirm
    // etwas anderes als in der Datenbank, und du wuerdest danach handeln.
    feld.value = vorher;
    feld.classList.add("fehler");
    setTimeout(() => feld.classList.remove("fehler"), 2500);
    alert("Konnte den Stand nicht speichern: " + fehler.message);
  }} finally {{
    feld.disabled = false;
  }}
}});

// Ein neuer Filter faengt auf Seite 1 an: Seite 7 eines anderen Ergebnisses
// ist keine sinnvolle Fortsetzung.
const filterGeaendert = () => {{ seite = 1; zeichne(); }};
document.querySelectorAll(".filter select, .filter input")
  .forEach((el) => el.addEventListener("input", filterGeaendert));
document.getElementById("f-beitrag").addEventListener("change", filterGeaendert);
document.querySelectorAll("th[data-sort]").forEach((th) =>
  th.addEventListener("click", () => {{
    const spalte = th.dataset.sort;
    sortAb = spalte === sortSpalte ? !sortAb : true;
    sortSpalte = spalte;
    zeichne();
  }}));

// Erst die Auswahlfelder fuellen (weiter oben), dann den gemerkten Stand
// setzen, dann zeichnen - in dieser Reihenfolge, sonst steht der gemerkte
// Wert in einem Feld, das seine Optionen noch nicht hat.
const merkY = standHolen();
zeichne();
// Nach dem Zeichnen: vorher ist die Tabelle leer und die Seite zu kurz zum
// Scrollen. requestAnimationFrame wartet auf das fertige Layout.
if (merkY) requestAnimationFrame(() => window.scrollTo(0, merkY));
</script>
</body>
</html>"""
