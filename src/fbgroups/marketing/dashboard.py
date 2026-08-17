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
from fbgroups.marketing.analytics import kennzahlen
from fbgroups.marketing.models import MarketingStatus
from fbgroups.marketing.store import MarketingStore
from fbgroups.models import Group
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


def _gruppe_als_zeile(
    group: Group,
    config: AppConfig,
    marketing_status: str,
    codes: list[str],
    anfragen: int,
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
        "codes": codes,
        # Verschiedene Anfragen, die diese Gruppe gefunden haben - nicht
        # times_seen. Siehe SqliteStore.count_distinct_sources: times_seen
        # waechst mit jedem Lauf weiter und misst damit das Alter des
        # Datensatzes statt der Auffindbarkeit der Gruppe.
        "anfragen": anfragen,
        "mitglieder": group.member_count_hint,
        "beschreibung": group.description_snippet or "",
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
        links = {c.campaign_id: mstore.links_for_campaign(c.campaign_id) for c in campaigns}

    codes_je_gruppe: dict[str, list[str]] = {}
    for campaign_links in links.values():
        for link in campaign_links:
            codes_je_gruppe.setdefault(link.group_id, []).append(link.tracking_code)

    zeilen = [
        _gruppe_als_zeile(
            g,
            config,
            marketing[g.group_id].marketing_status.value
            if g.group_id in marketing
            else "not_contacted",
            codes_je_gruppe.get(g.group_id, []),
            anfragen_je_gruppe.get(g.group_id, 0),
        )
        for g in groups
    ]

    bewertet = [z for z in zeilen if z["score"] is not None]
    schnitt = round(sum(z["score"] for z in bewertet) / len(bewertet), 1) if bewertet else None

    kampagnen = [
        {
            "id": c.campaign_id,
            "name": c.name,
            "status": c.status.value,
            "gruppen": len(links.get(c.campaign_id, [])),
            "klicks": klicks_je_kampagne.get((c.campaign_id, "click"), 0),
            "registrierungen": klicks_je_kampagne.get((c.campaign_id, "registration"), 0),
        }
        for c in campaigns
    ]

    return {
        "gruppen": zeilen,
        "kampagnen": kampagnen,
        "kennzahlen": {
            "gesamt": len(zeilen),
            "bewertet": len(bewertet),
            "schnitt": schnitt,
            "bestwert": max((z["score"] for z in bewertet), default=None),
            **zahlen,
        },
        "erzeugt_am": datetime.now().strftime("%d.%m.%Y %H:%M"),
    }


def _kachel(wert: str, label: str) -> str:
    return f'<div class="kachel"><b>{html.escape(wert)}</b><span>{html.escape(label)}</span></div>'


def render(daten: dict[str, Any]) -> str:
    """Baut die vollstaendige Seite.

    Die Daten stehen als JSON im Dokument; gefiltert und sortiert wird im
    Browser. ``</`` wird dabei maskiert - ohne das beendete ein Gruppenname mit
    dieser Zeichenfolge das Skript und die Seite bliebe leer.
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
            _kachel(str(k["clicks"]), "Klicks"),
            _kachel(str(k["registrations"]), "Registrierungen"),
        ]
    )

    kampagnen_zeilen = "".join(
        f"<tr><td>{html.escape(c['name'])}</td>"
        f"<td><code>{html.escape(c['id'])}</code></td>"
        f"<td>{html.escape(c['status'])}</td>"
        f"<td class='zahl'>{c['gruppen']}</td>"
        f"<td class='zahl'>{c['klicks']}</td>"
        f"<td class='zahl'>{c['registrierungen']}</td></tr>"
        for c in daten["kampagnen"]
    ) or "<tr><td colspan='6' class='leer'>Noch keine Kampagne angelegt.</td></tr>"

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
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #16181d; --karte: #1e2127; --text: #e8eaed; --leise: #9aa1ab;
      --rand: #2c313a; --akzent: #60a5fa; --gut: #4ade80; --mittel: #fbbf24;
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
</style>
</head>
<body>
<h1>Batraqiq – Gruppenübersicht</h1>
<p class="hinweis">
  Den Stand kannst du in der Tabelle direkt ändern – er wird sofort gespeichert.
  Alles andere entsteht aus der Suche und ist hier nicht änderbar.
  Facebook meldet nichts von selbst: Was du dort tust, trägst du hier nach.
  Stand: {html.escape(daten["erzeugt_am"])}
</p>

<div class="kacheln">{kacheln}</div>

<div class="filter">
  <select id="f-stadt"><option value="">Alle Städte</option></select>
  <select id="f-zielgruppe"><option value="">Alle Zielgruppen</option></select>
  <select id="f-kategorie"><option value="">Alle Kategorien</option></select>
  <select id="f-marketing"><option value="">Jeder Stand</option></select>
  <input type="search" id="f-suche" placeholder="Name durchsuchen – auch arabisch …">
  <label class="schalter"><input type="checkbox" id="f-bewertet" checked> nur bewertete</label>
  <span class="marke" id="treffer"></span>
</div>

<div class="tabelle-rahmen">
<table>
  <thead><tr>
    <th class="zahl" data-sort="score">Score</th>
    <th data-sort="name">Gruppe</th>
    <th data-sort="stadt">Stadt</th>
    <th data-sort="zielgruppen">Zielgruppe</th>
    <th data-sort="kategorie">Kategorie</th>
    <th data-sort="marketing_label">Stand</th>
    <th class="zahl" data-sort="anfragen"
        title="Von wie vielen verschiedenen Suchanfragen diese Gruppe gefunden
wurde. Höchstens 16 (9 Stadtmuster + 7 bundesweite) – für jede Stadt gleich,
also untereinander vergleichbar.">Anfragen</th>
  </tr></thead>
  <tbody id="zeilen"></tbody>
</table>
</div>

<h2>Kampagnen</h2>
<div class="tabelle-rahmen">
<table>
  <thead><tr>
    <th>Name</th><th>Kennung</th><th>Status</th>
    <th class="zahl">Gruppen</th><th class="zahl">Klicks</th><th class="zahl">Registrierungen</th>
  </tr></thead>
  <tbody>{kampagnen_zeilen}</tbody>
</table>
</div>

<footer>
  Kein Zugriff auf facebook.com. Diese Seite ist nur über localhost erreichbar –
  der Dienst selbst beantwortet <code>/r/{{code}}</code> weiterhin für alle.
</footer>

<script>
const DATEN = {nutzlast};
const zeilen = DATEN.gruppen;
let sortSpalte = "score", sortAb = true;

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

  return zeilen.filter((z) =>
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

function zeichne() {{
  const liste = sortiert(gefiltert());
  document.getElementById("treffer").textContent =
    liste.length + " von " + zeilen.length;

  document.getElementById("zeilen").innerHTML = liste.length === 0
    ? "<tr><td colspan='7' class='leer'>Keine Gruppe passt zu diesem Filter.</td></tr>"
    : liste.map((z) => {{
        const klasse = z.score === null ? "keine" : z.score >= 90 ? "hoch"
                     : z.score >= 70 ? "mittel" : "";
        const codes = z.codes.length
          ? " " + z.codes.map((c) => "<code>" + esc(c) + "</code>").join(" ") : "";
        return `<tr>
          <td class="zahl"><span class="punkte ${{klasse}}">${{zahl(z.score)}}</span></td>
          <td class="name" dir="auto">
            <a href="${{esc(z.url)}}" target="_blank"
               rel="noopener noreferrer">${{esc(z.name)}}</a>${{codes}}
            <span class="grund">${{esc(z.grund)}}</span>
          </td>
          <td>${{esc(z.stadt) || "–"}}</td>
          <td>${{esc(z.zielgruppen.join(", ")) || "–"}}</td>
          <td>${{esc(z.kategorie) || "–"}}</td>
          <td>
            <select class="stand" data-id="${{esc(z.id)}}">
              ${{DATEN.staende.map((s) =>
                  `<option value="${{s.wert}}"${{s.wert === z.marketing ? " selected" : ""}}>`
                  + esc(s.label) + "</option>").join("")}}
            </select>
          </td>
          <td class="zahl">${{z.anfragen}}</td>
        </tr>`;
      }}).join("");
}}

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
