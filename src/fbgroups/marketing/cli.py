"""Kommandozeile der Marketing-Erweiterung.

Zwei Unterbefehle, die an die bestehende Anwendung angehaengt werden:

    fbgroups campaign ...    Kampagnen anlegen, Gruppen zuordnen, Links holen
    fbgroups marketing ...   Arbeitsstand je Gruppe pflegen

Kein Befehl dieses Moduls ruft facebook.com auf, veroeffentlicht etwas oder
verschickt etwas. ``campaign message`` gibt die Textvorlage aus, ``campaign
next`` legt sie zusaetzlich in die Zwischenablage und oeffnet die Gruppe im
Browser - einfuegen und abschicken muss beides ein Mensch.
"""

from __future__ import annotations

import atexit
import csv
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from fbgroups.config import AppConfig, load_config
from fbgroups.marketing import vorlagen
from fbgroups.marketing.analytics import (
    code_bericht,
    funnel,
    kennzahlen,
    top_campaigns,
    top_groups,
)
from fbgroups.marketing.beitrag import beitragstext, in_zwischenablage, oeffne_im_browser
from fbgroups.marketing.models import (
    MARKETING_FORTSCHRITT,
    Campaign,
    CampaignGroup,
    CampaignParticipation,
    CampaignStatus,
    ContactStatus,
    GroupMarketing,
    JobStatus,
    MarketingStatus,
    PermissionStatus,
    PostStatus,
    QueueZustand,
    ReferralStatus,
    RewardStatus,
    Texttyp,
)
from fbgroups.marketing.queue import UngueltigerUebergang
from fbgroups.marketing.referral import code_fuer_benutzer, setze_status
from fbgroups.marketing.rewards import bewerte_benutzer, fortschritt, load_reward_rules
from fbgroups.marketing.selection import (
    ALLE,
    Auswahl,
    Zuordnungsplan,
    auswahl_der_kampagne,
    baue_plan,
    nach_prioritaet,
    passt,
    prioritaet,
    synchronisiere,
)
from fbgroups.marketing.store import MarketingStore, UnknownGroupError
from fbgroups.marketing.tracking import (
    app_base_url,
    app_base_url_quelle,
    ist_lokale_basis,
    slug,
    tracking_url,
)
from fbgroups.models import Group
from fbgroups.scoring import sort_by_rank
from fbgroups.storage import SqliteStore

console = Console()

campaign_app = typer.Typer(add_completion=False, help="Kampagnen verwalten.")
marketing_app = typer.Typer(
    add_completion=False, help="Arbeitsstand, Auswertung, Empfehlungen und Praemien."
)
referral_app = typer.Typer(add_completion=False, help="Empfehlungen verwalten.")
marketing_app.add_typer(referral_app, name="referral")


def _config() -> AppConfig:
    try:
        return load_config()
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]Konfigurationsfehler:[/red] {exc}")
        raise typer.Exit(code=1) from exc


def _slug(text: str) -> str:
    """Kennung aus dem Namen - dieselbe Funktion wie im Web-Weg."""
    return slug(text) or "kampagne"


def _datum(wert: str | None) -> date | None:
    if not wert:
        return None
    try:
        return date.fromisoformat(wert)
    except ValueError as exc:
        console.print(f"[red]Datum muss JJJJ-MM-TT sein:[/red] {wert}")
        raise typer.Exit(code=1) from exc


def _stores(config: AppConfig) -> tuple[SqliteStore, MarketingStore]:
    pfad = config.path("sqlite_path")
    if not pfad.exists():
        console.print(
            "[yellow]Noch keine Datenbank. Zuerst 'fbgroups import-seeds' "
            "oder 'fbgroups search' ausfuehren.[/yellow]"
        )
        raise typer.Exit(code=1)
    return SqliteStore(pfad), MarketingStore(pfad)


def _kampagne_oder_ende(store: MarketingStore, campaign_id: str) -> Campaign:
    campaign = store.load_campaign(campaign_id)
    if campaign is None:
        console.print(f"[red]Unbekannte Kampagne:[/red] {campaign_id}")
        console.print("[dim]Vorhandene zeigt: fbgroups campaign list[/dim]")
        raise typer.Exit(code=1)
    return campaign


# ---------------------------------------------------------------------------
# Kampagnen
# ---------------------------------------------------------------------------


@campaign_app.command("new")
def campaign_new(
    name: str = typer.Argument(..., help="Anzeigename, z. B. 'Batreeq Syrian Germany'."),
    campaign_id: str = typer.Option(None, "--id", help="Kennung (Standard: aus dem Namen)."),
    description: str = typer.Option("", "--beschreibung"),
    audience: list[str] = typer.Option(None, "--zielgruppe", help="Mehrfach moeglich."),
    city: list[str] = typer.Option(None, "--stadt", help="Mehrfach moeglich."),
    language: str = typer.Option("", "--sprache", help="de | ar | translit"),
    landing_page: str = typer.Option("", "--landingpage"),
    template: str = typer.Option("", "--vorlage", help="Textvorlage zum Selberposten."),
    template_file: Path = typer.Option(None, "--vorlage-datei", help="Vorlage aus einer Datei."),
    starts_on: str = typer.Option(None, "--start", help="JJJJ-MM-TT"),
    ends_on: str = typer.Option(None, "--ende", help="JJJJ-MM-TT"),
) -> None:
    """Legt eine Kampagne an (Status: draft)."""
    config = _config()
    kennung = _slug(campaign_id or name)

    if template_file:
        # utf-8-sig: Vorlagen entstehen oft in Notepad, das ein BOM schreibt.
        template = template_file.read_text(encoding="utf-8-sig")

    unbekannte_zielgruppen = [a for a in (audience or []) if a not in config.audiences]
    unbekannte_staedte = [c for c in (city or []) if c not in config.cities]
    if unbekannte_zielgruppen or unbekannte_staedte:
        console.print(
            f"[red]Unbekannt in der Konfiguration:[/red] "
            f"{', '.join(unbekannte_zielgruppen + unbekannte_staedte)}"
        )
        console.print("[dim]Gueltige Werte stehen in config/audiences.yaml und cities.yaml[/dim]")
        raise typer.Exit(code=1)

    with MarketingStore(config.path("sqlite_path")) as store:
        if store.load_campaign(kennung) is not None:
            console.print(f"[red]Es gibt bereits eine Kampagne '{kennung}'.[/red]")
            raise typer.Exit(code=1)

        campaign = Campaign(
            campaign_id=kennung,
            name=name,
            description=description,
            audiences=list(audience or []),
            cities=list(city or []),
            language=language,
            message_template=template,
            landing_page=landing_page,
            starts_on=_datum(starts_on),
            ends_on=_datum(ends_on),
        )
        store.save_campaign(campaign)

    console.print(
        Panel(
            f"[green]Kampagne angelegt:[/green] [bold]{kennung}[/bold]\n"
            f"Status: draft\n\n"
            f"Naechster Schritt - Gruppen zuordnen und Tracking-Codes vergeben:\n"
            f"  [bold]fbgroups campaign add-groups {kennung} --top 20[/bold]",
            title=name,
        )
    )


@campaign_app.command("list")
def campaign_list(
    status: str = typer.Option(None, "--status", help="draft | active | paused | completed"),
) -> None:
    """Zeigt alle Kampagnen."""
    config = _config()
    with MarketingStore(config.path("sqlite_path")) as store:
        filter_status = CampaignStatus(status) if status else None
        campaigns = store.load_campaigns(filter_status)
        zuordnungen = {
            c.campaign_id: len(store.links_for_campaign(c.campaign_id)) for c in campaigns
        }

    if not campaigns:
        console.print("[yellow]Noch keine Kampagne. Anlegen: fbgroups campaign new NAME[/yellow]")
        return

    table = Table(title="Kampagnen")
    table.add_column("Kennung")
    table.add_column("Name")
    table.add_column("Status")
    table.add_column("Zielgruppen")
    table.add_column("Staedte")
    table.add_column("Gruppen", justify="right")

    for campaign in campaigns:
        table.add_row(
            campaign.campaign_id,
            campaign.name[:34],
            campaign.status.value,
            ", ".join(campaign.audiences) or "[dim]alle[/dim]",
            ", ".join(campaign.cities) or "[dim]alle[/dim]",
            str(zuordnungen[campaign.campaign_id]),
        )
    console.print(table)


@campaign_app.command("show")
def campaign_show(campaign_id: str = typer.Argument(...)) -> None:
    """Zeigt eine Kampagne mit ihren Gruppen und Links."""
    config = _config()
    with MarketingStore(config.path("sqlite_path")) as store:
        campaign = _kampagne_oder_ende(store, campaign_id)
        links = store.links_for_campaign(campaign_id)

    with SqliteStore(config.path("sqlite_path")) as gruppen_store:
        namen = {g.group_id: g for g in gruppen_store.load_groups()}

    basis = app_base_url(config)
    quelle = app_base_url_quelle(config)

    kopf = Table(show_header=False, box=None)
    kopf.add_row("Name", campaign.name)
    kopf.add_row("Status", campaign.status.value)
    kopf.add_row("Basis-URL", f"{basis or '[red]nicht gesetzt[/red]'}  [dim]({quelle})[/dim]")
    kopf.add_row("Beschreibung", campaign.description or "[dim]-[/dim]")
    kopf.add_row("Zielgruppen", ", ".join(campaign.audiences) or "[dim]alle[/dim]")
    kopf.add_row("Staedte", ", ".join(campaign.cities) or "[dim]alle[/dim]")
    kopf.add_row("Sprache", campaign.language or "[dim]-[/dim]")
    kopf.add_row("Landingpage", campaign.landing_page or "[dim]-[/dim]")
    kopf.add_row("Zeitraum", f"{campaign.starts_on or '-'} bis {campaign.ends_on or '-'}")
    kopf.add_row("Zugeordnete Gruppen", str(len(links)))
    console.print(Panel(kopf, title=f"Kampagne {campaign.campaign_id}"))

    # Ein Link auf den eigenen Rechner sieht aus wie jeder andere. In einem
    # Beitrag fuehrt er jeden Leser auf dessen eigenen Rechner - deshalb hier
    # ausdruecklich benannt, statt es dem Auge zu ueberlassen.
    if basis and ist_lokale_basis(basis):
        console.print(
            f"[yellow]Hinweis:[/yellow] '{basis}' zeigt auf diesen Rechner. Fuer die "
            "Entwicklung richtig, fuer veroeffentlichte Links unbrauchbar.\n"
            "[dim]Oeffentliche Adresse setzen:  APP_BASE_URL=https://deine-domain.de "
            "in .env\ndanach:  fbgroups campaign refresh-urls "
            f"{campaign.campaign_id}[/dim]"
        )

    if links:
        _links_tabelle(links, namen)


def _links_tabelle(
    links: list[CampaignGroup],
    namen: dict[str, Group],
    grenze: int = 25,
) -> None:
    """Zeigt die Links. Bei vielen nur den Anfang - 310 Zeilen liest niemand.

    Die vollstaendige Liste holt ``campaign links --export``; im Terminal
    scrollte sie nur die Zusammenfassung aus dem Bild.
    """
    gezeigt = links if grenze <= 0 else links[:grenze]

    table = Table(title="Tracking-Links")
    table.add_column("Tracking-Code")
    table.add_column("Gruppe")
    table.add_column("Stadt")
    table.add_column("Link")

    for link in gezeigt:
        group = namen.get(link.group_id)
        table.add_row(
            link.tracking_code,
            ((group.name if group else "") or link.group_id)[:34],
            (group.city if group else None) or "[dim]-[/dim]",
            link.tracking_url or "[dim](keine APP_BASE_URL gesetzt)[/dim]",
        )
    console.print(table)

    if len(links) > len(gezeigt):
        console.print(
            f"[dim]... und {len(links) - len(gezeigt)} weitere. "
            f"Vollstaendig: campaign links --export data\\exports\\links.csv[/dim]"
        )


@campaign_app.command("set")
def campaign_set(
    campaign_id: str = typer.Argument(...),
    name: str = typer.Option(None, "--name"),
    description: str = typer.Option(None, "--beschreibung"),
    language: str = typer.Option(None, "--sprache", help="de | ar | translit"),
    landing_page: str = typer.Option(None, "--landingpage", help="Ziel der Tracking-Links."),
    template: str = typer.Option(None, "--vorlage", help="Textvorlage zum Selberposten."),
    template_file: Path = typer.Option(None, "--vorlage-datei", help="Vorlage aus einer Datei."),
    starts_on: str = typer.Option(None, "--start", help="JJJJ-MM-TT"),
    ends_on: str = typer.Option(None, "--ende", help="JJJJ-MM-TT"),
    ziel: str = typer.Option(
        None,
        "--ziel",
        help="store | landing | vorgabe - wohin ein Klick fuehrt.",
    ),
) -> None:
    """Aendert eine bestehende Kampagne. Nicht genannte Felder bleiben.

    Notwendig, weil eine Kampagne nicht neu angelegt werden darf, um etwa die
    Landingpage zu korrigieren: Beim Loeschen faellt ueber den Fremdschluessel
    auch die Zuordnung der Gruppen weg - und damit die vergebenen
    Tracking-Codes. Die stehen aber moeglicherweise schon in veroeffentlichten
    Beitraegen und muessen bleiben.

    Die Kennung selbst ist nicht aenderbar; sie steckt in jedem Code.
    """
    config = _config()

    if template_file:
        # utf-8-sig: Vorlagen entstehen oft in Notepad, das ein BOM schreibt.
        template = template_file.read_text(encoding="utf-8-sig")

    if ziel is not None and ziel not in ("store", "landing", "vorgabe"):
        console.print(
            f"[red]Unbekanntes Ziel: {ziel}[/red]\n"
            "Moeglich: store (Play Store), landing (eigene Seite), "
            "vorgabe (Wert aus config/settings.yaml)"
        )
        raise typer.Exit(code=2)

    with MarketingStore(config.path("sqlite_path")) as store:
        campaign = _kampagne_oder_ende(store, campaign_id)

        geaendert: list[str] = []
        for feld, wert in (
            ("name", name),
            ("description", description),
            ("language", language),
            ("landing_page", landing_page),
            ("message_template", template),
            ("starts_on", _datum(starts_on) if starts_on else None),
            ("ends_on", _datum(ends_on) if ends_on else None),
            # "vorgabe" schreibt den leeren Wert - das ist der Weg zurueck zur
            # Konfigurationsvorgabe. Ohne so ein Wort gaebe es ihn nicht: Ein
            # leerer Wert auf der Kommandozeile ist von "nicht angegeben" nicht
            # zu unterscheiden. Dieselbe Vereinbarung wie "alle" bei --stadt.
            ("ziel", "" if ziel == "vorgabe" else ziel),
        ):
            if wert is not None:
                setattr(campaign, feld, wert)
                geaendert.append(feld)

        if not geaendert:
            console.print("[yellow]Nichts angegeben - es wurde nichts geaendert.[/yellow]")
            raise typer.Exit(code=0)

        campaign.updated_at = datetime.now(UTC)
        store.save_campaign(campaign)

    console.print(f"[green]{campaign_id}:[/green] {', '.join(geaendert)} aktualisiert.")
    if landing_page:
        console.print(f"[dim]Klicks auf die Tracking-Links landen jetzt auf {landing_page}[/dim]")


@campaign_app.command("status")
def campaign_status(
    campaign_id: str = typer.Argument(...),
    status: str = typer.Argument(..., help="draft | active | paused | completed"),
) -> None:
    """Setzt den Status einer Kampagne."""
    config = _config()
    with MarketingStore(config.path("sqlite_path")) as store:
        campaign = _kampagne_oder_ende(store, campaign_id)
        campaign.status = CampaignStatus(status)
        campaign.updated_at = datetime.now(UTC)
        store.save_campaign(campaign)
    console.print(f"[green]{campaign_id}:[/green] Status ist jetzt {status}.")


def _liste_setzen(vorhanden: list[str], neu: list[str] | None) -> list[str] | None:
    """Wertet eine wiederholbare Option aus. ``None`` heisst "nicht angegeben".

    Der Wert ``alle`` loescht die Einschraenkung. Ohne so ein Wort gaebe es
    keinen Weg zurueck: Eine leere Liste ist von "nicht angegeben" nicht zu
    unterscheiden, und genau daran scheiterte bisher jeder Versuch, eine
    Kampagne wieder zu weiten.
    """
    if not neu:
        return None
    if any(wert.strip().lower() == ALLE for wert in neu):
        return []
    return [wert.strip().lower() for wert in neu if wert.strip()] or vorhanden


def _regel_anzeigen(campaign: Campaign, config: AppConfig, treffer: int, gesamt: int) -> None:
    auswahl = auswahl_der_kampagne(campaign, config)
    console.print(
        Panel(
            f"{auswahl.beschreibung()}\n\n"
            f"Passende Gruppen: [bold]{treffer}[/bold] von {gesamt} im Bestand\n"
            f"Neue Gruppen automatisch uebernehmen: "
            f"[bold]{'ja' if campaign.auto_assign else 'nein'}[/bold]",
            title=f"Auswahlregel {campaign.campaign_id}",
        )
    )


@campaign_app.command("target")
def campaign_target(
    campaign_id: str = typer.Argument(...),
    alle: bool = typer.Option(False, "--alle", help="Jede Einschraenkung aufheben."),
    city: list[str] = typer.Option(
        None, "--stadt", help=f"Staedte (Kennung). '{ALLE}' hebt die Einschraenkung auf."
    ),
    audience: list[str] = typer.Option(
        None, "--zielgruppe", help=f"Zielgruppen. '{ALLE}' hebt die Einschraenkung auf."
    ),
    category: list[str] = typer.Option(
        None, "--kategorie", help=f"Kategorien. '{ALLE}' hebt die Einschraenkung auf."
    ),
    status: list[str] = typer.Option(
        None, "--status", help=f"Datensatzstatus. '{ALLE}' hebt die Einschraenkung auf."
    ),
    min_score: float = typer.Option(None, "--min-score", help="Mindestscore (-1 hebt ihn auf)."),
    unbewertete: bool = typer.Option(
        None,
        "--auch-unbewertete/--nur-bewertete",
        help="Gruppen ohne Score mitnehmen?",
    ),
    auto_assign: bool = typer.Option(
        None,
        "--auto-assign/--kein-auto-assign",
        help="Neu gefundene Gruppen automatisch uebernehmen.",
    ),
) -> None:
    """Legt fest, welche Gruppen die Kampagne erfasst.

    Ohne Angaben zeigt der Befehl die geltende Regel und wie viele Gruppen sie
    trifft - eine Regel, die man nicht nachlesen kann, aendert niemand gern.

    Die Regel ist von ``audiences``/``cities`` der Kampagne getrennt: Die
    beschreiben, *wen* die Kampagne bewirbt, die Regel bestimmt, *welche
    Gruppen* einen Tracking-Code bekommen. Beides war frueher dasselbe Feld -
    dadurch liess sich eine Kampagne nicht auf den ganzen Bestand weiten, ohne
    ihre fachliche Beschreibung zu verfaelschen.
    """
    config = _config()
    gruppen_store, store = _stores(config)

    try:
        campaign = _kampagne_oder_ende(store, campaign_id)
        groups = gruppen_store.load_groups()

        geaendert: list[str] = []

        if alle:
            campaign.target_audiences = []
            campaign.target_cities = []
            campaign.target_categories = []
            campaign.target_statuses = []
            campaign.target_min_score = None
            campaign.target_include_unscored = True
            geaendert.append("alle Einschraenkungen aufgehoben")

        for feld, wert, name in (
            ("target_cities", _liste_setzen(campaign.target_cities, city), "Stadt"),
            (
                "target_audiences",
                _liste_setzen(campaign.target_audiences, audience),
                "Zielgruppe",
            ),
            (
                "target_categories",
                _liste_setzen(campaign.target_categories, category),
                "Kategorie",
            ),
            ("target_statuses", _liste_setzen(campaign.target_statuses, status), "Status"),
        ):
            if wert is not None:
                setattr(campaign, feld, wert)
                geaendert.append(f"{name}: {', '.join(wert) or 'keine Einschraenkung'}")

        if min_score is not None:
            # -1 statt eines eigenen Schalters: Ein Mindestscore unter 0 ist
            # fachlich sinnlos und damit als "aufheben" eindeutig.
            campaign.target_min_score = None if min_score < 0 else min_score
            geaendert.append(
                "Mindestscore aufgehoben"
                if campaign.target_min_score is None
                else f"Mindestscore {campaign.target_min_score:g}"
            )

        if unbewertete is not None:
            campaign.target_include_unscored = unbewertete
            geaendert.append("auch unbewertete Gruppen" if unbewertete else "nur bewertete Gruppen")

        if auto_assign is not None:
            campaign.auto_assign = auto_assign
            geaendert.append(
                "neue Gruppen automatisch uebernehmen"
                if auto_assign
                else "neue Gruppen nicht automatisch uebernehmen"
            )

        if geaendert:
            campaign.updated_at = datetime.now(UTC)
            store.save_campaign(campaign)

        auswahl = auswahl_der_kampagne(campaign, config)
        treffer = sum(1 for g in groups if passt(g, auswahl))
        _regel_anzeigen(campaign, config, treffer, len(groups))

        if geaendert:
            console.print(f"[green]Geaendert:[/green] {' · '.join(geaendert)}")
            zugeordnet = len(store.assigned_group_ids(campaign_id))
            if treffer > zugeordnet:
                console.print(
                    f"[dim]{treffer - zugeordnet} passende Gruppen haben noch keinen Code. "
                    f"Vergeben mit: fbgroups campaign sync {campaign_id}[/dim]"
                )
        else:
            console.print("[dim]Nichts angegeben - die Regel wurde nur angezeigt.[/dim]")
    finally:
        gruppen_store.close()
        store.close()


