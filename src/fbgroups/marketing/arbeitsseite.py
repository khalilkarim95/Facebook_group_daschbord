"""Die Arbeitsseite - **eine Gruppe**, zwei Spalten, zehn Fassungen.

Der Grund, warum es sie gibt, ist unveraendert: Der Bestand lebt auf dem
Server, Zwischenablage und Browser aber auf dem Arbeitsrechner. Die Arbeit
dorthin zu holen hiesse, in eine zweite Datenbank zu schreiben - genau davor
warnt ``docs/plan-go-subdomain.md``. Aufgeloest wird das, indem die *Arbeit*
dorthin kommt, wo der Bestand steht.

Was sich geaendert hat, ist die **Einheit**. Vorher war es der Beitrag: ein
Text, ein Bildschirm, und der einzige Knopf, der weiterfuehrte, war zugleich
der, der einen Ausgang meldete. Das ist ein Ablauf mit einer Richtung -
"veroeffentlichen, naechste" - und er nimmt die Entscheidung vorweg, die
eigentlich zu treffen ist: **welche** der fuenf Fassungen in **diese** Gruppe
passt.

Jetzt steht die Gruppe im Mittelpunkt und alles Uebrige daneben:

    Gruppe  ->  links  5 Beitraege     (blaettern, schreiben, melden)
            ->  rechts 5 Kommentare    (blaettern, schreiben, melden)

Die beiden Spalten wissen nichts voneinander. Jede Fassung hat ihren eigenen
Stand, und "veroeffentlicht" an einer Stelle aendert an keiner anderen etwas -
weder am Nachbarn in derselben Spalte noch an der Spalte daneben noch an der
Gruppe. Nach einer Meldung bleibt der Bildschirm stehen, wo er stand.

**Die Seite haelt keinen Zustand, aber sie laedt auch nicht neu.** Alle zehn
Fassungen kommen mit dem ersten Aufruf mit; Blaettern zwischen ihnen ist ein
Tausch im Browser und keine Anfrage. Geschrieben wird ausschliesslich ueber
die Wege ``/vorschlag/text`` und ``/vorschlag/ergebnis``, und jeder davon
antwortet mit dem neuen Stand
**dieser einen** Fassung. Die Wahrheit steht damit weiterhin in der
Datenbank - der Browser zeigt sie nur an, ohne sich fuer sie zu halten.

**Der Text geht nur in eine Richtung, mit einer benannten Ausnahme.** Das
Formular, das einen Ausgang meldet, traegt Kennung, Zweck, Nummer und Grund -
keinen Text. Wer einen Text aendern will, tut das ueber den Weg, der genau
dafuer da ist; dort ist das Aendern die Handlung selbst und nicht ein Feld,
das nebenbei mitfaehrt.

**``{link}`` bleibt im Textfeld sichtbar und in der Zwischenablage unsichtbar.**
Bearbeitet wird der gespeicherte Text mit dem Platzhalter - dort **muss** er
stehen, sonst bekaeme die Gruppe nie einen Klick gutgeschrieben. Kopiert wird
die vom Server eingesetzte Fassung. Deshalb heisst der Kopierknopf
"Speichern & kopieren", sobald im Feld etwas Ungespeichertes steht: Die
Ersetzung geschieht in ``beitrag.mit_link`` und nirgends sonst, also muss der
Text erst dorthin.
"""

from __future__ import annotations

import json
from html import escape
from urllib.parse import quote

from fbgroups.config import AppConfig
from fbgroups.marketing.arbeit import Fassung, Grund, Gruppenarbeit, Gruppeneintrag, Sperre
from fbgroups.marketing.models import CampaignGroup, Texttyp, VorschlagStatus
from fbgroups.models import Group

