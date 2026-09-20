"""Kommandozeile.

    fbgroups serve                     Dienst starten: Uebersicht und Tracking
    fbgroups config-check              Konfiguration pruefen
    fbgroups auth login                Interaktiver Browser-Login fuer Automatisierung
    fbgroups campaign ...              Kampagnen: Zuordnung, Texte, Lauf
    fbgroups marketing ...             Arbeitsstand, Auswertung, Praemien

Die Entdeckungsschicht ist am 20.09.2026 entfernt worden: ``import-seeds``,
``pruefliste``, ``rescore``, ``enrich``, ``report``, ``export``, ``queries``,
``providers``, ``search`` und ``search-log`` gibt es nicht mehr, ebenso wenig
die fuenf Konfigurationsdateien, an denen sie hingen (``audiences.yaml``,
``cities.yaml``, ``categories.yaml``, ``queries.yaml``, ``providers.yaml``).
Was der Bestand ueber eine Gruppe weiss, wird seither gepflegt und nicht mehr
aus Begriffslisten abgeleitet.
"""

from __future__ import annotations

import threading

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from fbgroups.config import AppConfig, load_config
from fbgroups.marketing.cli import campaign_app, marketing_app
from fbgroups.marketing.tracking import app_base_url

app = typer.Typer(
    add_completion=False,
    help="Facebook Groups Finder - Germany (with Automation features)",
)
console = Console()

auth_app = typer.Typer(help="Authentication and browser session management.")
app.add_typer(auth_app, name="auth")

# Die Marketing-Erweiterung haengt sich als eigene Unterbefehle an.
app.add_typer(campaign_app, name="campaign")
app.add_typer(marketing_app, name="marketing")


def _config() -> AppConfig:
    try:
        return load_config()
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]Konfigurationsfehler:[/red] {exc}")
        raise typer.Exit(code=1) from exc


@app.command("serve")
def serve_command(
    host: str = typer.Option("127.0.0.1", "--host", help="Bindeadresse."),
    port: int = typer.Option(3000, "--port"),
    reload: bool = typer.Option(False, "--reload", help="Neu laden bei Codeaenderung."),
) -> None:
    """Startet den Dienst: Uebersichtsseite und Tracking-Links.

    ``/`` zeigt den Bestand im Browser, ``/r/{code}`` zaehlt einen Klick und
    leitet zur Landingpage weiter, ``POST /events`` nimmt die Meldungen der
    Zielanwendung entgegen. Es wird nichts bei Facebook abgerufen und nichts
    veroeffentlicht.

    Standardmaessig nur lokal erreichbar (127.0.0.1). Wer den Dienst oeffentlich
    stellt, sollte ihn hinter einen Reverse Proxy mit TLS setzen; die
    Uebersichtsseite bleibt dabei auf den eigenen Rechner beschraenkt.
    """
    try:
        import uvicorn
    except ImportError as exc:
        console.print(
            "[red]Der Dienst braucht zwei zusaetzliche Pakete.[/red]\n"
            'Installieren mit:  [bold]pip install -e ".[web]"[/bold]'
        )
        raise typer.Exit(code=1) from exc

    config = _config()
    basis = app_base_url(config)
    console.print(
        Panel(
            f"Uebersicht im Browser: [bold]http://{host}:{port}/[/bold]\n"
            f"Tracking-Links zeigen auf: [bold]{basis or '(APP_BASE_URL fehlt)'}[/bold]\n\n"
            "  GET  /            Uebersicht (nur vom eigenen Rechner)\n"
            # Kein f-String: die geschweiften Klammern bleiben wie geschrieben.
            "  GET  /r/{code}    Klick zaehlen und weiterleiten\n"
            "  POST /events      Meldung der Zielanwendung\n"
            "  GET  /healthz     Lebenszeichen\n\n"
            "[dim]Beenden mit Strg+C[/dim]",
            title="fbgroups serve",
        )
    )
    if basis and f":{port}" not in basis and not reload:
        console.print(
            "[yellow]Hinweis:[/yellow] APP_BASE_URL und der Port passen nicht zusammen - "
            "die veroeffentlichten Links wuerden woanders landen."
        )

    uvicorn.run(
        "fbgroups.marketing.web:create_app",
        factory=True,
        host=host,
        port=port,
        reload=reload,
    )