def _plan_ausgeben(
    plan: Zuordnungsplan,
    campaign: Campaign,
    config: AppConfig,
    dry_run: bool,
    campaign_id: str,
) -> None:
    """Zeigt und - wenn kein Probelauf - speichert einen Zuordnungsplan."""
    if plan.neu:
        namen = {g.group_id: g for g, _ in plan.neu}
        _links_tabelle([link for _g, link in plan.neu], namen)

    hinweis = "[cyan]--dry-run:[/cyan] nichts gespeichert. " if dry_run else ""
    console.print(
        f"{hinweis}[green]{plan.anzahl_neu}[/green] Gruppen neu zugeordnet, "
        f"{plan.bereits_zugeordnet} waren es bereits (Codes unveraendert). "
        f"Kampagne {campaign.campaign_id}."
    )

    if plan.nicht_mehr_passend:
        console.print(
            f"[yellow]{len(plan.nicht_mehr_passend)}[/yellow] zugeordnete Gruppen "
            "entsprechen der Regel nicht mehr. Sie behalten ihren Code - er kann "
            "in einem veroeffentlichten Beitrag stehen."
        )

    if not app_base_url(config):
        console.print(
            "[yellow]Hinweis:[/yellow] APP_BASE_URL ist nicht gesetzt - die Codes stehen, "
            "die Links bleiben leer.\nSetzen in .env oder config/settings.yaml, danach: "
            f"[bold]fbgroups campaign refresh-urls {campaign_id}[/bold]"
        )


@campaign_app.command("sync")
def campaign_sync(
    campaign_id: str = typer.Argument(...),
    dry_run: bool = typer.Option(False, "--dry-run", help="Nur zeigen, nichts speichern."),
) -> None:
    """Wendet die Auswahlregel der Kampagne auf den gesamten Bestand an.

    Wiederholbar: Ein zweiter Aufruf ohne neue Gruppen aendert nichts. Genau
    das macht ihn zum richtigen Befehl nach jedem Import und jedem Suchlauf -
    und laesst ihn bei 10.000 Gruppen genauso arbeiten wie bei 310.

    Es wird ausschliesslich **hinzugefuegt**. Eine bestehende Zuordnung wird
    nie entfernt und ein vergebener Code nie neu berechnet: Er steht
    moeglicherweise in einem veroeffentlichten Beitrag.
    """
    config = _config()
    gruppen_store, store = _stores(config)

    try:
        campaign = _kampagne_oder_ende(store, campaign_id)
        groups = gruppen_store.load_groups()

        if dry_run:
            plan = baue_plan(
                groups,
                campaign,
                config,
                vorhandene_gruppen=store.assigned_group_ids(campaign_id),
                vergebene_codes=store.assigned_codes(),
            )
        else:
            plan = synchronisiere(store, groups, campaign, config)

        _plan_ausgeben(plan, campaign, config, dry_run, campaign_id)
    finally:
        gruppen_store.close()
        store.close()


@campaign_app.command("add-groups")
def campaign_add_groups(
    campaign_id: str = typer.Argument(...),
    top: int = typer.Option(0, "--top", help="Nur N neue Zuordnungen (0 = alle passenden)."),
    city: list[str] = typer.Option(None, "--stadt", help="Nur diese Staedte (Kennung)."),
    audience: list[str] = typer.Option(None, "--zielgruppe", help="Nur diese Zielgruppen."),
    min_score: float = typer.Option(0.0, "--min-score"),
    alle_gruppen: bool = typer.Option(
        False, "--auch-unbewertete", help="Auch Gruppen ohne Score zuordnen."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Nur zeigen, nichts speichern."),
) -> None:
    """Ordnet Gruppen einmalig zu - mit einer Auswahl nur fuer diesen Aufruf.

    Fuer den dauerhaften Fall ist ``campaign target`` + ``campaign sync``
    gedacht: Dort steht die Regel bei der Kampagne und laesst sich jederzeit
    erneut anwenden. Dieser Befehl bleibt fuer den einmaligen Griff - etwa
    "nur die besten zehn dieser einen Stadt, zum Ausprobieren".

    Ohne ``--stadt``/``--zielgruppe`` gilt die gespeicherte Regel der Kampagne.
    Bereits zugeordnete Gruppen bleiben unveraendert.
    """
    config = _config()
    gruppen_store, store = _stores(config)

    try:
        campaign = _kampagne_oder_ende(store, campaign_id)
        groups = gruppen_store.load_groups()

        gespeichert = auswahl_der_kampagne(campaign, config)
        stadt_namen = {
            c.name_de.lower()
            for cid, c in config.cities.items()
            if cid.lower() in {s.lower() for s in (city or [])}
        }
        auswahl = Auswahl(
            audiences=frozenset(a.lower() for a in audience) if audience else gespeichert.audiences,
            cities=frozenset(stadt_namen) if city else gespeichert.cities,
            categories=gespeichert.categories,
            statuses=gespeichert.statuses,
            min_score=min_score if min_score > 0 else gespeichert.min_score,
            include_unscored=alle_gruppen or gespeichert.include_unscored,
        )

        plan = baue_plan(
            groups,
            campaign,
            config,
            vorhandene_gruppen=store.assigned_group_ids(campaign_id),
            vergebene_codes=store.assigned_codes(),
            auswahl=auswahl,
            top=top,
        )

        if not dry_run and plan.neu:
            try:
                store.add_links([link for _g, link in plan.neu])
            except UnknownGroupError as exc:
                console.print(f"[yellow]Nicht im Bestand, uebersprungen:[/yellow] {exc}")

        _plan_ausgeben(plan, campaign, config, dry_run, campaign_id)
    finally:
        gruppen_store.close()
        store.close()


@campaign_app.command("links")
def campaign_links(
    campaign_id: str = typer.Argument(...),
    output: Path = typer.Option(None, "--export", help="Als CSV schreiben."),
) -> None:
    """Listet die Tracking-Links einer Kampagne (optional als CSV)."""
    config = _config()
    gruppen_store, store = _stores(config)
    try:
        _kampagne_oder_ende(store, campaign_id)
        # Fehlende Kurzcodes nachtragen, bevor die Liste entsteht: Wer die
        # CSV nimmt, um von Hand zu posten, soll dieselbe kurze Adresse
        # bekommen wie die Automatik - nicht die mit dem Kampagnencode.
        store.kurzcodes_nachtragen(campaign_id)
        links = store.links_for_campaign(campaign_id)
        namen = {g.group_id: g for g in gruppen_store.load_groups()}
    finally:
        gruppen_store.close()
        store.close()

    if not links:
        console.print("[yellow]Dieser Kampagne ist noch keine Gruppe zugeordnet.[/yellow]")
        return

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        # utf-8-sig und ';' - sonst zeigt Excel Arabisch und Spalten falsch an.
        with output.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh, delimiter=";")
            # ``public_url`` steht **hinten** angehaengt: Die bisherigen
            # sechs Spalten behalten ihre Stellung, damit eine Tabelle, die
            # jemand schon gebaut hat, weiter passt. Sie ist die Adresse, die
            # in einen Beitrag gehoert - die davor ist die des Datensatzes.
            writer.writerow(
                [
                    "tracking_code", "tracking_url", "group_id", "name", "stadt", "url",
                    "public_url",
                ]
            )
            for link in links:
                group = namen.get(link.group_id)
                writer.writerow(
                    [
                        link.tracking_code,
                        link.tracking_url,
                        link.group_id,
                        group.name if group else "",
                        (group.city if group else "") or "",
                        group.url_canonical if group else "",
                        link.url_fuer("store"),
                    ]
                )
        console.print(f"[green]CSV:[/green] {output}  ({len(links)} Links)")
        return

    _links_tabelle(links, namen)


@campaign_app.command("kurzlinks")
def campaign_kurzlinks(
    campaign_id: str = typer.Argument(
        "", help="Nur diese Kampagne. Ohne Angabe: der ganze Bestand."
    ),
) -> None:
    """Traegt die oeffentlichen Kurzcodes nach - fuer Zuordnungen von frueher.

    Neue Zuordnungen bringen ihren Deckname selbst mit; was hier noch fehlt,
    stammt aus der Zeit vor dem 14.09.2026. Der Befehl ist wiederholbar und
    fasst Vorhandenes nie an: Ein vergebener Kurzcode steht moeglicherweise
    schon in einem Beitrag, und ein Klick darauf muss ankommen.

    Er **aendert keine Tracking-Codes** und keine Auswertung. Was sich
    aendert, ist allein die Adresse, die ab dem naechsten Beitrag hinausgeht.
    """
    config = _config()
    with MarketingStore(config.path("sqlite_path")) as store:
        if campaign_id:
            _kampagne_oder_ende(store, campaign_id)
        anzahl = store.kurzcodes_nachtragen(campaign_id)

    umfang = f"Kampagne {campaign_id}" if campaign_id else "dem ganzen Bestand"
    if not anzahl:
        console.print(f"[green]In {umfang} hat jede Zuordnung ihre kurze Adresse.[/green]")
        return
    console.print(
        f"[green]{anzahl}[/green] Zuordnung(en) in {umfang} haben eine kurze Adresse "
        "bekommen. Die Tracking-Codes und alle Auswertungen bleiben unveraendert."
    )


@campaign_app.command("refresh-urls")
def campaign_refresh_urls(campaign_id: str = typer.Argument(...)) -> None:
    """Schreibt die Links mit der aktuellen APP_BASE_URL neu - Codes bleiben."""
    config = _config()
    basis = app_base_url(config)
    if not basis:
        console.print("[red]APP_BASE_URL ist nicht gesetzt.[/red]")
        raise typer.Exit(code=1)

    with MarketingStore(config.path("sqlite_path")) as store:
        _kampagne_oder_ende(store, campaign_id)
        geaendert = store.refresh_tracking_urls(
            campaign_id, lambda code: tracking_url(code, config)
        )

    console.print(f"[green]{geaendert}[/green] Links auf {basis} umgestellt. Codes unveraendert.")


@campaign_app.command("message")
def campaign_message(
    campaign_id: str = typer.Argument(...),
    group_id: str = typer.Argument(..., help="Gruppe, fuer die der Text gelten soll."),
    typ: str = typer.Option(
        "", "--typ", help="post | kommentar (Vorgabe: beide, soweit vorhanden)."
    ),
) -> None:
    """Gibt den fertigen Text mit eingesetztem Tracking-Link aus.

    Zum Kopieren. Dieses Programm postet nichts und schickt nichts - das
    Einfuegen und Absenden bleibt Handarbeit, und das ist Absicht.

    Ohne ``--typ`` erscheinen Beitrag **und** Kommentar, sofern es beide gibt:
    Wer in einer Gruppe steht, braucht meist beides, und zweimal denselben
    Befehl mit verschiedenen Schaltern zu tippen ist kein Gewinn.
    """
    config = _config()
    with MarketingStore(config.path("sqlite_path")) as store:
        campaign = _kampagne_oder_ende(store, campaign_id)
        link = store.link_for(campaign_id, group_id)

    if link is None:
        console.print(f"[red]{group_id} ist dieser Kampagne nicht zugeordnet.[/red]")
        raise typer.Exit(code=1)

    gewuenscht = (typ or "").strip().lower()
    if gewuenscht and gewuenscht not in tuple(t.value for t in Texttyp):
        console.print(f"[red]Unbekannter Texttyp:[/red] {typ}")
        raise typer.Exit(code=2)

    # Derselbe Textbauer wie queue, next und die Uebersicht - eine zweite
    # Fassung koennte abweichen, und der Unterschied fiele erst auf, wenn
    # ein Beitrag mit dem falschen Code veroeffentlicht ist.
    gezeigt = 0
    for texttyp in Texttyp:
        if gewuenscht and texttyp.value != gewuenscht:
            continue
        text = beitragstext(campaign, link, texttyp, config=config)
        # Ohne ausdruecklichen Wunsch nur zeigen, was es gibt: Eine leere
        # Kommentarkachel unter jedem Beitrag waere eine Meldung ueber eine
        # Kampagne, die gar keine Kommentare fuehrt.
        if not text and not gewuenscht:
            continue
        gezeigt += 1
        console.print(
            Panel(
                text or "[dim](kein Text hinterlegt)[/dim]",
                # Kein "[post]": Rich liest eckige Klammern als Auszeichnung
                # und verschluckt sie stillschweigend.
                title=f"{texttyp.value.upper()} - {link.tracking_code}",
                subtitle="von Hand einfuegen und absenden",
            )
        )

    if not gezeigt:
        console.print(
            "[yellow]Fuer diese Gruppe steht noch kein Text bereit.[/yellow] "
            f"Erzeugen mit:  fbgroups campaign text {campaign_id} --aus-vorlage --ja"
        )


# ---------------------------------------------------------------------------
# Arbeitsliste: welcher Beitrag steht noch aus?
# ---------------------------------------------------------------------------

_POST_FARBE = {
    PostStatus.OFFEN: "yellow",
    PostStatus.VEROEFFENTLICHT: "green",
    PostStatus.FEHLGESCHLAGEN: "red",
    PostStatus.UEBERSPRUNGEN: "dim",
}


def _gruppen_nach_id(gruppen_store: SqliteStore) -> dict[str, Group]:
    return {g.group_id: g for g in gruppen_store.load_groups()}


def _fortschritt_zeile(zaehler: dict[str, int]) -> str:
    """Eine Zeile mit allen Staenden - immer alle vier, auch die leeren.

    Ein Stand, der bei 0 verschwindet, laesst die Zeile je nach Datenlage
    anders aussehen; man vergleicht dann zwei Laeufe und sucht die Spalte.
    """
    gesamt = sum(zaehler.values())
    teile = [
        f"[{_POST_FARBE[status]}]{zaehler.get(status.value, 0)} {status.value}[/]"
        for status in PostStatus
    ]
    return " · ".join(teile) + f"  (von {gesamt})"


def _nach_rang(links: list[CampaignGroup], gruppen: dict[str, Group]) -> list[CampaignGroup]:
    """Beste Gruppen zuerst.

    Wer die Liste nicht zu Ende bringt - und bei 300 Gruppen bringt sie
    niemand an einem Tag zu Ende -, hat dann die wertvollsten Beitraege
    geschrieben und nicht die alphabetisch ersten. Gruppen ohne Datensatz
    wandern ans Ende statt zu verschwinden: Ein Beitrag, der nicht in der
    Liste steht, wird nie geschrieben.
    """
    bekannt = [gruppen[link.group_id] for link in links if link.group_id in gruppen]
    reihenfolge = {g.group_id: i for i, g in enumerate(sort_by_rank(bekannt))}
    return sorted(links, key=lambda link: reihenfolge.get(link.group_id, len(reihenfolge)))


@campaign_app.command("queue")
def campaign_queue(
    campaign_id: str = typer.Argument(...),
    alle: bool = typer.Option(
        False, "--alle", help="Auch die erledigten und uebersprungenen zeigen."
    ),
    limit: int = typer.Option(0, "--limit", help="Hoechstens so viele Zeilen (0 = alle)."),
) -> None:
    """Zeigt die Arbeitsliste: welcher Beitrag steht in welcher Gruppe noch aus.

    Ohne ``--alle`` erscheinen nur die offenen und die fehlgeschlagenen. Genau
    darum geht es: Bei 300 Gruppen ist die Frage nicht "was gibt es?", sondern
    "was fehlt noch?".
    """
    config = _config()
    gruppen_store, store = _stores(config)
    try:
        _kampagne_oder_ende(store, campaign_id)
        links = store.links_for_campaign(campaign_id) if alle else store.offene_links(campaign_id)
        zaehler = store.post_counts(campaign_id)
        gruppen = _gruppen_nach_id(gruppen_store)
        staende = store.load_all_marketing()
    finally:
        gruppen_store.close()
        store.close()

    console.print(_fortschritt_zeile(zaehler))

    if not links:
        console.print(
            "[yellow]Dieser Kampagne ist noch keine Gruppe zugeordnet.[/yellow]"
            if alle
            else "[green]Nichts offen.[/green]"
        )
        return

    links = _nach_rang(links, gruppen)
    if limit > 0:
        links = links[:limit]

    tabelle = Table(show_header=True, header_style="bold")
    tabelle.add_column("#", justify="right", width=4)
    tabelle.add_column("Gruppe")
    tabelle.add_column("Tracking-Code")
    tabelle.add_column("Stand")
    tabelle.add_column("Beitrag")
    tabelle.add_column("Grund")

    for nummer, link in enumerate(links, start=1):
        group = gruppen.get(link.group_id)
        stand = staende.get(link.group_id)
        post = PostStatus(link.post_status)
        tabelle.add_row(
            str(nummer),
            (group.name if group else link.group_id) or "(ohne Namen)",
            link.tracking_code,
            stand.marketing_status.value if stand else MarketingStatus.NOT_CONTACTED.value,
            f"[{_POST_FARBE[post]}]{post.value}[/]",
            link.post_error or "",
        )

    console.print(tabelle)


@campaign_app.command("fortschritt")
def campaign_fortschritt(campaign_id: str = typer.Argument(...)) -> None:
    """Wie weit die Kampagne beim Veroeffentlichen ist."""
    config = _config()
    with MarketingStore(config.path("sqlite_path")) as store:
        campaign = _kampagne_oder_ende(store, campaign_id)
        zaehler = store.post_counts(campaign_id)
        offen = len(store.offene_links(campaign_id))

    console.print(Panel(_fortschritt_zeile(zaehler), title=campaign.name))
    # Nicht dasselbe wie die Summe der offenen oben: Ausgeschlossene Gruppen
    # zaehlen dort mit, stehen aber nicht in der Arbeitsliste. Ohne diese Zeile
    # wundert man sich, warum 40 offene Beitraege nur 12 Zeilen ergeben.
    console.print(
        f"In der Arbeitsliste: [bold]{offen}[/bold] (ausgeschlossene Gruppen zaehlen nicht mit)"
    )