# Dieselbe Handschrift wie ``dashboard.py``: dunkler Grund, ein Akzent, keine
# Bilder. Wer zwischen Uebersicht und Arbeitsseite wechselt, soll nicht das
# Gefuehl haben, das Werkzeug gewechselt zu haben.
#
# Neu ist allein die Zweiteilung. Sie ist bewusst nicht dezent: Zwei Spalten
# mit demselben Aufbau, aber verschiedener Kopffarbe - wer nach einer Stunde
# aufsieht, soll ohne Lesen wissen, ob er im Beitrag oder im Kommentar steht.
_STIL = """
:root {
  color-scheme: dark;
  --grund:#14161a; --karte:#1c1f26; --tiefer:#0f1114; --rand:#2a2f38;
  --schrift:#e6e8eb; --zart:#8b929c; --akzent:#7cc4ff;
  --post:#7cc4ff; --kommentar:#c084fc;
  --gut:#4ade80; --schlecht:#f87171;
}
* { box-sizing: border-box; }
body { margin:0; padding:1.5rem 1.25rem 4rem; background:var(--grund);
       color:var(--schrift);
       font:16px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif; }
.huelle { max-width:96rem; margin:0 auto; }
a { color:var(--akzent); }
h1 { font-size:1.35rem; margin:0 0 .35rem; font-weight:600; }
.leiste { display:flex; gap:1rem; flex-wrap:wrap; align-items:center;
          color:var(--zart); font-size:.85rem; margin-bottom:1rem; }
.leiste b { color:var(--schrift); font-weight:600; }
.code { font-family:ui-monospace,"Cascadia Code",Consolas,monospace;
        font-size:.85rem; color:var(--akzent); }
.karte { background:var(--karte); border:1px solid var(--rand);
         border-radius:12px; padding:1.1rem 1.25rem; margin-bottom:1rem; }
button, .knopf { font:inherit; font-size:.9rem; padding:.55rem 1rem;
                 border-radius:8px; border:1px solid var(--rand); cursor:pointer;
                 background:#252a33; color:var(--schrift); text-decoration:none;
                 display:inline-block; line-height:1.3; }
button:hover:not(:disabled), .knopf:hover { background:#2f3540; }
button:disabled { opacity:.4; cursor:default; }
.haupt { background:#1d4ed8; border-color:#2563eb; font-weight:600; }
.haupt:hover:not(:disabled) { background:#2563eb; }
/* Die Ausgangsknoepfe sind deutlich sichtbar, aber nicht gefuellt: Ein
   gefuellter gruener Knopf neben dem gefuellten blauen "Speichern" ist der
   Klick, den man aus Versehen tut - und "veroeffentlicht" laesst sich nicht
   zuruecknehmen, "gespeichert" schon. */
.melden-gut { background:rgba(34,197,94,.12); border-color:#2b8a54;
              color:#86efac; font-weight:600; }
.melden-gut:hover:not(:disabled) { background:rgba(34,197,94,.22); }
.melden-schlecht { background:rgba(248,113,113,.1); border-color:#993a3a;
                   color:#fca5a5; }
.melden-schlecht:hover:not(:disabled) { background:rgba(248,113,113,.2); }
.knoepfe { display:flex; gap:.5rem; flex-wrap:wrap; align-items:center; }
.hinweis { color:var(--zart); font-size:.85rem; }
.leer { color:#6b727c; font-style:italic; }
.gut-text { color:var(--gut); }
.schlecht-text { color:var(--schlecht); }

/* Merkmale der Gruppe - eine Zeile, damit sie die Texte nicht wegdruecken. */
.merkmale { display:flex; gap:1.25rem; flex-wrap:wrap; margin:0 0 1rem;
            color:#c3c8d0; font-size:.85rem; }
.merkmale i { color:var(--zart); font-style:normal; margin-right:.35rem; }

/* Die Warteschlange im Automatikband. */
.ab-liste { list-style:none; margin:.6rem 0 0; padding:0;
            font-size:.85rem; color:#c3c8d0; }
.ab-liste li { padding:.15rem 0; }
.ab-marke { font-size:.72rem; text-transform:uppercase; letter-spacing:.05em;
            padding:.05rem .4rem; border-radius:3px; background:#2a2f38;
            color:var(--zart); margin:0 .4rem; }
.ab-laeuft .ab-marke { background:#1d4ed8; color:#fff; }
.ab-fertig { opacity:.55; }
.ab-fertig .ab-marke { background:rgba(34,197,94,.15); color:#86efac; }
.ab-befehl { margin-top:.6rem; font-size:.78rem; color:var(--zart); }
.ab-befehl code { display:block; margin-top:.2rem; color:var(--akzent);
                  font-family:ui-monospace,Consolas,monospace; }

/* Gruppen-Navigation: unabhaengig von der Vorschlags-Navigation, und deshalb
   ganz oben und ueber beide Spalten - nicht in einer von ihnen. */
.gruppenwahl { display:flex; gap:.6rem; align-items:center; flex-wrap:wrap; }
.gruppenwahl select { flex:1; min-width:14rem; max-width:44rem;
    padding:.5rem .6rem; border-radius:8px; border:1px solid var(--rand);
    background:var(--tiefer); color:var(--schrift); font:inherit;
    font-size:.9rem; }
.zaehler { color:var(--zart); font-size:.85rem; white-space:nowrap; }
.zaehler b { color:var(--schrift); }

/* Zwei Spalten, sichtbar getrennt. Unter 1100px untereinander - ein
   waagerecht scrollender Beitragstext waere unlesbar. */
.spalten { display:grid; grid-template-columns:1fr 1fr; gap:1.25rem;
           align-items:start; }
@media (max-width:1100px) { .spalten { grid-template-columns:1fr; } }
.spalte { background:var(--karte); border:1px solid var(--rand);
          border-radius:12px; overflow:hidden; }
.spalte > .inhalt { padding:1rem 1.15rem 1.15rem; }
.spaltenkopf { display:flex; align-items:baseline; gap:.7rem; flex-wrap:wrap;
               padding:.7rem 1.15rem; border-bottom:1px solid var(--rand);
               background:rgba(255,255,255,.02); }
.spaltenkopf .titel { font-weight:700; letter-spacing:.09em; font-size:.82rem;
                      text-transform:uppercase; }
.spalte.posts { border-top:3px solid var(--post); }
.spalte.posts .titel { color:var(--post); }
.spalte.kommentare { border-top:3px solid var(--kommentar); }
.spalte.kommentare .titel { color:var(--kommentar); }
.spaltenkopf .dazu { color:var(--zart); font-size:.82rem; }

/* Die Vorschlagsleiste: Pfeile links und rechts, dazwischen die Nummern mit
   ihrem Stand. Der Stand steht AN der Nummer und nicht nur ueber dem Text -
   sonst muesste man fuenfmal blaettern, um zu sehen, wo man steht. */
.blaettern { display:flex; align-items:center; gap:.4rem; flex-wrap:wrap;
             margin-bottom:.75rem; }
.blaettern .pfeil { padding:.4rem .7rem; }
.nummern { display:flex; gap:.35rem; flex-wrap:wrap; }
.nummer { min-width:2.9rem; padding:.35rem .5rem; text-align:center;
          font-variant-numeric:tabular-nums; font-size:.88rem;
          background:var(--tiefer); }
.nummer .glyphe { margin-left:.25rem; font-size:.9em; }
.nummer[data-stand="veroeffentlicht"] { border-color:#2b8a54; color:#86efac; }
.nummer[data-stand="fehlgeschlagen"] { border-color:#993a3a; color:#fca5a5; }
.nummer[data-stand="gespeichert"] { border-color:#3d5a75; }
.nummer.aktiv { background:#1d4ed8; border-color:#60a5fa; color:#fff;
                font-weight:700; }
.spalte.kommentare .nummer.aktiv { background:#7e22ce; border-color:#c084fc; }

.standzeile { display:flex; align-items:center; gap:.6rem; flex-wrap:wrap;
              margin-bottom:.5rem; font-size:.85rem; color:var(--zart); }
.abzeichen { padding:.15rem .55rem; border-radius:999px; font-size:.78rem;
             font-weight:600; border:1px solid var(--rand); }
.abzeichen[data-stand="entwurf"] { color:var(--zart); }
.abzeichen[data-stand="gespeichert"] { color:var(--akzent); border-color:#2f5d80; }
.abzeichen[data-stand="veroeffentlicht"] { color:#86efac; border-color:#2b8a54;
             background:rgba(34,197,94,.12); }
.abzeichen[data-stand="fehlgeschlagen"] { color:#fca5a5; border-color:#993a3a;
             background:rgba(248,113,113,.1); }
.vorlagezeile { font-size:.8rem; color:var(--zart); margin-bottom:.6rem;
                font-family:ui-monospace,Consolas,monospace; }

/* Gross genug, dass ein arabischer Beitrag am Stueck lesbar ist. */
textarea { width:100%; min-height:19rem; padding:.9rem; border-radius:8px;
           border:1px solid var(--rand); background:var(--tiefer);
           color:var(--schrift); font:inherit; font-size:.97rem;
           line-height:1.75; resize:vertical; }
textarea[dir="rtl"] { text-align:right; }
textarea:focus { outline:2px solid #2563eb; outline-offset:-1px; }
.ungespeichert textarea { border-color:#a16207; }

input[type=text] { width:100%; padding:.5rem .65rem; border-radius:8px;
                   border:1px solid var(--rand); background:var(--tiefer);
                   color:var(--schrift); font:inherit; font-size:.9rem; }
label { display:block; margin:.6rem 0 .25rem; color:var(--zart);
        font-size:.82rem; }
.melden { margin-top:.9rem; padding-top:.9rem; border-top:1px dashed var(--rand); }
.melden .ueber { font-size:.78rem; color:var(--zart); letter-spacing:.06em;
                 text-transform:uppercase; margin-bottom:.5rem; }
.meldung { margin-top:.6rem; font-size:.86rem; min-height:1.3em; }
.sperrhinweis { margin:.6rem 0 0; font-size:.85rem; color:#fbbf24; }

.sperre { text-align:center; padding:3rem 1.5rem; }
.sperre .grund { font-size:1.2rem; font-weight:600; margin-bottom:.5rem; }
.sperre .dazu { color:var(--zart); }
.sperre .bericht { margin-top:1rem; font-size:.85rem; line-height:1.8; }
.schritt { font-size:.88rem; }
.ueberschrift { font-weight:600; margin-bottom:.75rem; }
.versteckt { display:none; }
"""


def _kopf(titel: str) -> str:
    return (
        "<!doctype html><html lang='de'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{escape(titel)}</title><style>{_STIL}</style></head><body>"
        "<div class='huelle'>"
    )


_FUSS = "</div></body></html>"


#: Wie der Zweck ueber seiner Spalte heisst. "post" und "kommentar" sind
#: Kennungen aus der Konfiguration; ueber einem Text steht ein Wort.
_ZWECKNAME: dict[Texttyp, str] = {
    Texttyp.POST: "Beitrag",
    Texttyp.KOMMENTAR: "Kommentar",
}

#: Die Ueberschrift der Spalte - Mehrzahl, weil es fuenf sind.
_SPALTENNAME: dict[Texttyp, str] = {
    Texttyp.POST: "Posts",
    Texttyp.KOMMENTAR: "Kommentare",
}

#: Wie ein Stand heisst und woran man ihn erkennt. Zeichen **und** Wort: Ein
#: Haken allein ist eine Vermutung, ein Wort allein muss man lesen.
_STANDNAME: dict[VorschlagStatus, tuple[str, str]] = {
    VorschlagStatus.ENTWURF: ("", "Entwurf"),
    VorschlagStatus.GESPEICHERT: ("●", "Gespeichert"),
    VorschlagStatus.VEROEFFENTLICHT: ("✓", "Veroeffentlicht"),
    VorschlagStatus.FEHLGESCHLAGEN: ("✕", "Fehlgeschlagen"),
}


def _ist_arabisch(text: str) -> bool:
    """Grob, aber ausreichend: Steht arabische Schrift ueberhaupt drin?

    Entscheidet nur ueber die Leserichtung der Anzeige. Der Text selbst wird
    nicht angefasst - er geht Zeichen fuer Zeichen so hinaus, wie er
    vorbereitet wurde.
    """
    return any("؀" <= zeichen <= "ۿ" for zeichen in text)


def _js(wert: object) -> str:
    """JSON fuer ein ``<script>``-Element - ohne dass ``</script>`` es beendet."""
    return json.dumps(wert, ensure_ascii=False).replace("<", "\\u003c")


# ---------------------------------------------------------------------------
# Die Sperrseite und die Werkbank - unveraendert in ihrer Aufgabe.
# ---------------------------------------------------------------------------


