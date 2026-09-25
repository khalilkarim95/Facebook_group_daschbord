"""Kommandozeile.

    fbgroups import-mitglieder PFAD    Eigene Mitgliederliste einlesen
    fbgroups serve                     Dienst starten: Uebersicht und Tracking
    fbgroups config-check              Konfiguration pruefen
    fbgroups sicherung                 Bestand sichern (--liste, --zurueck)
    fbgroups auth login               Interaktiver Browser-Login fuer Automatisierung
    fbgroups campaign ...              Kampagnen: Zuordnung, Texte, Lauf
    fbgroups marketing ...             Arbeitsstand, Auswertung, Praemien

Die Entdeckungsschicht ist am 20.09.2026 entfernt worden: ``import-seeds``,
``pruefliste``, ``rescore``, ``enrich``, ``report``, ``export``, ``queries``,
``providers``, ``search`` und ``search-log`` gibt es nicht mehr, ebenso wenig
die fuenf Konfigurationsdateien, an denen sie hingen (``audiences.yaml``,
``cities.yaml``, ``categories.yaml``, ``queries.yaml``, ``providers.yaml``).
Was der Bestand ueber eine Gruppe weiss, wird seither gepflegt und nicht mehr
aus Begriffslisten abgeleitet. ``import-mitglieder`` ist seither der einzige
Weg, Gruppen in den Bestand zu bekommen.
"""

from __future__ import annotations

import threading
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from fbgroups.config import AppConfig, load_config
from fbgroups.marketing.cli import campaign_app, marketing_app
from fbgroups.marketing.tracking import app_base_url
from fbgroups.mitglieder import lies_mitgliederdatei
from fbgroups.models import AKTIVITAETSSTUFEN, LISTENPRIORITAETEN
from fbgroups.scoring import score_all
from fbgroups.storage import SqliteStore

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