@campaign_app.command("kaltmodus")
def campaign_kaltmodus(campaign_id: str = typer.Argument(...)) -> None:
    """Was heute ansteht - und wann die Kampagne bei diesem Takt durch ist."""
    from datetime import UTC, datetime

    from fbgroups.marketing import kaltmodus as km
    from fbgroups.marketing.arbeit import arbeitsreihenfolge
    from fbgroups.storage.sqlite_store import SqliteStore

    config = _config()
    aktiv, pro_tag, abstand = km.einstellungen(config)
    if not aktiv:
        console.print(
            "[yellow]Kaltmodus ist aus.[/yellow] Einschalten in "
            "config/settings.yaml: [bold]kaltmodus.aktiv: true[/bold]"
        )
        raise typer.Exit(code=0)

    jetzt = datetime.now(UTC)
    with SqliteStore(config.path("sqlite_path")) as bestand:
        gruppen = {g.group_id: g for g in bestand.load_groups()}
    with MarketingStore(config.path("sqlite_path")) as store:
        campaign = _kampagne_oder_ende(store, campaign_id)
        reihe = arbeitsreihenfolge(store, campaign_id, gruppen, config)
        heute = store.versuche_heute(jetzt.date().isoformat())
        roh = store.letzter_versuch()

    portion = km.tagesportion(reihe, erledigt_heute=heute, grenze=pro_tag)
    letzter = datetime.fromisoformat(roh) if roh else None
    frei_ab = km.naechster_zeitpunkt(
        letzter, abstand_minuten=abstand, jetzt=jetzt, erledigt_heute=heute
    )

    console.print(
        Panel(
            f"Heute: [bold]{portion.erledigt}[/bold] von "
            f"[bold]{portion.grenze}[/bold] - "
            f"noch [bold]{portion.offen_heute}[/bold] vorgesehen",
            title=campaign.name,
        )
    )
    console.print(f"Offen insgesamt: [bold]{portion.verbleibend_gesamt}[/bold]")
    fertig = portion.fertig_am(jetzt.date())
    if fertig:
        console.print(f"Bei diesem Takt fertig am: [bold]{fertig.isoformat()}[/bold]")
    warte = km.wartezeit_text(frei_ab, jetzt=jetzt)
    if warte:
        console.print(f"Naechster Beitrag: [yellow]{warte}[/yellow]")

    if portion.fertig_fuer_heute:
        # Kein Fehler und kein roter Text: Die Portion ist abgearbeitet, das ist
        # der Normalfall eines guten Tages und nicht eine Sperre.
        console.print("\n[green]Fuer heute erledigt.[/green] Morgen geht es weiter.")
        raise typer.Exit(code=0)

    console.print("\n[bold]Heute an der Reihe:[/bold]")
    for nummer, link in enumerate(portion.gruppen, start=1):
        gruppe = gruppen.get(link.group_id)
        name = (gruppe.name if gruppe else "") or link.group_id
        console.print(f"  {nummer:>3}. {name}")


@campaign_app.command("posted")
def campaign_posted(
    campaign_id: str = typer.Argument(...),
    group_id: str = typer.Argument(...),
    fehler: str = typer.Option(None, "--fehler", help="Grund - macht daraus einen Fehlschlag."),
    ueberspringen: bool = typer.Option(False, "--ueberspringen", help="Gruppe passt nicht."),
) -> None:
    """Traegt das Ergebnis eines Beitrags nach - fuer eine einzelne Gruppe."""
    if fehler and ueberspringen:
        console.print("[red]--fehler und --ueberspringen schliessen einander aus.[/red]")
        raise typer.Exit(code=2)

    status = (
        PostStatus.FEHLGESCHLAGEN
        if fehler
        else PostStatus.UEBERSPRUNGEN
        if ueberspringen
        else PostStatus.VEROEFFENTLICHT
    )

    config = _config()
    with MarketingStore(config.path("sqlite_path")) as store:
        _kampagne_oder_ende(store, campaign_id)
        link = store.set_post_status(campaign_id, group_id, status, fehler or "")

    if link is None:
        console.print(f"[red]{group_id} ist dieser Kampagne nicht zugeordnet.[/red]")
        raise typer.Exit(code=1)

    console.print(
        f"[{_POST_FARBE[status]}]{status.value}[/] · {link.tracking_code} · "
        f"Versuch {link.post_attempts}"
    )


@campaign_app.command("retry")
def campaign_retry(
    campaign_id: str = typer.Argument(
        "",
        help="Kennung - oder weglassen fuer ALLE Kampagnen.",
    ),
    alle: bool = typer.Option(
        False, "--alle", help="Auch die, die ihre Versuche aufgebraucht haben."
    ),
    kommentare: bool = typer.Option(
        False,
        "--kommentare",
        help="Zusaetzlich die gescheiterten Kommentarfassungen zuruecksetzen.",
    ),
) -> None:
    """Stellt die fehlgeschlagenen Beitraege zurueck in die Arbeitsliste.

    **Ohne Kennung gilt es fuer alle Kampagnen.** Der Fall, fuer den es
    gebraucht wird, ist naemlich nie einer: Ein geschlossenes Browserfenster
    laesst nicht eine Kampagne scheitern, sondern die, an der gerade
    gearbeitet wurde, und jede danach. Sieben Kampagnen einzeln aufzuzaehlen
    ist derselbe Handgriff siebenmal - und beim achten vergisst man eine.

    ``uebersprungen`` bleibt stehen - dort hat ein Mensch entschieden, dass
    die Gruppe nicht passt.

    Wer ``max_versuche`` (Vorgabe 3) erreicht hat, bleibt ebenfalls stehen und
    wird am Ende genannt: "erlaubt keine Links" geht beim vierten Mal nicht
    anders aus als beim ersten, kostet aber jedes Mal einen Handgriff.
    ``--alle`` uebergeht die Grenze.

    ``--kommentare`` geht eine Ebene tiefer und holt die gescheiterten
    **Fassungen** zurueck. Der Beitrag steht am Paar, die zehn Kommentare
    stehen einzeln - ein abgestuerzter Browser laesst beides scheitern, und
    ohne diesen Schalter waere allein ``campaign reset`` die Antwort: das
    setzt aber die ganze Kampagne auf Anfang, auch das bereits
    Veroeffentlichte. Veroeffentlichte Fassungen bleiben hier unberuehrt.
    """
    config = _config()
    grenze = 0 if alle else int(config.get("marketing", "posting", "max_versuche", default=3) or 0)

    with MarketingStore(config.path("sqlite_path")) as store:
        if campaign_id:
            _kampagne_oder_ende(store, campaign_id)
            kennungen = [campaign_id]
        else:
            # Alle, nicht nur die aktiven: Eine Kampagne, die der Lauf wegen
            # lauter Fehlschlaegen auf ``completed`` gesetzt hat, ist gerade
            # die, die zurueckgeholt werden muss.
            kennungen = [k.campaign_id for k in store.load_campaigns()]
            if not kennungen:
                console.print("[yellow]Keine Kampagne vorhanden.[/yellow]")
                return
            console.print(f"[cyan]Alle {len(kennungen)} Kampagne(n).[/cyan]")

        ergebnisse = []
        for kennung in kennungen:
            anzahl = store.fehlgeschlagene_zuruecksetzen(kennung, max_versuche=grenze)
            stehengeblieben = store.aufgegeben(kennung, grenze)
            fassungen, gruppen = (
                store.gescheiterte_fassungen_zuruecksetzen(kennung)
                if kommentare
                else (0, 0)
            )
            ergebnisse.append((kennung, anzahl, fassungen, gruppen, stehengeblieben))

    tabelle = Table(box=None)
    tabelle.add_column("Kampagne")
    tabelle.add_column("Beitraege", justify="right")
    if kommentare:
        tabelle.add_column("Fassungen", justify="right")
        tabelle.add_column("Gruppen", justify="right")
    tabelle.add_column("aufgebraucht", justify="right")
    for kennung, anzahl, fassungen, gruppen, stehengeblieben in ergebnisse:
        zeile = [kennung, str(anzahl)]
        if kommentare:
            zeile += [str(fassungen), str(gruppen)]
        zeile.append(str(len(stehengeblieben)))
        tabelle.add_row(*zeile)
    console.print(tabelle)

    summe_beitraege = sum(e[1] for e in ergebnisse)
    summe_fassungen = sum(e[2] for e in ergebnisse)
    summe_gruppen = sum(e[3] for e in ergebnisse)
    if kommentare:
        console.print(
            f"[green]{summe_fassungen}[/green] Kommentarfassung(en) in "
            f"[green]{summe_gruppen}[/green] Gruppe(n) wieder offen; "
            "Erschoepfung aus diesen Versuchen zurueckgenommen."
        )
    console.print(
        f"[green]{summe_beitraege}[/green] wieder offen. "
        "Uebersprungene und Veroeffentlichte bleiben unberuehrt."
    )

    offen = [(k, s) for k, _a, _f, _g, s in ergebnisse if s]
    if offen:
        gesamt = sum(len(s) for _k, s in offen)
        console.print(
            f"\n[yellow]{gesamt}[/yellow] haben {grenze} Versuche aufgebraucht "
            "und warten auf eine Entscheidung:"
        )
        for kennung, stehengeblieben in offen:
            for link in stehengeblieben[:5]:
                console.print(
                    f"  [dim]{kennung} · {link.post_attempts}x[/dim] {link.group_id} - "
                    f"{link.post_error or 'ohne Angabe'}"
                )
            if len(stehengeblieben) > 5:
                console.print(
                    f"  [dim]{kennung} · ... und {len(stehengeblieben) - 5} weitere[/dim]"
                )
        console.print(
            "[dim]Trotzdem erneut versuchen:  fbgroups campaign retry "
            f"{campaign_id or ''} --alle[/dim]".replace("retry  ", "retry ")
        )