def render_sperre(sperre: Sperre, campaign_id: str, bericht: str = "") -> str:
    """Die Seite, wenn es gar nichts zu bearbeiten gibt - mit dem Grund.

    Sie erscheint seit der Umstellung nur noch in **einem** Fall: Der Kampagne
    ist keine Gruppe zugeordnet. Pausiert und gestoppt sind keine Gruende mehr,
    die Seite zu verschliessen - sie halten das Veroeffentlichen an und stehen
    als Hinweis ueber den Knoepfen. Lesen, Schreiben und Speichern bleiben
    moeglich: Eine pausierte Kampagne ist ein Grund, heute nichts abzusenden,
    und kein Grund, die Texte von morgen nicht vorzubereiten.

    ``bericht`` steht darin, wenn die Vorbereitung beim Aufruf bereits selbst
    gelaufen ist und trotzdem nichts entstand. Er ist dann die einzige
    Auskunft darueber, woran es lag.
    """
    inhalt = f"<div class='grund'>{escape(sperre.grund)}</div>"

    if sperre.grund == Grund.PAUSIERT or sperre.grund == Grund.GESTOPPT:
        dazu = "Fortsetzen auf der Uebersicht oder mit: fbgroups campaign resume"
    else:
        dazu = (
            "Zuordnen in der Uebersicht (Spalte 'Kampagne') oder mit: "
            f"fbgroups campaign sync {campaign_id}"
        )
    inhalt += f"<div class='dazu'>{escape(dazu)}</div>"

    if bericht:
        inhalt += (
            "<div class='dazu bericht'>Die Vorbereitung ist bereits gelaufen:<br>"
            + escape(bericht)
            + "</div>"
        )

    werkbank = (
        _werkbank(campaign_id, nur_ausweichwege=bool(bericht))
        if sperre.grund == Grund.KEINE_GRUPPEN
        else ""
    )

    return (
        _kopf(f"Arbeit - {campaign_id}")
        + f"<h1>{escape(campaign_id)}</h1>"
        + "<div class='leiste'><a class='knopf' href='/'>&larr; Uebersicht</a></div>"
        + f"<div class='karte sperre'>{inhalt}</div>"
        + werkbank
        + _FUSS
    )


# Die Vorbereitungsschritte als Knopfreihe. Reihenfolge wie im Ablauf.
_SCHRITTE: tuple[tuple[str, str, str], ...] = (
    (
        "text",
        "1 · Texte erzeugen",
        "Fuellt je Gruppe alle Fassungen aus dem Vorrat - wo noch keine steht.",
    ),
    ("approve", "2 · Freigeben", "Gibt alles frei, was einen Text hat."),
    ("enqueue", "3 · Einreihen", "Stellt die freigegebenen nach Score in die Warteschlange."),
    (
        "zurueckholen",
        "Zurueckholen",
        "Nimmt 'Passt nicht' zurueck - die Gruppe steht danach wieder auf Entwurf.",
    ),
    (
        "text_neu",
        "Texte neu erzeugen",
        "Ueberschreibt auch vorhandene Texte - nach einer Aenderung an den Vorlagen.",
    ),
)

#: Die nummerierte Kette - genau das, was "Arbeiten" von selbst ausfuehrt.
_KETTE = ("text", "approve", "enqueue")


def _werkbank(campaign_id: str, *, nur_ausweichwege: bool = False) -> str:
    """Die Vorbereitungsschritte als Knoepfe statt als Befehle.

    ``nur_ausweichwege`` laesst die nummerierten weg. Sie sind dann gerade von
    selbst gelaufen; sie noch einmal anzubieten waere eine Einladung, etwas zu
    wiederholen, das eben nichts bewirkt hat.
    """
    schritte = [
        eintrag for eintrag in _SCHRITTE
        if not (nur_ausweichwege and eintrag[0] in _KETTE)
    ]
    knoepfe = "".join(
        f"<button type='button' class='schritt' data-schritt='{schluessel}' "
        f"title='{escape(erklaerung)}'>{escape(beschriftung)}</button>"
        for schluessel, beschriftung, erklaerung in schritte
    )
    return (
        "<div class='karte'>"
        "<div class='ueberschrift'>Texte vorbereiten</div>"
        f"<div class='knoepfe'>{knoepfe}</div>"
        "<div class='meldung' id='meldung'></div>"
        "<div class='hinweis'>Dieselben Schritte wie auf der Kommandozeile - "
        "und dieselben Regeln. Nichts davon veroeffentlicht etwas.</div>"
        "</div>"
        "<script>"
        f"const KAMPAGNE = {_js(campaign_id)};"
        + """
document.querySelectorAll('.schritt').forEach((knopf) => {
  knopf.addEventListener('click', async () => {
    const meldung = document.getElementById('meldung');
    const vorher = knopf.textContent;
    knopf.disabled = true;
    knopf.textContent = 'laeuft ...';
    meldung.textContent = '';
    try {
      const antwort = await fetch('/kampagnen/' + KAMPAGNE + '/vorbereiten', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({schritt: knopf.dataset.schritt}),
      });
      const daten = await antwort.json();
      if (!antwort.ok) {
        meldung.textContent = daten.detail || ('Fehler ' + antwort.status);
        meldung.className = 'meldung schlecht-text';
      } else {
        meldung.textContent = daten.betroffen + ' betroffen. ' + (daten.hinweis || '');
        meldung.className = 'meldung gut-text';
        if (daten.betroffen > 0) { setTimeout(() => location.reload(), 900); }
      }
    } catch (fehler) {
      meldung.textContent = String(fehler);
      meldung.className = 'meldung schlecht-text';
    } finally {
      knopf.disabled = false;
      knopf.textContent = vorher;
    }
  });
});
</script>"""
    )


# ---------------------------------------------------------------------------
# Die Gruppenseite
# ---------------------------------------------------------------------------


def merkmale(gruppe: Group | None, link: CampaignGroup, config: AppConfig | None) -> str:
    """Die Zeile unter dem Namen: woran man die Gruppe wiedererkennt.

    Wer 300 Gruppen hintereinander bearbeitet, sieht sonst immer denselben
    Bildschirm und muss in einem anderen Fenster nachsehen, um wen es diesmal
    geht - dabei steht alles davon ohnehin im Bestand.

    ``config`` nur fuer die Beschriftungen: ``audience_tags`` haelt Kennungen
    ("syrians"), und "syrians" ist keine Auskunft. Fehlt die Konfiguration,
    steht die Kennung da - schlechter lesbar, aber nicht falsch.
    """
    teile: list[tuple[str, str]] = []
    if gruppe is not None:
        if gruppe.city:
            teile.append(("Stadt", gruppe.city))
        if gruppe.audience_tags:
            teile.append((
                "Zielgruppe",
                ", ".join(
                    config.audiences[tag].label_de
                    if config is not None and tag in config.audiences
                    else tag
                    for tag in gruppe.audience_tags
                ),
            ))
        if gruppe.category:
            beschriftungen = (
                {k.id: k.label_de for k in config.categories} if config is not None else {}
            )
            teile.append(("Kategorie", beschriftungen.get(gruppe.category, gruppe.category)))
        if gruppe.score is not None:
            hoechst = f" von {gruppe.score_max:g}" if gruppe.score_max else ""
            teile.append(("Score", f"{gruppe.score:g}{hoechst}"))
        if gruppe.member_count:
            teile.append(("Mitglieder", f"{gruppe.member_count:,}".replace(",", ".")))

    if not teile:
        return ""
    return "<div class='merkmale'>" + "".join(
        f"<span><i>{escape(beschriftung)}</i> {escape(wert)}</span>"
        for beschriftung, wert in teile
    ) + "</div>"


