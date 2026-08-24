"""Die Arbeitsseite - ein Beitrag, ein Bildschirm, drei Knoepfe.

Der Grund, warum es sie gibt: Der Bestand lebt auf dem Server, aber
``campaign worker`` braucht Zwischenablage und Browser, die es dort nicht
gibt. Beides auf den Arbeitsrechner zu holen hiesse, in eine zweite Datenbank
zu schreiben - und genau davor warnt ``docs/plan-go-subdomain.md``: Klicks
entstehen auf dem Server, und zwei Fassungen laufen unbemerkt auseinander.

Die Aufloesung ist, die **Arbeit** dorthin zu holen, wo der Bestand steht.
Der Server braucht keine Zwischenablage - der Browser des Menschen hat eine.
Die Regeln (Tageslimit, Reihenfolge, Wartezeit, Protokoll, Zustand der
Warteschlange) liegen dabei unveraendert in ``arbeit.py``; diese Datei malt
sie nur.

**Die Seite haelt keinen Zustand.** Jeder Aufruf fragt ``hole_auftrag`` neu.
Wer den Reiter schliesst, den Rechner wechselt oder zwei Fenster offen hat,
bekommt denselben angefangenen Beitrag - er steht in der Datenbank, nicht im
Browser. Ein Zustand im Browser waere eine zweite Wahrheit ueber dieselbe
Warteschlange.

**Der Text wird nicht in ein Formularfeld gelegt, aus dem er wieder
zurueckkommt.** Er geht nur hinaus. Was zurueckkommt, ist der Ausgang und die
``versuch_id`` - so kann ein manipuliertes Formular keinen anderen Text in
einen Beitrag bringen, als der Server vorbereitet hat.
"""

from __future__ import annotations

from html import escape

from fbgroups.marketing.arbeit import Auftrag, Grund, Sperre

