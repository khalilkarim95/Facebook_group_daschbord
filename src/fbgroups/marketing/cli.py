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

import csv
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from fbgroups.config import AppConfig, load_config
from fbgroups.marketing.analytics import (
    code_bericht,
    funnel,
    kennzahlen,
    top_campaigns,
    top_groups,
)
from fbgroups.marketing.beitrag import beitragstext, in_zwischenablage, oeffne_im_browser
from fbgroups.marketing.ki import (
    ANBIETER,
    PLATZHALTER,
    KINichtVerfuegbar,
    UngueltigerVorschlag,
    auftrag_aus_gruppe,
    baue_modell,
    erzeuge_entwuerfe,
    gewaehlter_anbieter,
)
from fbgroups.marketing.ki import status as ki_status
from fbgroups.marketing.ki import teste as ki_teste
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
    TextQuelle,
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
from fbgroups.marketing.veroeffentlicher import (
    UnbekannterVeroeffentlicher,
    Veroeffentlicher,
    baue_veroeffentlicher,
    verfuegbare,
)
from fbgroups.marketing.worker import arbeite, lade_grenzen
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
        ):
            if wert is not None:
                setattr(campaign, feld, wert)
                geaendert.append(feld)

        if not geaendert:
            console.print("[yellow]Nichts angegeben - es wurde nichts geaendert.[/yellow]")
            raise typer.Exit(code=0)

        campaign.updated_at = datetime.now(UTC)
        store.save_campaign(campaign)

    console.print(
        f"[green]{campaign_id}:[/green] {', '.join(geaendert)} aktualisiert."
    )
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
            geaendert.append(
                "auch unbewertete Gruppen" if unbewertete else "nur bewertete Gruppen"
            )

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
            c.name_de.lower() for cid, c in config.cities.items() if cid.lower() in
            {s.lower() for s in (city or [])}
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
            writer.writerow(["tracking_code", "tracking_url", "group_id", "name", "stadt", "url"])
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
                    ]
                )
        console.print(f"[green]CSV:[/green] {output}  ({len(links)} Links)")
        return

    _links_tabelle(links, namen)


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
) -> None:
    """Gibt die Textvorlage mit eingesetztem Tracking-Link aus.

    Zum Kopieren. Dieses Programm postet nichts und schickt nichts - das
    Einfuegen und Absenden bleibt Handarbeit, und das ist Absicht.
    """
    config = _config()
    with MarketingStore(config.path("sqlite_path")) as store:
        campaign = _kampagne_oder_ende(store, campaign_id)
        link = store.link_for(campaign_id, group_id)

    if link is None:
        console.print(f"[red]{group_id} ist dieser Kampagne nicht zugeordnet.[/red]")
        raise typer.Exit(code=1)

    # Derselbe Textbauer wie queue, next und die Uebersicht - eine zweite
    # Fassung koennte abweichen, und der Unterschied fiele erst auf, wenn
    # ein Beitrag mit dem falschen Code veroeffentlicht ist.
    text = beitragstext(campaign, link)

    console.print(
        Panel(
            text or "[dim](keine Vorlage hinterlegt)[/dim]",
            title=f"{campaign.name} - {link.tracking_code}",
            subtitle="von Hand einfuegen und absenden",
        )
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
        f"In der Arbeitsliste: [bold]{offen}[/bold] "
        "(ausgeschlossene Gruppen zaehlen nicht mit)"
    )


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
    campaign_id: str = typer.Argument(...),
    alle: bool = typer.Option(
        False, "--alle", help="Auch die, die ihre Versuche aufgebraucht haben."
    ),
) -> None:
    """Stellt die fehlgeschlagenen Beitraege zurueck in die Arbeitsliste.

    ``uebersprungen`` bleibt stehen - dort hat ein Mensch entschieden, dass
    die Gruppe nicht passt.

    Wer ``max_versuche`` (Vorgabe 3) erreicht hat, bleibt ebenfalls stehen und
    wird am Ende genannt: "erlaubt keine Links" geht beim vierten Mal nicht
    anders aus als beim ersten, kostet aber jedes Mal einen Platz im
    Tageslimit. ``--alle`` uebergeht die Grenze.
    """
    config = _config()
    grenze = 0 if alle else int(
        config.get("marketing", "posting", "max_versuche", default=3) or 0
    )
    with MarketingStore(config.path("sqlite_path")) as store:
        _kampagne_oder_ende(store, campaign_id)
        anzahl = store.fehlgeschlagene_zuruecksetzen(campaign_id, max_versuche=grenze)
        stehengeblieben = store.aufgegeben(campaign_id, grenze)

    console.print(f"[green]{anzahl}[/green] wieder offen. Uebersprungene bleiben unberuehrt.")
    if stehengeblieben:
        console.print(
            f"\n[yellow]{len(stehengeblieben)}[/yellow] haben {grenze} Versuche aufgebraucht "
            "und warten auf eine Entscheidung:"
        )
        for link in stehengeblieben[:10]:
            console.print(
                f"  [dim]{link.post_attempts}x[/dim] {link.group_id} - "
                f"{link.post_error or 'ohne Angabe'}"
            )
        if len(stehengeblieben) > 10:
            console.print(f"  [dim]... und {len(stehengeblieben) - 10} weitere[/dim]")
        console.print(
            f"[dim]Trotzdem erneut versuchen:  fbgroups campaign retry {campaign_id} --alle[/dim]"
        )


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