def _gruppenwahl(
    campaign_id: str, arbeit: Gruppenarbeit, eintraege: list[Gruppeneintrag]
) -> str:
    """Vor, zurueck und direkt hin - die **Gruppen**-Navigation.

    Sie steht ganz oben und ueber beiden Spalten, denn sie ist von der
    Vorschlags-Navigation unabhaengig: Wer Fassung 3 ansieht, bleibt in
    derselben Gruppe; wer die Gruppe wechselt, bekommt dort wieder deren
    eigene fuenf und fuenf.

    Eine Auswahlliste und keine 310 Nummern: Namen sind das, wonach man eine
    Gruppe sucht. Der Stand steht daneben, damit sichtbar ist, wo schon etwas
    veroeffentlicht wurde - sonst blaettert man durch dreihundert Eintraege,
    um festzustellen, dass die ersten zwanzig erledigt sind.
    """
    ziel = f"/arbeit/{quote(campaign_id, safe='')}"
    zurueck = (
        f"<a class='knopf' href='{ziel}?gruppe={arbeit.nummer - 1}' "
        "title='vorherige Gruppe'>&larr;</a>"
        if arbeit.nummer > 1
        else "<span class='knopf' style='opacity:.35'>&larr;</span>"
    )
    weiter = (
        f"<a class='knopf' href='{ziel}?gruppe={arbeit.nummer + 1}' "
        "title='naechste Gruppe'>&rarr;</a>"
        if arbeit.nummer < arbeit.gesamt
        else "<span class='knopf' style='opacity:.35'>&rarr;</span>"
    )
    optionen = "".join(
        "<option value='{nr}'{gewaehlt}>{marke}{nr}. {name}</option>".format(
            nr=eintrag.nummer,
            gewaehlt=" selected" if eintrag.nummer == arbeit.nummer else "",
            marke=(
                "✓ " if eintrag.veroeffentlicht
                else "✕ " if eintrag.fehlgeschlagen
                else ""
            ),
            name=escape(eintrag.name[:70]),
        )
        for eintrag in eintraege
    )
    return (
        "<div class='karte gruppenwahl'>"
        + zurueck
        + f"<select id='gruppenwahl' aria-label='Gruppe waehlen'>{optionen}</select>"
        + weiter
        + f"<span class='zaehler'>Gruppe <b>{arbeit.nummer}</b> von {arbeit.gesamt}</span>"
        + (
            f"<a class='knopf' href='{escape(arbeit.url)}' target='_blank' "
            "rel='noopener noreferrer'>Gruppe bei Facebook oeffnen</a>"
            if arbeit.url
            else ""
        )
        + "</div>"
        + f"""<script>
(() => {{
  const wahl = document.getElementById('gruppenwahl');
  if (!wahl) return;
  wahl.addEventListener('change', () => {{
    location.href = {_js(ziel)} + '?gruppe=' + wahl.value;
  }});
}})();
</script>"""
    )


def _spalte(
    texttyp: Texttyp,
    fassungen: list[Fassung],
    arbeit: Gruppenarbeit,
    kalt_limit_erreicht: bool = False,
    *,
    automatik_moeglich: bool = True,
) -> str:
    """Eine der beiden Spalten - Aufbau identisch, Inhalt unabhaengig.

    Bewusst dieselbe Bauform fuer Beitrag und Kommentar: Es ist derselbe
    Handgriff (lesen, anpassen, kopieren, einfuegen, melden), und zwei
    verschiedene Bedienformen fuer denselben Handgriff waeren eine Huerde ohne
    Gegenwert. Unterschieden wird ueber die Farbe des Kopfes und die
    Beschriftung der Knoepfe, nicht ueber die Anordnung.

    Die Spalte steht auch dann, wenn es (noch) keine Fassung gibt: Sie ist die
    Stelle, an der eine entsteht, und eine Stelle, die es nur gibt, wenn schon
    etwas dasteht, ist keine.
    """
    kennung = texttyp.value
    zweck = _ZWECKNAME[texttyp]
    klasse = "posts" if texttyp is Texttyp.POST else "kommentare"

    if not fassungen:
        # Seit dem 28.08.2026 gibt es nur noch diesen einen Grund: Eine
        # Kampagne, die keine Kommentare fuehrt, gibt es nicht mehr.
        inhalt = (
            "<p class='leer'>Noch keine Fassung erzeugt. Unten schreiben und "
            "speichern, oder ueber &bdquo;Texte erzeugen&ldquo; aus dem "
            "Vorrat fuellen.</p>"
        )
    else:
        inhalt = (
            _nummernleiste(kennung, fassungen)
            + _standzeile(kennung, fassungen[0])
            + _textfeld(kennung, zweck, fassungen[0])
            + _knopfreihe(
                kennung,
                zweck,
                arbeit.url,
                kalt_limit_erreicht=kalt_limit_erreicht,
                automatik_moeglich=automatik_moeglich,
            )
            + _meldeblock(kennung, zweck, arbeit)
        )

    return (
        f"<section class='spalte {klasse}' data-spalte='{kennung}'>"
        "<div class='spaltenkopf'>"
        f"<span class='titel'>{escape(_SPALTENNAME[texttyp])}</span>"
        f"<span class='dazu' id='kopf-{kennung}'>"
        + (
            f"Vorschlag 1 von {len(fassungen)}" if fassungen else "keine Fassung"
        )
        + "</span></div>"
        f"<div class='inhalt'>{inhalt}</div>"
        "</section>"
    )


def _nummernleiste(kennung: str, fassungen: list[Fassung]) -> str:
    """Die fuenf Nummern mit ihrem Stand - und die Pfeile daneben.

    Der Stand steht **an** der Nummer, nicht nur ueber dem Text. Ohne das
    muesste man durch alle fuenf blaettern, um zu sehen, welche schon draussen
    ist - und genau diese Frage stellt sich bei jeder Gruppe neu.
    """
    knoepfe = "".join(
        "<button type='button' class='nummer{aktiv}' data-nummer='{nr}' "
        "data-stand='{stand}' title='{titel}'>{nr}"
        "<span class='glyphe'>{glyphe}</span></button>".format(
            aktiv=" aktiv" if index == 0 else "",
            nr=fassung.nummer,
            stand=fassung.status.value,
            titel=escape(_STANDNAME[fassung.status][1]),
            glyphe=_STANDNAME[fassung.status][0],
        )
        for index, fassung in enumerate(fassungen)
    )
    return (
        "<div class='blaettern'>"
        f"<button type='button' class='pfeil' data-schieben='-1'>&lsaquo;</button>"
        f"<div class='nummern' id='nummern-{kennung}'>{knoepfe}</div>"
        f"<button type='button' class='pfeil' data-schieben='1'>&rsaquo;</button>"
        "</div>"
    )


def _standzeile(kennung: str, fassung: Fassung) -> str:
    """Stand, Vorlage und Herkunft der gerade gezeigten Fassung."""
    zeichen, wort = _STANDNAME[fassung.status]
    link_html = (
        f" <a href='{escape(fassung.post_url)}' target='_blank' "
        "rel='noopener' class='post-link'>[Ansehen]</a>"
        if fassung.post_url
        else ""
    )
    return (
        "<div class='standzeile'>"
        f"<span class='abzeichen' id='stand-{kennung}' "
        f"data-stand='{fassung.status.value}'>{zeichen} {escape(wort)}{link_html}</span>"
        f"<span id='fehler-{kennung}' class='schlecht-text'>"
        + escape(fassung.vorschlag.fehler)
        + "</span>"
        "</div>"
        f"<div class='vorlagezeile' id='vorlage-{kennung}'>"
        + escape(_vorlagentext(fassung))
        + "</div>"
    )


def _vorlagentext(fassung: Fassung) -> str:
    teile = []
    if fassung.vorschlag.vorlage_key:
        teile.append(f"Vorlage: {fassung.vorschlag.vorlage_key}")
    teile.append(f"Text: {fassung.vorschlag.quelle.value}")
    return " · ".join(teile)


def _textfeld(kennung: str, zweck: str, fassung: Fassung) -> str:
    """Das Textfeld - offen, gross, und **immer** editierbar.

    Kein ``<details>`` davor und keine Vorschau daneben: Die Fassung, die man
    ansieht, ist die Fassung, die man bearbeitet. Solange der Regelfall aus
    der Vorlage kam, war Zuklappen richtig; seit der Mensch hier auswaehlt und
    schreibt, ist es ein Weg mehr zu dem, was die Hauptsache ist.

    Gezeigt wird der **gespeicherte** Text mit ``{link}``, nicht der
    angezeigte mit eingesetztem Link. Ein von Hand hineingeschriebener Link
    ergaebe einen Beitrag, der richtig aussieht und dessen Gruppe nie einen
    Klick gutgeschrieben bekommt.
    """
    richtung = "rtl" if _ist_arabisch(fassung.vorschlag.text) else "ltr"
    return (
        f"<label for='feld-{kennung}'>{escape(zweck)}text &ndash; "
        "{link} muss genau einmal darin vorkommen; dort kommt der "
        # {datum} steht im Feld und nicht im kopierten Text - ohne diesen
        # Satz sieht es aus wie ein Platzhalter, den jemand vergessen hat,
        # und wer ihn von Hand ausfuellt, schreibt den Monat fest.
        "Tracking-Link hinein. {datum} wird erst beim Kopieren durch den "
        "laufenden Monat ersetzt.</label>"
        f"<textarea id='feld-{kennung}' dir='{richtung}' spellcheck='false' "
        f"placeholder='{escape(zweck)} fuer diese Gruppe schreiben ... {{link}}'>"
        # Der fuehrende Zeilenumbruch ist kein Versehen: Ein Browser verwirft
        # genau einen direkt hinter dem oeffnenden Tag. Ohne ihn verloere ein
        # Text, der selbst mit einem Umbruch beginnt, ihn stillschweigend -
        # und die Seite hielte das Feld sofort fuer geaendert, weil es nicht
        # mehr dem gespeicherten Text entspricht.
        + "\n"
        + escape(fassung.vorschlag.text)
        + "</textarea>"
    )