@app.command("import-mitglieder")
def import_mitglieder_command(
    pfad: Path = typer.Argument(
        ...,
        help="CSV mit der eigenen Mitgliederliste (Spalten: url, name, category, activity, ...).",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Nur anzeigen, nichts speichern."
    ),
    status_mitglied: bool = typer.Option(
        True,
        "--mitglied/--ohne-status",
        help="Jede Zeile als 'mitglied' vermerken (Vorgabe). Mit --ohne-status "
        "wird nur der Gruppenbestand geschrieben.",
    ),
) -> None:
    """Liest die eigene Mitgliederliste ein - Gruppen, in denen wir schon drin sind.

    Seit dem 20.09.2026 der einzige Weg in den Bestand: Der Seed-Import ist
    mit der Entdeckungsschicht entfallen, und gesucht wird nicht mehr.

    **Jede Zeile bedeutet "wir sind Mitglied"**, und genau das wird vermerkt
    (``MarketingStatus.MEMBER``). Eine Beitrittsanfrage an eine Gruppe, in der
    wir schon stehen, waere ein Handgriff ohne Zweck - und eine der wenigen
    Handlungen, die bei Facebook auffallen. ``--ohne-status`` laesst den
    Arbeitsstand unberuehrt, falls die Datei einmal etwas anderes enthaelt.

    Ein bestehender Arbeitsstand wird **nicht** zurueckgesetzt: Wer schon
    weiter ist als "mitglied" (angesprochen, Zusammenarbeit laeuft), bleibt
    dort. Ein zweiter Lauf ueber dieselbe Datei aendert also nichts.
    """
    config = _config()

    if not pfad.exists():
        console.print(f"[red]Datei nicht gefunden:[/red] {pfad}")
        raise typer.Exit(code=1)

    bericht = lies_mitgliederdatei(pfad, config)

    for fehler in bericht.fehler:
        console.print(
            f"[yellow]Zeile {fehler.zeile}:[/yellow] {fehler.grund}"
            + (f"  ({fehler.wert})" if fehler.wert else "")
        )

    # Bewertet wird vor dem Speichern - sonst stuenden die Gruppen ohne Score
    # in der Uebersicht, und die Arbeitsliste sortierte sie ans Ende.
    gruppen = score_all(bericht.gruppen, config, {})

    tabelle = Table(title=f"Mitgliederliste {bericht.quelle}", show_header=False, box=None)
    tabelle.add_row("Zeilen", str(bericht.zeilen_gesamt))
    tabelle.add_row("gelesene Gruppen", str(len(gruppen)))
    tabelle.add_row("verworfen", str(len(bericht.fehler)))
    tabelle.add_row("mit Mitgliederzahl", str(sum(1 for g in gruppen if g.member_count)))
    tabelle.add_row("mit Aktivitaet", str(sum(1 for g in gruppen if g.activity_factor is not None)))
    tabelle.add_row("mit Kategorie", str(sum(1 for g in gruppen if g.category)))
    # Die beiden Einstufungen der Liste. Sie stehen hier, weil sie ab jetzt
    # eine Kampagne auswaehlen: Eine Datei, aus der nur 3 von 18 Zeilen eine
    # Note mitbringen, ergibt einen Filter, der 15 Gruppen nicht findet - und
    # das soll man beim Einlesen sehen und nicht beim Zuordnen.
    tabelle.add_row(
        "mit Prioritaet", str(sum(1 for g in gruppen if g.listenprioritaet))
    )
    tabelle.add_row(
        "mit Aktivitaetsstufe", str(sum(1 for g in gruppen if g.aktivitaetsstufe))
    )
    tabelle.add_row("bewertet", str(sum(1 for g in gruppen if g.score is not None)))
    console.print(tabelle)

    # Die drei Fallen der Quelldatei ausdruecklich benennen. Sie fielen sonst
    # erst auf, wenn eine Gruppe nie in Klasse A auftaucht oder ein falscher
    # Ortsname in einem Beitrag steht.
    if bericht.ohne_namen:
        console.print(
            f"[yellow]{len(bericht.ohne_namen)} Zeile(n) ohne verwertbaren Namen:[/yellow] "
            f"{', '.join(sorted(set(bericht.ohne_namen)))} - das ist kein Gruppenname, "
            f"sondern Beifang der Erfassung. Der Name bleibt leer."
        )
    if bericht.unbekannte_kategorien:
        console.print(
            f"[yellow]Unbekannte Kategorien:[/yellow] "
            f"{', '.join(sorted(set(bericht.unbekannte_kategorien)))}. Sie wurden "
            f"uebergangen - ergaenzen in mitglieder.KATEGORIEN."
        )
    if bericht.unbekannte_noten:
        console.print(
            f"[yellow]Unbekannte Prioritaet:[/yellow] "
            f"{', '.join(sorted(set(bericht.unbekannte_noten)))}. Moeglich sind "
            f"{', '.join(LISTENPRIORITAETEN)} - die Zeile bleibt ohne Note, "
            f"geraten wird nichts."
        )
    if bericht.unbekannte_aktivitaet:
        console.print(
            f"[dim]Spalte 'activity' ohne verwertbare Angabe "
            f"({len(bericht.unbekannte_aktivitaet)}x): weder Seitenkopf noch eine der "
            f"Stufen {', '.join(AKTIVITAETSSTUFEN)}.[/dim]"
        )
    if bericht.verworfene_staedte:
        console.print(
            f"[dim]Spalte 'city' nicht uebernommen ({len(bericht.verworfene_staedte)}x: "
            f"{', '.join(sorted(set(bericht.verworfene_staedte)))}): Sie nennt das "
            f"Reiseziel, nicht den Sitz der Gruppe. Steht als Hinweis in den Notizen.[/dim]"
        )

    # Bis zum 22.09.2026 stand hier die Zielprioritaet je Gruppe (A-D). Sie
    # ist als Entscheidungsgrundlage entfallen; beurteilt wird eine Gruppe
    # nach den Bezuegen in ihren Beitraegen, und die kennt ein Import nicht -
    # er liest keinen einzigen Beitrag.
    console.print(
        "[dim]Bezuege: noch keine - sie entstehen aus den Beitraegen, die der "
        "Lauf in einer Gruppe liest.[/dim]"
    )

    if dry_run:
        console.print("[cyan]--dry-run:[/cyan] es wurde nichts gespeichert.")
        return

    if not gruppen:
        console.print("[yellow]Nichts zu speichern.[/yellow]")
        return

    with SqliteStore(config.path("sqlite_path")) as store:
        neu, bekannt = store.upsert_groups(gruppen)
        gesamt = store.count_groups()
    console.print(
        f"[green]Gespeichert:[/green] {neu} neu, {bekannt} bereits bekannt "
        f"- Bestand gesamt: {gesamt}"
    )

    if status_mitglied:
        gesetzt, schon_weiter = _als_mitglied_vermerken(config, [g.group_id for g in gruppen])
        console.print(
            f"[green]Arbeitsstand:[/green] {gesetzt}x auf 'mitglied' gesetzt"
            + (f", {schon_weiter} waren bereits weiter" if schon_weiter else "")
        )