@app.command("config-check")
def config_check_command() -> None:
    """Prueft, ob die Konfiguration lesbar und plausibel ist.

    Geprueft werden zwei Dateien: ``settings.yaml`` und ``textvorlagen.yaml``.
    Bis zum 20.09.2026 waren es sieben; die fuenf der Entdeckungsschicht sind
    mit ihr entfernt.
    """
    config = _config()

    table = Table(title="Konfiguration", show_header=False, box=None)
    table.add_row("Wurzel", str(config.root))
    for name in ("settings.yaml", "textvorlagen.yaml"):
        datei = config.root / "config" / name
        table.add_row(name, "gelesen" if datei.exists() else "[yellow]fehlt[/yellow]")
    console.print(table)

    # Die Gewichte werden gegen die Registry geprueft, nicht gegen eine Liste
    # im Programm: Ein Name, den es nicht gibt, ist ein Tippfehler und keine
    # Erweiterung - er wuerde sonst still ignoriert, und der Bestandteil,
    # den er meinte, liefe mit seiner Vorgabe weiter.
    from fbgroups.scoring import BESTANDTEILE
    from fbgroups.scoring import gewichte as geltende_gewichte

    eingetragen = config.get("scoring", "weights", default={}) or {}
    unbekannt = sorted(set(eingetragen) - set(BESTANDTEILE))
    if unbekannt:
        console.print(
            f"[yellow]Warnung: unbekannte Bestandteile in scoring.weights: "
            f"{', '.join(unbekannt)}. Bekannt sind: "
            f"{', '.join(BESTANDTEILE)}.[/yellow]"
        )

    aktiv = geltende_gewichte(config)
    total = sum(aktiv.values())
    if abs(total - 100.0) > 0.01:
        console.print(
            f"[yellow]Warnung: Summe der Score-Gewichte ist {total:g}, erwartet 100.[/yellow]"
        )
    else:
        console.print("[green]Score-Gewichte ergeben 100.[/green]")

    # Reichweite und Betrieb sollen zusammen die Haelfte tragen. Das ist die
    # fachliche Vorgabe, und sie faellt beim Verschieben eines einzelnen
    # Gewichts leicht unter den Tisch - deshalb steht sie hier als Pruefung
    # und nicht nur als Kommentar in settings.yaml.
    haelfte = aktiv.get("members", 0.0) + aktiv.get("activity", 0.0)
    if abs(haelfte - 50.0) > 0.01:
        console.print(
            f"[yellow]Hinweis: Mitglieder + Aktivitaet ergeben {haelfte:g} statt 50 "
            f"Punkte. Der Score entsteht damit ueberwiegend aus der Passung.[/yellow]"
        )

    teile = ", ".join(
        f"{BESTANDTEILE[name].label} {gewicht:g}" for name, gewicht in aktiv.items()
    )
    console.print(f"[dim]Bestandteile: {teile}.[/dim]")
    abgeschaltet = sorted(set(BESTANDTEILE) - set(aktiv))
    if abgeschaltet:
        console.print(
            f"[dim]Abgeschaltet (Gewicht 0): "
            f"{', '.join(BESTANDTEILE[n].label for n in abgeschaltet)}.[/dim]"
        )

    # Die Beitragsvorlagen. Geprueft wird, was sich still auswirkt: eine
    # Vorlage ohne {link} ergaebe einen Beitrag, dessen Gruppe nie einen Klick
    # gutgeschrieben bekommt - und das faellt erst auf, wenn er in der Gruppe
    # steht. Ein fehlender Vorrat ist dagegen kein Fehler: Eine Kampagne darf
    # ihre eigene Vorlage mitbringen.
    from fbgroups.marketing import vorlagen

    beanstandungen = vorlagen.pruefe(config)
    # Drei Ebenen: Sprache, Einsatzzweck (Beitrag/Kommentar), Topf. Der Zweck
    # kam mit den Kommentaren dazu - ohne ihn zaehlte die Summe die Toepfe
    # statt der Fassungen und meldete "2 Fassungen" fuer zwanzig.
    anzahl = sum(
        len(liste)
        for zwecke in (config.textvorlagen.get("vorlagen") or {}).values()
        for toepfe in (zwecke or {}).values()
        for liste in (toepfe or {}).values()
    )
    if beanstandungen:
        console.print(f"[yellow]Beitragsvorlagen ({anzahl}) mit Beanstandung:[/yellow]")
        for beanstandung in beanstandungen[:8]:
            console.print(f"  - {beanstandung}")
    else:
        console.print(f"[green]Beitragsvorlagen in Ordnung:[/green] {anzahl} Fassungen.")

    # --- Zielprioritaet (13.09.2026) ---------------------------------------
    #
    # Geprueft wird dasselbe wie bei den Score-Gewichten: ein Name, den es
    # nicht gibt, ist ein Tippfehler und keine Erweiterung. Er faellt hier
    # schwerer auf als dort - die Klasse A verschwaende still, und die
    # Kampagne arbeitete wieder in den Gemeinschaftsgruppen, ohne dass etwas
    # eine Fehlermeldung gaebe.
    from fbgroups.marketing import zielgruppe

    block = config.get("marketing", "zielprioritaet", default={}) or {}
    kategorien = {str(k) for k in (block.get("kategorien") or [])}
    audiences = {str(a) for a in (block.get("audiences") or [])}
    # Gegengeprueft wird gegen den **Bestand**: ``categories.yaml`` und
    # ``audiences.yaml`` gibt es nicht mehr, und welche Kategorien vorkommen,
    # weiss seither allein der Bestand. Ist er leer (frische Datei), wird
    # nicht gewarnt - sonst meldete ein jungfraeuliches Projekt jeden
    # richtigen Eintrag als Tippfehler.
    from fbgroups.storage import SqliteStore as _Store

    try:
        with _Store(config.path("sqlite_path")) as _gs:
            _gruppen = _gs.load_groups()
    except Exception:  # noqa: BLE001 - ohne Bestand wird eben nicht gegengeprueft
        _gruppen = []

    vorhandene_kat = {(g.category or "").strip() for g in _gruppen if g.category}
    vorhandene_aud = {t for g in _gruppen for t in (g.audience_tags or [])}
    unbekannte_kat = sorted(kategorien - vorhandene_kat) if vorhandene_kat else []
    unbekannte_aud = sorted(audiences - vorhandene_aud) if vorhandene_aud else []

    if not block:
        console.print(
            "[yellow]Hinweis: marketing.zielprioritaet fehlt - dann gilt jede "
            "Gruppe als Klasse D und der Lauf bearbeitet keine.[/yellow]"
        )
    if unbekannte_kat:
        console.print(
            f"[yellow]Warnung: Kategorien aus marketing.zielprioritaet.kategorien "
            f"kommen im Bestand nicht vor: {', '.join(unbekannte_kat)}. "
            f"Im Bestand stehen: {', '.join(sorted(vorhandene_kat))}.[/yellow]"
        )
    if unbekannte_aud:
        console.print(
            f"[yellow]Warnung: Zielgruppen aus marketing.zielprioritaet.audiences "
            f"kommen im Bestand nicht vor: {', '.join(unbekannte_aud)}.[/yellow]"
        )
    if not _gruppen:
        console.print(
            "[dim]Der Bestand ist leer - Kategorien und Zielgruppen wurden "
            "nicht gegengeprueft.[/dim]"
        )
    if block and not unbekannte_kat and not unbekannte_aud:
        anspruch = zielgruppe.anspruch_aus_config(config)
        stufen = ", ".join(
            f"{klasse.value.upper()} ab {stufe.value}"
            + (" + Strecke" if strecke else "")
            for klasse, (stufe, strecke) in sorted(anspruch.items(), key=lambda kv: kv[0].value)
        )
        console.print(
            f"[green]Zielprioritaet in Ordnung:[/green] A = "
            f"{', '.join(sorted(kategorien))} + Ziel, B = {', '.join(sorted(audiences))} "
            f"+ Deutschland."
        )
        console.print(
            f"[dim]Mindestrelevanz je Klasse: {stufen}. "
            f"In D wird nicht geantwortet.[/dim]"
        )

        # --- Der geografische Vorrang (14.09.2026) ------------------------
        #
        # Die beiden Laenderlisten werden gezaehlt und nicht nur genannt.
        # Eine leere ``ausserhalb``-Liste ist der stillste Fehler, den es
        # hier gibt: Es sieht alles richtig aus, nur faellt keine einzige
        # Gruppe mehr aus dem Zielmarkt heraus - "نقل من لبنان إلى سورية"
        # stuende wieder vor den deutschen Gruppen. Dasselbe gilt fuer
        # ``europa``: ohne sie ist jede oesterreichische Gruppe "Land
        # unbekannt" und rutscht hinter die, die Deutschland nennen.
        zielregeln = zielgruppe.regeln_aus_config(config)
        fehlend = [
            name
            for name, liste in (
                ("europa", zielregeln.europa),
                ("ausserhalb", zielregeln.ausserhalb),
            )
            if not liste
        ]
        if fehlend:
            console.print(
                f"[yellow]Warnung: marketing.zielprioritaet.{' und .'.join(fehlend)} "
                f"ist leer. Dann entscheidet allein die Klasse, und eine Gruppe "
                f"mit einer Strecke ausserhalb Europas steht wieder vor den "
                f"deutschen.[/yellow]"
            )
        else:
            # Ein Wort, das in beiden Listen steht, waere ein Widerspruch mit
            # stiller Aufloesung: ``bestimme_region`` fragt Europa zuerst,
            # also gewaenne es immer - und die zweite Liste saehe aus, als
            # taete sie etwas.
            doppelt = sorted(set(zielregeln.europa) & set(zielregeln.ausserhalb))
            if doppelt:
                console.print(
                    f"[yellow]Warnung: in europa UND ausserhalb: "
                    f"{', '.join(doppelt)}. Europa gewinnt - der Eintrag in "
                    f"ausserhalb wirkt nie.[/yellow]"
                )
            # Und ein Ziel, das zugleich als "ausserhalb" gilt, nimmt den
            # ganzen Zielmarkt mit: Jede Gruppe nennt ihr Ziel.
            ziel_kollision = sorted(set(zielregeln.ziele) & set(zielregeln.ausserhalb))
            if ziel_kollision:
                console.print(
                    f"[red]Fehler: {', '.join(ziel_kollision)} steht in ziele UND "
                    f"ausserhalb. Damit faellt jede Gruppe des Zielmarkts aus "
                    f"Klasse A heraus.[/red]"
                )
            console.print(
                f"[dim]Geografischer Vorrang: Deutschland "
                f"({len(zielregeln.herkunft)} Woerter) vor Europa "
                f"({len(zielregeln.europa)}) vor unbekannt. "
                f"{len(zielregeln.ausserhalb)} Woerter nehmen eine Gruppe "
                f"aus Klasse A heraus.[/dim]"
            )

    # --- Grenzen je Aktion ------------------------------------------------
    #
    # Sie stehen hier, weil sie die einzige Einstellung sind, die den Lauf
    # taeglich begrenzt - und weil zwei Schluessel dieselbe Zahl tragen
    # koennen (``limits`` und der alte ``beitritt``-Block). Gezeigt wird, was
    # wirklich gilt, nicht was dasteht.
    from fbgroups.marketing import grenzen

    gelesen = grenzen.einstellungen(config)
    tabelle = Table(title="Grenzen je Aktion (Planungswerte, keine Facebook-Grenzen)")
    tabelle.add_column("Aktion")
    tabelle.add_column("je Tag", justify="right")
    tabelle.add_column("Abstand")
    for aktion in grenzen.Aktion:
        grenze = gelesen.fuer(aktion)
        tabelle.add_row(
            aktion.value,
            "aus" if grenze.abgeschaltet else str(grenze.pro_tag),
            f"{grenze.abstand_min}-{grenze.abstand_max} Min",
        )
    console.print(tabelle)

    alt_gesetzt = config.get("beitritt", "anfragen_pro_tag", default=None)
    neu_gesetzt = config.get("limits", "join_requests", "daily", default=None)
    if alt_gesetzt is not None and neu_gesetzt is not None and int(alt_gesetzt) != int(
        neu_gesetzt
    ):
        console.print(
            f"[dim]Hinweis: beitritt.anfragen_pro_tag ({alt_gesetzt}) wird von "
            f"limits.join_requests.daily ({neu_gesetzt}) ueberstimmt.[/dim]"
        )


