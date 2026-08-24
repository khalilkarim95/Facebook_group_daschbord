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
from fbgroups.marketing.analytics import funnel, kennzahlen
from fbgroups.marketing.beitrag import beitragstext
from fbgroups.marketing.models import CampaignStatus, MarketingStatus
from fbgroups.marketing.resonanz import resonanz_je_gruppe
from fbgroups.marketing.selection import Auswahl, auswahl_der_kampagne, passt
from fbgroups.marketing.store import MarketingStore
from fbgroups.models import Group
from fbgroups.scoring import Resonanz
from fbgroups.storage.sqlite_store import SqliteStore

# Klartext fuer die Statusnamen. Die englischen Kennungen stehen in der
# Datenbank; auf der Seite haben sie nichts verloren.
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


def _gruppe_als_zeile(
    group: Group,
    config: AppConfig,
    marketing_status: str,
    codes: list[str],
    anfragen: int,
    ereignisse: dict[str, int] | None = None,
    bearbeiten: bool = True,
    ausschlussgrund: str = "",
    beitraege: list[dict[str, Any]] | None = None,
    resonanz: Resonanz | None = None,
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
        "stadt": group.city or "",
        "zielgruppen": zielgruppen,
        "kategorie": kategorie,
        "status": group.status.value,
        "marketing": marketing_status,
        "marketing_label": _STATUS_LABEL.get(marketing_status, marketing_status),
        # Eigene Achse neben dem Kooperationsweg: "wo stehen wir?" und
        # "arbeiten wir ueberhaupt daran?" sind zwei Fragen. Siehe
        # GroupMarketing.bearbeiten.
        "bearbeiten": bearbeiten,
        "ausschlussgrund": ausschlussgrund,
        "codes": codes,
        # Je Zuordnung ein Eintrag: Kampagne, Code, fertiger Text, Stand des
        # Beitrags. Der Text entsteht in beitragstext() - derselben Stelle, die
        # auch 'campaign message' und 'campaign next' benutzen. Zwei Fassungen
        # koennten abweichen, und der Unterschied fiele erst auf, wenn ein
        # Beitrag mit dem falschen Code in einer Gruppe steht.
        "beitraege": beitraege or [],
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
        "mitglieder": group.member_count_hint,
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
                    "kampagne": campaign_id,
                    "kampagne_name": campaign.name if campaign else campaign_id,
                    "code": link.tracking_code,
                    "url": link.tracking_url,
                    "status": link.post_status.value,
                    "fehler": link.post_error,
                    "versuche": link.post_attempts,
                    # Der fertige Text zum Kopieren. Er steht im Dokument,
                    # damit der Knopf ohne zweiten Aufruf auskommt - bei 300
                    # Gruppen waeren das sonst 300 Anfragen an den Dienst,
                    # nur um dreimal etwas zu kopieren.
                    "text": beitragstext(campaign, link) if campaign else "",
                }
            )

    # counts_by liefert {(schluessel, event_type): anzahl} - hier einmal nach
    # Gruppe umgedreht, statt fuer jede der 310 Zeilen erneut zu suchen.
    ereignisse_je_gruppe: dict[str, dict[str, int]] = {}
    for (group_id, event_type), anzahl in klicks_je_gruppe.items():
        ereignisse_je_gruppe.setdefault(group_id, {})[event_type] = anzahl

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
        )
        for g in groups
    ]

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
        # Stand der KI. Billig und ungefaehrlich: Bei Ollama wird nur
        # nachgesehen, welche Modelle dort liegen (2 s Zeitgrenze), bei
        # Anthropic ueberhaupt nichts abgerufen. Es wird NIE etwas erzeugt -
        # eine Seite, die bei jedem Aufruf ein Modell anwirft, waere bei
        # Ollama langsam und bei Anthropic teuer.
        "ki": _ki_stand(config),
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