def _als_mitglied_vermerken(config: AppConfig, group_ids: list[str]) -> tuple[int, int]:
    """Setzt den Arbeitsstand auf 'mitglied' - ohne einen weiteren zurueckzudrehen.

    Dieselbe Regel wie bei ``marketing beitritt``: Der Sammelbefehl
    ueberspringt jede Gruppe, die laut ``MARKETING_FORTSCHRITT`` schon weiter
    ist. Wer die Leitung bereits angesprochen hat, faellt durch einen zweiten
    Import nicht auf "mitglied" zurueck.
    """
    from fbgroups.marketing.models import MARKETING_FORTSCHRITT, MarketingStatus
    from fbgroups.marketing.store import MarketingStore

    ziel = MARKETING_FORTSCHRITT.index(MarketingStatus.MEMBER)
    gesetzt = schon_weiter = 0

    with MarketingStore(config.path("sqlite_path")) as store:
        for group_id in group_ids:
            eintrag = store.load_marketing(group_id)
            try:
                stand = MARKETING_FORTSCHRITT.index(eintrag.marketing_status)
            except ValueError:
                stand = -1
            if stand >= ziel:
                schon_weiter += 1
                continue
            eintrag.marketing_status = MarketingStatus.MEMBER
            store.save_marketing(eintrag)
            gesetzt += 1

    return gesetzt, schon_weiter


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

    # --- Bezuege statt Zielklassen (23.09.2026) ---------------------------
    #
    # Die Zielprioritaet (A-D), die Region und die Note entscheiden nichts
    # mehr. Was bleibt, ist eine Schwelle fuer alle Gruppen und die feste
    # Liste der Bezuege. Ein alter ``marketing.zielprioritaet``-Block wird
    # nicht mehr gelesen - gesagt wird es, damit niemand dort etwas aendert
    # und sich wundert, dass es nichts bewirkt.
    from fbgroups.marketing import automatik as _automatik
    from fbgroups.marketing.bezug import Bezug

    if config.get("marketing", "zielprioritaet", default=None):
        console.print(
            "[yellow]Hinweis: marketing.zielprioritaet wird seit dem 23.09.2026 "
            "nicht mehr gelesen (Zielklassen A-D, Region und Note sind entfallen) "
            "und kann aus settings.yaml entfernt werden.[/yellow]"
        )
    console.print(
        f"[green]Bezuege:[/green] {len(Bezug)} festgelegt - "
        f"{', '.join(b.value for b in Bezug)}."
    )
    console.print(
        f"[dim]Mindestrelevanz fuer jede Gruppe: "
        f"{_automatik.mindestrelevanz(config).value} (marketing.mindestrelevanz).[/dim]"
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


@app.command("sicherung")
def sicherung_command(
    liste: bool = typer.Option(False, "--liste", help="Nur die vorhandenen Sicherungen zeigen."),
    zurueck: Path = typer.Option(
        None,
        "--zurueck",
        help="Diese Sicherung (.sqlite.gz) als Bestand einspielen. Der Stand davor "
        "wird vorher gesichert.",
    ),
    ja: bool = typer.Option(False, "--ja", help="Beim Zurueckspielen nicht nachfragen."),
) -> None:
    """Sichert den Bestand jetzt - geprueft, gepackt, an jeden Sicherungsort.

    Seit dem Umzug (25.09.2026) liegt der Bestand auf diesem Rechner. Der
    Waechter sichert von selbst, sobald die letzte Sicherung aelter ist als
    ``sicherung.abstand_stunden``; dieser Befehl tut es sofort.

    ``--zurueck`` ersetzt den Bestand durch eine Sicherung. Nur ohne
    laufenden ``campaign automatik`` und bei geschlossener Uebersicht.
    """
    from fbgroups import sicherung
    from fbgroups.marketing import watchdog

    config = _config()
    einst = sicherung.einstellungen(config)
    bestand = config.path("sqlite_path")

    if liste:
        for ordner, dateien in sicherung.uebersicht(einst):
            console.print(f"[bold]{ordner}[/bold]  ({len(dateien)} Sicherungen)")
            for datei in dateien[-10:]:
                console.print(f"  {datei.name}  [dim]{datei.stat().st_size / 1024:.0f} KB[/dim]")
            if len(dateien) > 10:
                console.print(f"  [dim]... und {len(dateien) - 10} aeltere[/dim]")
        return

    if zurueck is not None:
        console.print(
            f"Eingespielt wird [bold]{zurueck}[/bold]\n"
            f"anstelle von [bold]{bestand}[/bold]. Der jetzige Stand wird vorher gesichert."
        )
        if not ja and not typer.confirm("Wirklich zurueckspielen?", default=False):
            console.print("[yellow]Nichts geaendert.[/yellow]")
            raise typer.Exit(code=1)
        try:
            version, vorher = sicherung.zurueckspielen(
                zurueck,
                bestand,
                einst,
                lauf_aktiv=watchdog.sperre_fuer(config).laeuft(),
            )
        except sicherung.SicherungFehlgeschlagen as exc:
            console.print(f"[red]Nicht zurueckgespielt:[/red] {exc}")
            raise typer.Exit(code=2) from exc
        console.print(f"[green]Zurueckgespielt[/green] (Schema {version}).")
        if vorher is not None:
            console.print(f"[dim]Der Stand davor: {sicherung.beschreibe(vorher)}[/dim]")
        return

    try:
        ergebnis = sicherung.sichere(bestand, einst)
    except sicherung.SicherungFehlgeschlagen as exc:
        console.print(f"[red]Nicht gesichert:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    farbe = "yellow" if ergebnis.fehler else "green"
    console.print(f"[{farbe}]Gesichert:[/{farbe}] {sicherung.beschreibe(ergebnis)}")
    console.print(f"[dim]{ergebnis.pfad}[/dim]")


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