@campaign_app.command("draft")
def campaign_draft(
    campaign_id: str = typer.Argument(...),
    gruppe: str = typer.Option("", "--gruppe", help="Nur diese eine Gruppe."),
    top: int = typer.Option(0, "--top", help="Die besten N ohne Text (0 = alle)."),
    varianten: int = typer.Option(0, "--varianten", help="Fassungen je Gruppe."),
    neu: bool = typer.Option(
        False,
        "--neu",
        help="Auch dort erzeugen, wo schon ein Text steht - die Freigabe faellt dabei weg.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Nur zeigen, was erzeugt wuerde - kein Aufruf, keine Kosten."
    ),
) -> None:
    """Laesst Claude Beitragsvorschlaege schreiben - je Gruppe mehrere Fassungen.

    Erzeugt **Entwuerfe**, sonst nichts. Nichts wird dabei freigegeben,
    eingereiht oder veroeffentlicht; jeder Vorschlag wartet auf einen Menschen.

    Der Tracking-Code wird dem Modell nie gezeigt: Es schreibt den Platzhalter
    ``{link}``, und erst beim Zusammensetzen des Beitrags kommt der Code
    **dieser** Gruppe hinein. Was das Modell nie sieht, kann es nicht
    verfaelschen.

    ``--dry-run`` zeigt die Auswahl, ohne einen Aufruf zu machen - jeder Aufruf
    kostet, und bei 310 Gruppen ist das eine Zahl, die man vorher kennen will.
    """
    config = _config()
    anzahl_varianten = varianten or int(
        config.get("marketing", "posting", "ki", "varianten", default=3)
    )
    anbieter = gewaehlter_anbieter(config)

    gruppen_store, store = _stores(config)
    try:
        campaign = _kampagne_oder_ende(store, campaign_id)
        gruppen = _gruppen_nach_id(gruppen_store)
        links = store.jobs_mit_status(campaign_id)
    finally:
        gruppen_store.close()

    try:
        if gruppe:
            offen = [link for link in links if link.group_id == gruppe]
            if not offen:
                console.print(f"[red]{gruppe}[/red] gehoert nicht zu {campaign_id}.")
                raise typer.Exit(code=1)
        else:
            if neu:
                # Auch die mit Text - aber nie ein veroeffentlichter Beitrag
                # und nie einer, der gerade in Arbeit ist. Der erste steht in
                # einer Gruppe und laesst sich nicht mehr zurueckholen; beim
                # zweiten haette jemand den Text vor sich, waehrend er sich
                # unter der Hand aendert.
                offen = [
                    link
                    for link in links
                    if link.job_status not in (JobStatus.PUBLISHED, JobStatus.PROCESSING)
                ]
            else:
                # Nur die ohne Text: Ein zweiter Lauf soll nicht 310 Aufrufe
                # wiederholen, fuer die es laengst Entwuerfe gibt.
                offen = [
                    link
                    for link in links
                    if not link.post_text and link.job_status is JobStatus.DRAFT
                ]
            geordnet = nach_prioritaet(
                [gruppen[link.group_id] for link in offen if link.group_id in gruppen], config
            )
            reihenfolge = {g.group_id: i for i, g in enumerate(geordnet)}
            offen.sort(key=lambda link: reihenfolge.get(link.group_id, len(reihenfolge)))
            if top:
                offen = offen[:top]

        if not offen:
            console.print(
                "[green]Nichts zu erzeugen[/green] - alle haben schon einen Text.\n"
                f"[dim]Trotzdem neu schreiben lassen:  "
                f"fbgroups campaign draft {campaign_id} --neu[/dim]"
            )
            return

        if dry_run:
            table = Table(title=f"Wuerde erzeugen ({len(offen)} Gruppen)")
            table.add_column("Gruppe")
            table.add_column("Prioritaet")
            table.add_column("Score", justify="right")
            for link in offen:
                g = gruppen.get(link.group_id)
                table.add_row(
                    (g.name if g else link.group_id)[:40],
                    prioritaet(g, config) if g else "[dim]unbekannt[/dim]",
                    _score_text(g),
                )
            console.print(table)
            stand = ki_status(config)
            console.print(
                f"[dim]{len(offen)} Gruppen x {anzahl_varianten} Fassungen ueber "
                f"'{anbieter}' ({stand.modell or 'kein Modell eingestellt'}). "
                f"Ohne --dry-run wird das wirklich erzeugt.[/dim]"
            )
            if anbieter == "ollama" and not stand.erreichbar:
                console.print(
                    "[yellow]Ollama antwortet gerade nicht[/yellow] - "
                    "der echte Lauf wuerde scheitern. Pruefen mit: fbgroups ki status"
                )
            return

        try:
            modell = baue_modell(config)
        except KINichtVerfuegbar as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=2) from exc

        erzeugt = verworfen_gesamt = 0
        for link in offen:
            group = gruppen.get(link.group_id)
            if group is None:
                continue
            auftrag = auftrag_aus_gruppe(
                group, campaign, config, varianten=anzahl_varianten
            )
            auftrag.bisherige_texte = [
                e.text for e in store.entwuerfe_for(campaign_id, link.group_id)
            ]
            try:
                entwuerfe, verworfen = erzeuge_entwuerfe(
                    modell,
                    auftrag,
                    campaign_id=campaign_id,
                    group_id=link.group_id,
                )
            except (UngueltigerVorschlag, KINichtVerfuegbar) as exc:
                console.print(f"[red]{group.name or link.group_id}:[/red] {exc}")
                continue

            for entwurf in entwuerfe:
                store.add_entwurf(entwurf)
            erzeugt += len(entwuerfe)
            verworfen_gesamt += len(verworfen)

            if entwuerfe:
                # Die erste Fassung wird uebernommen - waehlbar bleibt jede
                # andere ueber 'campaign entwuerfe'.
                store.set_post_text(
                    campaign_id, link.group_id, entwuerfe[0].text, TextQuelle.KI
                )
                # Ein neuer Text macht jede bestehende Freigabe ungueltig: Ein
                # Mensch hat einen *anderen* Text freigegeben. Waere der Stand
                # geblieben, ginge ein ungeprueter Text in eine Gruppe hinaus -
                # und niemand haette ihn je gesehen. Der Weg zurueck fuehrt
                # ueber ``draft``, weil ``approved -> ai_generated`` in der
                # Uebergangstabelle zu Recht fehlt.
                if link.job_status not in _VOR_DER_PRUEFUNG:
                    _wechsle(
                        store, campaign_id, link.group_id, JobStatus.DRAFT, erzwingen=True
                    )
                _wechsle(store, campaign_id, link.group_id, JobStatus.AI_GENERATED)
                console.print(
                    f"[green]OK[/green] {group.name or link.group_id}: "
                    f"{len(entwuerfe)} Fassungen"
                )
            else:
                console.print(f"[yellow]--[/yellow] {group.name or link.group_id}: nichts Gutes")
            for grund in verworfen:
                console.print(f"   [dim]verworfen: {grund}[/dim]")
    finally:
        store.close()

    console.print(
        f"\n[green]{erzeugt}[/green] Entwuerfe erzeugt"
        + (f", [yellow]{verworfen_gesamt}[/yellow] verworfen." if verworfen_gesamt else ".")
    )
    console.print(f"[dim]Ansehen:  fbgroups campaign entwuerfe {campaign_id} <gruppe>[/dim]")