def _ki_stand(config: AppConfig) -> dict[str, Any]:
    """Der KI-Stand fuer die Anzeige - faellt nie aus.

    Der Import steht in der Funktion: ``marketing.ki`` zieht ``httpx`` und die
    Anbieter nach, und die Uebersicht soll auch dann bauen, wenn an der
    KI-Schicht gerade etwas fehlt. Sie ist ein Aufsatz, keine Voraussetzung.
    """
    try:
        from fbgroups.marketing.ki import gewaehlter_anbieter
        from fbgroups.marketing.ki import status as ki_status

        stand = ki_status(config)
        return {
            "anbieter": gewaehlter_anbieter(config),
            "erreichbar": stand.erreichbar,
            "modell": stand.modell,
            "adresse": stand.adresse,
            "meldung": stand.meldung,
            "modell_vorhanden": stand.modell_vorhanden,
            "modelle": stand.verfuegbare_modelle,
        }
    except Exception as exc:  # noqa: BLE001 - die Seite darf an nichts sterben
        return {
            "anbieter": "unbekannt",
            "erreichbar": False,
            "modell": "",
            "adresse": "",
            "meldung": f"KI-Stand nicht ermittelbar: {type(exc).__name__}: {exc}",
            "modell_vorhanden": False,
            "modelle": [],
        }


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
            _kachel(str(k["tracking_links"]), "Tracking-Links"),
            _kachel(str(k["beitraege_veroeffentlicht"]), "Beiträge"),
            _kachel(str(k["beitraege_offen"]), "offen"),
            _kachel(str(k["clicks"]), "Klicks"),
            _kachel(str(k["registrations"]), "Registrierungen"),
            _kachel(str(k["downloads"]), "Downloads"),
            _kachel(str(k["qualified"]), "qualifiziert"),
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

    ki = daten.get("ki", {})
    if ki.get("erreichbar") and ki.get("modell_vorhanden"):
        ki_ampel, ki_text = "🟢", "Verbunden"
    elif ki.get("erreichbar"):
        ki_ampel, ki_text = "🟡", "Verbunden, Modell fehlt"
    else:
        ki_ampel, ki_text = "🔴", "Nicht erreichbar"

    ki_hinweis = (
        f'<div class="ki-hinweis">{html.escape(ki.get("meldung", ""))}</div>'
        if ki.get("meldung")
        else ""
    )
    # Der Testknopf erzeugt wirklich etwas und gehoert deshalb zu den
    # schreibenden Wegen: Von aussen (nur_lesen) wird er ausgeblendet, und der
    # Weg selbst prueft ohnehin selbst - siehe web._nur_lokal.
    ki_knopf = (
        ""
        if nur_lesen
        else '<button id="ki-test" class="knopf">Ollama testen</button>'
        '<span id="ki-test-ergebnis" class="ki-ergebnis"></span>'
    )
    ki_block = (
        '<div class="ki-karte">'
        f'<div class="ki-kopf">{ki_ampel} <b>KI</b> '
        f'<span class="res-leise">{html.escape(ki.get("anbieter", ""))}'
        + (" (lokal)" if ki.get("anbieter") == "ollama" else "")
        + "</span></div>"
        f'<div class="ki-zeile">Status: {html.escape(ki_text)}</div>'
        f'<div class="ki-zeile">Modell: <code>{html.escape(ki.get("modell") or "-")}</code></div>'
        f'<div class="ki-zeile">URL: <code>{html.escape(ki.get("adresse") or "-")}</code></div>'
        f"{ki_hinweis}{ki_knopf}"
        "</div>"
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
        f"</span></td>"
        f"<td><code>{html.escape(c['id'])}</code></td>"
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
    --b-offen-bg: #fef3c7;  --b-offen-fg: #92400e;
    --b-gut-bg: #dcfce7;    --b-gut-fg: #166534;
    --b-fehler-bg: #fee2e2; --b-fehler-fg: #991b1b;
    --b-aus-bg: #e5e7eb;    --b-aus-fg: #4b5563;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #16181d; --karte: #1e2127; --text: #e8eaed; --leise: #9aa1ab;
      --rand: #2c313a; --akzent: #60a5fa; --gut: #4ade80; --mittel: #fbbf24;
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
  .ki-karte {{ border: 1px solid var(--linie); border-radius: 8px; padding: 12px 14px;
               margin-bottom: 18px; background: var(--flaeche); max-width: 560px; }}
  .ki-kopf {{ font-size: 15px; margin-bottom: 6px; }}
  .ki-zeile {{ font-size: 13px; color: var(--text-leise); }}
  .ki-hinweis {{ font-size: 12.5px; white-space: pre-wrap; margin: 8px 0;
                 padding: 8px 10px; border-radius: 6px; background: var(--sunk, #f4f4f2);
                 border-left: 3px solid var(--warn, #8a5a10); }}
  .ki-ergebnis {{ font-size: 13px; margin-left: 10px; }}
  .kachel {{
    background: var(--karte); border: 1px solid var(--rand); border-radius: 10px;
    padding: 12px 18px; min-width: 108px;
  }}
  .kachel b {{ display: block; font-size: 22px; }}
  .kachel span {{ color: var(--leise); font-size: 12px; }}
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
  .b-knopf {{
    cursor: pointer; border: 1px solid var(--rand); background: var(--karte);
    border-radius: 5px; padding: 2px 7px; font-size: 12px; line-height: 1.5;
  }}
  .b-knopf:hover {{ background: var(--bg); }}
  .b-fehler {{ font-size: 11px; color: var(--b-fehler-fg); }}
  .b-leer {{ color: var(--leise); font-size: 12px; }}
  /* Gemessene Resonanz */
  .res {{ font-size: 12px; line-height: 1.45; white-space: nowrap; }}
  .res b {{ font-size: 13px; }}
  .res-leise {{ color: var(--leise); }}
  .punkte-liste {{
    margin: 4px 0 0; padding: 0; list-style: none;
    font-size: 11px; color: var(--leise);
  }}
  .punkte-liste li {{ display: flex; gap: 6px; justify-content: space-between; }}
  .punkte-liste .wert {{ font-variant-numeric: tabular-nums; }}
  /* Nur-Lesen: alles weg, was einen schreibenden Weg ruft. */
  body.nur-lesen .sammel,
  body.nur-lesen th.auswahl, body.nur-lesen td.auswahl,
  body.nur-lesen .knopfzelle, body.nur-lesen .k-status,
  body.nur-lesen .neu-kampagne,
  body.nur-lesen .b-fertig, body.nur-lesen .b-fehlschlag {{ display: none; }}
</style>
</head>
<body{koerper_klasse}>
<h1>Batraqiq – Gruppenübersicht</h1>
<p class="hinweis">
  {hinweis}
  Stand: {html.escape(daten["erzeugt_am"])}
</p>

{warnung}
{ki_block}
<div class="kacheln">{kacheln}</div>

<div class="filter">
  <select id="f-stadt"><option value="">Alle Städte</option></select>
  <select id="f-zielgruppe"><option value="">Alle Zielgruppen</option></select>
  <select id="f-kategorie"><option value="">Alle Kategorien</option></select>
  <select id="f-marketing"><option value="">Jeder Stand</option></select>
  <select id="f-beitrag">
    <option value="">Jeder Beitrag</option>
    <option value="zu-tun">nur zu erledigen</option>
    <option value="offen">offen</option>
    <option value="fehlgeschlagen">fehlgeschlagen</option>
    <option value="veroeffentlicht">veröffentlicht</option>
    <option value="uebersprungen">übersprungen</option>
  </select>
  <input type="search" id="f-suche" placeholder="Name durchsuchen – auch arabisch …">
  <label class="schalter"><input type="checkbox" id="f-bewertet" checked> nur bewertete</label>
  <label class="schalter"><input type="checkbox" id="f-bearbeitet" checked> nur bearbeitete</label>
  <span class="marke" id="treffer"></span>
</div>

<div class="sammel" id="sammel" hidden>
  <strong id="sammel-anzahl"></strong>
  <input type="text" id="sammel-grund" placeholder="Grund für den Ausschluss (optional)">
  <button id="sammel-aus">Ausschließen</button>
  <button id="sammel-ein">Wieder aufnehmen</button>
  <span class="hinweis">Der Tracking-Code bleibt in jedem Fall gültig.</span>
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
    <th data-sort="marketing_label">Stand</th>
    <th data-sort="beitrag_status"
        title="Der Beitrag dieser Kampagne in dieser Gruppe. Der Text trägt den
Tracking-Link genau dieser Gruppe – er entsteht aus der Zuordnung, nicht aus
einer Liste im Programm.">Beitrag</th>
    <th class="zahl" data-sort="anfragen"
        title="Von wie vielen verschiedenen Suchanfragen diese Gruppe gefunden
wurde. Höchstens 16 (9 Stadtmuster + 7 bundesweite) – für jede Stadt gleich,
also untereinander vergleichbar.">Anfragen</th>
    <th data-sort="resonanz_quote"
        title="Gemessene Resonanz: was der Beitrag in dieser Gruppe gebracht hat.
Mitgliederzahl und Beitragszahlen der Gruppe stehen nur auf facebook.com – die
Meta Groups API wurde am 22.04.2024 abgeschaltet. Diese Zahlen erheben wir
selbst und sie beantworten dieselbe Frage genauer.">Resonanz</th>
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

<div class="spalten">
<section>
<h2>Kampagnen</h2>
<div class="tabelle-rahmen">
<table>
  <thead><tr>
    <th>Name</th><th>Kennung</th><th>Status</th>
    <th class="zahl">Gruppen</th><th class="zahl">Klicks</th>
    <th class="zahl">Registr.</th><th class="zahl">qualif.</th>
    <th class="zahl">Absch.</th><th class="zahl">Quote</th><th></th>
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
    <label class="breit">Textvorlage <span class="zart">– {{link}} wird ersetzt</span>
      <textarea id="k-vorlage" rows="2" placeholder="مرحبا! {{link}}"></textarea>
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

<section>
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

<footer>
  {fusszeile}
</footer>

<script>
const DATEN = {nutzlast};
const NUR_LESEN = {nur_lesen_js};
const zeilen = DATEN.gruppen;
let sortSpalte = "score", sortAb = true;
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

function gefiltert() {{
  const stadt = document.getElementById("f-stadt").value;
  const ziel = document.getElementById("f-zielgruppe").value;
  const kat = document.getElementById("f-kategorie").value;
  const stand = document.getElementById("f-marketing").value;
  const suche = document.getElementById("f-suche").value.trim().toLowerCase();
  const nurBewertet = document.getElementById("f-bewertet").checked;
  const nurBearbeitet = document.getElementById("f-bearbeitet").checked;
  const beitrag = document.getElementById("f-beitrag").value;

  // "zu-tun" fasst zusammen, wonach man taeglich sucht: was noch aussteht.
  // Uebersprungene gehoeren nicht dazu - dort hat ein Mensch entschieden.
  const passtBeitrag = (z) =>
    !beitrag ||
    (beitrag === "zu-tun"
      ? z.beitrag_status === "offen" || z.beitrag_status === "fehlgeschlagen"
      : z.beitrag_status === beitrag);

  return zeilen.filter((z) =>
    (!nurBearbeitet || z.bearbeiten) &&
    passtBeitrag(z) &&
    (!stadt || z.stadt === stadt) &&
    (!ziel || z.zielgruppen.includes(ziel)) &&
    (!kat || z.kategorie === kat) &&
    (!stand || z.marketing_label === stand) &&
    (!nurBewertet || z.score !== null) &&
    (!suche || z.name.toLowerCase().includes(suche) ||
               z.beschreibung.toLowerCase().includes(suche))
  );
}}

function sortiert(liste) {{
  return [...liste].sort((a, b) => {{
    // Die Resonanzquote liegt eine Ebene tiefer; ohne Beitrag ist sie null
    // und landet damit hinten - wie jeder andere unbekannte Wert auch.
    const hol = (z) => sortSpalte === "resonanz_quote"
      ? (z.resonanz ? z.resonanz.quote : null) : z[sortSpalte];
    let x = hol(a), y = hol(b);
    if (Array.isArray(x)) {{ x = x.join(); y = y.join(); }}
    // Nicht bewertbare Datensaetze stehen immer hinten - sie sind kein
    // schlechtes Ergebnis, sondern ein offener Punkt.
    if (x === null) return 1;
    if (y === null) return -1;
    const v = typeof x === "number" ? x - y : String(x).localeCompare(String(y), "de");
    return sortAb ? -v : v;
  }});
}}

function zeichne() {{
  const liste = sortiert(gefiltert());
  document.getElementById("treffer").textContent =
    liste.length + " von " + zeilen.length;

  document.getElementById("zeilen").innerHTML = liste.length === 0
    ? "<tr><td colspan='14' class='leer'>Keine Gruppe passt zu diesem Filter.</td></tr>"
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
          <td class="zahl">
            <span class="punkte ${{klasse}}" title="${{esc(punkteText(z))}}"
                  >${{zahl(z.score)}}</span>
          </td>
          <td class="name" dir="auto">
            <a href="${{esc(z.url)}}" target="_blank"
               rel="noopener noreferrer">${{esc(z.name)}}</a>${{codes}}${{ausGrund}}
          </td>
          <td>${{esc(z.stadt) || "–"}}</td>
          <td>${{esc(z.zielgruppen.join(", ")) || "–"}}</td>
          <td>${{esc(z.kategorie) || "–"}}</td>
          <td>${{NUR_LESEN
            ? esc(z.marketing_label)
            : `<select class="stand" data-id="${{esc(z.id)}}">`
              + DATEN.staende.map((s) =>
                  `<option value="${{s.wert}}"${{s.wert === z.marketing ? " selected" : ""}}>`
                  + esc(s.label) + "</option>").join("")
              + `</select>`}}</td>
          <td>${{beitragZelle(z)}}</td>
          <td class="zahl">${{z.anfragen}}</td>
          <td>${{resonanzZelle(z)}}</td>
          <td class="zahl">${{z.click}}</td>
          <td class="zahl">${{z.registration}}</td>
          <td class="zahl">${{z.download}}</td>
          <td class="zahl">${{z.activation}}</td>
          <td class="zahl">${{z.qualified}}</td>
          <td class="zahl">${{z.conversion}}</td>
        </tr>`;
      }}).join("");
}}

// --- Gemessene Resonanz ------------------------------------------------

// Klartext fuer die Score-Bestandteile. Dieselben Namen wie scoring._LABELS -
// eine zweite Liste liefe beim naechsten neuen Bestandteil auseinander, und
// die Zeile zeigte dann etwas anderes als der Export.
const PUNKT_LABEL = {{
  audience_match: "Zielgruppe",
  city_match: "Stadt",
  category_match: "Kategorie",
  member_count: "Mitglieder",
  name_quality: "Name",
  resonanz_engagement: "Resonanz",
  resonanz_reichweite: "Reichweite",
  resonanz_aktualitaet: "Aktualität",
}};

function seit(iso) {{
  if (!iso) return null;
  const tage = (Date.now() - new Date(iso).getTime()) / 86400000;
  if (tage < 1 / 24) return "gerade eben";
  if (tage < 1) return `vor ${{Math.round(tage * 24)}} Std.`;
  if (tage < 60) return `vor ${{Math.round(tage)}} Tagen`;
  return `vor ${{Math.round(tage / 30)}} Monaten`;
}}

function resonanzZelle(z) {{
  const r = z.resonanz;
  if (!r) {{
    // Kein veroeffentlichter Beitrag: Null Klicks sagen hier nichts ueber die
    // Gruppe aus, sondern nur ueber uns. Deshalb "–" und nicht "0 %".
    return '<span class="b-leer">noch nicht gemessen</span>';
  }}
  const regung = seit(r.letzte_regung);
  return `<div class="res">
    <b>${{r.quote === null ? "–" : r.quote.toLocaleString("de-DE") + " %"}}</b>
    <span class="res-leise">${{r.registrierungen}}/${{r.klicks}}</span>
    <div class="res-leise">${{r.beitraege}} Beitr. ·
      ${{regung ? "letzte Regung " + regung : "keine Regung"}}</div>
  </div>`;
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
  const teile = Object.entries(z.punkte || {{}}).filter(([, wert]) => wert > 0);
  const zeilen = teile.map(([name, wert]) =>
    `${{PUNKT_LABEL[name] || name}}: ${{zahl(wert)}}`);
  if (z.grund) zeilen.push(z.grund);
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

function beitragZelle(z) {{
  if (!z.beitraege.length) {{
    return '<span class="b-leer">kein Tracking-Link</span>';
  }}
  return '<div class="beitrag">' + z.beitraege.map((b, i) => {{
    const erledigt = b.status === "veroeffentlicht";
    const fehler = b.fehler
      ? `<span class="b-fehler" title="${{esc(b.fehler)}}">${{esc(b.fehler)}}</span>` : "";
    return `<div class="b-zeile" data-gruppe="${{esc(z.id)}}"
                 data-kampagne="${{esc(b.kampagne)}}" data-i="${{i}}">
      <span class="b-marke b-${{b.status}}" title="${{esc(b.kampagne_name)}}">
        ${{BEITRAG_LABEL[b.status] || b.status}}</span>
      <button class="b-knopf b-kopieren" title="Text mit ${{esc(b.code)}} kopieren">Text</button>
      <button class="b-knopf b-oeffnen" title="Gruppe im Browser öffnen">Gruppe</button>
      ${{erledigt ? "" : '<button class="b-knopf b-fertig" title="Beitrag steht">✓</button>'}}
      ${{erledigt ? "" : '<button class="b-knopf b-fehlschlag" title="Ging nicht">✕</button>'}}
      ${{fehler}}
    </div>`;
  }}).join("") + '</div>';
}}

// Ein Klick auf einen der vier Knoepfe. Delegiert, weil die Zeilen bei jedem
// Filterwechsel neu entstehen - einzeln gebundene Handler waeren nach dem
// ersten Tastendruck im Suchfeld verloren.
document.getElementById("zeilen").addEventListener("click", async (ereignis) => {{
  const knopf = ereignis.target.closest(".b-knopf");
  if (!knopf) return;
  const zeile = knopf.closest(".b-zeile");
  const gruppe = DATEN.gruppen.find((g) => g.id === zeile.dataset.gruppe);
  if (!gruppe) return;
  const beitrag = gruppe.beitraege[Number(zeile.dataset.i)];

  if (knopf.classList.contains("b-kopieren")) {{
    try {{
      await navigator.clipboard.writeText(beitrag.text);
      knopf.textContent = "kopiert";
      setTimeout(() => {{ knopf.textContent = "Text"; }}, 1200);
    }} catch (err) {{
      // Ohne sicheren Kontext verweigert der Browser die Zwischenablage.
      // Dann bleibt der Text sichtbar statt zu verschwinden.
      window.prompt("Text kopieren:", beitrag.text);
    }}
    return;
  }}

  if (knopf.classList.contains("b-oeffnen")) {{
    window.open(gruppe.url, "_blank", "noopener");
    return;
  }}

  const fehlschlag = knopf.classList.contains("b-fehlschlag");
  let grund = "";
  if (fehlschlag) {{
    grund = window.prompt("Warum ging es nicht?", beitrag.fehler || "") || "";
  }}
  const antwort = await fetch("/beitrag", {{
    method: "POST",
    headers: {{ "Content-Type": "application/json" }},
    body: JSON.stringify({{
      campaign_id: beitrag.kampagne,
      group_id: gruppe.id,
      status: fehlschlag ? "fehlgeschlagen" : "veroeffentlicht",
      grund: grund,
    }}),
  }});
  if (!antwort.ok) {{ alert("Nicht gespeichert (" + antwort.status + ")."); return; }}
  const ergebnis = await antwort.json();
  beitrag.status = ergebnis.status;
  beitrag.fehler = ergebnis.fehler;
  beitrag.versuche = ergebnis.versuche;
  gruppe.beitrag_status = ergebnis.gesamtstand;
  zeichne();
}});

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

// Der Testknopf erzeugt wirklich einen Satz - deshalb nur auf Klick und nie
// beim Laden der Seite. Bei einem lokalen Modell dauert das je nach Karte ein
// paar Sekunden; ohne Rueckmeldung sieht das aus, als waere nichts passiert.
document.getElementById("ki-test")?.addEventListener("click", async (e) => {{
  const knopf = e.target;
  const ziel = document.getElementById("ki-test-ergebnis");
  knopf.disabled = true;
  ziel.textContent = "läuft …";
  try {{
    const antwort = await fetch("/ki/test", {{ method: "POST" }});
    const daten = await antwort.json();
    ziel.textContent = daten.ok ? "✅ " + daten.text : "❌ " + daten.text;
  }} catch (fehler) {{
    ziel.textContent = "❌ " + fehler;
  }} finally {{
    knopf.disabled = false;
  }}
}});

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
        message_template: document.getElementById("k-vorlage").value,
      }}),
    }});
    const ergebnis = await antwort.json();
    if (!antwort.ok) throw new Error(ergebnis.detail || "HTTP " + antwort.status);
    alert("Angelegt: " + ergebnis.campaign_id + " (Entwurf, noch ohne Codes).");
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
      + vorschau.entwuerfe + " Entwuerfe, " + vorschau.versuche + " Versuche\\n"
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
    location.reload();
  }} catch (fehler) {{
    alert("Loeschen fehlgeschlagen: " + fehler.message);
  }} finally {{
    knopf.disabled = false;
  }}
}});

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

document.querySelectorAll(".filter select, .filter input")
  .forEach((el) => el.addEventListener("input", zeichne));
document.getElementById("f-beitrag").addEventListener("change", zeichne);
document.querySelectorAll("th[data-sort]").forEach((th) =>
  th.addEventListener("click", () => {{
    const spalte = th.dataset.sort;
    sortAb = spalte === sortSpalte ? !sortAb : true;
    sortSpalte = spalte;
    zeichne();
  }}));

zeichne();
</script>
</body>
</html>"""
