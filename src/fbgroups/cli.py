"""Kommandozeile.

    fbgroups import-seeds [PFAD ...]   Seeds einlesen, verarbeiten, speichern
    fbgroups pruefliste                Liste zum Ausfuellen von Hand schreiben
    fbgroups rescore                   Bestand neu bewerten (ohne Suchanfrage)
    fbgroups report                    Bestand auswerten
    fbgroups export --format xlsx      Ergebnisse exportieren
    fbgroups queries                   Geplante Suchanfragen anzeigen (keine Suche)
    fbgroups providers                 Verfuegbarkeit der Suchdienste pruefen
    fbgroups search --dry-run          Plan und Verbrauch zeigen, nichts abfragen
    fbgroups search --limit N          Hoechstens N neue Anfragen absetzen
    fbgroups search-log                Protokoll der bisherigen Anfragen
    fbgroups config-check              Konfiguration pruefen
    fbgroups auth login                Interaktiver Browser-Login fuer Automatisierung
    fbgroups post                      Automatisch einen Beitrag in einer Gruppe posten
    fbgroups comment                   Automatisch auf einen Beitrag kommentieren
"""

from __future__ import annotations

import csv
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from fbgroups.config import AppConfig, load_config
from fbgroups.export import export_csv, export_excel
from fbgroups.marketing.cli import campaign_app, marketing_app
from fbgroups.marketing.selection import synchronisiere
from fbgroups.marketing.store import MarketingStore
from fbgroups.marketing.tracking import app_base_url
from fbgroups.models import Group, PrivacyHint, ValidationStatus
from fbgroups.pipeline import classify_group, process_search_results, run_seed_import
from fbgroups.providers.base import ProviderState
from fbgroups.providers.factory import (
    build_provider,
    check_all_providers,
    load_provider_config,
    resolve_active_provider,
)
from fbgroups.query.builder import build_queries, max_results_per_query
from fbgroups.report import (
    build_distribution,
    build_quality_counts,
    build_score_stats,
    build_status_counts,
    build_validation_counts,
    coverage_percent,
    passes_min_score,
    rejection_reasons,
)
from fbgroups.scoring import score_all, sort_by_rank
from fbgroups.search import build_plan, open_query_cache, run_search
from fbgroups.storage import SqliteStore, save_run_artifacts

app = typer.Typer(
    add_completion=False,
    help="Facebook Groups Finder - Germany (with Automation features)",
)
console = Console()

auth_app = typer.Typer(help="Authentication and browser session management.")
app.add_typer(auth_app, name="auth")



def _mit_zeitzone(zeitpunkt: datetime) -> datetime:
    """Zeitpunkt mit Zeitzone - ein Vergleich ohne wirft sonst TypeError."""
    return zeitpunkt if zeitpunkt.tzinfo else zeitpunkt.replace(tzinfo=UTC)

# Die Marketing-Erweiterung haengt sich als eigene Unterbefehle an. Bestehende
# Befehle bleiben unveraendert; wer sie nicht nutzt, merkt nichts davon.
app.add_typer(campaign_app, name="campaign")
app.add_typer(marketing_app, name="marketing")


def _config() -> AppConfig:
    try:
        return load_config()
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]Konfigurationsfehler:[/red] {exc}")
        raise typer.Exit(code=1) from exc


def _auto_zuordnen(config: AppConfig) -> None:
    """Uebernimmt neu gefundene Gruppen in Kampagnen mit ``auto_assign``.

    Laeuft am Ende von Import und Suchlauf. Ohne diesen Schritt muesste jemand
    nach jedem Lauf daran denken, ``campaign sync`` aufzurufen - bei woechentlich
    neuen Gruppen ist das genau die Art Handgriff, die irgendwann ausfaellt und
    dann monatelang niemandem auffaellt.

    Betroffen sind ausschliesslich Kampagnen, bei denen der Schalter
    ausdruecklich gesetzt wurde; Vorgabe ist aus. Es wird nur hinzugefuegt, nie
    entfernt, und kein bestehender Code angefasst. Ein Fehler hier darf einen
    gelungenen Suchlauf nicht nachtraeglich zunichtemachen - die Gruppen sind
    zu dem Zeitpunkt bereits gespeichert.
    """
    pfad = config.path("sqlite_path")
    if not pfad.exists():
        return

    try:
        with MarketingStore(pfad) as store:
            kampagnen = store.campaigns_mit_auto_assign()
            if not kampagnen:
                return

            with SqliteStore(pfad) as gruppen_store:
                groups = gruppen_store.load_groups()

            for campaign in kampagnen:
                plan = synchronisiere(store, groups, campaign, config)
                if plan.anzahl_neu:
                    console.print(
                        f"[green]{campaign.campaign_id}:[/green] {plan.anzahl_neu} neue "
                        f"Gruppen zugeordnet, Tracking-Codes vergeben "
                        f"(insgesamt {plan.anzahl_neu + plan.bereits_zugeordnet})."
                    )
    except Exception as exc:  # noqa: BLE001 - der Lauf selbst war erfolgreich
        console.print(
            f"[yellow]Automatische Kampagnen-Zuordnung uebersprungen:[/yellow] {exc}\n"
            "[dim]Der Bestand ist gespeichert. Nachholen mit: fbgroups campaign sync ...[/dim]"
        )