def _automatikband() -> str:
    """Der Stand der Kommentarautomatik - hier und nicht in der Uebersicht.

    Er stand zuerst ueber den Kacheln der Uebersicht. Dort beantwortet er eine
    Frage, die sich beim Ueberblicken selten stellt; **hier** ist er die
    Auskunft, die man beim Arbeiten braucht: Was hat die Automatik schon
    erledigt, an welcher Gruppe ist sie, und wie viele warten noch auf eine
    Mitgliedschaft.

    Gefuellt wird per Abruf, nicht aus der Seite: Der Stand aendert sich
    waehrend eines Laufs, und diese Seite laedt bewusst nicht neu. Verborgen,
    solange es nichts zu melden gibt - ein leerer Kasten waere eine Zeile
    ohne Auskunft.
    """
    return (
        "<div class='karte automatikband' id='automatikband' hidden "
        "style='border-left:4px solid #4a9eff'>"
        "<div style='display:flex;gap:.6rem;align-items:center;flex-wrap:wrap'>"
        "<b>Kommentarautomatik</b>"
        "<span id='ab-status' class='code'></span>"
        "<span id='ab-warten' class='hinweis' style='margin:0'></span>"
        "</div>"
        "<div id='ab-zahlen' class='hinweis' style='margin:.4rem 0 0'></div>"
        "<div id='ab-jetzt' class='hinweis' style='margin:.2rem 0 0'></div>"
        # Die ganze Warteschlange. Sie beantwortet "was kommt noch?" - die
        # Frage, die sich beim Arbeiten stellt und die eine Gesamtzahl nicht
        # beantwortet.
        "<ol id='ab-liste' class='ab-liste'></ol>"
        "<div class='ab-befehl'>Alle aktiven Kampagnen nacheinander abarbeiten:"
        "<code>fbgroups campaign automatik --server http://127.0.0.1:8090</code></div>"
        "</div>"
    )


def _knopfreihe(
    kennung: str,
    zweck: str,
    url: str,
    kalt_limit_erreicht: bool = False,
    *,
    automatik_moeglich: bool = True,
) -> str:
    """Speichern, Kopieren, Gruppe oeffnen - der Handgriff in seiner Reihenfolge.

    Der dritte Knopf war "Zurueck zur Vorlage" und ist es seit dem 28.08.2026
    nicht mehr. Er beantwortete eine Frage, die sich hier selten stellt, und
    stand an der Stelle, an der man nach dem Kopieren weitergeht: in die
    Gruppe. Die Reihe liest sich jetzt als das, was sie ist - schreiben,
    kopieren, einfuegen.

    **Der Weg zurueck bleibt, nur der Knopf ist weg.** ``generated_text``
    steht unveraendert neben ``text``, und ``POST /arbeit/{k}/vorschlag/
    zuruecksetzen`` holt ihn weiterhin. Wer eine ueberarbeitete Fassung
    verwerfen will, waehlt eine der vier anderen Nummern - oder ruft den Weg
    auf.

    Der Knopf steht in **beiden** Spalten. Derselbe Link im Kopf der Seite
    bleibt: Er gehoert zur Gruppen-Navigation und ist auch dann da, wenn man
    nur blaettert und gar keinen Text kopiert.
    """
    oeffnen = (
        f"<a class='knopf' href='{escape(url)}' target='_blank' "
        "rel='noopener noreferrer'>Gruppe bei Facebook oeffnen</a>"
        if url
        else ""
    )
    # Der Automatikknopf erscheint nur dort, wo er etwas ausrichten kann.
    #
    # Er laesst den **Dienst** einen sichtbaren Browser oeffnen. Auf dem
    # Server gibt es weder ein $DISPLAY noch eine angemeldete Sitzung; dort
    # endete jeder Druck in "Executable doesn't exist" - und hinterliess einen
    # Fehlversuch im Protokoll sowie eine Kaltmodus-Sperre von vier Minuten.
    # Ein Knopf, der zuverlaessig scheitert und dabei Spuren hinterlaesst, ist
    # schlimmer als kein Knopf.
    #
    # Massgeblich ist die Sitzung, nicht der Rechnername: Wer die Oberflaeche
    # auf seinem eigenen Rechner betreibt, hat den Knopf weiterhin. ``POST
    # /arbeit/{k}/vorschlag/auto`` bleibt unveraendert bestehen - was fehlt,
    # ist der Knopf, nicht der Weg.
    auto = ""
    if automatik_moeglich:
        auto_text = "Automatisch posten" if kennung == "post" else "Automatisch kommentieren"
        auto_desc = "Auf der Gruppenseite" if kennung == "post" else "Sucht den aktivsten Beitrag"
        auto_disabled = " disabled" if kalt_limit_erreicht else ""
        auto_style = (
            "background:#9ca3af;border-color:#6b7280;cursor:not-allowed"
            if kalt_limit_erreicht
            else "background:#7e22ce;border-color:#9333ea"
        )
        auto = (
            f"<button type='button' id='auto-{kennung}' class='haupt'{auto_disabled} "
            f"style='{auto_style}'>"
            f"{auto_text}<br>"
            f"<span style='font-size:0.7em;font-weight:normal;opacity:0.8'>{auto_desc}</span>"
            "</button>"
        )

    return (
        "<div class='knoepfe' style='margin-top:.7rem'>"
        f"<button type='button' class='haupt' id='speichern-{kennung}'>"
        "Speichern</button>"
        f"<button type='button' id='kopieren-{kennung}'>"
        f"{escape(zweck)} kopieren</button>"
        + auto
        + oeffnen
        + "</div>"
        f"<div class='meldung' id='meldung-{kennung}'></div>"
    )


def _meldeblock(kennung: str, zweck: str, arbeit: Gruppenarbeit) -> str:
    """Der Ausgang **dieser** Fassung - abgesetzt vom Speichern.

    Eigener Block mit eigener Ueberschrift und einer Trennlinie darueber, und
    das ist keine Zierde: "Speichern" ist zuruecknehmbar, "veroeffentlicht"
    nicht. Sie nebeneinanderzustellen und beide gefuellt einzufaerben waere
    die Einladung zu genau dem Klick, den man nicht rueckgaengig machen kann.

    Eine Sperre nimmt die Knoepfe nicht weg, sondern schaltet sie ab und sagt
    warum. Ein verschwundener Knopf sieht aus wie ein Fehler der Seite.
    """
    sperre = arbeit.sperre()
    gesperrt = " disabled" if sperre else ""
    hinweis = ""
    if sperre:
        hinweis = (
            f"<p class='sperrhinweis'>{escape(sperre.grund)} &ndash; "
            "Melden ist gerade nicht moeglich. Schreiben und Speichern schon.</p>"
        )
    return (
        "<div class='melden'>"
        f"<div class='ueber'>Ausgang dieser Fassung</div>"
        f"<div class='knoepfe'>"
        f"<button type='button' class='melden-gut' id='gut-{kennung}'{gesperrt}>"
        f"{escape(zweck)} veroeffentlicht</button>"
        f"<button type='button' class='melden-schlecht' id='schlecht-{kennung}'{gesperrt}>"
        f"{escape(zweck)} fehlgeschlagen</button>"
        "</div>"
        f"<div id='grundfeld-{kennung}' class='versteckt'>"
        f"<label for='grund-{kennung}'>Was ging schief?</label>"
        f"<input type='text' id='grund-{kennung}' "
        "placeholder='z. B. erlaubt keine Links'>"
        "</div>"
        + hinweis
        + f"<div class='meldung' id='ausgang-{kennung}'></div>"
        "</div>"
    )