@campaign_app.command("text")
def campaign_text(
    campaign_id: str = typer.Argument(...),
    aus_vorlage: bool = typer.Option(
        False, "--aus-vorlage", help="Die Vorlage der Kampagne in jede Zuordnung schreiben."
    ),
    ueberschreiben: bool = typer.Option(
        False, "--ueberschreiben", help="Auch dort, wo schon ein Text steht."
    ),
    ja: bool = typer.Option(False, "--ja", help="Wirklich schreiben (sonst nur zeigen)."),
) -> None:
    """Traegt die Textvorlage der Kampagne als Beitragstext ein - ohne KI.

    Der Weg fuer alle, die keine Textvorschlaege erzeugen wollen oder koennen:
    Ohne ``post_text`` kommt eine Zuordnung nicht durch die Freigabe
    (``pruefe_uebergang`` verlangt einen Text) und damit nie in die
    Warteschlange. ``campaign draft`` fuellt das Feld mit einem Modell, dieser
    Befehl mit der Vorlage.

    Die Vorlage bleibt dabei **je Gruppe** eine eigene Kopie. Das ist kein
    Umweg: ``{link}`` wird erst beim Absetzen durch den Code *dieser* Gruppe
    ersetzt, und wer einen einzelnen Text nachtraeglich anpasst, soll damit
    nicht alle anderen aendern.

    Vorhandene Texte bleiben stehen, sofern nicht ``--ueberschreiben``. Ein von
    Hand ueberarbeiteter oder freigegebener Text ist Arbeit eines Menschen; ein
    Sammelbefehl macht sie nicht beilaeufig zunichte.
    """
    if not aus_vorlage:
        console.print(
            "Nichts zu tun. Der Text kommt entweder aus der Vorlage "
            f"([bold]--aus-vorlage[/bold]) oder aus einem Modell "
            f"([bold]fbgroups campaign draft {campaign_id}[/bold])."
        )
        raise typer.Exit(code=2)

    config = _config()
    with MarketingStore(config.path("sqlite_path")) as store:
        campaign = _kampagne_oder_ende(store, campaign_id)

        if not campaign.message_template.strip():
            console.print(
                "[red]Diese Kampagne hat keine Textvorlage.[/red]\n"
                f"Setzen mit:  fbgroups campaign set {campaign_id} --vorlage \"...\""
            )
            raise typer.Exit(code=2)

        if PLATZHALTER not in campaign.message_template:
            console.print(
                f"[yellow]Die Vorlage enthaelt kein {PLATZHALTER}.[/yellow] "
                "Der Beitrag ginge dann ohne Tracking-Link hinaus, und keine "
                "Gruppe bekaeme je einen Klick gutgeschrieben."
            )
            raise typer.Exit(code=2)

        links = store.links_for_campaign(campaign_id)
        betroffen = [
            link
            for link in links
            if ueberschreiben or not link.post_text.strip()
            if link.job_status not in (JobStatus.PUBLISHED, JobStatus.PROCESSING)
        ]

        console.print(Panel(campaign.message_template, title="Vorlage"))
        console.print(
            f"{len(betroffen)} von {len(links)} Zuordnungen bekaemen diesen Text."
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

        for link in betroffen:
            store.set_post_text(
                campaign_id, link.group_id, campaign.message_template, TextQuelle.VORLAGE
            )

    console.print(f"\n[green]{len(betroffen)}[/green] Texte eingetragen.")
    console.print(
        f"[dim]Weiter:  fbgroups campaign approve {campaign_id} alle"
        f"  →  fbgroups campaign enqueue {campaign_id}[/dim]"
    )


@campaign_app.command("entwuerfe")
def campaign_entwuerfe(
    campaign_id: str = typer.Argument(...),
    group_id: str = typer.Argument(...),
    waehle: int = typer.Option(0, "--waehle", help="Diese Variantennummer uebernehmen."),
) -> None:
    """Zeigt die Fassungen einer Gruppe - und uebernimmt auf Wunsch eine.

    Die verworfenen bleiben stehen. Sie kosten nichts und beantworten spaeter
    die Frage, wogegen entschieden wurde.
    """
    config = _config()
    with MarketingStore(config.path("sqlite_path")) as store:
        _kampagne_oder_ende(store, campaign_id)
        link = _job_oder_ende(store, campaign_id, group_id)
        entwuerfe = store.entwuerfe_for(campaign_id, group_id)

        if waehle:
            passend = next((e for e in entwuerfe if e.variante == waehle), None)
            if passend is None or passend.entwurf_id is None:
                console.print(f"[red]Variante {waehle}[/red] gibt es nicht.")
                raise typer.Exit(code=1)
            store.waehle_entwurf(passend.entwurf_id)
            console.print(f"[green]Variante {waehle} uebernommen.[/green]")
            console.print(
                f"[dim]Freigeben:  fbgroups campaign approve {campaign_id} {group_id}[/dim]"
            )
            return

        if not entwuerfe:
            console.print(
                f"Keine Entwuerfe. Erzeugen mit:\n"
                f"  fbgroups campaign draft {campaign_id} --gruppe {group_id}"
            )
            return

        console.print(f"[bold]{group_id}[/bold] - Stand: {link.job_status.value}\n")
        for entwurf in entwuerfe:
            marke = "[green]* gewaehlt[/green]" if entwurf.gewaehlt else ""
            console.print(f"[bold]Variante {entwurf.variante}[/bold] "
                          f"([dim]{entwurf.modell}[/dim]) {marke}")
            console.print(entwurf.text)
            console.print()
        console.print(
            f"[dim]Uebernehmen:  fbgroups campaign entwuerfe {campaign_id} "
            f"{group_id} --waehle 2[/dim]"
        )


# Staende, aus denen ein Beitrag zur Pruefung vorgelegt werden kann.
# ``draft`` gehoert dazu: Ein von Hand geschriebener Text ist genauso
# freizugeben wie einer von Claude - sonst waere Handarbeit der einzige Weg,
# der in der Warteschlange nicht ankommt.
_VOR_DER_PRUEFUNG: frozenset[JobStatus] = frozenset(
    {JobStatus.DRAFT, JobStatus.AI_GENERATED}
)


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
                if _wechsle(
                    store, campaign_id, link.group_id, JobStatus.APPROVED, akteur=akteur
                ):
                    fertig += 1
            console.print(f"[green]{fertig}[/green] freigegeben.")
            return

        link = _job_oder_ende(store, campaign_id, group_id)
        if link.job_status in _VOR_DER_PRUEFUNG:
            _wechsle(store, campaign_id, group_id, JobStatus.PENDING_REVIEW)
        if _wechsle(store, campaign_id, group_id, JobStatus.APPROVED, akteur=akteur):
            console.print("[green]Freigegeben.[/green]")
            console.print(
                f"[dim]Einreihen:  fbgroups campaign enqueue {campaign_id}[/dim]"
            )


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

    kopf = Table(title=f"{campaign_id} - Warteschlange {zustand.value}",
                 show_header=False, box=None)
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
            link.last_attempt_at.strftime("%d.%m. %H:%M") if link.last_attempt_at
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
            text = beitragstext(campaign, link)

            kopiert = False if keine_zwischenablage else in_zwischenablage(text)
            geoeffnet = (
                False
                if kein_browser or group is None
                else oeffne_im_browser(group.url_canonical)
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
                store.set_post_status(
                    campaign_id, link.group_id, PostStatus.FEHLGESCHLAGEN, grund
                )
                console.print(f"[red]fehlgeschlagen[/red] · {grund}")
            else:
                store.set_post_status(campaign_id, link.group_id, PostStatus.VEROEFFENTLICHT)
                console.print(f"[green]veroeffentlicht[/green] · {link.tracking_code}")

        console.print()
        console.print(_fortschritt_zeile(store.post_counts(campaign_id)))
    finally:
        store.close()


def _baue_veroeffentlicher(name: str) -> Veroeffentlicher:
    """Sucht den Adapter aus der Registry - oder endet mit einer Auskunft.

    Die Liste steht nicht hier: Ein neuer Adapter traegt sich ueber
    ``@register_veroeffentlicher`` selbst ein, und diese Datei aendert sich
    dabei nicht. Was die Kommandozeile anbietet, ist damit immer genau das,
    was es wirklich gibt.
    """
    try:
        return baue_veroeffentlicher(
            name,
            frage=console.input,
            melde=lambda zeile: console.print(Panel(zeile)),
        )
    except UnbekannterVeroeffentlicher as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc


@campaign_app.command("worker")
def campaign_worker(
    campaign_id: str = typer.Argument(...),
    limit: int = typer.Option(
        0, "--limit", help="Hoechstens so viele in diesem Lauf (0 = Wert aus der Konfiguration)."
    ),
    tageslimit: int = typer.Option(
        0, "--tageslimit", help="Ueberschreibt max_pro_tag fuer diesen Lauf."
    ),
    adapter: str = typer.Option(
        "assistiert", "--adapter", help=" | ".join(verfuegbare())
    ),
    trocken: bool = typer.Option(
        False, "--dry-run", help="Zeigt den Plan und die Grenzen, ohne etwas abzusetzen."
    ),
) -> None:
    """Arbeitet die Warteschlange ab - eine Gruppe nach der anderen.

    Die Reihenfolge kommt aus der Warteschlange und damit aus dem Score: Beim
    ``enqueue`` wird nach Rang sortiert, hier nicht mehr - sonst entschiede
    eine Neubewertung mitten im Lauf, welcher Beitrag als naechstes hinausgeht.

    ``pause``, ``resume`` und ``stop`` wirken **waehrend** der Arbeiter laeuft:
    Der Zustand wird vor jedem Beitrag und nach jeder Wartezeit frisch aus der
    Datenbank gelesen. Ein zweites Fenster genuegt.

    Das Tageslimit zaehlt aus ``post_versuche`` und ueberlebt damit einen
    Neustart - zwei Laeufe am selben Tag setzen zusammen nicht mehr Beitraege
    als einer.
    """
    config = _config()
    gruppen_store, store = _stores(config)
    try:
        campaign = _kampagne_oder_ende(store, campaign_id)
        gruppen = _gruppen_nach_id(gruppen_store)
    finally:
        gruppen_store.close()

    try:
        grenzen = lade_grenzen(config)
        if limit:
            grenzen = replace(grenzen, max_pro_lauf=limit)
        if tageslimit:
            grenzen = replace(grenzen, tageslimit=tageslimit)

        heute = store.versuche_heute(campaign_id)
        offen = store.job_counts(campaign_id).get(JobStatus.QUEUED.value, 0)
        zustand = store.queue_zustand(campaign_id)

        tabelle = Table(box=None, show_header=False)
        tabelle.add_column(style="dim")
        tabelle.add_column()
        tabelle.add_row("Warteschlange", f"{offen} eingereiht · Zustand: {zustand.value}")
        tabelle.add_row("Heute schon", f"{heute} von {grenzen.tageslimit} Versuchen")
        tabelle.add_row("Dieser Lauf", f"hoechstens {grenzen.max_pro_lauf}")
        tabelle.add_row(
            "Wartezeit", f"{grenzen.pause_min:.0f}-{grenzen.pause_max:.0f}s zwischen Beitraegen"
        )
        tabelle.add_row("Adapter", f"{adapter} - {verfuegbare().get(adapter, 'unbekannt')}")
        console.print(tabelle)
        console.print()

        if trocken:
            # Der Arbeiter schlaeft nicht bis zur Startzeit: Ein Prozess, der
            # vierzehn Stunden wartet, ueberlebt keinen Neustart. Die
            # Aufgabenplanung des Systems kann das, und sie tut es zuverlaessig
            # - hier steht der fertige Befehl dafuer.
            startzeit = str(
                config.get("marketing", "posting", "startzeit", default="08:00")
            )
            console.print(
                f"[dim]Taeglich um {startzeit} starten (einmalig einrichten):[/dim]\n"
                f'  schtasks /create /tn "fbgroups-worker-{campaign_id}" /sc daily '
                f'/st {startzeit} /tr "cmd /c cd /d {Path.cwd()} && '
                f'.venv\\Scripts\\python.exe -m fbgroups.cli campaign worker {campaign_id}"'
            )
            console.print()
            console.print("[dim]--dry-run: es wurde nichts abgesetzt.[/dim]")
            return

        if zustand is not QueueZustand.LAUFEND:
            console.print(
                f"[yellow]Die Warteschlange ist {zustand.value}.[/yellow]\n"
                f"Weiter mit:  fbgroups campaign resume {campaign_id}"
            )
            raise typer.Exit(code=2)

        veroeffentlicher = _baue_veroeffentlicher(adapter)
        bericht = arbeite(
            store,
            campaign,
            gruppen,
            veroeffentlicher,
            grenzen,
            melde=lambda zeile: console.print(f"[dim]{zeile}[/dim]"),
        )
    finally:
        store.close()

    console.print()
    for name, ausgang in bericht.zeilen:
        farbe = "green" if ausgang == "veroeffentlicht" else "dim"
        console.print(f"  [{farbe}]{ausgang:<24}[/{farbe}] {name}")
    console.print()
    console.print(
        f"[green]{bericht.veroeffentlicht}[/green] veroeffentlicht · "
        f"[red]{bericht.fehlgeschlagen}[/red] fehlgeschlagen · "
        f"{bericht.uebersprungen} uebersprungen"
    )
    console.print(f"[dim]Ende: {bericht.grund}[/dim]")


@campaign_app.command("tageslauf")
def campaign_tageslauf(
    campaign_id: str = typer.Argument(...),
    adapter: str = typer.Option("assistiert", "--adapter", help=" | ".join(verfuegbare())),
    trocken: bool = typer.Option(False, "--dry-run", help="Nur zeigen, was liefe."),
) -> None:
    """Der ganze Tag in einem Befehl: nachfuellen, einreihen, abarbeiten.

    Genau das, was die Aufgabenplanung des Systems taeglich aufruft. Die drei
    Schritte stehen bewusst auch einzeln zur Verfuegung (``retry``,
    ``enqueue``, ``worker``) - dieser Befehl ist ihre Reihenfolge, nicht ihr
    Ersatz.

    **Zuerst ``retry``, dann ``enqueue``.** Ein gestern gescheiterter Beitrag
    hat seinen Text und seine Freigabe schon; er gehoert vor die Gruppen, die
    heute zum ersten Mal drankommen. Umgekehrt fuellte die Warteschlange sich
    mit Neuem, und der Fehlschlag von gestern rutschte Tag um Tag nach hinten.

    Eingereiht wird nur so viel, wie heute noch hineinpasst: Das Tageslimit
    zaehlt ueber alle Kampagnen, und was darueber hinaus in der Warteschlange
    stuende, waere eine Liste, die den Tag ueberlebt und morgen die
    Score-Reihenfolge durcheinanderbraechte.
    """
    config = _config()
    grenzen = lade_grenzen(config)
    max_versuche = int(config.get("marketing", "posting", "max_versuche", default=3) or 0)

    gruppen_store, store = _stores(config)
    try:
        campaign = _kampagne_oder_ende(store, campaign_id)
        gruppen = _gruppen_nach_id(gruppen_store)
    finally:
        gruppen_store.close()

    try:
        heute_schon = store.versuche_heute()
        rest = max(0, grenzen.tageslimit - heute_schon)
        bereits_offen = store.job_counts(campaign_id).get(JobStatus.QUEUED.value, 0)

        console.print(f"[bold]Tageslauf {campaign_id}[/bold]")
        console.print(
            f"[dim]Heute schon {heute_schon} von {grenzen.tageslimit} Versuchen · "
            f"{bereits_offen} bereits eingereiht[/dim]\n"
        )

        if rest == 0:
            console.print("[yellow]Tageslimit erreicht - heute geht nichts mehr hinaus.[/yellow]")
            return

        # 1. Gescheiterte von gestern zurueckholen.
        zurueck = 0 if trocken else store.fehlgeschlagene_zuruecksetzen(
            campaign_id, max_versuche=max_versuche
        )
        if zurueck:
            console.print(f"  [green]{zurueck}[/green] fehlgeschlagene zurueckgeholt")

        # 2. Auffuellen - die besten zuerst, hoechstens bis zur Tagesgrenze.
        nachzulegen = max(0, rest - store.job_counts(campaign_id).get(JobStatus.QUEUED.value, 0))
        freigegeben = store.jobs_mit_status(campaign_id, JobStatus.APPROVED)
        geordnet = nach_prioritaet(
            [gruppen[link.group_id] for link in freigegeben if link.group_id in gruppen], config
        )
        reihenfolge = {g.group_id: i for i, g in enumerate(geordnet)}
        freigegeben.sort(key=lambda link: reihenfolge.get(link.group_id, len(reihenfolge)))

        eingereiht = 0
        for link in freigegeben[:nachzulegen]:
            if trocken or _wechsle(store, campaign_id, link.group_id, JobStatus.QUEUED):
                eingereiht += 1
        if eingereiht:
            console.print(f"  [green]{eingereiht}[/green] nach Score eingereiht")

        offen = store.job_counts(campaign_id).get(JobStatus.QUEUED.value, 0)
        console.print(f"  [bold]{offen}[/bold] in der Warteschlange · heute noch {rest} moeglich\n")

        if trocken:
            console.print("[dim]--dry-run: es wurde nichts geaendert und nichts abgesetzt.[/dim]")
            return

        # 3. Abarbeiten.
        zustand = store.queue_zustand(campaign_id)
        if zustand is not QueueZustand.LAUFEND:
            console.print(
                f"[yellow]Die Warteschlange ist {zustand.value} - "
                f"es wird nichts abgesetzt.[/yellow]\n"
                f"Weiter mit:  fbgroups campaign resume {campaign_id}"
            )
            raise typer.Exit(code=2)

        bericht = arbeite(
            store,
            campaign,
            gruppen,
            _baue_veroeffentlicher(adapter),
            grenzen,
            melde=lambda zeile: console.print(f"[dim]{zeile}[/dim]"),
        )
    finally:
        store.close()

    console.print()
    console.print(
        f"[green]{bericht.veroeffentlicht}[/green] veroeffentlicht · "
        f"[red]{bericht.fehlgeschlagen}[/red] fehlgeschlagen · "
        f"{bericht.uebersprungen} uebersprungen"
    )
    console.print(f"[dim]Ende: {bericht.grund}[/dim]")


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
    console.print(
        f"[dim]Weiter mit:  fbgroups campaign enqueue {campaign_id}[/dim]"
    )


@campaign_app.command("zeitplan")
def campaign_zeitplan(
    campaign_id: str = typer.Argument(...),
    einrichten: bool = typer.Option(
        False, "--einrichten", help="Die Aufgabe wirklich anlegen (sonst nur zeigen)."
    ),
    entfernen: bool = typer.Option(False, "--entfernen", help="Die Aufgabe wieder loeschen."),
) -> None:
    """Richtet den taeglichen Lauf in der Aufgabenplanung von Windows ein.

    Der Arbeiter schlaeft **nicht** bis zur Startzeit: Ein Prozess, der
    vierzehn Stunden wartet, ueberlebt keinen Neustart, keine Abmeldung und
    keinen zugeklappten Deckel. Die Aufgabenplanung kann genau das, und sie
    tut es seit Jahrzehnten zuverlaessig - dieser Befehl traegt dort ein, was
    in ``startzeit`` steht.

    Ohne ``--einrichten`` wird der Befehl nur angezeigt. Etwas, das sich
    taeglich von selbst startet, legt man nicht beilaeufig an.
    """
    config = _config()
    startzeit = str(config.get("marketing", "posting", "startzeit", default="08:00"))
    aufgabe = f"fbgroups-tageslauf-{campaign_id}"

    with MarketingStore(config.path("sqlite_path")) as store:
        _kampagne_oder_ende(store, campaign_id)

    if entfernen:
        befehl = ["schtasks", "/delete", "/tn", aufgabe, "/f"]
    else:
        ziel = (
            f'cmd /c cd /d "{Path.cwd()}" && '
            f".venv\\Scripts\\python.exe -m fbgroups.cli campaign tageslauf {campaign_id}"
        )
        befehl = ["schtasks", "/create", "/tn", aufgabe, "/sc", "daily", "/st", startzeit,
                  "/tr", ziel, "/f"]

    if not einrichten and not entfernen:
        console.print(f"[bold]Taeglicher Lauf um {startzeit}[/bold]  ({aufgabe})\n")
        console.print(subprocess.list2cmdline(befehl))
        console.print(
            f"\n[dim]Wirklich einrichten:  fbgroups campaign zeitplan {campaign_id} --einrichten"
            f"\nStartzeit aendern:     marketing.posting.startzeit in config/settings.yaml[/dim]"
        )
        return

    if sys.platform != "win32":
        console.print(
            "[yellow]schtasks gibt es nur unter Windows.[/yellow]\n"
            "Anderswo: cron, systemd-timer oder launchd - der Befehl dahinter ist derselbe:\n"
            f"  fbgroups campaign tageslauf {campaign_id}"
        )
        raise typer.Exit(code=2)

    ergebnis = subprocess.run(befehl, capture_output=True, text=True, check=False)
    if ergebnis.returncode != 0:
        console.print(f"[red]Fehlgeschlagen:[/red] {ergebnis.stderr.strip() or ergebnis.stdout}")
        raise typer.Exit(code=1)
    console.print(
        f"[green]{'Entfernt' if entfernen else f'Eingerichtet - taeglich um {startzeit}'}.[/green]"
    )


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

    arbeit = "in Arbeit" if eintrag.bearbeiten else (
        f"ausgeschlossen ({eintrag.ausschlussgrund})" if eintrag.ausschlussgrund
        else "ausgeschlossen"
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
    table.add_row("Referrals", f"{zahlen['referrals']}  (qualifiziert: "
                               f"{zahlen['referrals_qualified']})")
    table.add_row("Rewards", str(zahlen["rewards"]))
    table.add_row("", "")
    for name, anzahl in sorted(nach_status.items(), key=lambda x: -x[1]):
        table.add_row(name, str(anzahl))
    console.print(table)

    if zahlen["clicks"] == 0:
        console.print(
            "[dim]Noch keine Klicks. Der Dienst, der sie zaehlt, startet mit: "
            "fbgroups serve[/dim]"
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
        gruppen_namen = {
            g.group_id: (g.name or g.group_id) for g in gruppen_store.load_groups()
        }
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
                ["ebene", "schluessel", "name", "clicks", "landing_visits",
                 "registrations", "downloads", "activations", "qualified",
                 "conversions", "conversion_rate"]
            )
            for ebene, zeilen in (("group", gruppen_liste), ("campaign", kampagnen_liste)):
                for zeile in zeilen:
                    writer.writerow(
                        [
                            ebene, zeile.schluessel, zeile.label, zeile.clicks,
                            zeile.landing_visits, zeile.registrations,
                            zeile.downloads, zeile.activations, zeile.qualified,
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
        console.print("[dim]Noch niemand - bisher nur Klicks, und die tragen "
                      "keine Kennung.[/dim]")
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
# KI-Anbieter: Stand und Selbsttest
# ---------------------------------------------------------------------------

ki_app = typer.Typer(help="Lokale KI (Ollama) und der optionale Anthropic-Weg.")


@ki_app.command("status")
def ki_stand() -> None:
    """Zeigt, welcher Anbieter eingestellt ist und ob er antwortet.

    Ruft nichts ab, was Geld kostet: Bei Ollama wird nur nachgesehen, welche
    Modelle dort liegen; bei Anthropic wird ueberhaupt nichts abgerufen,
    sondern nur geprueft, ob Paket und Schluessel da sind.
    """
    config = _config()
    anbieter = gewaehlter_anbieter(config)
    # Ausdruecklich frisch: Wer diesen Befehl tippt, sieht gerade nach und will
    # nicht die Antwort von vor zehn Sekunden - er hat womoeglich genau
    # dazwischen Ollama gestartet.
    stand = ki_status(config, frisch=True)

    table = Table(title="KI-Status", show_header=False, box=None)
    table.add_row("Anbieter", f"{anbieter}" + (" (Standard)" if anbieter == "ollama" else ""))
    table.add_row(
        "Verbindung",
        "[green]verbunden[/green]" if stand.erreichbar else "[red]nicht erreichbar[/red]",
    )
    table.add_row("Modell", stand.modell or "[dim]-[/dim]")
    table.add_row("Adresse", stand.adresse or "[dim]-[/dim]")
    if stand.erreichbar and stand.verfuegbare_modelle:
        table.add_row(
            "Modell liegt vor",
            "[green]ja[/green]" if stand.modell_vorhanden else "[red]nein[/red]",
        )
    console.print(table)

    if stand.verfuegbare_modelle:
        console.print(f"[dim]Vorhanden: {', '.join(stand.verfuegbare_modelle)}[/dim]")
    if stand.meldung:
        farbe = "yellow" if stand.erreichbar else "red"
        console.print(f"[{farbe}]{stand.meldung}[/{farbe}]")

    # Kein Exit-Code ungleich 0: Der Befehl hat seine Auskunft gegeben, auch
    # wenn die Auskunft "laeuft nicht" lautet. Ein Fehlercode waere hier ein
    # Fehler des Befehls, und das ist er nicht.


@ki_app.command("test")
def ki_test(
    anbieter: str = typer.Option("", "--anbieter", help=f"Einer von: {', '.join(ANBIETER)}"),
) -> None:
    """Schickt eine sehr kurze echte Anfrage - einen Testsatz auf Arabisch.

    Anders als ``ki status`` wird hier wirklich etwas erzeugt. Bei Ollama
    kostet das nichts ausser Sekunden; bei Anthropic ein paar Cent. Der Test
    beantwortet in einem Zug drei Fragen: Laeuft der Dienst, liegt das Modell
    vor, und gibt es arabische Schrift sauber aus.
    """
    config = _config()
    name = anbieter or gewaehlter_anbieter(config)
    console.print(f"[dim]Frage '{name}' ...[/dim]")

    geklappt, text = ki_teste(config, anbieter)
    if geklappt:
        console.print(f"[green]OK[/green] {name} funktioniert.\n")
        console.print(text)
        return

    console.print(f"[red]Fehlgeschlagen[/red] - {name} hat nicht geantwortet.\n")
    console.print(text)
    raise typer.Exit(code=1)


@ki_app.command("modelle")
def ki_modelle() -> None:
    """Listet die Modelle, die bei Ollama tatsaechlich vorliegen."""
    config = _config()
    stand = ki_status(config, "ollama")

    if not stand.erreichbar:
        console.print(f"[red]{stand.meldung}[/red]")
        raise typer.Exit(code=1)
    if not stand.verfuegbare_modelle:
        console.print(
            "Ollama laeuft, aber es liegt kein Modell vor.\n"
            f"[dim]Holen mit:  ollama pull {stand.modell}[/dim]"
        )
        return

    table = Table(title=f"Modelle auf {stand.adresse}")
    table.add_column("Modell")
    table.add_column("eingestellt", justify="center")
    eingestellt = stand.modell.split(":")[0]
    for name in stand.verfuegbare_modelle:
        marke = "[green]<--[/green]" if name.split(":")[0] == eingestellt else ""
        table.add_row(name, marke)
    console.print(table)


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