@auth_app.command("login")
def login_command() -> None:
    """Oeffnet einen sichtbaren Browser fuer die Anmeldung bei Facebook.

    Drei Dinge unterscheiden diesen Befehl von einem blossen ``goto``:

    * **Die vorhandene Seite wird benutzt, keine zweite geoeffnet.** Ein
      dauerhafter Kontext bringt bereits eine Seite mit; ``new_page()`` machte
      daraus zwei Fenster, und wer das falsche schloss, wartete danach auf
      eines, das niemand mehr ansah.
    * **Ein geschlossenes Fenster ist keine Ausnahme, sondern das Ende.**
      Vorher stieg ``page.goto`` mit ``TargetClosedError`` samt Traceback aus,
      wenn jemand das Fenster waehrend des Ladens schloss - ein Abbruch, der
      wie ein Programmfehler aussah.
    * **Gemeldet wird, was wirklich gespeichert wurde.** Die alte Fassung
      schrieb "Session saved!" auch dann, wenn kein einziges Cookie
      zurueckblieb. Eine falsche Erfolgsmeldung ist hier teuer: Der naechste
      Lauf sucht dann das Schreibfeld auf einer Anmeldeseite und meldet, die
      Gruppe erlaube kein Posten.
    """
    import contextlib

    from fbgroups.automation.browser import get_browser_context

    config = _config()
    console.print("[yellow]Es oeffnet sich ein sichtbares Browserfenster.[/yellow]")
    console.print("Melde dich bei Facebook an (ggf. mit 2FA) und schliesse dann das Fenster.")

    with get_browser_context(config, headless=False) as context:
        # Der dauerhafte Kontext hat schon eine Seite - die nehmen wir.
        page = context.pages[0] if context.pages else context.new_page()

        with contextlib.suppress(Exception):
            page.goto("https://www.facebook.com/", wait_until="domcontentloaded")

        console.print("Der Browser ist offen. Schliesse das Fenster, wenn du fertig bist ...")

        # Auf das Ende des ganzen Kontexts warten, nicht auf eine einzelne
        # Seite: Wer sich anmeldet, landet oft in einem neuen Tab.
        #
        # Gewartet wird *in* Playwright, nicht daneben. Die sync-API laeuft auf
        # Greenlets desselben Threads: Ereignisse werden nur zugestellt,
        # solange der Aufrufer selbst in die Bibliothek springt. Ein
        # ``threading.Event.wait()`` tut das nicht - das ``close``-Ereignis
        # blieb liegen, ``context.pages`` behielt seinen alten Stand, und der
        # Befehl lief nach dem Schliessen des Fensters ewig weiter, statt die
        # Sitzung zu pruefen. ``wait_for_timeout`` ist ein Aufruf in Playwright
        # und laesst den Verteiler an die Reihe kommen.
        fertig = threading.Event()
        context.on("close", lambda _: fertig.set())
        with contextlib.suppress(Exception):
            while not fertig.is_set() and context.pages:
                context.pages[0].wait_for_timeout(500)

    # Nach dem Schliessen liegt das Profil auf der Platte - erst jetzt laesst
    # sich nachsehen, ob wirklich eine Sitzung entstanden ist.
    #
    # Zwei Orte, und der zweite ist der heutige: Neuere Chromium-Fassungen
    # legen die Cookies unter ``Default/Network/`` ab. Nur am alten Pfad
    # gesucht, meldete dieser Befehl "keine Sitzung" fuer eine Anmeldung, die
    # tatsaechlich gespeichert war.
    profil = config.path("data_dir") / "browser_state" / "Default"
    cookies = next(
        (p for p in (profil / "Network" / "Cookies", profil / "Cookies") if p.exists()),
        None,
    )
    if cookies is not None and cookies.stat().st_size > 0:
        console.print("[green]Sitzung gespeichert.[/green] Die Automatisierung kann sie nutzen.")
    else:
        console.print(
            "[red]Keine Sitzung gespeichert.[/red] Das Fenster wurde offenbar vor der "
            "Anmeldung geschlossen - bitte noch einmal, und diesmal erst nach dem "
            "Einloggen schliessen."
        )
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