def render_gruppenarbeit(
    arbeit: Gruppenarbeit,
    campaign_id: str,
    eintraege: list[Gruppeneintrag],
    config: AppConfig | None = None,
    kalt_text: str = "",
    kalt_limit_erreicht: bool = False,
    *,
    automatik_moeglich: bool = True,
) -> str:
    """Eine Gruppe, zwei Spalten, zehn Fassungen - die ganze Seite.

    Was hier **nicht** passiert, ist der Punkt: Kein Knopf blaettert weiter,
    kein Ausgang schaltet um, keine Meldung laedt die Seite neu. Wer einen
    Beitrag veroeffentlicht hat, steht danach genau dort, wo er stand - bei
    derselben Gruppe, bei derselben Fassung, mit unveraendertem Kommentar
    daneben.
    """
    return (
        _kopf(f"{arbeit.name} - {campaign_id}")
        + f"<h1>{escape(arbeit.name)}</h1>"
        + (
            # Was hier steht, ist das, wonach man **waehrend** der Arbeit
            # greift: der Code dieser Gruppe, die Kampagne, der Weg zurueck.
            # Der Tageszaehler stand einmal dazwischen; er gehoerte zum
            # Arbeiter und zaehlte eine Grenze, die es nicht mehr gibt.
            "<div class='leiste'>"
            f"<span class='code'>{escape(arbeit.link.tracking_code)}</span>"
            f"<span>Kampagne <b>{escape(campaign_id)}</b></span>"
            + (
                f"<span class='hinweis' style='margin:0; padding:0 1rem; "
                "border-left:1px solid #ccc; "
                f"{'color:#dc2626;font-weight:bold;' if kalt_limit_erreicht else ''}'>"
                f"{kalt_text}</span>"
                if kalt_text
                else ""
            )
            + "<a class='knopf' href='/'>&larr; Uebersicht</a>"
            "</div>"
        )
        + merkmale(arbeit.gruppe, arbeit.link, config)
        + _automatikband()
        + _gruppenwahl(campaign_id, arbeit, eintraege)
        + "<div class='spalten'>"
        + _spalte(
            Texttyp.POST,
            arbeit.posts,
            arbeit,
            kalt_limit_erreicht=kalt_limit_erreicht,
            automatik_moeglich=automatik_moeglich,
        )
        + _spalte(
            Texttyp.KOMMENTAR,
            arbeit.kommentare,
            arbeit,
            kalt_limit_erreicht=kalt_limit_erreicht,
            automatik_moeglich=automatik_moeglich,
        )
        + "</div>"
        + (
            "<p class='hinweis' style='margin-top:1rem'>"
            "Der Tracking-Link steckt im Text und wird beim Kopieren eingesetzt. "
            "Einfuegen, absenden, dann hier den Ausgang melden &ndash; die Seite "
            "bleibt danach stehen, wo sie steht."
            "</p>"
        )
        + _skript(campaign_id, arbeit)
        + _FUSS
    )


def _zustand_fuer_js(fassungen: list[Fassung]) -> list[dict[str, object]]:
    """Was der Browser von einer Fassung wissen muss - und nicht mehr.

    Beides muss mit: der gespeicherte Text (er steht im Textfeld) und der
    angezeigte (er geht in die Zwischenablage). Der Browser rechnet den einen
    **nicht** in den anderen um - die Ersetzung geschieht in
    ``beitrag.mit_link`` und nirgends sonst; deshalb bekommt er beide fertig.
    """
    return [
        {
            "nummer": fassung.nummer,
            "text": fassung.vorschlag.text,
            "angezeigt": fassung.angezeigt,
            "stand": fassung.status.value,
            "fehler": fassung.vorschlag.fehler,
            "post_url": fassung.post_url,
            "vorlage": _vorlagentext(fassung),
        }
        for fassung in fassungen
    ]