@app.command("import-seeds")
def import_seeds_command(
    paths: list[Path] = typer.Argument(
        None, help="Seed-Dateien (.csv/.txt). Ohne Angabe: alle aus data/seeds/."
    ),
    phase: int = typer.Option(1, help="Zielgruppen und Staedte bis zu dieser Phase."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Nichts speichern, nur anzeigen."),
) -> None:
    """Liest manuelle Seed-Dateien und verarbeitet sie vollstaendig."""
    config = _config()
    groups, run = run_seed_import(config, paths=list(paths) if paths else None, phase=phase)

    for error in run.errors:
        console.print(f"[yellow]Hinweis:[/yellow] {error}")

    table = Table(title=f"Importlauf {run.run_id}", show_header=False, box=None)
    table.add_row("Dateien", ", ".join(run.source_files) or "-")
    table.add_row("Zeilen gesamt", str(run.rows_total))
    table.add_row("davon gueltig", f"{run.rows_valid}  ({coverage_percent(run)} %)")
    table.add_row("verworfen", str(run.rows_rejected))
    table.add_row("eindeutige Gruppen", str(run.groups_new))
    table.add_row("exakte Dubletten", f"{run.groups_duplicate}  ({run.duplicate_rate} %)")
    table.add_row("Dublettenverdacht", str(len(run.duplicate_suspects)))
    table.add_row("", "")
    table.add_row("[green]validated[/green]", str(run.groups_validated))
    table.add_row("[yellow]insufficient_data[/yellow]", str(run.groups_insufficient_data))
    table.add_row(
        "[red]invalid[/red]",
        f"{run.groups_invalid}  (davon Platzhalter/test_data: {run.groups_test_data})",
    )
    table.add_row("bewertet (Score)", f"{run.groups_scored} von {run.groups_new}")
    console.print(table)

    if run.groups_scored == 0 and run.groups_new:
        console.print(
            "[yellow]Keine einzige Gruppe war bewertbar.[/yellow] Ohne Gruppennamen "
            "und weitere Angaben ist keine Klassifikation moeglich - die Spalte "
            "'Score Reason' im Export nennt fuer jede Zeile den Grund."
        )

    if run.rejected:
        reasons = Table(title="Verworfene Zeilen", header_style="bold")
        reasons.add_column("Grund")
        reasons.add_column("Anzahl", justify="right")
        for reason, count in rejection_reasons(run).most_common():
            reasons.add_row(reason, str(count))
        console.print(reasons)

    if run.duplicate_suspects:
        suspects = Table(title="Dublettenverdacht (nicht automatisch zusammengefuehrt)")
        suspects.add_column("Gruppe A")
        suspects.add_column("Gruppe B")
        suspects.add_column("Aehnlichkeit", justify="right")
        for suspect in run.duplicate_suspects[:10]:
            suspects.add_row(suspect.name_a, suspect.name_b, f"{suspect.similarity:.0f}")
        console.print(suspects)

    if dry_run:
        console.print("[cyan]--dry-run:[/cyan] es wurde nichts gespeichert.")
        _print_groups(groups)
        return

    if groups:
        with SqliteStore(config.path("sqlite_path")) as store:
            new_count, known_count = store.upsert_groups(groups)
            store.save_run(run)
            total = store.count_groups()
        console.print(
            f"[green]Gespeichert:[/green] {new_count} neu, {known_count} bereits bekannt "
            f"- Bestand gesamt: {total}"
        )
        _auto_zuordnen(config)

    run_dir = save_run_artifacts(config.path("runs_dir"), run, groups)
    console.print(f"Laufprotokoll: {run_dir}")


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


@app.command("pruefliste")
def pruefliste_command(
    top: int = typer.Option(0, help="Nur die besten N Gruppen (0 = alle)."),
    output: Path = typer.Option(None, help="Zieldatei (Standard: data/seeds/pruefliste.csv)."),
    offen: bool = typer.Option(
        False, "--offen", help="Nur Gruppen ohne Mitgliederzahl auflisten."
    ),
) -> None:
    """Schreibt eine Liste zum Ausfuellen von Hand.

    Zwei Angaben kann dieses Programm nicht selbst beschaffen: die
    Mitgliederzahl und die Frage, ob es die Gruppe ueberhaupt noch gibt.
    Beides steht nur auf facebook.com, und dorthin greift das Projekt nicht zu.
    Die Datei wird in derselben Rangfolge geschrieben wie der Export - oben
    steht, was sich zuerst zu pruefen lohnt.

    Ausfuellen in Excel, dann zurueckspielen mit:
        fbgroups import-seeds data\\seeds\\pruefliste.csv
    Leere Zellen aendern nichts.
    """
    config = _config()
    with SqliteStore(config.path("sqlite_path")) as store:
        groups = sort_by_rank(store.load_groups())

    if offen:
        groups = [g for g in groups if g.member_count is None]
    if top > 0:
        groups = groups[:top]

    if not groups:
        console.print("[yellow]Keine Gruppen fuer die Pruefliste.[/yellow]")
        raise typer.Exit(code=0)

    ziel = output or config.path("seeds_dir") / "pruefliste.csv"
    ziel.parent.mkdir(parents=True, exist_ok=True)

    # utf-8-sig und ';' - sonst zeigt Excel Arabisch und Spalten falsch an.
    with ziel.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow(["url", "name", "mitglieder", "erreichbar", "notiz"])
        for group in groups:
            writer.writerow(
                [
                    group.url_canonical,
                    group.name,
                    group.member_count if group.member_count is not None else "",
                    "",
                    group.notes,
                ]
            )

    console.print(
        Panel(
            f"[green]{len(groups)} Zeilen[/green] geschrieben nach\n[bold]{ziel}[/bold]\n\n"
            "In Excel oeffnen und ausfuellen:\n"
            "  [bold]mitglieder[/bold]  Zahl aus der Gruppe, z. B. 12.400 oder 12,4k\n"
            "  [bold]erreichbar[/bold]  'nein', wenn die Seite nicht mehr aufgeht\n"
            "  [bold]name[/bold]        nachtragen, wo er fehlt\n\n"
            "Zurueckspielen (leere Zellen aendern nichts):\n"
            f"  [bold]fbgroups import-seeds {ziel}[/bold]",
            title="Pruefliste",
        )
    )


@app.command("rescore")
def rescore_command(
    phase: int = typer.Option(1, help="Staedte und Zielgruppen bis zu dieser Phase."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Nur zeigen, was sich aendern wuerde."
    ),
) -> None:
    """Bewertet den gespeicherten Bestand neu - ohne Suchanfrage, ohne Guthaben.

    Noetig nach jeder Aenderung an den Gewichten in config/settings.yaml oder
    an der Klassifikation: Ein Suchlauf fasst nur die Gruppen an, die er
    gerade findet: der uebrige Bestand behielte sonst seine alten Werte, und
    im Export stuenden zwei Bewertungen nebeneinander.
    """
    config = _config()
    with SqliteStore(config.path("sqlite_path")) as store:
        groups = store.load_groups()
        vorher = {g.group_id: g.score for g in groups}

        for group in groups:
            classify_group(group, config, phase)

        # Die gemessene Resonanz wird hier geholt und hereingereicht: Der Kern
        # in scoring.py kennt die Marketing-Erweiterung nicht. Fehlt die
        # Tabelle (eine Datei aus alter Zeit), bleibt die Bewertung die
        # bisherige, statt den ganzen Lauf abzubrechen.
        try:
            from fbgroups.marketing.resonanz import resonanz_je_gruppe
            from fbgroups.marketing.store import MarketingStore

            with MarketingStore(config.path("sqlite_path")) as mstore:
                gemessen = resonanz_je_gruppe(mstore)
        except Exception as exc:  # noqa: BLE001
            console.print(f"[yellow]Resonanz nicht lesbar ({exc}) - ohne sie bewertet.[/yellow]")
            gemessen = {}

        bewertet = score_all(groups, config, gemessen)
        if gemessen:
            console.print(
                f"[dim]Gemessene Resonanz fuer {len(gemessen)} Gruppen mit "
                f"veroeffentlichtem Beitrag.[/dim]"
            )

        geaendert = [g for g in bewertet if g.score != vorher[g.group_id]]
        if dry_run:
            console.print(
                f"[cyan]--dry-run:[/cyan] {len(geaendert)} von {len(bewertet)} Gruppen "
                f"bekaemen einen anderen Score. Es wurde nichts geschrieben."
            )
        else:
            aktualisiert = store.update_scores(bewertet)
            console.print(
                f"[green]Neu bewertet:[/green] {aktualisiert} Gruppen, "
                f"davon {len(geaendert)} mit geaendertem Score."
            )

    _print_groups(bewertet[:10])


@app.command("enrich")
def enrich_command(
    limit: int = typer.Option(
        None, "--limit", help="Hoechstens N Gruppenseiten abrufen."
    ),
    alle: bool = typer.Option(
        False, "--alle", help="Ohne --limit laufen (bis enrich.max_pro_lauf)."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Nur zeigen, welche Gruppen an der Reihe waeren."
    ),
    erneut: bool = typer.Option(
        False, "--erneut", help="Auch Gruppen abrufen, deren Befund noch frisch ist."
    ),
) -> None:
    """Liest Mitgliederzahl und Aktivitaet von den oeffentlichen Gruppenseiten.

    **Dieser Befehl ruft facebook.com auf.** Bis zum 27.08.2026 war das
    ausgeschlossen; der Nutzer hat die Grenze an diesem Tag ausdruecklich fuer
    das Lesen oeffentlicher Seiten geoeffnet. Ohne Login, ohne Cookies, ohne
    Umgehung von Sperren - wer blockt, bekommt None und keinen Trick.

    Der Lauf startet nie beilaeufig: ohne --limit oder --alle passiert nichts.
    Dieselbe Vorsicht wie bei "fbgroups search", aber aus einem anderen Grund -
    dort geht es um Guthaben, hier um das Konto des Nutzers.
    """
    from fbgroups.extract.aktivitaet import faktor_aus_posts_pro_tag
    from fbgroups.extract.gruppenseite import Blockiert, Gruppenseiten
    from fbgroups.models import ActivitySource, MemberCountSource

    config = _config()

    if not dry_run and limit is None and not alle:
        console.print(
            Panel(
                "Es wurde nichts abgerufen.\n\n"
                "Ein Abruf von facebook.com startet absichtlich nicht ohne "
                "Mengenangabe.\n\n"
                "  [bold]fbgroups enrich --dry-run[/bold]   zeigt, wer an der Reihe waere\n"
                "  [bold]fbgroups enrich --limit 5[/bold]   hoechstens 5 Gruppenseiten\n"
                "  [bold]fbgroups enrich --alle[/bold]      bis zur Obergrenze aus "
                "config/settings.yaml",
                title="Abbruch",
                border_style="yellow",
            )
        )
        raise typer.Exit(code=2)

    obergrenze = int(config.get("enrich", "max_pro_lauf", default=100) or 100)
    hoechstalter = float(config.get("enrich", "hoechstalter_tage", default=30) or 30)
    wieviele = min(limit if limit is not None else obergrenze, obergrenze)

    with SqliteStore(config.path("sqlite_path")) as store:
        groups = store.load_groups()

    # Die besten Gruppen zuerst: Wer abbricht, soll die wertvollsten Zahlen
    # erhoben haben - dieselbe Ueberlegung wie bei der Arbeitsliste.
    frist = datetime.now(UTC) - timedelta(days=hoechstalter)
    offen = [
        g for g in sort_by_rank(groups)
        if g.validation_status is ValidationStatus.VALID
        and (
            erneut
            or g.member_count_checked_at is None
            or _mit_zeitzone(g.member_count_checked_at) < frist
        )
    ]

    console.print(
        f"{len(groups)} Gruppen im Bestand, {len(offen)} ohne frischen Befund, "
        f"{min(wieviele, len(offen))} in diesem Lauf."
    )
    if dry_run:
        for g in offen[:wieviele]:
            steht = "?" if g.member_count is None else str(g.member_count)
            console.print(f"  {g.group_id:>18}  {g.name[:42]:<42}  Mitglieder: {steht}")
        console.print("[dim]--dry-run: es wurde nichts abgerufen.[/dim]")
        return

    cache = config.path("data_dir") / "gruppenseiten.sqlite"
    geaendert: list[Group] = []
    abgerufen = gefunden_zahl = gefunden_aktiv = nicht_erreichbar = 0

    with Gruppenseiten(cache, config) as seiten, httpx.Client(
        timeout=20.0, follow_redirects=True
    ) as client:
        for group in offen[:wieviele]:
            try:
                befund = seiten.hole(group.group_id, client)
            except Blockiert as exc:
                console.print(f"[yellow]{exc}[/yellow]")
                break

            abgerufen += 1
            jetzt = datetime.now(UTC)
            group.member_count_checked_at = jetzt
            group.activity_checked_at = jetzt
            group.last_checked_at = jetzt

            if not befund.erreichbar:
                nicht_erreichbar += 1
                geaendert.append(group)
                continue

            # Nur uebernehmen, was tatsaechlich dastand. Eine Zahl, die der
            # Abruf nicht gefunden hat, loescht keine vorhandene: Ein
            # Anmeldefenster ist kein Beleg dafuer, dass die Gruppe geschrumpft
            # ist.
            if befund.member_count is not None:
                group.member_count = befund.member_count
                group.member_count_source = MemberCountSource.FACEBOOK
                gefunden_zahl += 1
            if befund.privacy is not PrivacyHint.UNKNOWN:
                group.privacy_hint = befund.privacy

            # Beitraege je Tag aus den gefundenen Zeitpunkten: nur, wenn
            # mindestens zwei vorliegen - aus einem einzelnen laesst sich
            # keine Rate ableiten, und eine geratene waere schlimmer als keine.
            if len(befund.beitrag_daten) >= 2:
                spanne = (max(befund.beitrag_daten) - min(befund.beitrag_daten)).total_seconds()
                tage = max(spanne / 86400.0, 1.0 / 24)
                group.posts_per_day = round(len(befund.beitrag_daten) / tage, 2)
                group.activity_factor = faktor_aus_posts_pro_tag(group.posts_per_day, config)
                group.activity_confidence = 1.0
                group.activity_source = ActivitySource.FACEBOOK
                gefunden_aktiv += 1
            if befund.juengster_beitrag is not None:
                group.last_post_at = befund.juengster_beitrag

            geaendert.append(group)

    if geaendert:
        with SqliteStore(config.path("sqlite_path")) as store:
            store.upsert_groups(geaendert)

    console.print(
        f"[green]{abgerufen} Seiten abgerufen[/green] - "
        f"{gefunden_zahl} Mitgliederzahlen, {gefunden_aktiv} Aktivitaetsmasse, "
        f"{nicht_erreichbar} nicht erreichbar (Anmeldefenster oder Sperre)."
    )
    if abgerufen and not gefunden_zahl:
        console.print(
            "[yellow]Keine einzige Mitgliederzahl gefunden.[/yellow] Facebook liefert "
            "einem nicht angemeldeten Abruf meist eine Anmeldeseite - das ist der "
            "Normalfall und kein Fehler des Programms. Die Zahlen bleiben leer statt "
            "geraten; von Hand pflegen geht ueber 'fbgroups pruefliste'."
        )
    console.print("[dim]Danach: fbgroups rescore[/dim]")


@app.command("report")
def report_command(
    top: int = typer.Option(10, help="Anzahl der angezeigten Top-Gruppen."),
) -> None:
    """Wertet den gespeicherten Bestand aus."""
    config = _config()
    db_path = config.path("sqlite_path")
    if not db_path.exists():
        console.print(
            "[yellow]Noch keine Datenbank. Zuerst 'fbgroups import-seeds' ausfuehren.[/yellow]"
        )
        raise typer.Exit(code=0)

    with SqliteStore(db_path) as store:
        groups = store.load_groups()

    if not groups:
        console.print("[yellow]Der Bestand ist leer.[/yellow]")
        raise typer.Exit(code=0)

    stats = build_score_stats(groups)
    dist = build_distribution(groups)

    console.print(
        Panel(
            f"Gruppen gesamt: [bold]{len(groups)}[/bold]    "
            f"bewertet: [bold]{stats.count}[/bold]    "
            f"nicht bewertbar: [bold]{stats.unscored}[/bold]\n"
            f"Score min/Ø/max (nur bewertete): "
            f"{stats.minimum} / {stats.average} / {stats.maximum}",
            title="Bestand",
        )
    )

    _distribution_table("Status", build_status_counts(groups), 0)
    _distribution_table("Validation Status", build_validation_counts(groups), 0)
    _distribution_table("Data Quality", build_quality_counts(groups), 0)
    _distribution_table("Zielgruppen", dist.by_audience, dist.unclassified_audience)
    _distribution_table("Staedte", dist.by_city, dist.unclassified_city)
    _distribution_table("Kategorien", dist.by_category, dist.unclassified_category)

    _print_groups(groups[:top], title=f"Top {top} nach Score")


@app.command("export")
def export_command(
    fmt: str = typer.Option("xlsx", "--format", help="xlsx | csv | both"),
    output: Path = typer.Option(None, help="Zieldatei (Standard: data/exports/...)."),
    min_score: float = typer.Option(0.0, help="Nur Gruppen ab diesem Score."),
) -> None:
    """Exportiert den Bestand nach Excel und/oder CSV."""
    config = _config()
    db_path = config.path("sqlite_path")
    if not db_path.exists():
        console.print(
            "[yellow]Noch keine Datenbank. Zuerst 'fbgroups import-seeds' ausfuehren.[/yellow]"
        )
        raise typer.Exit(code=1)

    with SqliteStore(db_path) as store:
        groups = [g for g in store.load_groups() if passes_min_score(g, min_score)]

    # Dieselbe Rangfolge wie im Lauf: die beste Gruppe steht in Zeile 2.
    groups = sort_by_rank(groups)

    if not groups:
        console.print("[yellow]Keine Gruppen zum Exportieren.[/yellow]")
        raise typer.Exit(code=0)

    exports_dir = config.path("exports_dir")
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    fmt = fmt.lower()

    if fmt in {"xlsx", "both"}:
        target = output if (output and fmt == "xlsx") else exports_dir / f"groups_{stamp}.xlsx"
        console.print(f"[green]Excel:[/green] {export_excel(groups, target)}")

    if fmt in {"csv", "both"}:
        target = output if (output and fmt == "csv") else exports_dir / f"groups_{stamp}.csv"
        console.print(f"[green]CSV:[/green] {export_csv(groups, target)}")

    if fmt not in {"xlsx", "csv", "both"}:
        console.print(f"[red]Unbekanntes Format: {fmt}[/red]")
        raise typer.Exit(code=1)

    console.print(f"{len(groups)} Gruppen exportiert.")


@app.command("queries")
def queries_command(
    phase: int = typer.Option(1, help="Staedte bis zu dieser Phase."),
    show_all: bool = typer.Option(False, "--all", help="Alle Anfragen auflisten."),
) -> None:
    """Zeigt die geplanten Suchanfragen. Fuehrt KEINE Suche aus."""
    config = _config()
    planned = build_queries(config, phase)
    per_query = max_results_per_query(config)

    table = Table(title=f"Geplante Suchanfragen (Phase {phase})")
    table.add_column("#", justify="right", style="dim")
    table.add_column("ID")
    table.add_column("Sprache")
    table.add_column("Bereich")
    table.add_column("Anfrage")

    shown = planned if show_all else planned[:15]
    for index, query in enumerate(shown, start=1):
        table.add_row(str(index), query.query_id, query.lang, query.scope, query.text)

    console.print(table)
    if not show_all and len(planned) > len(shown):
        console.print(f"[dim]... {len(planned) - len(shown)} weitere (--all zeigt alle)[/dim]")

    console.print(
        f"\n[bold]{len(planned)}[/bold] Anfragen x max. [bold]{per_query}[/bold] Ergebnisse "
        f"= max. [bold]{len(planned) * per_query}[/bold] Treffer"
    )
    console.print(
        "[dim]Dieser Befehl fragt nichts ab. Was ein Lauf tatsaechlich senden wuerde "
        "und was er kostet, zeigt: fbgroups search --dry-run[/dim]"
    )


@app.command("providers")
def providers_command() -> None:
    """Prueft alle konfigurierten Suchdienste - ohne Kontingentverbrauch."""
    config = _config()
    try:
        provider_config = load_provider_config(config.root)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    table = Table(title="Search-Provider")
    table.add_column("Name")
    table.add_column("Verfuegbar")
    table.add_column("Status")
    table.add_column("Hinweis")

    for name, status in check_all_providers(provider_config, config.root):
        marker = "[green]ja[/green]" if status.available else "[red]nein[/red]"
        table.add_row(name, marker, status.state.value, status.message)

    console.print(table)
    aktiv = provider_config.get("active")
    console.print(f"Aktiver Provider laut Konfiguration: [bold]{aktiv}[/bold]")


@app.command("search")
def search_command(
    provider_name: str = typer.Option(None, "--provider", help="Provider erzwingen."),
    phase: int = typer.Option(1, help="Staedte und Zielgruppen bis zu dieser Phase."),
    limit: int = typer.Option(
        None, "--limit", help="Hoechstens N NEUE Anfragen an den Dienst senden."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Nur planen: zeigt Anfragen und Verbrauch, fragt nichts ab."
    ),
    alle: bool = typer.Option(
        False,
        "--alle",
        help="Ohne --limit laufen lassen (bis zur Obergrenze aus providers.yaml).",
    ),
    show_all: bool = typer.Option(
        False, "--show-all", help="Im dry-run alle Anfragen auflisten statt nur der ersten."
    ),
    kurz: bool = typer.Option(
        False, "--kurz", help="Im dry-run nur die Zusammenfassung, ohne Anfrageliste."
    ),
) -> None:
    """Fuehrt die geplanten Suchanfragen aus und speichert die gefundenen Gruppen."""
    config = _config()
    provider_config = load_provider_config(config.root)

    # Ein vollstaendiger Deutschland-Scan startet nie beilaeufig: ohne --limit
    # oder ausdrueckliches --alle wird nichts abgefragt.
    if not dry_run and limit is None and not alle:
        console.print(
            Panel(
                "Es wurde nichts abgefragt.\n\n"
                "Ein Suchlauf ohne Mengenangabe wird absichtlich nicht gestartet - "
                "jede neue Anfrage kostet Guthaben.\n\n"
                "  [bold]fbgroups search --dry-run[/bold]   Plan und Verbrauch ansehen\n"
                "  [bold]fbgroups search --limit 5[/bold]   hoechstens 5 neue Anfragen\n"
                "  [bold]fbgroups search --alle[/bold]      bis zur Obergrenze aus "
                "config/providers.yaml",
                title="[yellow]Mengenangabe fehlt[/yellow]",
            )
        )
        raise typer.Exit(code=2)

    if dry_run:
        _search_dry_run(config, provider_config, provider_name, phase, limit, show_all, kurz)
        return

    try:
        provider = resolve_active_provider(provider_config, config.root, override=provider_name)
    except (RuntimeError, KeyError, ValueError) as exc:
        console.print(f"[red]Kein Suchlauf moeglich.[/red]\n{exc}")
        console.print(
            "\n[dim]Schluessel gehoeren ausschliesslich in die Datei .env "
            "(Vorlage: .env.example).\n"
            "Ohne Schluessel bleiben 'fbgroups import-seeds' und der Provider "
            "'fixture' uneingeschraenkt nutzbar.\n"
            "Uebersicht: fbgroups providers[/dim]"
        )
        raise typer.Exit(code=1) from exc

    caps = provider.capabilities
    if caps.state is not ProviderState.AVAILABLE:
        console.print(
            f"[yellow]Warnung:[/yellow] Provider '{provider.name}' meldet Status "
            f"'{caps.state.value}'"
            + (f", Abschaltung am {caps.sunset_date}" if caps.sunset_date else "")
        )

    with open_query_cache(config, provider_config) as cache:
        plan = build_plan(config, provider, provider_config, cache, phase, limit)
        console.print(
            f"Plan: [bold]{plan.n_planned}[/bold] Anfragen, davon "
            f"[bold]{plan.n_cached}[/bold] aus dem Speicher und hoechstens "
            f"[bold]{plan.n_to_send}[/bold] neu "
            f"([bold]{plan.estimated_credits}[/bold] Credits)."
        )
        groups, run = run_search(
            config,
            provider,
            provider_config,
            phase=phase,
            limit=limit,
            cache=cache,
        )

    processed = process_search_results(groups, config, run, phase)

    table = Table(title=f"Suchlauf {run.run_id}", show_header=False, box=None)
    table.add_row("Provider", provider.name)
    table.add_row("Anfragen geplant", str(run.queries_planned))
    table.add_row("davon ausgefuehrt", str(run.queries_executed))
    table.add_row("aus Anfragespeicher", f"{run.queries_cached}  (kein Guthaben verbraucht)")
    verbrauch = (
        f"{run.credits_used} Credits (vom Dienst gemeldet)"
        if run.credits_used
        else f"~{run.queries_ok * plan.credits_per_query} Credits (geschaetzt)"
    )
    table.add_row("neu an den Dienst", f"{run.queries_ok}  ({verbrauch})")
    table.add_row("fehlgeschlagen", str(run.queries_failed))
    table.add_row("", "")
    table.add_row("Treffer gesamt", str(run.hits_total))
    table.add_row(
        "davon Gruppen-URLs",
        f"{run.group_urls_found}  ({run.precision} % Treffsicherheit)",
    )
    table.add_row("eindeutige Gruppen", str(run.groups_unique))
    table.add_row("Ausbeute je Anfrage", str(run.yield_per_query))
    if run.quota_remaining is not None:
        table.add_row("Restguthaben", str(run.quota_remaining))
    console.print(table)
    if run.credits_used and run.quota_remaining is None:
        console.print(
            "[dim]Das Restguthaben meldet der Dienst nicht in der Antwort - "
            "es steht im Konto (serper.dev/dashboard).[/dim]"
        )

    if processed:
        with SqliteStore(config.path("sqlite_path")) as store:
            new_count, known_count = store.upsert_groups(processed)
            total = store.count_groups()
        run.groups_new, run.groups_known = new_count, known_count
        console.print(
            f"[green]Gespeichert:[/green] {new_count} neu, {known_count} bereits bekannt "
            f"- Bestand gesamt: {total}"
        )
        _auto_zuordnen(config)

    _query_quality_table(run)

    run_dir = config.path("runs_dir") / run.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "search_run.json").write_text(run.model_dump_json(indent=2), encoding="utf-8")
    console.print(f"Laufprotokoll: {run_dir}")