@campaign_app.command("beitritt")
def campaign_beitritt(
    server: str = typer.Option(
        "http://127.0.0.1:8090", "--server", help="Basisadresse des Dienstes (SSH-Tunnel)."
    ),
    limit: int = typer.Option(
        0, "--limit", help="Hoechstens N Anfragen in diesem Lauf (0 = bis zur Tagesmenge)."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Nur zeigen, wer drankaeme."),
) -> None:
    """Stellt Beitrittsanfragen - sichtbar im Browser, gebucht auf dem Server.

    Die riskanteste Handlung des Projekts, deshalb die vorsichtigste:

    * Die **Tagesmenge bestimmt der Server**, nicht dieser Rechner. Sonst
      haette jedes Fenster sein eigenes Kontingent, und zwei nebeneinander
      verdoppelten die Anfragen.
    * Gruppen mit Beitrittsfragen werden **uebersprungen**, nicht beantwortet.
    * Ein uebersprungener oder gescheiterter Versuch hinterlaesst **nichts** -
      ``beitritt_angefragt`` zu setzen waere die Behauptung, es sei etwas
      abgeschickt worden.
    """
    import httpx

    config = _config()
    basis = server.rstrip("/")
    kopf = {"Origin": basis, "Content-Type": "application/json"}

    with httpx.Client(timeout=120.0, headers=kopf) as klient:
        antwort = klient.post(f"{basis}/automatik/beitritt/naechste", json={})
        if antwort.status_code == 404:
            console.print(
                "[red]Der Dienst haelt den Aufruf fuer nicht-oertlich.[/red] "
                "Laeuft der SSH-Tunnel?"
            )
            raise typer.Exit(code=1)
        antwort.raise_for_status()
        daten = antwort.json()

        gruppen = daten["gruppen"]
        if limit > 0:
            gruppen = gruppen[:limit]

        console.print(
            f"Heute gestellt: [bold]{daten['heute']}[/bold] von {daten['pro_tag']}  ·  "
            f"jetzt an der Reihe: [bold]{len(gruppen)}[/bold]"
        )
        if daten.get("meldung"):
            console.print(f"[yellow]{daten['meldung']}[/yellow]")
        if not gruppen:
            return

        for g in gruppen[:10]:
            console.print(f"  {g['name']}")
        if len(gruppen) > 10:
            console.print(f"  ... und {len(gruppen) - 10} weitere")

        if dry_run:
            console.print("\n[dry-run] Es wird nichts abgeschickt.")
            return

        from fbgroups.automation.actions import request_join
        from fbgroups.automation.browser import get_browser_context
        from fbgroups.marketing import beitritt as bt

        _, abstand = bt.einstellungen(config)
        gezaehlt = {"angefragt": 0, "bereits_mitglied": 0, "fragen": 0, "fehler": 0}

        with get_browser_context(config, headless=False) as context:
            for i, g in enumerate(gruppen, start=1):
                console.print(f"\n[bold]{i}/{len(gruppen)}  {g['name']}[/bold]")
                try:
                    ausgang, bemerkung = request_join(context, g["url"])
                except Exception as exc:  # noqa: BLE001 - ein Fehlschlag ist ein Ausgang
                    ausgang, bemerkung = "fehler", str(exc).splitlines()[0][:120]

                gezaehlt[str(ausgang)] = gezaehlt.get(str(ausgang), 0) + 1
                try:
                    klient.post(
                        f"{basis}/automatik/beitritt/ergebnis",
                        json={
                            "group_id": g["group_id"],
                            "ausgang": str(ausgang),
                            "bemerkung": bemerkung,
                        },
                    ).raise_for_status()
                except Exception as exc:  # noqa: BLE001 - eine Gruppe, nicht der Lauf
                    # Eine Anfrage, die hinausging, aber nicht gebucht werden
                    # konnte: Sie steht bei Facebook und fehlt im Bestand. Das
                    # ist zu melden und kein Grund, die uebrigen Gruppen
                    # ausfallen zu lassen.
                    console.print(
                        f"[red]  nicht gebucht ({str(exc).splitlines()[0][:80]}) - "
                        "die Anfrage selbst ist heraus[/red]"
                    )

                # Der Takt gilt zwischen den Anfragen, nicht danach: Nach der
                # letzten zu warten haelt nur den Menschen auf.
                if i < len(gruppen) and abstand > 0:
                    import random
                    import time

                    pause = abstand * 60 * random.uniform(0.8, 1.3)
                    console.print(f"[dim]  Pause {pause / 60:.1f} Min[/dim]")
                    time.sleep(pause)

    console.print(
        Panel(
            f"Angefragt:        {gezaehlt['angefragt']}\n"
            f"Schon Mitglied:   {gezaehlt['bereits_mitglied']}\n"
            f"Uebersprungen:    {gezaehlt['fragen']}  (Gruppe stellt Beitrittsfragen)\n"
            f"Fehlgeschlagen:   {gezaehlt['fehler']}",
            title="Beitrittsanfragen",
        )
    )


@campaign_app.command("abgleich")
def campaign_abgleich(
    server: str = typer.Option(
        "http://127.0.0.1:8090", "--server", help="Basisadresse des Dienstes (SSH-Tunnel)."
    ),
    ja: bool = typer.Option(False, "--ja", help="Wirklich uebertragen (sonst nur zeigen)."),
) -> None:
    """Traegt oertlich veroeffentlichte Fassungen auf dem Server nach.

    Fuer den Bestand, der vor dem Fernbetrieb entstanden ist: Wer die
    Automatik oertlich gefahren hat, hat die Kommentare abgesetzt, aber in
    seiner **eigenen** Datei gebucht. Auf dem Server stehen sie weiter als
    offen, und die Arbeitsliste dort bietet Arbeit an, die getan ist.

    Uebertragen wird nur in eine Richtung und nur, was oertlich als
    veroeffentlicht gilt. Ein Rueckweg waere gefaehrlich: Der Server ist die
    gueltige Fassung, und ein Abgleich, der ihn ueberschreibt, kostet
    gezaehlte Klicks.
    """
    import httpx

    from fbgroups.marketing.models import MAX_VORSCHLAEGE, Texttyp, VorschlagStatus

    config = _config()
    basis = server.rstrip("/")

    with MarketingStore(config.path("sqlite_path")) as store:
        offen: list[dict] = []
        for kampagne in store.load_campaigns():
            for link in store.links_for_campaign(kampagne.campaign_id):
                # Beide Zwecke: Der Beitrag zieht auf dem Server ausserdem die
                # Spalte BEITRAG mit (``_gruppenstand_nachziehen``), was ein
                # Kommentar bewusst nicht tut.
                for texttyp in (Texttyp.POST, Texttyp.KOMMENTAR):
                    for nummer in range(1, MAX_VORSCHLAEGE + 1):
                        v = store.vorschlag(
                            kampagne.campaign_id, link.group_id, texttyp, nummer
                        )
                        if v is not None and v.status is VorschlagStatus.VEROEFFENTLICHT:
                            offen.append(
                                {
                                    "campaign_id": kampagne.campaign_id,
                                    "group_id": link.group_id,
                                    "nummer": nummer,
                                    "texttyp": texttyp.value,
                                    "erfolg": True,
                                    "post_url": store.letzte_post_url(
                                        kampagne.campaign_id,
                                        link.group_id,
                                        texttyp.value,
                                        nummer,
                                    ),
                                }
                            )

    if not offen:
        console.print("[dim]Oertlich ist nichts veroeffentlicht - nichts abzugleichen.[/dim]")
        return

    console.print(f"Oertlich veroeffentlicht: [bold]{len(offen)}[/bold] Fassung(en)")
    for e in offen:
        console.print(
            f"  {e['campaign_id']} / {e['group_id']} / {e['texttyp']} {e['nummer']}"
        )

    if not ja:
        console.print("\n[dry-run] Mit [bold]--ja[/bold] werden sie auf dem Server nachgetragen.")
        return

    kopf = {"Origin": basis, "Content-Type": "application/json"}
    uebertragen = 0
    with httpx.Client(timeout=30.0, headers=kopf) as klient:
        for e in offen:
            antwort = klient.post(f"{basis}/automatik/ergebnis", json=e)
            if antwort.status_code == 404:
                console.print(
                    "[red]Der Dienst haelt den Aufruf fuer nicht-oertlich.[/red] "
                    "Laeuft der SSH-Tunnel?"
                )
                raise typer.Exit(code=1)
            antwort.raise_for_status()
            if antwort.json().get("ok"):
                uebertragen += 1

    console.print(f"[green]{uebertragen} von {len(offen)} auf dem Server nachgetragen.[/green]")


@campaign_app.command("watchdog")
def campaign_watchdog(
    server: str = typer.Option(
        None,
        "--server",
        help="An den Lauf durchgereicht, z. B. http://127.0.0.1:8090. "
        "Ohne Angabe gilt watchdog.server aus settings.yaml.",
    ),
    abstand: int = typer.Option(
        0, "--abstand", help="Sekunden zwischen zwei Blicken (0 = aus settings.yaml)."
    ),
    einmal: bool = typer.Option(
        False, "--einmal", help="Nur einmal nachsehen und melden - nichts ueberwachen."
    ),
) -> None:
    """Sorgt dafuer, dass **ein** ``campaign automatik`` laeuft - dauerhaft.

    Einmal starten, dann laeuft er:

        fbgroups campaign watchdog --server http://127.0.0.1:8090

    Alle paar Minuten sieht er nach. Laeuft ein Lauf, tut er nichts. Ist
    keiner da - abgestuerzt, beendet, nie gestartet -, startet er genau den
    Befehl, den Sie sonst von Hand eintippen.

    **Er enthaelt keine Kampagnenlogik.** Keine Warteschlange, keine
    Rangfolge, kein Takt, keine Entscheidung ueber eine Gruppe - das bleibt
    alles, wo es ist. Er legt keine Kampagne an, setzt keine auf
    ``completed`` und beginnt keine von vorn: ``campaign automatik`` ohne
    ``--neu`` nimmt den offenen Lauf mitsamt Fortschritt wieder auf.

    **Antwortet der Dienst nicht**, wird gewartet statt gestartet. Ein
    geschlossener SSH-Tunnel ist kein Fehlschlag der Kampagne, und ein Lauf
    ohne Tunnel scheiterte an der ersten Anfrage, ohne etwas zu buchen.

    Beenden mit Strg+C. Soll er einen Neustart des Rechners ueberleben,
    gehoert er in einen Dienst (systemd, Aufgabenplanung) - das ist eine
    Entscheidung ueber den Rechner und keine dieses Programms.
    """
    from fbgroups.marketing import watchdog

    config = _config()
    einst = watchdog.einstellungen(config)
    if server:
        einst = replace(einst, server=server)
    if abstand:
        einst = replace(einst, abstand_sekunden=abstand)

    sperre = watchdog.sperre_fuer(config)

    if not einst.aktiv and not einmal:
        console.print(
            "[yellow]Der Waechter ist abgeschaltet[/yellow] "
            "(watchdog.enabled: false in config/settings.yaml)."
        )
        raise typer.Exit(code=2)

    farbe = {
        "laeuft": "green",
        "gestartet": "cyan",
        "dienst_weg": "yellow",
        "abgeschaltet": "yellow",
    }

    def melde(blick: watchdog.Blick) -> None:
        # Jede Zeile mit Zeitstempel: Der Waechter laeuft tagelang, und die
        # Frage an sein Protokoll ist immer "wann war das?".
        jetzt = datetime.now().strftime("%d.%m. %H:%M:%S")
        console.print(
            f"[dim]{jetzt}[/dim] [{farbe.get(blick.art, 'white')}]"
            f"{blick.art}[/{farbe.get(blick.art, 'white')}]  {blick.meldung}"
        )

    if not einmal:
        console.print(
            f"[cyan]Waechter laeuft.[/cyan] Blick alle "
            f"{int(einst.abstand)} Sekunden"
            + (f", Dienst {einst.server}" if einst.server else "")
            + ".\n[dim]Beenden mit Strg+C. Es wird nichts gestartet, solange "
            "ein Lauf die Sperre haelt.[/dim]"
        )

    try:
        watchdog.wache(sperre, einst, melde=melde, durchgaenge=1 if einmal else 0)
    except KeyboardInterrupt:
        # Der laufende Lauf bleibt laufen - der Waechter ist nur sein
        # Aufpasser, nicht sein Besitzer.
        console.print("\n[yellow]Waechter beendet.[/yellow] "
                      "[dim]Ein laufender campaign automatik laeuft weiter.[/dim]")


@campaign_app.command("automatik")
def campaign_automatik(
    dry_run: bool = typer.Option(False, "--dry-run", help="Nur zeigen, was liefe."),
    max_schritte: int = typer.Option(
        0, "--limit", help="Hoechstens N Kommentare in diesem Lauf (0 = ohne Grenze)."
    ),
    status: bool = typer.Option(False, "--status", help="Nur den Stand zeigen, nichts tun."),
    server: str = typer.Option(
        None,
        "--server",
        help="Auf dem Bestand des Servers arbeiten, z. B. http://127.0.0.1:8090 (SSH-Tunnel).",
    ),
    nur: list[str] = typer.Option(
        None,
        "--kampagne",
        help="Nur diese Kampagne(n) - wirkt nur beim Start eines neuen Laufs.",
    ),
    frisch: bool = typer.Option(
        False,
        "--neu",
        help="Offenen Lauf abschliessen und die Kampagnenliste neu einfrieren.",
    ),
) -> None:
    """Arbeitet ALLE aktiven Kampagnen ab - Kampagne fuer Kampagne, in fester Folge.

    Der eine Startpunkt: Kampagnenliste einfrieren, dann streng der Reihe nach.
    Je Kampagne gilt diese Reihenfolge, und zwar nicht nur in der Anzeige:

    1. **Beitrittsanfragen** an die Gruppen dieser Kampagne, soweit die
       Tagesmenge es zulaesst (``beitritt.anfragen_pro_tag``). Zwischen zwei
       Anfragen wartet der Lauf den Mindestabstand ab, statt Beitraege
       vorzuziehen.
    2. **Neubewertung** ihrer Gruppen - erst danach steht die Rangfolge fest.
    3. **Die besten qualifizierten Gruppen zuerst**: Wo Beitrag *und*
       Kommentare moeglich sind, vor "nur eines von beiden". Wo nichts
       moeglich ist, wird uebersprungen - das ist kein Urteil, die Gruppe wird
       im naechsten Lauf neu beurteilt.
    4. In jeder Gruppe zuerst der eigene **Beitrag**, danach die zehn
       **Kommentare**. Geht der Beitrag nicht, wird er vermerkt und der Lauf
       macht mit den Kommentaren weiter; zurueckgeholt wird er mit
       ``campaign retry``.
    5. Erst wenn die Kampagne durch ist, beginnt die naechste.

    Dabei werden die Regeln der Gruppe eingehalten: kein Link, wo Links
    verboten sind, kein Kommentar, wo Kommentare abgelehnt werden.

    **Ein Fehler bei einer Gruppe beendet den Lauf nicht.** Er wird gemeldet,
    die Gruppe fuer diesen Lauf beiseitegelegt, und es geht mit der naechsten
    weiter - und wenn die Kampagne nichts mehr hergibt, mit der naechsten
    Kampagne.

    Fortgesetzt statt neu begonnen: Ein unterbrochener Lauf wird beim naechsten
    Aufruf dort aufgenommen, wo er stand - der Fortschritt steht in den
    Fassungen selbst und nicht in einem Zaehler, der veralten koennte.
    """
    from fbgroups.marketing import automatik, grenzen, lauf
    from fbgroups.marketing.models import CampaignStatus

    config = _config()

    # --- Genau ein Lauf zur selben Zeit --------------------------------
    #
    # Die Sperre gehoert **dem Lauf**, nicht dem Waechter (15.09.2026). Der
    # Unterschied ist der zwischen "der Waechter startet keinen zweiten" und
    # "es gibt keinen zweiten": Auch ein von Hand gestarteter Lauf haelt sie,
    # und der Waechter sieht ihn. Merkte der Waechter sich nur seine eigenen
    # Kinder, blieben zwei Fenster nebeneinander unsichtbar - und zwei
    # Browser arbeiteten in derselben Gruppe.
    #
    # ``--status`` und ``--dry-run`` nehmen sie nicht: Sie sehen nur nach.
    from fbgroups.marketing import watchdog

    sperre = watchdog.sperre_fuer(config)
    if not (status or dry_run):
        if not sperre.nimm():
            gehalten = sperre.lies() or {}
            console.print(
                f"[yellow]Es laeuft bereits ein Lauf (PID {gehalten.get('pid', '?')}).[/yellow]\n"
                "[dim]Zwei gleichzeitige Laeufe wuerden denselben Kommentar zweimal "
                "absetzen. Warten, oder den anderen beenden.[/dim]"
            )
            raise typer.Exit(code=3)
        # Freigeben, was immer danach passiert - auch bei einem Abbruch mit
        # Strg+C. Eine liegengebliebene Sperre spaerre den naechsten Lauf
        # aus; ``lies`` faengt den Fall zwar ab (tote Kennung), aber erst
        # nach dem naechsten Blick.
        atexit.register(sperre.gib_frei)

    # --- Fernbetrieb: der Server haelt den Stand, dieser Rechner den Browser
    #
    # Der Grund steht in ``automatik.fuehre_lauf_fern_aus``: Ohne ihn bucht
    # die Automatik in die Datei, in der sie laeuft - auf diesem Rechner also
    # in eine Kopie. Der Kommentar geht hinaus, gebucht wird daneben, und der
    # Server bietet dieselbe Gruppe weiter als offen an.
    if server:
        if status or dry_run:
            console.print(
                "[red]--status und --dry-run gelten fuer den oertlichen Bestand.[/red]\n"
                "Den Stand des Servers zeigt: curl "
                f"{server.rstrip('/')}/automatik"
            )
            raise typer.Exit(code=2)

        from fbgroups.automation.browser import get_browser_context

        console.print(f"[cyan]Fernbetrieb: Stand und Buchung auf {server}[/cyan]")
        console.print("[dim]Dieser Rechner steuert nur den Browser.[/dim]")
        if nur:
            console.print(f"[yellow]Eingeschraenkt auf: {', '.join(nur)}[/yellow]")
        if frisch:
            console.print(
                "[yellow]--neu: Ein offener Lauf wird abgeschlossen, die "
                "Kampagnenliste neu eingefroren.[/yellow]"
            )

        with get_browser_context(config, headless=False) as context:

            def fern(
                gruppen_url: str,
                group_id: str,
                text: str,
                bisherige: list[str],
                texttyp: str = "kommentar",
                vorgaben: dict | None = None,
                link_url: str = "",
            ) -> automatik.Schrittergebnis:
                try:
                    if texttyp == "post":
                        # Der Beitrag nimmt den vorbereiteten Text - er ist
                        # bereits aufgeloest. Nur der Kommentar waehlt seinen
                        # Text neu und braucht deshalb die Adresse.
                        return automatik.browser_schritt_post(context, gruppen_url, text)
                    # ``vorgaben`` traegt, was dieser Rechner nicht
                    # nachschlagen kann: die gelesenen Gruppenregeln, die
                    # Schwelle der Gruppenklasse und die schon benutzten
                    # Vorlagen. Ohne sie nahm der Fernbetrieb bis zum
                    # 14.09.2026 den lautesten Beitrag - ohne Inhaltsurteil.
                    return automatik.browser_schritt_fern(
                        context, gruppen_url, group_id, text, bisherige, vorgaben, link_url
                    )
                except Exception as exc:  # noqa: BLE001 - ein Fehlschlag ist ein Ausgang
                    return automatik.Schrittergebnis(
                        erfolg=False, fehler=str(exc).splitlines()[0][:120]
                    )

            def beitritt(gruppen_url: str) -> tuple[str, str]:
                """Schritt 2 des Ablaufs, im sichtbaren Browser dieses Rechners.

                Ein Fehlschlag ist ein **Ausgang**, keine Ausnahme: Der Server
                vermerkt dann nichts (es ist nichts abgeschickt worden) und
                legt die Gruppe fuer diesen Lauf beiseite.
                """
                from fbgroups.automation.actions import request_join

                try:
                    ausgang, bemerkung = request_join(context, gruppen_url)
                except Exception as exc:  # noqa: BLE001 - ein Fehlschlag ist ein Ausgang
                    return "fehler", str(exc).splitlines()[0][:120]
                return str(ausgang), bemerkung

            def regeln(gruppen_url: str) -> str:
                """Schritt 1 des Ablaufs: die Gruppenseite lesen.

                Ein Abruf, keine Handlung in der Gruppe. Scheitert er, kommt
                ein leerer Text zurueck - ``lies_regeln`` macht daraus einen
                **ungelesenen** Befund, und der schreibt nichts. Eine
                Anmeldewand ist kein Beleg dafuer, dass eine Regel weg ist.
                """
                from fbgroups.automation.actions import fetch_group_html

                try:
                    return fetch_group_html(context, gruppen_url)
                except Exception:  # noqa: BLE001 - nicht lesbar ist ein Befund
                    return ""

            meldung = automatik.fuehre_lauf_fern_aus(
                server,
                ausfuehren=fern,
                beitreten=beitritt,
                regeln_lesen=regeln,
                max_schritte=max_schritte,
                nur=list(nur or []),
                frisch=frisch,
            )
        console.print(Panel(meldung, title="Automatik (Server)"))
        return

    if status:
        with MarketingStore(config.path("sqlite_path")) as store:
            offen = store.offener_lauf()
            if offen is None:
                aktive = automatik.aktive_kampagnen(store)
                console.print("[dim]Kein Lauf im Gange.[/dim]")
                console.print(f"Aktive Kampagnen, die ein Start einfrieren wuerde: {len(aktive)}")
                for cid in aktive:
                    console.print(f"  - {cid}")
                return
            with SqliteStore(config.path("sqlite_path")) as gruppen_store:
                gruppen = {g.group_id: g for g in gruppen_store.load_groups()}
            # Mit der Tagesmenge, sonst zeigte die Anzeige nie den Abschnitt
            # "Beitrittsanfragen" - und der ist der erste des Ablaufs.
            lagen = automatik.aktionslage(store, config)
            fortschritt = lauf.lies_fortschritt(
                store,
                int(offen["lauf_id"]),
                gruppen,
                aktionen=lagen,
            )
        console.print(Panel(lauf.fortschrittstext(fortschritt), title="Automatik"))

        # **Welche** Kampagnen der Lauf eingefroren hat - die Zahl allein sagt
        # es nicht. Genau hier faellt auf, dass eine spaeter angelegte
        # Kampagne nicht dabei ist; der Ausweg steht darunter.
        tabelle = Table(title="Eingefrorene Kampagnenliste", box=None)
        tabelle.add_column("Kampagne")
        tabelle.add_column("Abschnitt")
        tabelle.add_column("davon offen", justify="right")
        tabelle.add_column("Gruppen", justify="right")
        for k in fortschritt.kampagnen:
            phase = k.phase(beitritt_frei=fortschritt.beitritt_frei)
            # **Wie viele der Abschnitt noch vor sich hat.** Der Abschnittsname
            # allein beantwortet die haeufigste Frage nicht: "Gruppenregeln
            # lesen" sieht nach Stillstand aus, wenn man nicht weiss, ob noch
            # drei oder dreihundert ausstehen. Beides ist derselbe Schritt,
            # aber das eine dauert eine Minute und das andere eine Stunde.
            offen_im_abschnitt = {
                lauf.Phase.REGELN: len(k.regeln_offen),
                lauf.Phase.BEITRITT: len(k.beitritt_offen),
                lauf.Phase.ARBEIT: sum(1 for g in k.gruppen if g.bearbeitbar),
            }.get(phase)
            tabelle.add_row(
                k.name,
                lauf.PHASENTEXT[phase],
                "-" if offen_im_abschnitt is None else str(offen_im_abschnitt),
                f"{k.gruppen_fertig} / {k.gruppen_gesamt}",
            )
        console.print(tabelle)

        # --- Was jede Aktion gerade darf ---------------------------------
        #
        # Die haeufigste Frage beim Nachsehen ist nicht "wie weit?", sondern
        # "warum passiert nichts?" - und die Antwort steht fast immer hier.
        aktionen = Table(title="Aktionen", box=None)
        aktionen.add_column("Aktion")
        aktionen.add_column("Lage")
        aktionen.add_column("heute frei", justify="right")
        for aktion in grenzen.Aktion:
            lage = fortschritt.lage(aktion)
            aktionen.add_row(
                aktion.value,
                "[green]moeglich[/green]"
                if lage.moeglich
                else f"[yellow]{lage.grund}{' - ' + lage.wartezeit if lage.wartezeit else ''}"
                "[/yellow]",
                str(lage.rest_heute),
            )
        console.print(aktionen)

        # --- Was in diesem Lauf beiseiteliegt -----------------------------
        #
        # Mit Grund, nicht nur als Zahl: "kein Anlass" ist ein Ergebnis,
        # "Browserfenster zu" ein Hinweis auf den Rechner, und die beiden
        # verlangen Verschiedenes.
        uebersprungen = [
            (g.name, g.uebersprungen_grund)
            for k in fortschritt.kampagnen
            for g in k.gruppen
            if g.uebersprungen
        ]
        if uebersprungen:
            liste = Table(
                title=f"In diesem Lauf uebersprungen ({len(uebersprungen)})", box=None
            )
            liste.add_column("Gruppe", max_width=34)
            liste.add_column("Grund", max_width=56)
            for name, grund in uebersprungen[:12]:
                liste.add_row(name, grund or "ohne Angabe")
            console.print(liste)
            if len(uebersprungen) > 12:
                console.print(f"[dim]... und {len(uebersprungen) - 12} weitere[/dim]")
            console.print(
                "[dim]Mit dem naechsten Lauf sind sie wieder dabei - der Vermerk "
                "haengt an dieser lauf_id.[/dim]"
            )

        with MarketingStore(config.path("sqlite_path")) as store:
            aktive = set(automatik.aktive_kampagnen(store))
        fehlen = aktive - {k.campaign_id for k in fortschritt.kampagnen}
        if fehlen:
            console.print("")
            console.print(
                f"[yellow]{len(fehlen)} aktive Kampagne(n) stehen nicht in diesem "
                f"Lauf:[/yellow] {', '.join(sorted(fehlen))}"
            )
            console.print(
                "[dim]Ein Lauf behaelt seine Liste. Mit [bold]--neu[/bold] wird er "
                "abgeschlossen und eine frische eingefroren.[/dim]"
            )
        return

    if dry_run:
        with MarketingStore(config.path("sqlite_path")) as store:
            lauf_id, neu = (
                (int(z["lauf_id"]), False)
                if (z := store.offener_lauf()) is not None
                else (0, True)
            )
            if neu:
                aktive = automatik.aktive_kampagnen(store)
                console.print(f"[cyan]Es entstuende ein neuer Lauf ueber {len(aktive)} "
                              f"Kampagne(n):[/cyan]")
                for cid in aktive:
                    kampagne = store.load_campaign(cid)
                    anzahl = len(store.links_for_campaign(cid))
                    console.print(
                        f"  - {kampagne.name if kampagne else cid}: {anzahl} Gruppen "
                        f"= bis zu {anzahl * lauf.ZIEL_JE_GRUPPE} Kommentare"
                    )
                console.print("\n[dry-run] Es wird nichts abgesetzt.")
                return
            with SqliteStore(config.path("sqlite_path")) as gruppen_store:
                gruppen = {g.group_id: g for g in gruppen_store.load_groups()}
            lagen = automatik.aktionslage(store, config)
            fortschritt = lauf.lies_fortschritt(
                store,
                lauf_id,
                gruppen,
                aktionen=lagen,
            )
            schritt = lauf.naechster_schritt(fortschritt)
        console.print(Panel(lauf.fortschrittstext(fortschritt), title="Automatik (dry-run)"))
        if schritt is not None:
            # Die Art steht vorn: "Als naechstes: Gruppe X" laesst offen, ob
            # dort eine Beitrittsanfrage, ein Beitrag oder ein Kommentar
            # hingeht - und das ist gerade die Frage vor einem Lauf.
            was = {
                lauf.Schrittart.BEITRITT: "Beitrittsanfrage",
                lauf.Schrittart.BEWERTEN: "Neubewertung der Kampagne",
            }.get(
                schritt.art,
                f"{'Beitrag' if schritt.texttyp is Texttyp.POST else 'Kommentar'} "
                f"{schritt.kommentar_nr}/{schritt.kommentar_ziel} "
                f"(Fassung {schritt.nummer})",
            )
            console.print(f"Als naechstes: {schritt.gruppe_name} - {was}")
        console.print("\n[dry-run] Es wird nichts abgesetzt.")
        return

    with MarketingStore(config.path("sqlite_path")) as store:
        if not store.load_campaigns(CampaignStatus.ACTIVE) and store.offener_lauf() is None:
            console.print("[red]Keine aktive Kampagne.[/red] Ein Lauf haette nichts zu tun.")
            raise typer.Exit(code=1)

    from fbgroups.automation.browser import get_browser_context

    # Ein Browser fuer den ganzen Lauf, nicht einer je Kommentar.
    with get_browser_context(config, headless=False) as context:

        def schritt(
            gruppen_url: str,
            group_id: str,
            text: str,
            texttyp: str = "kommentar",
            link_url: str = "",
        ) -> automatik.Schrittergebnis:
            try:
                if texttyp == "post":
                    # Der Beitrag nimmt den vorbereiteten Text - er ist
                    # bereits aufgeloest. Nur der Kommentar waehlt seinen
                    # Text neu und braucht deshalb die Adresse.
                    return automatik.browser_schritt_post(context, gruppen_url, text)
                return automatik.browser_schritt(
                    context, config, gruppen_url, group_id, text, link_url
                )
            except Exception as exc:  # noqa: BLE001 - ein Fehlschlag ist ein Ausgang
                return automatik.Schrittergebnis(
                    erfolg=False, fehler=str(exc).splitlines()[0][:120]
                )

        def beitritt(gruppen_url: str) -> tuple[str, str]:
            """Schritt 2 des Ablaufs - vor allem anderen in dieser Kampagne."""
            from fbgroups.automation.actions import request_join

            try:
                ausgang, bemerkung = request_join(context, gruppen_url)
            except Exception as exc:  # noqa: BLE001 - ein Fehlschlag ist ein Ausgang
                return "fehler", str(exc).splitlines()[0][:120]
            return str(ausgang), bemerkung

        def regeln(gruppen_url: str) -> str:
            """Schritt 1 des Ablaufs: die Gruppenseite lesen - vor der Anfrage."""
            from fbgroups.automation.actions import fetch_group_html

            try:
                return fetch_group_html(context, gruppen_url)
            except Exception:  # noqa: BLE001 - nicht lesbar ist ein Befund
                return ""

        fortschritt = automatik.fuehre_lauf_aus(
            config,
            ausfuehren=schritt,
            beitreten=beitritt,
            regeln_lesen=regeln,
            max_schritte=max_schritte,
            nur=list(nur or []),
            frisch=frisch,
        )

    console.print(Panel(lauf.abschlusstext(fortschritt), title="Automatik"))
    if not fortschritt.fertig:
        raise typer.Exit(code=1)


@campaign_app.command("auto")
def campaign_auto(
    campaign_id: str = typer.Argument(...),
    group_id: str = typer.Argument(
        None, help="Gruppen-ID (optional, andernfalls wird die naechste Gruppe gewaehlt)"
    ),
    typ: str = typer.Option("post", "--typ", help="post | kommentar"),
    nummer: int = typer.Option(1, "--nummer", help="Nummer der Fassung (meist 1)"),
) -> None:
    """Postet oder kommentiert automatisch mit Playwright - mit Tracking-Code."""
    from fbgroups.automation.actions import comment_on_post, fetch_top_posts, post_to_group
    from fbgroups.automation.browser import get_browser_context
    from fbgroups.marketing.arbeit import Ergebnis, Sperre, melde_vorschlag
    from fbgroups.marketing.beitrag import mit_link
    from fbgroups.marketing.models import Texttyp
    from fbgroups.storage.sqlite_store import SqliteStore

    config = _config()

    # 1. READ-Phase: Vorbereitungen treffen (Datenbank ist nur kurz offen)
    with MarketingStore(config.path("sqlite_path")) as store:
        campaign = _kampagne_oder_ende(store, campaign_id)

        # Wenn keine Gruppe angegeben ist, die nächste offene holen
        if not group_id:
            from fbgroups.marketing.arbeit import arbeitsreihenfolge
            from fbgroups.marketing.models import PostStatus

            with SqliteStore(config.path("sqlite_path")) as gruppen_store:
                gruppen = {g.group_id: g for g in gruppen_store.load_groups()}
            reihe = arbeitsreihenfolge(store, campaign_id, gruppen, config)
            offen = [link for link in reihe if link.post_status != PostStatus.VEROEFFENTLICHT]
            if not offen:
                console.print("[green]Alle zugeordneten Gruppen sind abgearbeitet.[/green]")
                raise typer.Exit(0)
            group_id = offen[0].group_id
            console.print(f"[cyan]Automatisch ausgewaehlt: Gruppe {group_id}[/cyan]")

        from fbgroups.marketing.models import QueueZustand

        zustand = store.queue_zustand(campaign_id)
        if zustand is QueueZustand.PAUSIERT:
            console.print("[red]Kampagne ist pausiert.[/red]")
            raise typer.Exit(code=1)
        if zustand is QueueZustand.GESTOPPT:
            console.print("[red]Kampagne ist gestoppt.[/red]")
            raise typer.Exit(code=1)

        from fbgroups.marketing import kaltmodus

        aktiv, pro_tag, abstand = kaltmodus.einstellungen(config)
        if aktiv:
            from datetime import UTC, datetime

            jetzt = datetime.now(UTC)
            heute = store.versuche_heute(jetzt.date().isoformat())
            if heute >= pro_tag:
                console.print(f"[red]Tageslimit von {pro_tag} erreicht (Kaltmodus).[/red]")
                raise typer.Exit(code=1)

            letzter_versuch = store.letzter_versuch()
            letzter_dt = datetime.fromisoformat(letzter_versuch) if letzter_versuch else None
            naechster = kaltmodus.naechster_zeitpunkt(
                letzter_dt, abstand_minuten=abstand, jetzt=jetzt, erledigt_heute=heute
            )
            if naechster:
                wartezeit = kaltmodus.wartezeit_text(naechster, jetzt=jetzt)
                console.print(
                    f"[yellow]Abstandsregel aktiv, {wartezeit} warten (Kaltmodus).[/yellow]"
                )
                raise typer.Exit(code=1)

        try:
            texttyp = Texttyp(typ)
        except ValueError as err:
            console.print(f"[red]Unbekannter Texttyp:[/red] {typ} (Erlaubt: post, kommentar)")
            raise typer.Exit(code=2) from err

        vorschlag = store.vorschlag(campaign_id, group_id, texttyp, nummer)
        if not vorschlag or not vorschlag.text.strip():
            console.print(f"[red]Vorschlag {nummer} als {typ} nicht gefunden oder leer.[/red]")
            raise typer.Exit(code=1)

        link = store.link_for(campaign_id, group_id)
        if not link:
            console.print(f"[red]Gruppe {group_id} ist nicht in der Kampagne {campaign_id}.[/red]")
            raise typer.Exit(code=1)

        from fbgroups.marketing.lauf import ziel_zu_nummer

        text = mit_link(
            campaign, link, vorschlag.text, config=config,
            ziel=ziel_zu_nummer(vorschlag.nummer),
        )

    with SqliteStore(config.path("sqlite_path")) as gruppen_store:
        group = next((g for g in gruppen_store.load_groups() if g.group_id == group_id), None)
        if not group or not group.url_canonical:
            console.print(f"[red]Keine gueltige URL fuer Gruppe {group_id} gefunden.[/red]")
            raise typer.Exit(code=1)

    console.print(f"Starte Automatisierung in {group.url_canonical}...")

    # 2. BROWSER-Phase (Datenbank geschlossen, kann Minuten dauern)
    erfolg = False
    fehler_text = "Element nicht gefunden oder blockiert"
    used_post_url = ""
    try:
        with get_browser_context(config, headless=False) as context:
            if texttyp == Texttyp.POST:
                ausgang = post_to_group(context, group.url_canonical, text)
                erfolg = ausgang.erfolg
                # Der Ausgang sagt mehr als "ging es?": ``link_sichtbar``
                # heisst, dass die nackte Adresse im Beitrag steht, weil die
                # Vorschaukarte ohne sie nicht gehalten hat. Kein Fehlschlag -
                # der Beitrag steht und wird gezaehlt -, aber es gehoert ins
                # Protokoll und nicht in die Stille.
                if ausgang.hinweis:
                    fehler_text = ausgang.hinweis
                    console.print(f"[yellow]{ausgang.hinweis}[/yellow]")
            else:
                console.print("[cyan]Fetching posts for commenting...[/cyan]")
                raw_posts = fetch_top_posts(context, group.url_canonical, group.group_id)
                if raw_posts:
                    from fbgroups.models import GroupPost

                    posts = [
                        GroupPost(
                            group_id=group.group_id,
                            post_url=p["post_url"],
                            interactions=p["interactions"],
                            comments=p["comments"],
                        )
                        for p in raw_posts
                    ]
                    with SqliteStore(config.path("sqlite_path")) as gruppen_store:
                        gruppen_store.upsert_group_posts(group.group_id, posts)

                    with MarketingStore(config.path("sqlite_path")) as store:
                        bisherige = store.bisherige_post_urls(group.group_id)

                    offene_posts = [p for p in posts if p.post_url not in bisherige]
                    if offene_posts:
                        best_post = max(offene_posts, key=lambda p: p.interactions + p.comments)
                        used_post_url = best_post.post_url
                        # ``comment_on_post`` liefert seit dem 12.09.2026
                        # einen Ausgang statt eines ``bool``: Er sagt auch,
                        # ob die Gruppe gerade nichts mehr annimmt oder der
                        # Kommentar auf eine Freigabe wartet.
                        ausgang = comment_on_post(context, used_post_url, text)
                        erfolg = ausgang.erfolg
                        if ausgang.hinweis:
                            fehler_text = ausgang.hinweis[:100]
                    else:
                        fehler_text = "Alle aktuellen Beiträge wurden bereits kommentiert."
                else:
                    fehler_text = "Keine passenden Beiträge zum Kommentieren gefunden."
    except Exception as exc:
        erfolg = False
        fehler_text = str(exc).split("\n")[0][:100]
        console.print(f"[red]Automatisierung fehlgeschlagen: {fehler_text}[/red]")

    # 3. WRITE-Phase: Ergebnis eintragen
    with MarketingStore(config.path("sqlite_path")) as store:
        ergebnis = melde_vorschlag(
            store,
            campaign,
            link,
            texttyp,
            nummer,
            Ergebnis(erfolg=erfolg, fehler="" if erfolg else fehler_text, post_url=used_post_url),
            ausgeloest_von="cli",
            sitzung="auto",
        )

        if isinstance(ergebnis, Sperre):
            if erfolg:
                console.print(f"[yellow]Gepostet, aber DB blockiert: {ergebnis.grund}[/yellow]")
            else:
                console.print(f"[red]Blockiert: {ergebnis.grund}[/red]")
            raise typer.Exit(code=1)

        if erfolg:
            console.print("[green]Erfolgreich veroeffentlicht![/green]")
        else:
            console.print(f"[red]Automatisierung fehlgeschlagen: {ergebnis.fehler}[/red]")
            raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# Beitrags-Warteschlange: erzeugen, freigeben, einreihen, anhalten
# ---------------------------------------------------------------------------


def _job_oder_ende(store: MarketingStore, campaign_id: str, group_id: str) -> CampaignGroup:
    """Die Zuordnung - oder ein Hinweis, wie sie entsteht."""
    link = store.link_for(campaign_id, group_id)
    if link is None:
        console.print(
            f"[red]Keine Zuordnung[/red] fuer {group_id} in {campaign_id}.\n"
            f"Anlegen mit:  fbgroups campaign sync {campaign_id}"
        )
        raise typer.Exit(code=1)
    return link


def _wechsle(
    store: MarketingStore,
    campaign_id: str,
    group_id: str,
    ziel: JobStatus,
    *,
    akteur: str = "",
    fehler: str | None = None,
    erzwingen: bool = False,
) -> CampaignGroup | None:
    """Setzt den Stand und meldet einen unerlaubten Schritt lesbar.

    Die Fehlermeldung der Zustandsmaschine nennt bereits die moeglichen Wege -
    sie wird deshalb durchgereicht und nicht durch eine eigene ersetzt.
    """
    try:
        return store.set_job_status(
            campaign_id, group_id, ziel, akteur=akteur, fehler=fehler, erzwingen=erzwingen
        )
    except UngueltigerUebergang as exc:
        console.print(f"[red]{group_id}:[/red] {exc}")
        return None


@campaign_app.command("text")
def campaign_text(
    campaign_id: str = typer.Argument(...),
    aus_vorlage: bool = typer.Option(
        False, "--aus-vorlage", help="Die Vorlage der Kampagne in jede Zuordnung schreiben."
    ),
    ueberschreiben: bool = typer.Option(
        False, "--ueberschreiben", help="Auch dort, wo schon ein Text steht."
    ),
    typ: str = typer.Option(
        "",
        "--typ",
        help="post | kommentar | beide (Vorgabe: was die Kampagne fuehrt).",
    ),
    ja: bool = typer.Option(False, "--ja", help="Wirklich schreiben (sonst nur zeigen)."),
) -> None:
    """Fuellt die Vorlagen aus ``config/textvorlagen.yaml`` je Gruppe.

    Ohne Text kommt eine Zuordnung nicht durch die Freigabe
    (``pruefe_uebergang`` verlangt einen) und damit nie in die Warteschlange.
    Dieser Befehl schreibt den Text fuer viele Gruppen auf einmal; die eine
    Gruppe, bei der die Vorlage nicht passt, bekommt ihren Text auf der
    Arbeitsseite.

    Die Vorlage bleibt dabei **je Gruppe** eine eigene Kopie. Das ist kein
    Umweg: ``{link}`` wird erst beim Absetzen durch den Code *dieser* Gruppe
    ersetzt, und wer einen einzelnen Text nachtraeglich anpasst, soll damit
    nicht alle anderen aendern.

    Vorhandene Texte bleiben stehen, sofern nicht ``--ueberschreiben``. Ein von
    Hand ueberarbeiteter oder freigegebener Text ist Arbeit eines Menschen; ein
    Sammelbefehl macht sie nicht beilaeufig zunichte.

    Ohne ``--typ`` entstehen **beide** Textarten; ``--typ`` schraenkt den Lauf
    auf eine ein. Beide haben eigene Vorlagen und eigene Felder - ein Beitrag
    wird nie als Kommentar wiederverwendet.
    """
    if not aus_vorlage:
        console.print(
            "Nichts zu tun. Der Text kommt aus der Vorlage "
            "([bold]--aus-vorlage[/bold]) - oder, fuer eine einzelne Gruppe, "
            "aus dem Textfeld auf der Arbeitsseite."
        )
        raise typer.Exit(code=2)

    config = _config()
    with MarketingStore(config.path("sqlite_path")) as store:
        campaign = _kampagne_oder_ende(store, campaign_id)

        # Eine eigene Vorlage der Kampagne ist optional geworden: Ohne sie
        # kommt der Text aus config/textvorlagen.yaml, und das ist seither der
        # Normalfall. Gibt es sie aber, muss sie den Platzhalter tragen - sonst
        # ginge der Beitrag ohne Tracking-Link hinaus und keine Gruppe bekaeme
        # je einen Klick gutgeschrieben.
        eigene = campaign.message_template.strip()
        if eigene and vorlagen.PLATZHALTER_LINK not in eigene:
            console.print(
                f"[yellow]Die eigene Vorlage der Kampagne enthaelt kein "
                f"{vorlagen.PLATZHALTER_LINK}.[/yellow] Der Beitrag ginge dann ohne "
                "Tracking-Link hinaus."
            )
            raise typer.Exit(code=2)

        beanstandungen = vorlagen.pruefe(config)
        if not eigene and beanstandungen:
            console.print("[red]config/textvorlagen.yaml stimmt nicht:[/red]")
            for beanstandung in beanstandungen[:5]:
                console.print(f"  - {beanstandung}")
            raise typer.Exit(code=2)

        gewuenscht = (typ or "").strip().lower()
        if gewuenscht == "beide":
            zwecke = list(Texttyp)
        elif gewuenscht:
            try:
                zwecke = [Texttyp(gewuenscht)]
            except ValueError:
                console.print(f"[red]Unbekannter Texttyp:[/red] {typ}")
                raise typer.Exit(code=2) from None
        else:
            zwecke = list(Texttyp)

        links = store.links_for_campaign(campaign_id)

        # ``job_status`` beschreibt den **Beitrag** - Freigabe, Warteschlange,
        # Versuche. Er sperrte hier bis zum 31.08.2026 auch die Erzeugung der
        # Kommentare, und das widerspricht der Aufteilung in CLAUDE.md: "Der
        # Beitrag traegt den Ablauf, der Kommentar wird kopiert; der Kommentar
        # hat davon nichts."
        #
        # Aufgefallen ist es, als die Kommentare von fuenf auf zehn Fassungen
        # gingen: Eine Gruppe mit veroeffentlichtem **Beitrag** bekam die
        # Fassungen 6 bis 10 nicht mehr - obwohl es sie dort noch gar nicht
        # gab und kein Kommentar davon beruehrt gewesen waere.
        beitrag_gesperrt = (JobStatus.PUBLISHED, JobStatus.PROCESSING)

        # Gefragt wird nach der Sammelspalte, denn genau die schreibt dieser
        # Befehl (``set_generierten_text``). Die **nummerierten** Fassungen
        # entstehen an anderer Stelle - in ``arbeit.stelle_texte_bereit``,
        # also beim Oeffnen der Arbeitsseite ("Arbeiten bereitet selbst vor").
        # Dort werden aus fuenf Vorlagen zehn Kommentarfassungen.
        def offen_fuer(link, texttyp: Texttyp) -> bool:
            if texttyp is Texttyp.POST and link.job_status in beitrag_gesperrt:
                return False
            return ueberschreiben or not link.text_fuer(texttyp).strip()

        betroffen = [
            link for link in links if any(offen_fuer(link, texttyp) for texttyp in zwecke)
        ]

        # Die Gruppen werden gebraucht, weil der Text je Gruppe ein anderer
        # ist: Stadt und Zielgruppe gehen hinein, und welche der fuenf
        # Fassungen es wird, entscheidet die Gruppenkennung.
        with SqliteStore(config.path("sqlite_path")) as gruppen_store:
            gruppen = _gruppen_nach_id(gruppen_store)

        # Vorschau an einer wirklich betroffenen Gruppe. Die Vorlage roh zu
        # zeigen hiesse, etwas anderes zu zeigen als das, was geschrieben wird.
        beispiel = next((link for link in betroffen if link.group_id in gruppen), None)
        if beispiel is not None:
            for texttyp in zwecke:
                schluessel, beispieltext = vorlagen.text_fuer_gruppe(
                    gruppen[beispiel.group_id],
                    campaign,
                    config,
                    schluessel=beispiel.vorlage_fuer(texttyp),
                    texttyp=texttyp,
                )
                titel = gruppen[beispiel.group_id].name or beispiel.group_id
                console.print(Panel(beispieltext, title=f"Beispiel: {titel}  [{schluessel}]"))

        console.print(
            f"{len(betroffen)} von {len(links)} Zuordnungen bekaemen einen Text "
            f"({', '.join(t.value for t in zwecke)})."
        )
        uebersprungen = len(links) - len(betroffen)
        if uebersprungen:
            console.print(
                f"[dim]{uebersprungen} uebersprungen "
                "(schon ein Text vorhanden, veroeffentlicht oder gerade in Arbeit).[/dim]"
            )

        if not ja:
            console.print(
                f"\n[yellow]Nichts geaendert.[/yellow] Wirklich schreiben:  "
                f"fbgroups campaign text {campaign_id} --aus-vorlage"
                f"{' --ueberschreiben' if ueberschreiben else ''} --ja"
            )
            return

        # Gruppen ohne Datensatz werden uebersprungen statt mit einem Text
        # versehen, der ihre Stadt und Zielgruppe raten muesste.
        geschrieben = [link for link in betroffen if link.group_id in gruppen]
        gezaehlt = dict.fromkeys((t.value for t in zwecke), 0)
        for link in geschrieben:
            for texttyp in zwecke:
                # Ein vorhandener Text bleibt stehen, sofern nicht
                # ``--ueberschreiben``: Handarbeit ueberlebt einen
                # Sammelbefehl. Die Gruppe steht trotzdem in der Liste, weil
                # der andere Zweck vielleicht noch fehlt.
                if not ueberschreiben and link.text_fuer(texttyp).strip():
                    continue
                schluessel, text = vorlagen.text_fuer_gruppe(
                    gruppen[link.group_id],
                    campaign,
                    config,
                    schluessel=link.vorlage_fuer(texttyp),
                    texttyp=texttyp,
                )
                store.set_generierten_text(
                    campaign_id,
                    link.group_id,
                    text=text,
                    vorlage_key=schluessel,
                    uebernehmen=True,
                    texttyp=texttyp,
                )
                gezaehlt[texttyp.value] += 1

        # Und die **nummerierten** Fassungen dazu.
        #
        # Bis zum 31.08.2026 tat dieser Befehl nur das Obige - er fuellte die
        # Sammelspalte. Die zehn Kommentarfassungen entstanden allein beim
        # Oeffnen der Arbeitsseite, und auch das nur bei leerer Warteschlange.
        # Die Folge war ein Bestand mit ungleichen Zahlen: vier Paare mit
        # einer Fassung, sechs mit fuenf, eines mit zehn, fuenfundzwanzig
        # ohne jede. Wer "Texte erzeugen" aufruft, meint aber die Texte, mit
        # denen gearbeitet wird.
        #
        # ``stelle_texte_bereit`` ueberschreibt nichts Vorhandenes; der Aufruf
        # ist damit wiederholbar und fuellt allein die Luecken.
        from fbgroups.marketing.arbeit import stelle_texte_bereit

        # Ueber **alle** Zuordnungen, nicht nur ueber die eben geschriebenen.
        #
        # ``betroffen`` filtert an der Sammelspalte: Wo dort schon ein Text
        # steht, gilt die Gruppe als erledigt. Fuer die nummerierten Fassungen
        # trifft das nicht zu - ein Paar mit gefuellter Sammelspalte hatte am
        # 31.08.2026 trotzdem nur fuenf statt zehn Kommentarfassungen, weil
        # jene aus der Zeit vor der Umstellung stammten.
        ergaenzt = 0
        for link in links:
            gruppe = gruppen.get(link.group_id)
            if gruppe is None:
                continue
            bericht = stelle_texte_bereit(
                store, campaign, gruppe, config, ueberschreiben=ueberschreiben
            )
            ergaenzt += sum(bericht.values())
        if ergaenzt:
            console.print(f"[dim]{ergaenzt} nummerierte Fassung(en) ergaenzt.[/dim]")

        fehlend = len(betroffen) - len(geschrieben)
        betroffen = geschrieben

    if fehlend:
        console.print(f"[dim]{fehlend} ohne Datensatz im Bestand uebersprungen.[/dim]")
    console.print(
        "\n[green]"
        + ", ".join(f"{anzahl} {name}" for name, anzahl in gezaehlt.items())
        + "[/green] eingetragen."
    )
    console.print(
        f"[dim]Weiter:  fbgroups campaign approve {campaign_id} alle"
        f"  →  fbgroups campaign enqueue {campaign_id}[/dim]"
    )


# Staende, aus denen ein Beitrag zur Pruefung vorgelegt werden kann.
# ``draft`` gehoert dazu: Ein von Hand geschriebener Text ist genauso
# freizugeben wie einer von Claude - sonst waere Handarbeit der einzige Weg,
# der in der Warteschlange nicht ankommt.
_VOR_DER_PRUEFUNG: frozenset[JobStatus] = frozenset({JobStatus.DRAFT, JobStatus.AI_GENERATED})


@campaign_app.command("approve")
def campaign_approve(
    campaign_id: str = typer.Argument(...),
    group_id: str = typer.Argument(..., help="Kennung der Gruppe, oder 'alle'."),
    akteur: str = typer.Option("", "--von", help="Wer freigibt - fuers Protokoll."),
) -> None:
    """Gibt einen Beitrag frei. Ohne Freigabe geht nichts in die Warteschlange.

    ``alle`` gibt jede Gruppe frei, die auf Pruefung wartet. Das ist bewusst
    ein eigenes Wort und kein Schalter: Eine Sammelfreigabe ist eine
    Entscheidung ueber viele Beitraege auf einmal, und sie soll sich beim
    Tippen anfuehlen wie eine.
    """
    config = _config()
    with MarketingStore(config.path("sqlite_path")) as store:
        _kampagne_oder_ende(store, campaign_id)

        if group_id == "alle":
            offen = [
                *store.jobs_mit_status(campaign_id, JobStatus.PENDING_REVIEW),
                *(
                    link
                    for stand in _VOR_DER_PRUEFUNG
                    for link in store.jobs_mit_status(campaign_id, stand)
                    # Ein Entwurf ohne Text ist kein Beitrag. Er wuerde beim
                    # Uebergang ohnehin abgewiesen - ihn hier zu uebergehen
                    # spart eine Fehlermeldung je Gruppe bei 310 Zeilen.
                    if link.post_text.strip()
                ),
            ]
            if not offen:
                console.print("Nichts wartet auf Freigabe.")
                return
            fertig = 0
            for link in offen:
                if link.job_status in _VOR_DER_PRUEFUNG:
                    _wechsle(store, campaign_id, link.group_id, JobStatus.PENDING_REVIEW)
                if _wechsle(store, campaign_id, link.group_id, JobStatus.APPROVED, akteur=akteur):
                    fertig += 1
            console.print(f"[green]{fertig}[/green] freigegeben.")
            return

        link = _job_oder_ende(store, campaign_id, group_id)
        if link.job_status in _VOR_DER_PRUEFUNG:
            _wechsle(store, campaign_id, group_id, JobStatus.PENDING_REVIEW)
        if _wechsle(store, campaign_id, group_id, JobStatus.APPROVED, akteur=akteur):
            console.print("[green]Freigegeben.[/green]")
            console.print(f"[dim]Einreihen:  fbgroups campaign enqueue {campaign_id}[/dim]")


@campaign_app.command("enqueue")
def campaign_enqueue(
    campaign_id: str = typer.Argument(...),
    top: int = typer.Option(0, "--top", help="Hoechstens so viele (0 = alle freigegebenen)."),
) -> None:
    """Stellt freigegebene Beitraege in die Warteschlange - die besten zuerst.

    Sortiert wird **hier** und nicht beim Abarbeiten: Sonst entschiede eine
    Neubewertung mitten im Lauf, welcher Beitrag als naechstes hinausgeht, und
    die Reihenfolge waere von Tag zu Tag eine andere.
    """
    config = _config()
    gruppen_store, store = _stores(config)
    try:
        _kampagne_oder_ende(store, campaign_id)
        gruppen = _gruppen_nach_id(gruppen_store)
        freigegeben = store.jobs_mit_status(campaign_id, JobStatus.APPROVED)
    finally:
        gruppen_store.close()

    try:
        if not freigegeben:
            console.print(
                "Nichts freigegeben.\n"
                f"[dim]Freigeben:  fbgroups campaign approve {campaign_id} alle[/dim]"
            )
            return

        geordnet = nach_prioritaet(
            [gruppen[link.group_id] for link in freigegeben if link.group_id in gruppen], config
        )
        reihenfolge = {g.group_id: i for i, g in enumerate(geordnet)}
        freigegeben.sort(key=lambda link: reihenfolge.get(link.group_id, len(reihenfolge)))
        if top:
            freigegeben = freigegeben[:top]

        eingereiht = 0
        for link in freigegeben:
            if _wechsle(store, campaign_id, link.group_id, JobStatus.QUEUED):
                eingereiht += 1

        zustand = store.queue_zustand(campaign_id)
    finally:
        store.close()

    console.print(f"[green]{eingereiht}[/green] in der Warteschlange.")
    if zustand is not QueueZustand.LAUFEND:
        console.print(
            f"[yellow]Achtung:[/yellow] Die Warteschlange ist {zustand.value}. "
            f"Weiter mit:  fbgroups campaign resume {campaign_id}"
        )


@campaign_app.command("cancel")
def campaign_cancel(
    campaign_id: str = typer.Argument(...),
    group_id: str = typer.Argument(...),
    grund: str = typer.Option("", "--grund", help="Warum - fuers Protokoll."),
) -> None:
    """Bricht den Beitrag einer Gruppe ab.

    Der Tracking-Code bleibt gueltig: Er steht moeglicherweise schon in einem
    veroeffentlichten Beitrag, und ein Klick darauf muss ankommen und gezaehlt
    werden. Abbrechen ist eine Entscheidung ueber die eigene Arbeit, kein
    Widerruf des Codes.
    """
    config = _config()
    with MarketingStore(config.path("sqlite_path")) as store:
        _kampagne_oder_ende(store, campaign_id)
        _job_oder_ende(store, campaign_id, group_id)
        if _wechsle(store, campaign_id, group_id, JobStatus.CANCELLED, fehler=grund):
            console.print("[green]Abgebrochen.[/green] Der Tracking-Code bleibt gueltig.")


def _queue_umschalten(campaign_id: str, zustand: QueueZustand) -> None:
    """Gemeinsamer Rumpf von pause/resume/stop - eine Regel, eine Stelle."""
    config = _config()
    with MarketingStore(config.path("sqlite_path")) as store:
        _kampagne_oder_ende(store, campaign_id)
        zurueck = store.set_queue_zustand(campaign_id, zustand)
        store.audit("queue_zustand", campaign_id, zustand.value)
        zaehler = store.job_counts(campaign_id)

    console.print(f"Warteschlange {campaign_id}: [bold]{zustand.value}[/bold]")
    if zurueck:
        console.print(
            f"[yellow]{zurueck}[/yellow] eingereihte Beitraege sind auf 'approved' "
            f"zurueckgegangen - die Freigabe bleibt erhalten."
        )
    if zaehler.get(JobStatus.PROCESSING.value):
        console.print(
            f"[dim]{zaehler[JobStatus.PROCESSING.value]} Beitrag/Beitraege sind gerade "
            f"in Arbeit und laufen zu Ende - sie werden nicht abgebrochen.[/dim]"
        )


@campaign_app.command("pause")
def campaign_pause(campaign_id: str = typer.Argument(...)) -> None:
    """Haelt die Warteschlange an. Was eingereiht ist, bleibt eingereiht."""
    _queue_umschalten(campaign_id, QueueZustand.PAUSIERT)


@campaign_app.command("resume")
def campaign_resume(campaign_id: str = typer.Argument(...)) -> None:
    """Laesst die Warteschlange weiterlaufen."""
    _queue_umschalten(campaign_id, QueueZustand.LAUFEND)


@campaign_app.command("stop")
def campaign_stop(campaign_id: str = typer.Argument(...)) -> None:
    """Haelt an und raeumt die Warteschlange.

    Unterschied zu ``pause``: Was noch nicht angefangen wurde, geht auf
    ``approved`` zurueck. Das ist der Unterschied zwischen "kurz warten" und
    "heute nicht mehr" - und er soll im Zustand stehen, nicht im Kopf des
    Bedienenden.
    """
    _queue_umschalten(campaign_id, QueueZustand.GESTOPPT)


@campaign_app.command("jobs")
def campaign_jobs(
    campaign_id: str = typer.Argument(...),
    status: str = typer.Option("", "--status", help="Nur dieser Stand."),
    limit: int = typer.Option(30, "--limit", help="Hoechstens so viele Zeilen."),
) -> None:
    """Der Stand aller Beitraege einer Kampagne - Zaehler und Liste."""
    config = _config()
    gruppen_store, store = _stores(config)
    try:
        _kampagne_oder_ende(store, campaign_id)
        gruppen = _gruppen_nach_id(gruppen_store)
        zaehler = store.job_counts(campaign_id)
        zustand = store.queue_zustand(campaign_id)
        gewaehlt: JobStatus | None = None
        if status:
            try:
                gewaehlt = JobStatus(status)
            except ValueError:
                moeglich = ", ".join(j.value for j in JobStatus)
                console.print(f"[red]Unbekannter Stand:[/red] {status}\nMoeglich: {moeglich}")
                raise typer.Exit(code=1) from None
        links = store.jobs_mit_status(campaign_id, gewaehlt)
    finally:
        gruppen_store.close()
        store.close()

    kopf = Table(
        title=f"{campaign_id} - Warteschlange {zustand.value}", show_header=False, box=None
    )
    for stand in JobStatus:
        anzahl = zaehler.get(stand.value, 0)
        if anzahl or stand in (JobStatus.DRAFT, JobStatus.APPROVED, JobStatus.QUEUED):
            kopf.add_row(stand.value, str(anzahl))
    console.print(kopf)

    if not links:
        return

    geordnet = nach_prioritaet(
        [gruppen[link.group_id] for link in links if link.group_id in gruppen], config
    )
    reihenfolge = {g.group_id: i for i, g in enumerate(geordnet)}
    links.sort(key=lambda link: reihenfolge.get(link.group_id, len(reihenfolge)))

    table = Table(title="Beitraege")
    table.add_column("Gruppe")
    table.add_column("Score", justify="right")
    table.add_column("Prio")
    table.add_column("Stand")
    table.add_column("Text")
    table.add_column("Letzter Versuch")
    for link in links[:limit]:
        g = gruppen.get(link.group_id)
        table.add_row(
            (g.name if g else link.group_id)[:32],
            _score_text(g),
            prioritaet(g, config) if g else "[dim]-[/dim]",
            link.job_status.value,
            (link.post_text[:34] + "...") if link.post_text else "[dim]-[/dim]",
            link.last_attempt_at.strftime("%d.%m. %H:%M")
            if link.last_attempt_at
            else "[dim]-[/dim]",
        )
    console.print(table)
    if len(links) > limit:
        console.print(f"[dim]... und {len(links) - limit} weitere.[/dim]")


def _score_text(group: Group | None) -> str:
    """Score als "130/175" - nie als blosse Zahl.

    Der Score ist nicht auf 100 normiert; ohne den Nenner ist "130" nicht
    einzuordnen und "100" sieht besser aus, als es ist.
    """
    if group is None or group.score is None:
        return "[dim]-[/dim]"
    return f"{group.score:.0f}/{group.score_max:.0f}" if group.score_max else f"{group.score:.0f}"


# Staende, in denen ein Beitrag ueberhaupt moeglich ist: Bei Facebook muss man
# aufgenommen sein, bevor man posten kann.
_POSTEN_MOEGLICH: frozenset[MarketingStatus] = frozenset(
    {
        MarketingStatus.MEMBER,
        MarketingStatus.CONTACTED,
        MarketingStatus.INTERESTED,
        MarketingStatus.APPROVED,
        MarketingStatus.ACTIVE,
    }
)


@campaign_app.command("next")
def campaign_next(
    campaign_id: str = typer.Argument(...),
    limit: int = typer.Option(0, "--limit", help="Hoechstens so viele Gruppen (0 = alle)."),
    nur_mitglied: bool = typer.Option(
        False,
        "--nur-mitglied",
        help="Nur Gruppen, in denen wir laut Arbeitsstand aufgenommen sind.",
    ),
    kein_browser: bool = typer.Option(False, "--kein-browser", help="Nichts oeffnen."),
    keine_zwischenablage: bool = typer.Option(
        False, "--keine-zwischenablage", help="Nichts kopieren."
    ),
) -> None:
    """Arbeitet die Liste Gruppe fuer Gruppe ab - Text bereit, Gruppe offen.

    Je Gruppe: der fertige Text mit **ihrem** Tracking-Link liegt in der
    Zwischenablage, die Gruppe steht im Browser. Einfuegen, absenden, eine
    Taste druecken - der Ausgang wird sofort protokolliert und die naechste
    Gruppe kommt. Das Programm sieht Facebook dabei nie; es weiss nur, was
    der Mensch ihm sagt.

    Der Tracking-Code steht nirgends im Code: Er kommt fuer jede Gruppe aus
    ihrer Zuordnung. Ob 3 Gruppen oder 300 - der Ablauf ist derselbe.

    ``uebersprungen`` ist ein eigener Ausgang und kein Fehlschlag: "passt
    nicht" ist ein Urteil, und ``campaign retry`` holt es spaeter nicht
    versehentlich zurueck.
    """
    config = _config()
    gruppen_store, store = _stores(config)
    try:
        campaign = _kampagne_oder_ende(store, campaign_id)
        links = store.offene_links(campaign_id)
        gruppen = _gruppen_nach_id(gruppen_store)
        staende = store.load_all_marketing()
    finally:
        gruppen_store.close()

    try:
        if not campaign.message_template:
            console.print(
                "[red]Diese Kampagne hat keine Textvorlage.[/red]\n"
                f"Setzen mit:  fbgroups campaign set {campaign_id} --vorlage ..."
            )
            raise typer.Exit(code=2)

        if nur_mitglied:
            links = [
                link
                for link in links
                if (
                    staende.get(link.group_id) or GroupMarketing(group_id=link.group_id)
                ).marketing_status
                in _POSTEN_MOEGLICH
            ]

        if not links:
            console.print("[green]Nichts offen.[/green]")
            return

        links = _nach_rang(links, gruppen)
        if limit > 0:
            links = links[:limit]

        gesamt = len(links)
        for nummer, link in enumerate(links, start=1):
            group = gruppen.get(link.group_id)
            name = (group.name if group else "") or link.group_id
            text = beitragstext(campaign, link, config=config)

            kopiert = False if keine_zwischenablage else in_zwischenablage(text)
            geoeffnet = (
                False if kein_browser or group is None else oeffne_im_browser(group.url_canonical)
            )

            hinweise = [link.tracking_code]
            if kopiert:
                hinweise.append("Text kopiert")
            if geoeffnet:
                hinweise.append("Gruppe geoeffnet")

            console.print()
            console.print(
                Panel(text, title=f"[{nummer}/{gesamt}]  {name}", subtitle=" · ".join(hinweise))
            )
            # Ohne Browser (oder wenn er nicht aufging) bleibt die URL das
            # Einzige, womit man weiterkommt.
            if group is not None and not geoeffnet:
                console.print(f"[dim]{group.url_canonical}[/dim]")

            antwort = (
                console.input(
                    "[bold][Enter][/bold] veroeffentlicht · [bold]f[/bold] Fehler · "
                    "[bold]u[/bold] ueberspringen · [bold]q[/bold] Schluss  > "
                )
                .strip()
                .lower()
            )

            if antwort == "q":
                # nummer - 1: Diese Gruppe wurde gerade nicht bearbeitet.
                console.print(f"[dim]Abgebrochen nach {nummer - 1} von {gesamt}.[/dim]")
                break
            if antwort == "u":
                store.set_post_status(campaign_id, link.group_id, PostStatus.UEBERSPRUNGEN)
                console.print("[dim]uebersprungen[/dim]")
            elif antwort == "f":
                grund = console.input("Grund: ").strip()
                store.set_post_status(campaign_id, link.group_id, PostStatus.FEHLGESCHLAGEN, grund)
                console.print(f"[red]fehlgeschlagen[/red] · {grund}")
            else:
                store.set_post_status(campaign_id, link.group_id, PostStatus.VEROEFFENTLICHT)
                console.print(f"[green]veroeffentlicht[/green] · {link.tracking_code}")

        console.print()
        console.print(_fortschritt_zeile(store.post_counts(campaign_id)))
    finally:
        store.close()


@campaign_app.command("reset")
def campaign_reset(
    campaign_id: str = typer.Argument(...),
    auch_ereignisse: bool = typer.Option(
        False,
        "--auch-ereignisse",
        help="Zusaetzlich Klicks, Registrierungen und Downloads dieser Kampagne loeschen.",
    ),
    ja: bool = typer.Option(False, "--ja", help="Wirklich ausfuehren (sonst nur zeigen)."),
) -> None:
    """Setzt den Beitragsstand einer Kampagne auf Anfang - fuer Testlaeufe.

    **Tracking-Codes bleiben unangetastet.** Ein vergebener Code steht
    moeglicherweise in einem veroeffentlichten Beitrag; ein Klick darauf muss
    weiterhin ankommen und gezaehlt werden. Zurueckgesetzt wird der *Stand*,
    nie die Zuordnung - und ebenso wenig die Gruppen oder der Kooperationsstand.

    Ohne ``--ja`` wird nur gezeigt, was geschaehe. Das ist kein Zierrat:
    ``--auch-ereignisse`` loescht gemessene Resonanz, und die ist das einzige
    in dieser Datenbank, was sich nicht wiederherstellen laesst. Sie ist von
    aussen entstanden und kommt nicht noch einmal.
    """
    config = _config()
    with MarketingStore(config.path("sqlite_path")) as store:
        _kampagne_oder_ende(store, campaign_id)
        zahlen = store.zaehle_zuruecksetzbar(campaign_id)

        tabelle = Table(box=None, show_header=False)
        tabelle.add_column(style="dim")
        tabelle.add_column(justify="right")
        tabelle.add_row("Zuordnungen zurueckgesetzt", str(zahlen["zuordnungen"]))
        tabelle.add_row("davon veroeffentlicht", str(zahlen["veroeffentlicht"]))
        tabelle.add_row("Versuchsprotokoll geloescht", str(zahlen["versuche"]))
        tabelle.add_row(
            "Ereignisse geloescht",
            str(zahlen["ereignisse"]) if auch_ereignisse else "0 (bleiben erhalten)",
        )
        console.print(tabelle)
        console.print()
        console.print(
            "[dim]Unangetastet: Tracking-Codes und -URLs, die Gruppen selbst, "
            "der Kooperationsstand (Mitglied/kontaktiert) und die Entwuerfe.[/dim]"
        )

        if not ja:
            console.print(
                f"\n[yellow]Nichts geaendert.[/yellow] Wirklich ausfuehren:  "
                f"fbgroups campaign reset {campaign_id}"
                f"{' --auch-ereignisse' if auch_ereignisse else ''} --ja"
            )
            return

        if auch_ereignisse and zahlen["ereignisse"]:
            console.print(
                f"\n[red]{zahlen['ereignisse']} gemessene Ereignisse werden geloescht.[/red] "
                "Sie sind von aussen entstanden und kommen nicht wieder."
            )

        getan = store.setze_kampagne_zurueck(campaign_id, auch_ereignisse=auch_ereignisse)

    console.print(
        f"\n[green]Zurueckgesetzt.[/green] {getan['zuordnungen']} Zuordnungen stehen wieder "
        "am Anfang, die Warteschlange laeuft."
    )
    console.print(f"[dim]Weiter mit:  fbgroups campaign enqueue {campaign_id}[/dim]")


# ---------------------------------------------------------------------------
# Arbeitsstand je Gruppe
# ---------------------------------------------------------------------------


@marketing_app.command("set")
def marketing_set(
    group_id: str = typer.Argument(...),
    status: str = typer.Option(None, "--status", help=" | ".join(m.value for m in MarketingStatus)),
    contact: str = typer.Option(None, "--kontakt", help=" | ".join(c.value for c in ContactStatus)),
    permission: str = typer.Option(
        None, "--erlaubnis", help=" | ".join(p.value for p in PermissionStatus)
    ),
    participation: str = typer.Option(
        None, "--kampagne", help=" | ".join(c.value for c in CampaignParticipation)
    ),
    note: str = typer.Option(None, "--notiz"),
    contacted_now: bool = typer.Option(False, "--kontaktiert-jetzt"),
    posted_now: bool = typer.Option(False, "--gepostet-jetzt"),
    bearbeiten: bool = typer.Option(
        None,
        "--bearbeiten/--ausschliessen",
        help="Ob die Gruppe in der Arbeitsliste steht. Der Tracking-Code bleibt gueltig.",
    ),
    grund: str = typer.Option(None, "--grund", help="Begruendung des Ausschlusses."),
) -> None:
    """Pflegt den Arbeitsstand einer Gruppe. Nicht genannte Felder bleiben.

    ``--ausschliessen`` nimmt die Gruppe aus der Arbeitsliste, ohne den
    Kooperationsweg anzutasten: Wer in der Gruppe bereits Mitglied ist, bleibt
    es auch im Datensatz. Und der Tracking-Code bleibt unberuehrt gueltig - er
    steht moeglicherweise schon in einem veroeffentlichten Beitrag.
    """
    config = _config()
    gruppen_store, store = _stores(config)
    try:
        vorhanden = {g.group_id for g in gruppen_store.load_groups()}
        if group_id not in vorhanden:
            console.print(f"[red]Unbekannte Gruppe:[/red] {group_id}")
            raise typer.Exit(code=1)

        eintrag = store.load_marketing(group_id)
        if status:
            eintrag.marketing_status = MarketingStatus(status)
        if contact:
            eintrag.contact_status = ContactStatus(contact)
        if permission:
            eintrag.permission_status = PermissionStatus(permission)
        if participation:
            eintrag.campaign_status = CampaignParticipation(participation)
        if note is not None:
            eintrag.notes = note
        if contacted_now:
            eintrag.last_contacted_at = datetime.now(UTC)
        if posted_now:
            eintrag.last_posted_at = datetime.now(UTC)
        if bearbeiten is not None:
            eintrag.bearbeiten = bearbeiten
            # Der Grund gehoert zum Ausschluss und faellt bei der
            # Wiederaufnahme weg - sonst stuende bei einer bearbeiteten Gruppe
            # eine Begruendung, warum sie nicht bearbeitet wird.
            eintrag.ausschlussgrund = "" if bearbeiten else (grund or "")
        eintrag.updated_at = datetime.now(UTC)

        store.save_marketing(eintrag)
    finally:
        gruppen_store.close()
        store.close()

    arbeit = (
        "in Arbeit"
        if eintrag.bearbeiten
        else (
            f"ausgeschlossen ({eintrag.ausschlussgrund})"
            if eintrag.ausschlussgrund
            else "ausgeschlossen"
        )
    )
    console.print(
        f"[green]{group_id}:[/green] {eintrag.marketing_status.value} / "
        f"Kontakt {eintrag.contact_status.value} / Erlaubnis {eintrag.permission_status.value} / "
        f"{arbeit}"
    )


def _ist_schon_weiter(stand: MarketingStatus, ziel: MarketingStatus) -> bool:
    """Hat die Gruppe den Zielzustand bereits erreicht oder ueberholt?

    Zustaende ausserhalb der Ablaufreihenfolge (abgelehnt, beendet) gelten als
    "weiter": Sie sind ein Ergebnis, das ein Sammelbefehl nicht ueberschreiben
    darf. Wer es doch will, setzt den Stand einzeln mit ``marketing set``.
    """
    if stand not in MARKETING_FORTSCHRITT:
        return True
    return MARKETING_FORTSCHRITT.index(stand) >= MARKETING_FORTSCHRITT.index(ziel)


@marketing_app.command("beitritt")
def marketing_beitritt(
    group_ids: list[str] = typer.Argument(
        None, help="Gruppen-Kennungen. Ohne Angabe zaehlt --kampagne oder --top."
    ),
    campaign_id: str = typer.Option(None, "--kampagne", help="Alle Gruppen dieser Kampagne."),
    top: int = typer.Option(0, "--top", help="Die besten N Gruppen des Bestands."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Nur zeigen, nichts speichern."),
) -> None:
    """Haelt fest, dass du fuer diese Gruppen eine Beitrittsanfrage gestellt hast.

    Der Befehl stellt die Anfrage **nicht** - facebook.com wird nie aufgerufen.
    Du schickst sie im Browser; hier wird nur mitgeschrieben, fuer welche
    Gruppen und wann. Genau dafuer gibt es die Linkliste:
    ``fbgroups campaign links <kampagne> --export``.

    Gruppen, die bereits weiter sind, bleiben unangetastet - ein Sammelbefehl
    darf einen erreichten Stand nicht zurueckdrehen.
    """
    config = _config()
    gruppen_store, store = _stores(config)

    try:
        if campaign_id:
            _kampagne_oder_ende(store, campaign_id)
            auswahl = [link.group_id for link in store.links_for_campaign(campaign_id)]
        elif top > 0:
            auswahl = [g.group_id for g in sort_by_rank(gruppen_store.load_groups())[:top]]
        elif group_ids:
            auswahl = list(group_ids)
        else:
            console.print(
                "[red]Keine Auswahl.[/red] Erwartet wird eine Gruppen-Kennung, "
                "--kampagne oder --top."
            )
            raise typer.Exit(code=2)

        bekannt = {g.group_id: g for g in gruppen_store.load_groups()}
        jetzt = datetime.now(UTC)

        markiert: list[tuple[str, str]] = []
        uebersprungen: list[tuple[str, str]] = []
        unbekannt: list[str] = []

        for gid in auswahl:
            group = bekannt.get(gid)
            if group is None:
                unbekannt.append(gid)
                continue

            eintrag = store.load_marketing(gid)
            if _ist_schon_weiter(eintrag.marketing_status, MarketingStatus.JOIN_REQUESTED):
                uebersprungen.append((group.name or gid, eintrag.marketing_status.value))
                continue

            eintrag.marketing_status = MarketingStatus.JOIN_REQUESTED
            eintrag.join_requested_at = jetzt
            eintrag.updated_at = jetzt
            if not dry_run:
                store.save_marketing(eintrag)
            markiert.append((group.name or gid, gid))
    finally:
        gruppen_store.close()
        store.close()

    if markiert:
        table = Table(title="Beitrittsanfrage vermerkt")
        table.add_column("Gruppe")
        table.add_column("Kennung")
        for name, gid in markiert:
            table.add_row(name[:50], gid)
        console.print(table)

    vorspann = "[cyan]--dry-run:[/cyan] " if dry_run else ""
    console.print(
        f"{vorspann}[green]{len(markiert)}[/green] Gruppen auf "
        f"'{MarketingStatus.JOIN_REQUESTED.value}' gesetzt"
        + (f", {len(uebersprungen)} bereits weiter" if uebersprungen else "")
        + (f", {len(unbekannt)} unbekannt" if unbekannt else "")
        + ("." if not dry_run else " - es wurde nichts geschrieben.")
    )
    for name, stand in uebersprungen[:10]:
        console.print(f"  [dim]uebersprungen: {name[:50]} steht auf '{stand}'[/dim]")
    for gid in unbekannt[:10]:
        console.print(f"  [red]unbekannt:[/red] {gid}")


@marketing_app.command("list")
def marketing_list(
    status: str = typer.Option(None, "--status"),
    permission: str = typer.Option(None, "--erlaubnis"),
    top: int = typer.Option(30, "--top", help="0 = alle."),
) -> None:
    """Zeigt den Arbeitsstand in der Rangfolge des Scores."""
    config = _config()
    gruppen_store, store = _stores(config)
    try:
        gruppen = sort_by_rank(gruppen_store.load_groups())
        staende = store.load_all_marketing()
        zuordnungen: dict[str, int] = {}
        for campaign in store.load_campaigns():
            for link in store.links_for_campaign(campaign.campaign_id):
                zuordnungen[link.group_id] = zuordnungen.get(link.group_id, 0) + 1
    finally:
        gruppen_store.close()
        store.close()

    zeilen = []
    for group in gruppen:
        stand = staende.get(group.group_id) or GroupMarketing(group_id=group.group_id)
        if status and stand.marketing_status.value != status:
            continue
        if permission and stand.permission_status.value != permission:
            continue
        zeilen.append((group, stand))

    if top > 0:
        zeilen = zeilen[:top]

    if not zeilen:
        console.print("[yellow]Keine Gruppe passt zu diesem Filter.[/yellow]")
        return

    table = Table(title="Marketing-Stand")
    table.add_column("Score", justify="right")
    table.add_column("Gruppe")
    table.add_column("Stadt")
    table.add_column("Marketing")
    table.add_column("Kontakt")
    table.add_column("Erlaubnis")
    table.add_column("Kampagnen", justify="right")

    for group, stand in zeilen:
        table.add_row(
            f"{group.score:.1f}" if group.score is not None else "[dim]-[/dim]",
            (group.name or f"[dim]{group.group_id}[/dim]")[:34],
            group.city or "[dim]-[/dim]",
            stand.marketing_status.value,
            stand.contact_status.value,
            stand.permission_status.value,
            str(zuordnungen.get(group.group_id, 0)),
        )
    console.print(table)
    console.print(
        "[dim]Aendern: fbgroups marketing set GROUP_ID --status contacted "
        "--erlaubnis requested --kontaktiert-jetzt[/dim]"
    )


@marketing_app.command("overview")
def marketing_overview() -> None:
    """Kennzahlen des Marketing-Bereichs."""
    config = _config()
    gruppen_store, store = _stores(config)
    try:
        gruppen = gruppen_store.load_groups()
        staende = store.load_all_marketing()
        campaigns = store.load_campaigns()
        links = [
            link
            for campaign in campaigns
            for link in store.links_for_campaign(campaign.campaign_id)
        ]
        zahlen = kennzahlen(store)
    finally:
        gruppen_store.close()
        store.close()

    aktive = [c for c in campaigns if c.status is CampaignStatus.ACTIVE]
    nach_status: dict[str, int] = {}
    for stand in staende.values():
        name = stand.marketing_status.value
        nach_status[name] = nach_status.get(name, 0) + 1
    ohne_stand = len(gruppen) - len(staende)
    if ohne_stand > 0:
        nach_status["not_contacted"] = nach_status.get("not_contacted", 0) + ohne_stand

    table = Table(title="Marketing", show_header=False, box=None)
    table.add_row("Facebook Groups", str(len(gruppen)))
    table.add_row("Active Campaigns", f"{len(aktive)} von {len(campaigns)}")
    table.add_row("Tracking-Codes", str(len(links)))
    table.add_row("Clicks", str(zahlen["clicks"]))
    table.add_row("Registrations", str(zahlen["registrations"]))
    table.add_row("Downloads", str(zahlen["downloads"]))
    table.add_row("Activations", str(zahlen["activated"]))
    table.add_row("Qualified Users", str(zahlen["qualified"]))
    table.add_row("Conversions", str(zahlen["conversions"]))
    table.add_row(
        "Referrals", f"{zahlen['referrals']}  (qualifiziert: {zahlen['referrals_qualified']})"
    )
    table.add_row("Rewards", str(zahlen["rewards"]))
    table.add_row("", "")
    for name, anzahl in sorted(nach_status.items(), key=lambda x: -x[1]):
        table.add_row(name, str(anzahl))
    console.print(table)

    if zahlen["clicks"] == 0:
        console.print(
            "[dim]Noch keine Klicks. Der Dienst, der sie zaehlt, startet mit: fbgroups serve[/dim]"
        )


@marketing_app.command("analytics")
def marketing_analytics(
    top: int = typer.Option(10, "--top", help="Laenge der Bestenlisten."),
    output: Path = typer.Option(None, "--export", help="Als CSV schreiben."),
) -> None:
    """Bestenlisten und Trichter: Welche Gruppe bringt Benutzer?"""
    config = _config()
    gruppen_store, store = _stores(config)
    try:
        gruppen_namen = {g.group_id: (g.name or g.group_id) for g in gruppen_store.load_groups()}
        kampagnen_namen = {c.campaign_id: c.name for c in store.load_campaigns()}
        gruppen_liste = top_groups(store, gruppen_namen)[:top]
        kampagnen_liste = top_campaigns(store, kampagnen_namen)[:top]
        stufen = funnel(store)
    finally:
        gruppen_store.close()
        store.close()

    if not gruppen_liste and not kampagnen_liste:
        console.print(
            "[yellow]Noch keine Ereignisse.[/yellow] Klicks entstehen, sobald der "
            "Dienst laeuft (fbgroups serve) und jemand einen Tracking-Link anklickt."
        )
        return

    for titel, zeilen in (("Top Groups", gruppen_liste), ("Top Campaigns", kampagnen_liste)):
        if not zeilen:
            continue
        table = Table(title=titel)
        table.add_column(titel.split()[-1])
        table.add_column("Clicks", justify="right")
        table.add_column("Registrations", justify="right")
        table.add_column("Downloads", justify="right")
        table.add_column("Qualified", justify="right")
        table.add_column("Conversions", justify="right")
        table.add_column("Rate", justify="right")
        for zeile in zeilen:
            rate = zeile.conversion_rate
            table.add_row(
                zeile.label[:38],
                str(zeile.clicks),
                str(zeile.registrations),
                str(zeile.downloads),
                str(zeile.qualified),
                str(zeile.conversions),
                f"{rate} %" if rate is not None else "[dim]-[/dim]",
            )
        console.print(table)

    trichter = Table(title="Conversion Funnel")
    trichter.add_column("Stufe")
    trichter.add_column("Anzahl", justify="right")
    trichter.add_column("Anteil an Clicks", justify="right")
    for event_type, anzahl, anteil in stufen:
        trichter.add_row(
            event_type.value,
            str(anzahl),
            f"{anteil} %" if anteil is not None else "[dim]-[/dim]",
        )
    console.print(trichter)

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh, delimiter=";")
            writer.writerow(
                [
                    "ebene",
                    "schluessel",
                    "name",
                    "clicks",
                    "landing_visits",
                    "registrations",
                    "downloads",
                    "activations",
                    "qualified",
                    "conversions",
                    "conversion_rate",
                ]
            )
            for ebene, zeilen in (("group", gruppen_liste), ("campaign", kampagnen_liste)):
                for zeile in zeilen:
                    writer.writerow(
                        [
                            ebene,
                            zeile.schluessel,
                            zeile.label,
                            zeile.clicks,
                            zeile.landing_visits,
                            zeile.registrations,
                            zeile.downloads,
                            zeile.activations,
                            zeile.qualified,
                            zeile.conversions,
                            zeile.conversion_rate if zeile.conversion_rate is not None else "",
                        ]
                    )
        console.print(f"[green]CSV:[/green] {output}")


@marketing_app.command("code")
def marketing_code(
    tracking_code: str = typer.Argument(..., help="z. B. FB-SYR-KLN-002"),
    benutzer: bool = typer.Option(
        False, "--benutzer", "-b", help="Die Menschen dahinter einzeln zeigen."
    ),
) -> None:
    """Der Trichter eines einzelnen Tracking-Codes.

    Beantwortet die Frage, die eine Gesamtzahl nicht beantwortet: Gehoeren
    dieser Download und diese Aktivierung wirklich zu **diesem** Code? Mit
    ``--benutzer`` steht die Begruendung daneben - je Mensch die Kennungen,
    unter denen er aufgetreten ist, und die Stufen, die auf ihn entfallen.
    """
    config = _config()
    gruppen_store, store = _stores(config)
    try:
        namen = {g.group_id: (g.name or g.group_id) for g in gruppen_store.load_groups()}
        bericht = code_bericht(store, tracking_code, namen)
    finally:
        gruppen_store.close()
        store.close()

    if not bericht.group_id and not bericht.zahlen:
        console.print(
            f"[yellow]{tracking_code}[/yellow] ist keiner Gruppe zugeordnet und hat "
            f"keine Ereignisse.\n"
            f"Vergebene Codes zeigt: fbgroups campaign links <kampagne>"
        )
        raise typer.Exit(code=1)

    kopf = Table(title=f"Tracking-Code {tracking_code}", show_header=False, box=None)
    kopf.add_row("Facebook-Gruppe", bericht.group_name or "[dim]unbekannt[/dim]")
    kopf.add_row("Gruppen-ID", bericht.group_id or "[dim]-[/dim]")
    kopf.add_row("Kampagne", bericht.campaign_id or "[dim]-[/dim]")
    console.print(kopf)

    stufen = Table(title="Trichter")
    stufen.add_column("Stufe")
    stufen.add_column("Anzahl", justify="right")
    stufen.add_column("Woher", style="dim")
    for stufe, anzahl in bericht.stufen:
        stufen.add_row(stufe.value, str(anzahl), _WOHER.get(stufe.value, ""))
    console.print(stufen)

    if not benutzer:
        console.print("[dim]Mit --benutzer steht daneben, wer dahintersteckt.[/dim]")
        return

    if not bericht.benutzer:
        console.print("[dim]Noch niemand - bisher nur Klicks, und die tragen keine Kennung.[/dim]")
        return

    wege = Table(title="Menschen hinter diesem Code")
    wege.add_column("Kennungen")
    wege.add_column("Stufen")
    for weg in bericht.benutzer:
        wege.add_row(
            "\n".join(weg.kennungen),
            " -> ".join(stufe.value for stufe in weg.stufen),
        )
    console.print(wege)


# Wer die Stufe erzeugt. Steht in der Ausgabe, weil die haeufigste Frage bei
# einer Null lautet "ist das kaputt oder meldet es nur niemand?".
_WOHER: dict[str, str] = {
    "click": "Redirect-Dienst (hier)",
    "landing_visit": "Web-App ueber die API",
    "registration": "API",
    "download": "API",
    "activation": "mobile App ueber die API",
    "qualified": "API",
    "conversion": "API",
}


# ---------------------------------------------------------------------------
# Empfehlungen
# ---------------------------------------------------------------------------


@referral_app.command("code")
def referral_code(
    user_ref: str = typer.Argument(..., help="Kennung aus der Zielanwendung."),
) -> None:
    """Zeigt den Empfehlungscode eines Benutzers (legt ihn beim ersten Mal an)."""
    config = _config()
    with MarketingStore(config.path("sqlite_path")) as store:
        code = code_fuer_benutzer(store, config, user_ref)
    console.print(f"[green]{user_ref}:[/green] [bold]{code}[/bold]")


@referral_app.command("list")
def referral_list(
    status: str = typer.Option(None, "--status", help=" | ".join(s.value for s in ReferralStatus)),
    top: int = typer.Option(30, "--top", help="0 = alle."),
) -> None:
    """Listet Empfehlungen."""
    config = _config()
    with MarketingStore(config.path("sqlite_path")) as store:
        referrals = store.all_referrals(ReferralStatus(status) if status else None)

    if not referrals:
        console.print("[yellow]Noch keine Empfehlungen.[/yellow]")
        return

    if top > 0:
        referrals = referrals[:top]

    table = Table(title="Empfehlungen")
    table.add_column("Code")
    table.add_column("Werber")
    table.add_column("Geworben")
    table.add_column("Status")
    table.add_column("Gruppe")
    for referral in referrals:
        table.add_row(
            referral.referral_code,
            referral.referrer_user_ref,
            referral.referred_user_ref,
            referral.status.value,
            referral.group_id or "[dim]-[/dim]",
        )
    console.print(table)


@referral_app.command("set")
def referral_set(
    referred_user_ref: str = typer.Argument(..., help="Kennung des geworbenen Benutzers."),
    status: str = typer.Argument(..., help=" | ".join(s.value for s in ReferralStatus)),
    note: str = typer.Option("", "--notiz"),
) -> None:
    """Setzt den Stand einer Empfehlung von Hand - z. B. auf 'review'."""
    config = _config()
    with MarketingStore(config.path("sqlite_path")) as store:
        referral = setze_status(store, referred_user_ref, ReferralStatus(status), note)
        if referral is None:
            console.print(f"[red]Keine Empfehlung fuer {referred_user_ref}.[/red]")
            raise typer.Exit(code=1)
        neu = bewerte_benutzer(store, load_reward_rules(config.root), referral.referrer_user_ref)

    console.print(f"[green]{referred_user_ref}:[/green] {referral.status.value}")
    for reward in neu:
        console.print(f"  [bold]Praemie erreicht:[/bold] {reward.rule_id} ({reward.value})")


@marketing_app.command("rewards")
def marketing_rewards(
    user_ref: str = typer.Option(None, "--benutzer", help="Nur diesen Benutzer."),
    claim: str = typer.Option(None, "--einloesen", help="Regel-Kennung als eingeloest markieren."),
) -> None:
    """Zeigt Praemienregeln und vergebene Praemien."""
    config = _config()
    regeln = load_reward_rules(config.root)

    with MarketingStore(config.path("sqlite_path")) as store:
        if claim and user_ref:
            geaendert = store.set_reward_status(user_ref, claim, RewardStatus.CLAIMED)
            store.audit("reward_eingeloest", user_ref, claim)
            console.print(f"[green]{geaendert}[/green] Praemie als eingeloest markiert.")

        if user_ref:
            stand = fortschritt(store, regeln, user_ref)
            table = Table(title=f"Praemien: {user_ref}")
            table.add_column("Regel")
            table.add_column("Braucht", justify="right")
            table.add_column("Erreicht", justify="right")
            table.add_column("Praemie")
            table.add_column("Status")
            for regel, erreicht, status in stand:
                table.add_row(
                    regel.rule_id,
                    f"{regel.threshold} {regel.metric}",
                    str(erreicht),
                    f"{regel.reward_type.value} {regel.value}",
                    status.value,
                )
            console.print(table)
            return

        vergeben = store.all_rewards()

    table = Table(title="Praemienregeln (config/rewards.yaml)")
    table.add_column("Regel")
    table.add_column("Schwelle", justify="right")
    table.add_column("Typ")
    table.add_column("Wert")
    table.add_column("Aktiv")
    for regel in regeln:
        table.add_row(
            regel.rule_id,
            f"{regel.threshold} {regel.metric}",
            regel.reward_type.value,
            regel.value,
            "ja" if regel.active else "[dim]nein[/dim]",
        )
    console.print(table)
    console.print(f"Vergeben: [bold]{len(vergeben)}[/bold] Praemien.")


@marketing_app.command("audit")
def marketing_audit(limit: int = typer.Option(25, "--limit")) -> None:
    """Zeigt das Pruefprotokoll - auch die abgelehnten Faelle."""
    config = _config()
    with MarketingStore(config.path("sqlite_path")) as store:
        eintraege = store.audit_log(limit)

    if not eintraege:
        console.print("[yellow]Noch keine Eintraege.[/yellow]")
        return

    table = Table(title="Audit-Log")
    table.add_column("Zeitpunkt")
    table.add_column("Vorgang")
    table.add_column("Betrifft")
    table.add_column("Detail")
    for eintrag in eintraege:
        table.add_row(
            eintrag["occurred_at"][:19],
            eintrag["action"],
            eintrag["subject"][:24],
            eintrag["detail"][:50],
        )
    console.print(table)


@marketing_app.command("regeln")
def marketing_regeln(
    limit: int = typer.Option(0, "--limit", help="Hoechstens N Gruppenseiten lesen."),
    alle: bool = typer.Option(False, "--alle", help="Alle offenen Gruppen."),
    erneut: bool = typer.Option(
        False, "--erneut", help="Auch Gruppen, deren Regeln schon gelesen sind."
    ),
) -> None:
    """Liest die Regeln der Gruppenseiten - Beitritt, Links, Werbung, Freigabe.

    **Startet nie beilaeufig.** Ohne ``--limit`` oder ``--alle`` bricht der
    Befehl mit Exit-Code 2 ab, ohne etwas abzurufen - dieselbe Vorsicht wie
    bei ``fbgroups enrich`` und aus demselben Grund: Es geht um das Konto des
    Nutzers, nicht um Guthaben.

    **Nur mit angemeldetem Browser.** Ueber ``httpx`` antwortet Facebook mit
    einer Anmeldewand; dort steht keine Gruppenregel. Ein Weg, der
    zuverlaessig nichts findet, waere schlimmer als keiner - er trueg "nichts
    verboten" in den Bestand ein, und das ist eine Erlaubnis, die niemand
    erteilt hat.

    Ein **nicht gelesener** Befund schreibt nichts (``merke_regeln``): Eine
    Anmeldewand ist kein Beleg dafuer, dass eine frueher gelesene Regel weg
    ist.
    """
    from fbgroups.marketing.qualifikation import lies_regeln

    if not limit and not alle:
        console.print(
            "[red]Kein Umfang angegeben.[/red] "
            "Ein Abruf bei facebook.com beginnt nicht beilaeufig:\n"
            "  --limit N   hoechstens N Gruppenseiten\n"
            "  --alle      alle offenen"
        )
        raise typer.Exit(code=2)

    config = _config()
    with SqliteStore(config.path("sqlite_path")) as bestand:
        gruppen = [g for g in bestand.load_groups() if g.url_canonical]
    with MarketingStore(config.path("sqlite_path")) as store:
        staende = store.load_all_marketing()

    offen = [
        g
        for g in gruppen
        if erneut
        or (g.group_id not in staende)
        or staende[g.group_id].regeln_gelesen_am is None
    ]
    wieviele = len(offen) if alle else min(limit, len(offen))
    console.print(
        f"{len(gruppen)} Gruppen, davon [bold]{len(offen)}[/bold] ohne gelesene Regeln. "
        f"{wieviele} in diesem Lauf."
    )
    if not wieviele:
        return

    from fbgroups.automation.actions import fetch_group_html
    from fbgroups.automation.browser import get_browser_context

    console.print("[cyan]Mit angemeldetem Browser - das Fenster bleibt sichtbar.[/cyan]")
    gelesen = mit_regel = 0
    with get_browser_context(config, headless=False) as context, MarketingStore(
        config.path("sqlite_path")
    ) as store:
        for gruppe in offen[:wieviele]:
            try:
                html = fetch_group_html(context, gruppe.url_canonical)
            except Exception as exc:  # noqa: BLE001 - ein Fehlschlag ist ein Befund
                console.print(f"[red]{gruppe.group_id}: {str(exc).splitlines()[0][:80]}[/red]")
                continue

            befund = lies_regeln(html)
            if not befund.gelesen:
                console.print(f"[dim]{gruppe.name or gruppe.group_id}: nichts lesbar[/dim]")
                continue

            store.merke_regeln(gruppe.group_id, befund)
            gelesen += 1
            zusammenfassung = befund.zusammenfassung()
            if zusammenfassung:
                mit_regel += 1
                console.print(
                    f"[yellow]{gruppe.name or gruppe.group_id}: {zusammenfassung}[/yellow]"
                )
            else:
                console.print(f"[green]{gruppe.name or gruppe.group_id}: nichts verboten[/green]")

    console.print(
        f"\n[green]{gelesen}[/green] Gruppenseiten gelesen, "
        f"[yellow]{mit_regel}[/yellow] davon mit einer einschraenkenden Regel."
    )


@campaign_app.command("pruefe-inhalt")
def campaign_pruefe_inhalt(
    text: list[str] = typer.Argument(
        None, help="Der Beitragstext. Mehrere Texte: mehrfach angeben."
    ),
    datei: Path = typer.Option(
        None, "--datei", help="Texte aus einer Datei, ein Beitrag je Zeile."
    ),
    regeln: str = typer.Option(
        "",
        "--regeln",
        help="Regeltext der Gruppe (z. B. 'Keine Links'). Leer = ungelesen.",
    ),
    mitglied: bool = typer.Option(
        True, "--mitglied/--kein-mitglied", help="Ist das Konto in der Gruppe?"
    ),
) -> None:
    """Zeigt, was der Runner in einem Beitrag sieht - **ohne Browser und Konto**.

    Der Weg, die Erkennung an den eigenen Gruppen zu pruefen, bevor irgendwo
    ein Kommentar steht. Er ruft nichts ab, schreibt nichts und braucht keine
    Anmeldung: Der Text kommt von der Kommandozeile, und heraus kommt genau
    das, was auch im Lauf entschieden wuerde.

    Gezeigt wird beides - der **Befund** (worum geht es, was will die
    Person, hat es mit uns zu tun) und die **Entscheidung** (welche Form
    einer Antwort passt, mit oder ohne Link). Wer die Schlagwortlisten in
    ``marketing/inhalt.py`` erweitert, prueft hier, ob es gewirkt hat.

    ``--regeln`` stellt die Gruppe nach: Ohne Angabe gelten ihre Regeln als
    **ungelesen**, und dann faellt die Entscheidung vorsichtiger aus - genau
    wie im Betrieb.
    """
    from fbgroups.marketing import automatik, inhalt, qualifikation
    from fbgroups.marketing.entscheidung import Erlaubnis, entscheide
    from fbgroups.marketing.qualifikation import Regelbefund, beurteile, lies_regeln

    texte = list(text or [])
    if datei:
        # utf-8-sig: Die Datei entsteht oft in Notepad, das ein BOM schreibt -
        # ohne das haenge es am ersten Text.
        texte += [
            zeile.strip()
            for zeile in datei.read_text(encoding="utf-8-sig").splitlines()
            if zeile.strip()
        ]
    if not texte:
        console.print(
            "[red]Kein Text.[/red] Beispiel:\n"
            '  fbgroups campaign pruefe-inhalt "كيف فيني ابعت غرض لسوريا؟"'
        )
        raise typer.Exit(code=2)

    config = _config()
    befund_regeln = lies_regeln(regeln) if regeln else Regelbefund()
    urteil = beurteile(mitglied=mitglied, regeln=befund_regeln)
    erlaubnis = Erlaubnis.aus_regeln(befund_regeln, urteil.qualifikation)

    console.print(
        Panel(
            f"Gruppe: {urteil.beschriftung} - {urteil.grund}\n"
            f"Erlaubt: Kommentare {'ja' if erlaubnis.kommentare else 'nein'} · "
            f"Links {'ja' if erlaubnis.links else 'nein'} · "
            f"Werbung {'ja' if erlaubnis.werbung else 'nein'} · "
            f"Regeln {'gelesen' if erlaubnis.regeln_gelesen else 'UNGELESEN'}",
            title="Angenommene Gruppe",
        )
    )

    tabelle = Table(box=None)
    tabelle.add_column("Beitrag", max_width=38)
    tabelle.add_column("Thema")
    tabelle.add_column("Absicht")
    tabelle.add_column("Bezug")
    tabelle.add_column("Entscheidung")
    tabelle.add_column("Link")

    for roh in texte:
        befund = inhalt.lies(roh)
        entscheidung = entscheide(befund, erlaubnis)
        farbe = "green" if entscheidung.antwortet else "dim"
        tabelle.add_row(
            roh[:38],
            befund.thema.value,
            befund.absicht.value,
            befund.relevanz.value,
            f"[{farbe}]{entscheidung.art.value}[/{farbe}]",
            "ja" if entscheidung.mit_link else "-",
        )
    console.print(tabelle)

    console.print(
        "\n[dim]Kein Abruf, keine Buchung. Was hier ``no_reply`` heisst, "
        "bekommt im Lauf keinen Kommentar - das ist ein Ergebnis und kein "
        "Fehlschlag.[/dim]"
    )

    if not mitglied:
        # Die Mitgliedschaft ist **kein** Teil dieser Entscheidung, und das
        # ist Absicht: Sie ist eine Angabe ueber unseren Arbeitsstand, nicht
        # ueber den Beitrag. Ob sie sperrt, sagt
        # ``automatik.mitgliedschaft_pflicht`` - sonst stuende hier eine
        # Entscheidung, die der Lauf gar nicht erst treffen wuerde.
        sperrt = automatik.mitgliedschaft_pflicht(config) or qualifikation.pflicht(config)
        console.print(
            "[yellow]Ohne Mitgliedschaft:[/yellow] Diese Entscheidung beschreibt "
            "nur den Beitrag. Ob in der Gruppe ueberhaupt etwas versucht wird, "
            "sagt der Schalter - er steht gerade auf "
            f"[bold]{'sperren' if sperrt else 'versuchen'}[/bold] "
            "(automatik.mitgliedschaft_pflicht / qualifikation.pflicht)."
        )


@campaign_app.command("qualifikation")
def campaign_qualifikation(
    campaign_id: str = typer.Argument(...),
    stufe: str = typer.Option("", "--stufe", help="Nur diese Stufe zeigen."),
) -> None:
    """Der Trichter dieser Kampagne: entdeckt -> Beitritt -> Bewertung -> geeignet.

    Die Antwort auf "wir haben 300 Gruppen, also posten wir in 300 Gruppen".
    Gerechnet wird bei jedem Aufruf neu aus Mitgliedschaft, gelesenen Regeln
    und dem Versuchsprotokoll - es gibt keine gespeicherte Einstufung, die
    veralten koennte.
    """
    from fbgroups.marketing import qualifikation as qual

    config = _config()
    with SqliteStore(config.path("sqlite_path")) as bestand:
        gruppen = {g.group_id: g for g in bestand.load_groups()}
    with MarketingStore(config.path("sqlite_path")) as store:
        _kampagne_oder_ende(store, campaign_id)
        links = store.links_for_campaign(campaign_id)
        staende = store.load_all_marketing()
        beobachtet = store.beobachtungen()

    mitgliedschaft = {
        MarketingStatus.MEMBER,
        MarketingStatus.CONTACTED,
        MarketingStatus.INTERESTED,
        MarketingStatus.APPROVED,
        MarketingStatus.ACTIVE,
    }
    je_stufe: dict[str, list[tuple[str, str]]] = {}
    for link in links:
        stand = staende.get(link.group_id)
        befund = qual.beurteile(
            mitglied=bool(stand and stand.marketing_status in mitgliedschaft),
            beitritt_angefragt=bool(
                stand and stand.marketing_status is MarketingStatus.JOIN_REQUESTED
            ),
            regeln=qual.Regelbefund(
                gelesen=bool(stand and stand.regeln_gelesen_am is not None),
                keine_links=bool(stand and stand.regel_keine_links),
                keine_werbung=bool(stand and stand.regel_keine_werbung),
                freigabe_noetig=bool(stand and stand.regel_freigabe_noetig),
                neue_ohne_links=bool(stand and stand.regel_neue_ohne_links),
            ),
            beobachtung=beobachtet.get(link.group_id),
        )
        gruppe = gruppen.get(link.group_id)
        name = (gruppe.name if gruppe else "") or link.group_id
        je_stufe.setdefault(befund.qualifikation.value, []).append((name, befund.grund))

    tabelle = Table(box=None)
    tabelle.add_column("Stufe", style="bold")
    tabelle.add_column("Gruppen", justify="right")
    for q in qual.TRICHTER:
        eintraege = je_stufe.get(q.value, [])
        if eintraege or q is qual.Qualifikation.GEEIGNET:
            tabelle.add_row(qual.BESCHRIFTUNG[q], str(len(eintraege)))
    console.print(tabelle)

    arbeitsfaehig = sum(
        len(je_stufe.get(q.value, []))
        for q in qual.TRICHTER
        if qual.darf(q, Texttyp.KOMMENTAR, mit_link=False)
    )
    console.print(
        f"\n[bold]{arbeitsfaehig}[/bold] von {len(links)} Gruppen koennten "
        "ueberhaupt etwas bekommen."
    )
    if not qual.pflicht(config):
        console.print(
            "[dim]Der Schalter qualifikation.pflicht steht auf false - "
            "gearbeitet wird weiterhin in allen.[/dim]"
        )

    if stufe:
        for name, grund in je_stufe.get(stufe, []):
            console.print(f"  {name[:44]:<44}  {grund}")
