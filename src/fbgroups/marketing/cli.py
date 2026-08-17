"""Kommandozeile der Marketing-Erweiterung.

Zwei Unterbefehle, die an die bestehende Anwendung angehaengt werden:

    fbgroups campaign ...    Kampagnen anlegen, Gruppen zuordnen, Links holen
    fbgroups marketing ...   Arbeitsstand je Gruppe pflegen

Kein Befehl dieses Moduls ruft facebook.com auf, veroeffentlicht etwas oder
verschickt etwas. ``campaign message`` gibt die Textvorlage aus - abschicken
muss sie ein Mensch.
"""

from __future__ import annotations

import csv
import re
from datetime import UTC, date, datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from fbgroups.config import AppConfig, load_config
from fbgroups.marketing.analytics import funnel, kennzahlen, top_campaigns, top_groups
from fbgroups.marketing.models import (
    MARKETING_FORTSCHRITT,
    Campaign,
    CampaignGroup,
    CampaignParticipation,
    CampaignStatus,
    ContactStatus,
    GroupMarketing,
    MarketingStatus,
    PermissionStatus,
    ReferralStatus,
    RewardStatus,
)
from fbgroups.marketing.referral import code_fuer_benutzer, setze_status
from fbgroups.marketing.rewards import bewerte_benutzer, fortschritt, load_reward_rules
from fbgroups.marketing.store import MarketingStore, UnknownGroupError
from fbgroups.marketing.tracking import (
    app_base_url,
    app_base_url_quelle,
    ist_lokale_basis,
    next_tracking_code,
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
    """Aus "Batreeq Syrian Germany" wird "batreeq-syrian-germany"."""
    klein = re.sub(r"[^a-z0-9]+", "-", text.lower().strip())
    return klein.strip("-") or "kampagne"


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


def _links_tabelle(links: list[CampaignGroup], namen: dict[str, Group]) -> None:
    table = Table(title="Tracking-Links")
    table.add_column("Tracking-Code")
    table.add_column("Gruppe")
    table.add_column("Stadt")
    table.add_column("Link")

    for link in links:
        group = namen.get(link.group_id)
        table.add_row(
            link.tracking_code,
            ((group.name if group else "") or link.group_id)[:34],
            (group.city if group else None) or "[dim]-[/dim]",
            link.tracking_url or "[dim](keine APP_BASE_URL gesetzt)[/dim]",
        )
    console.print(table)


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


@campaign_app.command("add-groups")
def campaign_add_groups(
    campaign_id: str = typer.Argument(...),
    top: int = typer.Option(0, "--top", help="Nur die besten N Gruppen (0 = alle passenden)."),
    city: list[str] = typer.Option(None, "--stadt", help="Nur diese Staedte (Kennung)."),
    audience: list[str] = typer.Option(None, "--zielgruppe", help="Nur diese Zielgruppen."),
    min_score: float = typer.Option(0.0, "--min-score"),
    alle_gruppen: bool = typer.Option(
        False, "--auch-unbewertete", help="Auch Gruppen ohne Score zuordnen."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Nur zeigen, nichts speichern."),
) -> None:
    """Ordnet Gruppen zu und vergibt je Paar einen festen Tracking-Code.

    Die Auswahl folgt der Kampagne: Ohne ``--stadt``/``--zielgruppe`` gelten
    die in der Kampagne hinterlegten Werte. Bereits zugeordnete Gruppen bleiben
    unveraendert - ihr Code steht moeglicherweise schon in einem Beitrag.
    """
    config = _config()
    gruppen_store, store = _stores(config)

    try:
        campaign = _kampagne_oder_ende(store, campaign_id)

        staedte = {c.lower() for c in (city or campaign.cities)}
        zielgruppen = {a.lower() for a in (audience or campaign.audiences)}
        stadt_namen = {
            c.name_de.lower() for cid, c in config.cities.items() if cid.lower() in staedte
        }

        kandidaten = sort_by_rank(gruppen_store.load_groups())
        passend = [
            g
            for g in kandidaten
            if (g.score is not None or alle_gruppen)
            and (g.score or 0.0) >= min_score
            and (not stadt_namen or (g.city or "").lower() in stadt_namen)
            and (not zielgruppen or zielgruppen & {t.lower() for t in g.audience_tags})
        ]
        if top > 0:
            passend = passend[:top]

        vergeben = store.assigned_codes(campaign_id)
        alle_codes = store.assigned_codes()
        neu: list[tuple[Group, CampaignGroup]] = []
        bekannt = 0

        for group in passend:
            vorhanden = store.link_for(campaign_id, group.group_id)
            if vorhanden is not None:
                bekannt += 1
                continue

            code = next_tracking_code(group, config, vergeben | alle_codes)
            link = CampaignGroup(
                campaign_id=campaign_id,
                group_id=group.group_id,
                tracking_code=code,
                tracking_url=tracking_url(code, config),
            )
            vergeben.add(code)
            alle_codes.add(code)
            neu.append((group, link))

        if not dry_run:
            for _group, link in neu:
                try:
                    store.add_link(link)
                except UnknownGroupError:
                    console.print(
                        f"[yellow]Uebersprungen (nicht im Bestand):[/yellow] {link.group_id}"
                    )

        namen = {g.group_id: g for g, _ in neu}
        if neu:
            _links_tabelle([link for _g, link in neu], namen)

        hinweis = "[cyan]--dry-run:[/cyan] nichts gespeichert. " if dry_run else ""
        console.print(
            f"{hinweis}[green]{len(neu)}[/green] Gruppen neu zugeordnet, "
            f"{bekannt} waren es bereits (Codes unveraendert). "
            f"Kampagne {campaign.campaign_id}."
        )
        if not app_base_url(config):
            console.print(
                "[yellow]Hinweis:[/yellow] APP_BASE_URL ist nicht gesetzt - die Codes stehen, "
                "die Links bleiben leer.\nSetzen in .env oder config/settings.yaml, danach: "
                f"[bold]fbgroups campaign refresh-urls {campaign_id}[/bold]"
            )
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

    text = (campaign.message_template or "").replace("{link}", link.tracking_url)
    text = text.replace("{tracking_code}", link.tracking_code)
    text = text.replace("{landing_page}", campaign.landing_page)

    console.print(
        Panel(
            text or "[dim](keine Vorlage hinterlegt)[/dim]",
            title=f"{campaign.name} - {link.tracking_code}",
            subtitle="von Hand einfuegen und absenden",
        )
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
) -> None:
    """Pflegt den Arbeitsstand einer Gruppe. Nicht genannte Felder bleiben."""
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
        eintrag.updated_at = datetime.now(UTC)

        store.save_marketing(eintrag)
    finally:
        gruppen_store.close()
        store.close()

    console.print(
        f"[green]{group_id}:[/green] {eintrag.marketing_status.value} / "
        f"Kontakt {eintrag.contact_status.value} / Erlaubnis {eintrag.permission_status.value}"
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
        table.add_column("Qualified", justify="right")
        table.add_column("Conversions", justify="right")
        table.add_column("Rate", justify="right")
        for zeile in zeilen:
            rate = zeile.conversion_rate
            table.add_row(
                zeile.label[:38],
                str(zeile.clicks),
                str(zeile.registrations),
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
                ["ebene", "schluessel", "name", "clicks", "registrations",
                 "qualified", "conversions", "conversion_rate"]
            )
            for ebene, zeilen in (("group", gruppen_liste), ("campaign", kampagnen_liste)):
                for zeile in zeilen:
                    writer.writerow(
                        [
                            ebene, zeile.schluessel, zeile.label, zeile.clicks,
                            zeile.registrations, zeile.qualified, zeile.conversions,
                            zeile.conversion_rate if zeile.conversion_rate is not None else "",
                        ]
                    )
        console.print(f"[green]CSV:[/green] {output}")


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