# Bewusst dieselbe Handschrift wie ``dashboard.py``: dunkler Grund, ein Akzent,
# keine Bilder. Wer zwischen Uebersicht und Arbeitsseite wechselt, soll nicht
# das Gefuehl haben, das Werkzeug gewechselt zu haben.
_STIL = """
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body { margin:0; padding:2rem 1rem; background:#14161a; color:#e6e8eb;
       font:16px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif; }
.huelle { max-width:52rem; margin:0 auto; }
h1 { font-size:1.3rem; margin:0 0 .25rem; font-weight:600; }
.leiste { display:flex; gap:1rem; flex-wrap:wrap; align-items:baseline;
          color:#8b929c; font-size:.85rem; margin-bottom:1.5rem; }
.leiste b { color:#e6e8eb; font-weight:600; }
.karte { background:#1c1f26; border:1px solid #2a2f38; border-radius:10px;
         padding:1.25rem 1.5rem; margin-bottom:1rem; }
.gruppe { font-size:1.15rem; font-weight:600; margin:0 0 .25rem; }
.code { font-family:ui-monospace,"Cascadia Code",Consolas,monospace;
        font-size:.85rem; color:#7cc4ff; }
.beitrag { white-space:pre-wrap; word-wrap:break-word; background:#0f1114;
           border:1px solid #2a2f38; border-radius:8px; padding:1rem;
           margin:1rem 0; font-size:.95rem; }
/* Arabisch laeuft von rechts nach links - ohne dies steht die Satzzeichen-
   folge falsch, und der Text sieht aus wie ein Fehler. */
.beitrag[dir="rtl"] { text-align:right; }
.knoepfe { display:flex; gap:.6rem; flex-wrap:wrap; margin-top:1.25rem; }
button, .knopf { font:inherit; font-size:.92rem; padding:.6rem 1.1rem;
                 border-radius:7px; border:1px solid #2a2f38; cursor:pointer;
                 background:#252a33; color:#e6e8eb; text-decoration:none;
                 display:inline-block; }
button:hover, .knopf:hover { background:#2f3540; }
.gut { background:#1f6f43; border-color:#2b8a54; }
.gut:hover { background:#248050; }
.schlecht { background:#7a2e2e; border-color:#993a3a; }
.schlecht:hover { background:#8d3636; }
.haupt { background:#1d4ed8; border-color:#2563eb; font-weight:600; }
.haupt:hover { background:#2563eb; }
.sperre { text-align:center; padding:3rem 1.5rem; }
.sperre .grund { font-size:1.2rem; font-weight:600; margin-bottom:.5rem; }
.sperre .dazu { color:#8b929c; }
.uhr { font-size:2.5rem; font-family:ui-monospace,monospace; color:#7cc4ff;
       margin:1rem 0; }
.hinweis { color:#8b929c; font-size:.85rem; margin-top:1.5rem; }
label { display:block; margin:.75rem 0 .25rem; color:#8b929c; font-size:.85rem; }
input[type=text] { width:100%; padding:.55rem .7rem; border-radius:7px;
                   border:1px solid #2a2f38; background:#0f1114; color:#e6e8eb;
                   font:inherit; font-size:.92rem; }
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


def _ist_arabisch(text: str) -> bool:
    """Grob, aber ausreichend: Steht arabische Schrift ueberhaupt drin?

    Entscheidet nur ueber die Leserichtung der Anzeige. Der Text selbst wird
    nicht angefasst - er geht Zeichen fuer Zeichen so hinaus, wie er
    vorbereitet wurde.
    """
    return any("؀" <= zeichen <= "ۿ" for zeichen in text)


def render_sperre(sperre: Sperre, campaign_id: str) -> str:
    """Die Seite, wenn gerade nichts drankommt - mit dem Grund, nicht nur leer."""
    dazu = ""
    inhalt = f"<div class='grund'>{escape(sperre.grund)}</div>"

    if sperre.grund == Grund.WARTEZEIT:
        # Die Uhr laeuft im Browser herunter und laedt dann selbst neu. Ohne
        # das sitzt jemand vor "warte noch" und drueckt alle zehn Sekunden F5.
        inhalt += (
            f"<div class='uhr' id='uhr'>{sperre.wartet_noch}s</div>"
            "<div class='dazu'>Der Abstand zwischen zwei Beitraegen. "
            "Die Seite laedt von selbst neu.</div>"
            "<script>"
            f"let rest={sperre.wartet_noch};"
            "const u=document.getElementById('uhr');"
            "setInterval(()=>{rest--;u.textContent=rest+'s';"
            "if(rest<=0)location.reload();},1000);"
            "</script>"
        )
    elif sperre.grund == Grund.TAGESLIMIT:
        dazu = (
            f"Heute {sperre.heute_schon} von {sperre.tageslimit} Versuchen. "
            "Morgen geht es weiter."
        )
    elif sperre.grund == Grund.PAUSIERT:
        dazu = "Fortsetzen auf der Uebersicht oder mit: fbgroups campaign resume"
    elif sperre.grund == Grund.GESTOPPT:
        dazu = "Die Warteschlange wurde geraeumt. Neu einreihen mit: campaign enqueue"
    else:
        dazu = "Nachfuellen mit:  fbgroups campaign enqueue " + campaign_id

    if dazu:
        inhalt += f"<div class='dazu'>{escape(dazu)}</div>"

    return (
        _kopf(f"Arbeit - {campaign_id}")
        + f"<h1>{escape(campaign_id)}</h1>"
        + "<div class='leiste'><a class='knopf' href='/'>&larr; Uebersicht</a></div>"
        + f"<div class='karte sperre'>{inhalt}</div>"
        + _FUSS
    )


def render_auftrag(auftrag: Auftrag, campaign_id: str) -> str:
    """Ein Beitrag: Text zum Kopieren, Gruppe zum Oeffnen, Ausgang zum Melden."""
    richtung = "rtl" if _ist_arabisch(auftrag.text) else "ltr"
    rest = max(0, auftrag.tageslimit - auftrag.heute_schon)

    return (
        _kopf(f"{auftrag.name} - {campaign_id}")
        + f"<h1>{escape(auftrag.name)}</h1>"
        + (
            "<div class='leiste'>"
            f"<span class='code'>{escape(auftrag.link.tracking_code)}</span>"
            f"<span>heute <b>{auftrag.heute_schon}</b> von {auftrag.tageslimit}"
            f" &middot; noch <b>{rest}</b> moeglich</span>"
            f"<span><b>{auftrag.offen}</b> in der Warteschlange</span>"
            "<a class='knopf' href='/'>&larr; Uebersicht</a>"
            "</div>"
        )
        + "<div class='karte'>"
        + f"<div class='beitrag' dir='{richtung}' id='beitrag'>{escape(auftrag.text)}</div>"
        + "<div class='knoepfe'>"
        + "<button type='button' class='haupt' id='kopieren'>Text kopieren</button>"
        + (
            f"<a class='knopf' href='{escape(auftrag.url)}' target='_blank' "
            "rel='noopener noreferrer'>Gruppe oeffnen</a>"
            if auftrag.url
            else ""
        )
        + "</div></div>"
        # Die Rueckmeldung. Nur der Ausgang geht zurueck, nie der Text.
        + "<div class='karte'>"
        + "<form method='post' action='/arbeit/"
        + escape(campaign_id)
        + "/ergebnis'>"
        + f"<input type='hidden' name='versuch_id' value='{auftrag.versuch_id}'>"
        + f"<input type='hidden' name='group_id' value='{escape(auftrag.link.group_id)}'>"
        + "<div id='grundfeld' class='versteckt'>"
        + "<label for='fehler'>Was ging schief?</label>"
        + "<input type='text' id='fehler' name='fehler' "
        + "placeholder='z. B. erlaubt keine Links'>"
        + "</div>"
        + "<div class='knoepfe'>"
        + "<button type='submit' name='ausgang' value='veroeffentlicht' class='gut'>"
        + "Veroeffentlicht &rarr; naechste</button>"
        + "<button type='submit' name='ausgang' value='fehlgeschlagen' class='schlecht' "
        + "id='fehlknopf'>Fehlgeschlagen</button>"
        + "<button type='submit' name='ausgang' value='uebersprungen'>"
        + "Passt nicht</button>"
        + "<button type='submit' name='ausgang' value='schluss'>Schluss fuer heute</button>"
        + "</div></form></div>"
        + (
            "<div class='hinweis'>Der Tracking-Code steht im Text. "
            "Einfuegen, absenden, dann hier den Ausgang melden - "
            "erst danach kommt die naechste Gruppe.</div>"
        )
        + """<script>