def _search_dry_run(
    config: AppConfig,
    provider_config: dict,
    provider_name: str | None,
    phase: int,
    limit: int | None,
    show_all: bool,
    kurz: bool = False,
) -> None:
    """Zeigt den Plan, ohne den Dienst zu befragen.

    Der Provider wird hier bewusst ohne Verfuegbarkeitspruefung aufgebaut: Der
    Plan soll auch dann lesbar sein, wenn noch gar kein Schluessel hinterlegt
    ist - genau dann braucht man ihn am dringendsten.
    """
    name = provider_name or provider_config.get("active", "fixture")
    try:
        provider = build_provider(name, provider_config, config.root)
    except (KeyError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    with open_query_cache(config, provider_config) as cache:
        plan = build_plan(config, provider, provider_config, cache, phase, limit)
        gespeichert_gesamt = cache.count(provider.name)

    table = Table(title=f"Geplante Anfragen an '{provider.name}' (Phase {phase})")
    table.add_column("#", justify="right", style="dim")
    table.add_column("ID")
    table.add_column("Spr.")
    table.add_column("Status")
    table.add_column("Anfrage")

    marker = {
        "cached": "[green]gespeichert[/green]",
        "send": "[yellow]neu[/yellow]",
        "skip": "[dim]zurueckgestellt[/dim]",
    }
    shown = plan.requests if show_all else plan.requests[:15]
    for index, request in enumerate(shown, start=1):
        zustand = "cached" if request.cached else ("send" if request.will_send else "skip")
        table.add_row(
            str(index),
            request.query.query_id,
            request.query.lang,
            marker[zustand],
            request.text,
        )

    if not kurz:
        console.print(table)
        if not show_all and len(plan.requests) > len(shown):
            console.print(
                f"[dim]... {len(plan.requests) - len(shown)} weitere "
                f"(--show-all zeigt alle)[/dim]"
            )

    status = provider.check_availability()
    schluessel = (
        "[green]vorhanden[/green]"
        if status.available
        else f"[red]nicht einsatzbereit[/red] - {status.message}"
    )
    # Ohne --limit zeigt der Plan, was ein Lauf mit --alle taete. Ein echter
    # Lauf ohne Mengenangabe startet nicht - das gehoert in dieselbe Ausgabe.
    hinweis = (
        ""
        if limit is not None
        else (
            "\n[dim]Ohne --limit zeigt dieser Plan, was 'fbgroups search --alle' "
            "senden wuerde.\nEin Lauf ohne Mengenangabe startet nicht.[/dim]"
        )
    )

    console.print(
        Panel(
            f"Provider: [bold]{provider.name}[/bold]   Zustand: {schluessel}\n\n"
            f"Geplante Anfragen:        [bold]{plan.n_planned}[/bold]\n"
            f"davon bereits gespeichert: [green]{plan.n_cached}[/green]  "
            f"(kein Guthaben)\n"
            f"davon neu:                 [yellow]{plan.n_new}[/yellow]\n"
            f"in diesem Lauf gesendet:   [bold]{plan.n_to_send}[/bold]  "
            f"(Obergrenze {plan.limit_effective} aus {plan.limit_source})\n"
            f"zurueckgestellt:           {plan.n_skipped}\n\n"
            f"Geschaetzter Verbrauch:    [bold]{plan.estimated_credits}[/bold] Credits "
            f"({plan.credits_per_query} je Anfrage, max. "
            f"{plan.max_results_per_query} Ergebnisse)\n"
            f"Im Anfragespeicher gesamt: {gespeichert_gesamt} Anfragen\n\n"
            f"[bold]Es wurde nichts abgefragt und kein Guthaben verbraucht.[/bold]"
            f"{hinweis}",
            title="dry-run",
        )
    )


@app.command("search-log")
def search_log_command(
    limit: int = typer.Option(20, help="Anzahl der angezeigten Eintraege."),
) -> None:
    """Zeigt das dauerhafte Protokoll der bisherigen Suchanfragen."""
    config = _config()
    provider_config = load_provider_config(config.root)

    with open_query_cache(config, provider_config) as cache:
        eintraege = cache.history(limit)
        gesamt = cache.count()

    if not eintraege:
        console.print("[yellow]Noch keine Anfrage protokolliert.[/yellow]")
        raise typer.Exit(code=0)

    table = Table(title=f"Anfrageprotokoll (juengste {len(eintraege)})")
    table.add_column("Zeitpunkt")
    table.add_column("Provider")
    table.add_column("ID")
    table.add_column("Ergebnis")
    table.add_column("Treffer", justify="right")
    table.add_column("Anfrage")

    for eintrag in eintraege:
        if not eintrag["success"]:
            ergebnis = f"[red]{eintrag['error_type'] or 'Fehler'}[/red]"
        elif eintrag["from_cache"]:
            ergebnis = "[green]Speicher[/green]"
        else:
            ergebnis = "[yellow]gesendet[/yellow]"
        table.add_row(
            str(eintrag["executed_at"])[:19],
            eintrag["provider"],
            eintrag["query_id"],
            ergebnis,
            str(eintrag["n_results"]),
            eintrag["query_text"][:60],
        )

    console.print(table)
    console.print(f"Dauerhaft gespeicherte Anfragen: [bold]{gesamt}[/bold]")


def _query_quality_table(run) -> None:
    """Zeigt, welche Sprache und welcher Bereich tatsaechlich etwas beitragen."""
    from collections import defaultdict

    by_lang: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for record in run.records:
        if record.status.value in {"skipped"}:
            continue
        bucket = by_lang[record.lang or "-"]
        bucket[0] += 1
        bucket[1] += record.n_results
        bucket[2] += record.n_group_urls

    if not by_lang:
        return

    table = Table(title="Ausbeute nach Sprache")
    table.add_column("Sprache")
    table.add_column("Anfragen", justify="right")
    table.add_column("Treffer", justify="right")
    table.add_column("Gruppen-URLs", justify="right")
    for lang, (queries, hits, urls) in sorted(by_lang.items()):
        table.add_row(lang, str(queries), str(hits), str(urls))
    console.print(table)


@app.command("config-check")
def config_check_command() -> None:
    """Prueft, ob alle Konfigurationsdateien lesbar und plausibel sind."""
    config = _config()

    table = Table(title="Konfiguration", show_header=False, box=None)
    table.add_row("Wurzel", str(config.root))
    table.add_row("Zielgruppen gesamt", str(len(config.audiences)))
    table.add_row("davon Phase 1", ", ".join(a.id for a in config.audiences_for_phase(1)))
    table.add_row("Staedte gesamt", str(len(config.cities)))
    table.add_row("davon Phase 1", ", ".join(c.name_de for c in config.cities_for_phase(1)))
    table.add_row("Kategorien", str(len(config.categories)))
    table.add_row("Geplante Anfragen", str(len(build_queries(config, 1))))
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

    seeds_dir = config.path("seeds_dir")
    seed_files = sorted(p.name for p in seeds_dir.glob("*") if p.suffix.lower() in {".csv", ".txt"})
    console.print(f"Seed-Dateien in {seeds_dir}: {', '.join(seed_files) or 'keine'}")


def _distribution_table(title: str, counter, unclassified: int) -> None:
    table = Table(title=title)
    table.add_column("Wert")
    table.add_column("Gruppen", justify="right")
    for key, count in counter.most_common():
        table.add_row(str(key), str(count))
    if unclassified:
        table.add_row("[dim]nicht zugeordnet[/dim]", f"[dim]{unclassified}[/dim]")
    console.print(table)


def _print_groups(groups: list, title: str = "Gruppen") -> None:
    if not groups:
        return
    table = Table(title=title)
    table.add_column("Score", justify="right")
    table.add_column("Status")
    table.add_column("Name")
    table.add_column("Zielgruppen")
    table.add_column("Stadt")
    table.add_column("Mitglieder", justify="right")

    for group in groups:
        table.add_row(
            f"{group.score:.1f}" if group.score is not None else "[dim]-[/dim]",
            group.status.value,
            (group.name or f"[dim]{group.group_id}[/dim]")[:44],
            ", ".join(group.audience_tags) or "[dim]unknown[/dim]",
            group.city or "[dim]unknown[/dim]",
            f"{group.member_count:,}".replace(",", ".")
            if group.member_count is not None
            else "[dim]unknown[/dim]",
        )
    console.print(table)


@auth_app.command("login")
def login_command() -> None:
    """Launches an interactive browser to log into Facebook and save the session."""
    from fbgroups.automation.browser import get_browser_context
    config = _config()
    console.print("[yellow]A visible browser window will now open.[/yellow]")
    console.print("Please log into Facebook, complete 2FA if necessary, and then close the window.")
    
    with get_browser_context(config, headless=False) as context:
        page = context.new_page()
        page.goto("https://www.facebook.com/")
        console.print("Browser is open. Waiting for you to finish and close the window manually...")
        import contextlib
        with contextlib.suppress(Exception):
            # Wait for the page to be closed by the user
            page.wait_for_event("close", timeout=0)
    console.print("[green]Session saved! You can now use automated posting features.[/green]")


if __name__ == "__main__":
    app()