def _skript(campaign_id: str, arbeit: Gruppenarbeit) -> str:
    """Die Bedienung: blaettern, speichern, kopieren, melden.

    Ein Skript fuer beide Spalten. Sie verhalten sich gleich und
    unterscheiden sich nur im Zweck, den sie mitschicken - zwei Fassungen
    desselben Ablaufs waeren zwei Stellen, an denen der Zweck vertauscht
    werden kann, und ein Kommentar im Beitragsfeld faellt erst auf, wenn er
    in der Gruppe steht.
    """
    ziel = f"/arbeit/{quote(campaign_id, safe='')}"
    zustand = {
        "post": _zustand_fuer_js(arbeit.posts),
        "kommentar": _zustand_fuer_js(arbeit.kommentare),
    }
    zwecknamen = {"post": "Beitrag", "kommentar": "Kommentar"}
    return f"""<script>
(() => {{
  const ZIEL = {_js(ziel)};
  const GRUPPE = {_js(arbeit.link.group_id)};
  const FASSUNGEN = {_js(zustand)};
  const ZWECK = {_js(zwecknamen)};
  const STAND = {_js({s.value: list(_STANDNAME[s]) for s in VorschlagStatus})};
  const gewaehlt = {{post: 1, kommentar: 1}};

  // --- Stand der Kommentarautomatik ---------------------------------------
  //
  // Abgerufen statt eingebettet: Der Stand aendert sich waehrend eines Laufs,
  // und diese Seite laedt bewusst nicht neu. Jeder Zugriff in try/catch - eine
  // Auskunft nebenbei darf die Arbeitsseite nicht mitreissen.
  const automatikband = async () => {{
    const kasten = document.getElementById('automatikband');
    if (!kasten) return;
    let d;
    try {{
      const antwort = await fetch('/automatik', {{headers: {{'Accept': 'application/json'}}}});
      if (!antwort.ok) return;
      d = await antwort.json();
    }} catch (e) {{ return; }}

    const setz = (id, text) => {{
      const el = document.getElementById(id);
      if (el) el.textContent = text;
    }};

    if (d.status === 'untaetig') {{
      const anzahl = (d.aktive_kampagnen || []).length;
      if (!anzahl) return;
      setz('ab-status', 'bereit');
      setz('ab-zahlen', anzahl + ' aktive Kampagne(n) warten auf einen Lauf');
      setz('ab-jetzt', 'Start:  fbgroups campaign automatik --server http://127.0.0.1:8090');
    }} else {{
      setz('ab-status', d.fertig ? 'fertig' : d.status);
      setz('ab-zahlen',
        'Kampagnen ' + d.kampagnen.fertig + '/' + d.kampagnen.gesamt +
        '   ·   Gruppen ' + d.gruppen.fertig + '/' + d.gruppen.gesamt +
        '   ·   Kommentare ' + d.kommentare.fertig + '/' + d.kommentare.gesamt);
      setz('ab-jetzt', d.fertig
        ? 'Alle Kampagnen dieses Laufs sind abgearbeitet.'
        : (d.aktuelle_gruppe
            ? 'Als naechstes: ' + d.aktuelle_kampagne + ' / ' + d.aktuelle_gruppe +
              ' (' + d.aktuelle_kommentare + ')'
            : ''));
    }}

    // Der haeufigste Grund fuer einen kurzen Lauf - und der einzige, den man
    // nicht in der Technik suchen sollte.
    if (d.gruppen_wartend) {{
      setz('ab-warten', d.gruppen_wartend + ' Gruppe(n) ohne Mitgliedschaft - dort '
        + 'wird nichts versucht');
    }}

    // Die Warteschlange: welche Kampagne laeuft, welche folgt, welche ist
    // durch. Abgearbeitet wird sequentiell - erst wenn eine fertig ist,
    // beginnt die naechste. Eine Gesamtzahl beantwortet "wo stehe ich?",
    // aber nicht "was kommt noch?", und das ist beim Arbeiten die Frage.
    const liste = document.getElementById('ab-liste');
    if (liste) {{
      const reihe = d.warteschlange || [];
      // Ueber das DOM gebaut, nicht ueber innerHTML: Ein Kampagnenname ist
      // Text aus der Datenbank, und ein "<" darin braeche sonst die Liste.
      liste.textContent = '';
      for (const k of reihe) {{
        const marke = k.fertig ? 'fertig' : (k.laeuft ? 'laeuft' : 'wartet');
        const zeile = document.createElement('li');
        zeile.className = 'ab-' + marke;
        const name = document.createElement('b');
        name.textContent = k.name;
        const stand = document.createElement('span');
        stand.className = 'ab-marke';
        stand.textContent = marke;
        const rest = document.createTextNode(
          ' ' + k.gruppen_fertig + '/' + k.gruppen_gesamt + ' Gruppen \\u00b7 '
          + k.kommentare_fertig + '/' + k.kommentare_gesamt + ' Kommentare'
          + (k.gruppen_wartend
              ? ' \\u00b7 ' + k.gruppen_wartend + ' ohne Mitgliedschaft' : '')
        );
        zeile.append(name, document.createTextNode(' '), stand, rest);
        liste.appendChild(zeile);
      }}
      liste.hidden = reihe.length === 0;
    }}
    kasten.hidden = false;
  }};
  automatikband();

  const arabisch = (text) => /[\\u0600-\\u06FF]/.test(text || '');
  const finde = (typ, nummer) =>
    (FASSUNGEN[typ] || []).find((f) => f.nummer === nummer);
  const id = (name, typ) => document.getElementById(name + '-' + typ);

  // Ungespeichert? Dann heisst der Kopierknopf anders. Kopiert wird die vom
  // Server eingesetzte Fassung - der Browser setzt keinen Tracking-Link ein.
  const schmutzig = (typ) => {{
    const feld = id('feld', typ);
    const fassung = finde(typ, gewaehlt[typ]);
    return !!(feld && fassung && feld.value !== fassung.text);
  }};

  const zeigeSchmutz = (typ) => {{
    const spalte = document.querySelector('[data-spalte="' + typ + '"]');
    const knopf = id('kopieren', typ);
    if (!spalte || !knopf) return;
    const offen = schmutzig(typ);
    spalte.classList.toggle('ungespeichert', offen);
    knopf.textContent = offen
      ? 'Speichern & kopieren'
      : ZWECK[typ] + ' kopieren';
  }};

  const zeichneStand = (typ) => {{
    const fassung = finde(typ, gewaehlt[typ]);
    if (!fassung) return;
    const abzeichen = id('stand', typ);
    if (abzeichen) {{
      const beschriftung = STAND[fassung.stand] || ['', fassung.stand];
      abzeichen.dataset.stand = fassung.stand;
      abzeichen.textContent = (beschriftung[0] + ' ' + beschriftung[1]).trim();
      if (fassung.post_url) {{
        const a = document.createElement('a');
        a.href = fassung.post_url;
        a.target = '_blank';
        a.rel = 'noopener';
        a.className = 'post-link';
        a.textContent = '[Ansehen]';
        abzeichen.appendChild(document.createTextNode(' '));
        abzeichen.appendChild(a);
      }}
    }}
    const fehlerfeld = id('fehler', typ);
    if (fehlerfeld) fehlerfeld.textContent = fassung.fehler || '';
    const vorlage = id('vorlage', typ);
    if (vorlage) vorlage.textContent = fassung.vorlage || '';
    const knopf = document.querySelector(
      '#nummern-' + typ + ' [data-nummer="' + fassung.nummer + '"]');
    if (knopf) {{
      const beschriftung = STAND[fassung.stand] || ['', fassung.stand];
      knopf.dataset.stand = fassung.stand;
      knopf.title = beschriftung[1];
      const glyphe = knopf.querySelector('.glyphe');
      if (glyphe) glyphe.textContent = beschriftung[0];
    }}
  }};

  // Der einzige Ort, an dem die Spalte umschaltet. Er laedt nichts nach: Alle
  // Fassungen stehen schon auf der Seite - ein Wechsel ist ein Tausch im
  // Textfeld und keine Anfrage.
  const waehle = (typ, nummer) => {{
    const fassung = finde(typ, nummer);
    if (!fassung) return;
    if (schmutzig(typ) && !window.confirm(
        'Der Text von Vorschlag ' + gewaehlt[typ] +
        ' ist nicht gespeichert. Trotzdem wechseln?')) {{
      return;
    }}
    gewaehlt[typ] = nummer;
    const feld = id('feld', typ);
    if (feld) {{
      feld.value = fassung.text;
      feld.dir = arabisch(fassung.text) ? 'rtl' : 'ltr';
    }}
    document.querySelectorAll('#nummern-' + typ + ' .nummer').forEach((k) => {{
      k.classList.toggle('aktiv', Number(k.dataset.nummer) === nummer);
    }});
    const kopf = document.getElementById('kopf-' + typ);
    if (kopf) {{
      kopf.textContent =
        'Vorschlag ' + nummer + ' von ' + (FASSUNGEN[typ] || []).length;
    }}
    const meldung = id('meldung', typ);
    if (meldung) {{ meldung.textContent = ''; }}
    const ausgang = id('ausgang', typ);
    if (ausgang) {{ ausgang.textContent = ''; }}
    zeichneStand(typ);
    zeigeSchmutz(typ);
  }};

  const sende = async (weg, rumpf) => {{
    const antwort = await fetch(ZIEL + weg, {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify(rumpf),
    }});
    return antwort.json();
  }};

  // Speichert **genau** die gewaehlte Fassung. Die Nummer faehrt mit, sonst
  // landete der Text im Nachbarn - und das faellt erst auf, wenn er in der
  // Gruppe steht.
  const speichere = async (typ) => {{
    const feld = id('feld', typ);
    const meldung = id('meldung', typ);
    const fassung = finde(typ, gewaehlt[typ]);
    if (!feld || !fassung) return false;
    const daten = await sende('/vorschlag/text', {{
      group_id: GRUPPE, texttyp: typ, nummer: gewaehlt[typ], text: feld.value,
    }});
    if (!daten.ok) {{
      meldung.className = 'meldung schlecht-text';
      meldung.textContent = daten.meldung || 'Ging nicht.';
      return false;
    }}
    fassung.text = daten.text;
    fassung.angezeigt = daten.angezeigt;
    fassung.stand = daten.stand;
    meldung.className = 'meldung gut-text';
    meldung.textContent = 'Vorschlag ' + gewaehlt[typ] + ' gespeichert.';
    zeichneStand(typ);
    zeigeSchmutz(typ);
    return true;
  }};

  // Wer von Hand aus dem Bearbeitungsfeld kopiert (Strg+A, Strg+C), bekommt
  // den Platzhalter - im Feld **muss** er stehen, sonst liesse sich der Text
  // nicht bearbeiten. Am 31.08.2026 stand deshalb ``{{link}}`` woertlich in
  // einem veroeffentlichten Beitrag.
  //
  // Der Griff in das Kopier-Ereignis loest ihn genau dort auf, wo er sonst
  // hinausginge. Das Feld selbst bleibt unveraendert: Was gespeichert wird,
  // traegt weiterhin den Platzhalter, und nur die Zwischenablage bekommt die
  // fertige Fassung. Wird nur ein Ausschnitt markiert, der kein ``{{link}}``
  // enthaelt, wird nichts angefasst.
  const linkSchutz = (typ) => {{
    const feld = id('feld', typ);
    if (!feld) return;
    feld.addEventListener('copy', (e) => {{
      const auswahl = feld.value.substring(feld.selectionStart, feld.selectionEnd);
      if (!auswahl.includes('{{link}}')) return;
      const fassung = finde(typ, gewaehlt[typ]);
      if (!fassung || !fassung.angezeigt) return;
      // Nur der Platzhalter wird getauscht - der Rest der Auswahl bleibt,
      // wie er ist. Den ganzen Text zu ersetzen waere eine Ueberraschung.
      const link = fassung.angezeigt.match(/https?:\\/\\/\\S+/);
      if (!link) return;
      e.clipboardData.setData('text/plain', auswahl.split('{{link}}').join(link[0]));
      e.preventDefault();
      const meldung = id('meldung', typ);
      if (meldung) {{
        meldung.className = 'meldung gut-text';
        meldung.textContent = 'Link beim Kopieren eingesetzt.';
      }}
    }});
  }};

  const kopiere = async (typ) => {{
    const knopf = id('kopieren', typ);
    const meldung = id('meldung', typ);
    if (schmutzig(typ) && !(await speichere(typ))) return;
    const fassung = finde(typ, gewaehlt[typ]);
    if (!fassung) return;
    const vorher = knopf.textContent;
    try {{
      await navigator.clipboard.writeText(fassung.angezeigt);
      knopf.textContent = 'Kopiert';
      setTimeout(() => {{ knopf.textContent = vorher; }}, 1500);
    }} catch (_) {{
      // Ersatzweg, wenn die asynchrone Zwischenablage nicht erlaubt ist.
      //
      // Hier stand bis zum 31.08.2026 ``feld.select()`` - das markierte das
      // **Bearbeitungsfeld**, und darin steht ``{{link}}``. Wer auf "kopieren"
      // drueckte und Strg+C machte, setzte den Platzhalter in die Gruppe;
      // genau das ist an dem Tag in einem echten Beitrag gelandet. Die
      // Warnzeile darunter half nicht: Sie stand klein und auf Deutsch neben
      // einem arabischen Text.
      //
      // Jetzt wird der **angezeigte** Text ueber ein unsichtbares Hilfsfeld
      // kopiert. Das Bearbeitungsfeld wird dabei nicht angefasst.
      let geschafft = false;
      const hilfsfeld = document.createElement('textarea');
      hilfsfeld.value = fassung.angezeigt;
      hilfsfeld.setAttribute('readonly', '');
      hilfsfeld.style.position = 'fixed';
      hilfsfeld.style.top = '-1000px';
      document.body.appendChild(hilfsfeld);
      hilfsfeld.select();
      try {{ geschafft = document.execCommand('copy'); }} catch (__) {{ geschafft = false; }}
      document.body.removeChild(hilfsfeld);

      meldung.className = geschafft ? 'meldung gut-text' : 'meldung schlecht-text';
      if (geschafft) {{
        knopf.textContent = 'Kopiert';
        setTimeout(() => {{ knopf.textContent = vorher; }}, 1500);
        meldung.textContent = 'Kopiert (Ersatzweg) - mit eingesetztem Link.';
      }} else {{
        // Auch das ging nicht. Dann bekommt der Mensch den **fertigen** Text
        // zum Markieren - niemals das Feld mit dem Platzhalter.
        meldung.textContent =
          'Zwischenablage nicht moeglich. Diesen Text kopieren (er traegt den Link):';
        const kasten = document.createElement('textarea');
        kasten.value = fassung.angezeigt;
        kasten.readOnly = true;
        kasten.rows = 6;
        kasten.style.width = '100%';
        kasten.style.marginTop = '.4rem';
        meldung.appendChild(kasten);
        kasten.focus();
        kasten.select();
      }}
    }}
  }};

  // Meldet den Ausgang - und **bleibt stehen**. Kein Sprung zur naechsten
  // Gruppe, kein Sprung zum naechsten Vorschlag, keine zweite Spalte, die
  // sich mitaendert. Genau das war vorher der Fehler.
  const melde = async (typ, erfolg) => {{
    const ausgang = id('ausgang', typ);
    const grundfeld = id('grundfeld', typ);
    const grund = id('grund', typ);
    if (!erfolg && grundfeld.classList.contains('versteckt')) {{
      grundfeld.classList.remove('versteckt');
      grund.focus();
      return;
    }}
    const daten = await sende('/vorschlag/ergebnis', {{
      group_id: GRUPPE, texttyp: typ, nummer: gewaehlt[typ],
      ausgang: erfolg ? 'veroeffentlicht' : 'fehlgeschlagen',
      fehler: erfolg ? '' : (grund.value || ''),
    }});
    if (!daten.ok) {{
      ausgang.className = 'meldung schlecht-text';
      ausgang.textContent = daten.meldung || 'Ging nicht.';
      return;
    }}
    const fassung = finde(typ, gewaehlt[typ]);
    if (fassung) {{
      fassung.stand = daten.stand;
      fassung.fehler = daten.fehler || '';
    }}
    grundfeld.classList.add('versteckt');
    if (grund) grund.value = '';
    ausgang.className = 'meldung ' + (erfolg ? 'gut-text' : 'schlecht-text');
    ausgang.textContent = erfolg
      ? ZWECK[typ] + ' ' + gewaehlt[typ] + ' steht als veroeffentlicht.'
      : ZWECK[typ] + ' ' + gewaehlt[typ] + ' steht als fehlgeschlagen.';
    zeichneStand(typ);
  }};

  const automatisch = async (typ) => {{
    const knopf = id('auto', typ);
    const meldung = id('meldung', typ);
    if (schmutzig(typ) && !(await speichere(typ))) return;
    const fassung = finde(typ, gewaehlt[typ]);
    if (!fassung) return;
    
    const vorher = knopf.textContent;
    knopf.disabled = true;
    knopf.textContent = 'Laeuft im Browser ...';
    meldung.className = 'meldung';
    meldung.textContent = 'Oeffne Browser und fuehre Aktion aus ...';
    
    try {{
      const daten = await sende('/vorschlag/auto', {{
        group_id: GRUPPE, texttyp: typ, nummer: gewaehlt[typ]
      }});
      if (!daten.ok) {{
        meldung.className = 'meldung schlecht-text';
        meldung.textContent = daten.meldung || 'Automatisierung fehlgeschlagen.';
      }} else {{
        meldung.className = 'meldung gut-text';
        meldung.textContent = daten.meldung || 'Erfolgreich veroeffentlicht!';
        if (fassung) {{
          fassung.stand = daten.stand;
          fassung.fehler = daten.fehler || '';
          if (daten.post_url) {{
            fassung.post_url = daten.post_url;
          }}
        }}
        zeichneStand(typ);
      }}
    }} catch (e) {{
      meldung.className = 'meldung schlecht-text';
      meldung.textContent = 'Verbindungsfehler zur Automatisierung.';
    }} finally {{
      knopf.disabled = false;
      knopf.textContent = vorher;
    }}
  }};

  ['post', 'kommentar'].forEach((typ) => {{
    if (!(FASSUNGEN[typ] || []).length) return;

    document.querySelectorAll('#nummern-' + typ + ' .nummer').forEach((knopf) => {{
      knopf.addEventListener('click', () => waehle(typ, Number(knopf.dataset.nummer)));
    }});
    const spalte = document.querySelector('[data-spalte="' + typ + '"]');
    spalte.querySelectorAll('[data-schieben]').forEach((pfeil) => {{
      pfeil.addEventListener('click', () => {{
        const anzahl = FASSUNGEN[typ].length;
        const schritt = Number(pfeil.dataset.schieben);
        const nummern = FASSUNGEN[typ].map((f) => f.nummer);
        const platz = nummern.indexOf(gewaehlt[typ]);
        waehle(typ, nummern[(platz + schritt + anzahl) % anzahl]);
      }});
    }});

    const feld = id('feld', typ);
    if (feld) feld.addEventListener('input', () => zeigeSchmutz(typ));

    id('speichern', typ).addEventListener('click', async (e) => {{
      e.target.disabled = true;
      try {{ await speichere(typ); }} finally {{ e.target.disabled = false; }}
    }});
    id('kopieren', typ).addEventListener('click', () => kopiere(typ));
    linkSchutz(typ);
    const autoknopf = id('auto', typ);
    if (autoknopf) autoknopf.addEventListener('click', () => automatisch(typ));

    const gut = id('gut', typ);
    if (gut && !gut.disabled) gut.addEventListener('click', () => melde(typ, true));
    const schlecht = id('schlecht', typ);
    if (schlecht && !schlecht.disabled) {{
      schlecht.addEventListener('click', () => melde(typ, false));
      // Enter im Grundfeld darf nicht den ersten Knopf ausloesen - der heisst
      // "veroeffentlicht", und wer gerade den Fehlergrund tippt, meldete
      // damit das Gegenteil dessen, was er meint.
      id('grund', typ).addEventListener('keydown', (e) => {{
        if (e.key === 'Enter') {{ e.preventDefault(); melde(typ, false); }}
      }});
    }}

    zeigeSchmutz(typ);
  }});

  // Die Gruppen-Navigation fuehrt von der Seite weg (Auswahlliste, Pfeile,
  // "Uebersicht"). Ein ungespeicherter Text waere damit weg, ohne dass
  // jemand es merkt - und er ist genau das, woran eben gearbeitet wurde.
  // Der Wechsel zwischen zwei Fassungen fragt selbst nach; hier faengt der
  // Browser alles Uebrige ab.
  window.addEventListener('beforeunload', (e) => {{
    if (schmutzig('post') || schmutzig('kommentar')) {{
      e.preventDefault();
      e.returnValue = '';
    }}
  }});
}})();
</script>"""