document.getElementById('kopieren').addEventListener('click', async (e) => {
  const text = document.getElementById('beitrag').textContent;
  try {
    await navigator.clipboard.writeText(text);
  } catch (_) {
    // Ohne HTTPS gibt es keine Zwischenablage-Berechtigung. Dann wird der
    // Text markiert - kopieren kann der Mensch selbst, und er sieht sofort,
    // was gemeint ist.
    const bereich = document.createRange();
    bereich.selectNodeContents(document.getElementById('beitrag'));
    const auswahl = window.getSelection();
    auswahl.removeAllRanges();
    auswahl.addRange(bereich);
    e.target.textContent = 'Markiert - Strg+C';
    return;
  }
  e.target.textContent = 'Kopiert';
  setTimeout(() => { e.target.textContent = 'Text kopieren'; }, 1500);
});
// "Fehlgeschlagen" fragt erst nach dem Grund. Ohne ihn ist ein Fehlschlag
// spaeter nicht zu deuten, und "retry" liefe in denselben Fehler.
document.getElementById('fehlknopf').addEventListener('click', (e) => {
  const feld = document.getElementById('grundfeld');
  if (feld.classList.contains('versteckt')) {
    e.preventDefault();
    feld.classList.remove('versteckt');
    document.getElementById('fehler').focus();
  }
});
</script>"""
        + _FUSS
    )
