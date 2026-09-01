"""Der Treiber der Kommentarautomatik - hier trifft der Plan auf den Browser.

``lauf.py`` entscheidet, **was** als naechstes kommt, und kennt weder Netz
noch Playwright. Dieses Modul fuehrt es aus: Es oeffnet **einen** Browser fuer
den ganzen Lauf, holt sich Schritt fuer Schritt den naechsten Auftrag und
meldet jeden Ausgang ueber ``arbeit.melde_vorschlag`` - denselben Weg, den
auch die Arbeitsseite nimmt. Eine zweite Buchungsart daneben waere eine zweite
Zaehlweise fuer dieselben Kommentare.

## Ein Browser fuer den ganzen Lauf

``campaign auto`` oeffnet je Aufruf einen eigenen Browser. Fuer einen Lauf
ueber vierzig Gruppen waeren das vierzig Starts, jeder mit Profilaufbau und
Anmeldepruefung - und vierzig Fenster, die auf- und zugehen. Der Lauf haelt
den Kontext deshalb offen und gibt ihn erst am Ende zurueck.

## Warum die Datenbank zwischendurch zugeht

Ein Schritt dauert Minuten. Waehrend der Browser arbeitet, ist keine
Verbindung offen - dieselbe Aufteilung wie in ``campaign auto`` (lesen,
handeln, schreiben). Sonst hielte ein Lauf ueber Stunden eine Sperre auf einer
Datei, die zugleich die Weiterleitung bedient.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console

from fbgroups.config import AppConfig
from fbgroups.marketing import kaltmodus, lauf
from fbgroups.marketing.models import (
    CampaignStatus,
    KampagnenLaufStatus,
    LaufStatus,
)
from fbgroups.marketing.store import MarketingStore
from fbgroups.storage import SqliteStore

console = Console()


@dataclass(frozen=True)
class Schrittergebnis:
    """Was ein einzelner Kommentarversuch ergeben hat."""

    erfolg: bool
    fehler: str = ""
    post_url: str = ""
    # Die Gruppe gibt nichts mehr her - kein Fehlschlag, sondern ein Ende.
    # Der Unterschied entscheidet, ob wiederholt oder weitergegangen wird.
    erschoepft: bool = False


def aktive_kampagnen(store: MarketingStore) -> list[str]:
    """Die Kampagnen, die ein Lauf abarbeiten wuerde - in fester Reihenfolge.

    ``active`` und nichts anderes: Ein Entwurf ist nicht in Betrieb, eine
    pausierte Kampagne ist ausdruecklich angehalten, eine abgeschlossene ist
    fertig. Sortiert nach ``created_at``, damit zwei Laeufe dieselbe Folge
    ergeben - bei gleicher Zeit entscheidet die Kennung.
    """
    kampagnen = store.load_campaigns(CampaignStatus.ACTIVE)
    return [k.campaign_id for k in sorted(kampagnen, key=lambda k: (k.created_at, k.campaign_id))]


def hole_oder_starte_lauf(
    store: MarketingStore, *, ziel_je_gruppe: int, nur: list[str] | None = None
) -> tuple[int, bool]:
    """``(lauf_id, neu)`` - einen offenen Lauf fortsetzen oder einen neuen einfrieren.

    Das ist Punkt 17: Wird der Vorgang waehrend Kampagne 3 unterbrochen, setzt
    der naechste Start dort auf, statt Kampagne 1 noch einmal zu fahren. Und
    es ist Punkt 16: Ein **laufender** Lauf behaelt seine Liste; eine Kampagne,
    die inzwischen auf ``active`` gesetzt wurde, greift nicht in ihn ein.

    ``nur`` schraenkt die einzufrierende Liste auf bestimmte Kampagnen ein und
    wirkt **ausschliesslich beim Anlegen**. Geschnitten wird gegen die aktiven:
    Eine pausierte Kampagne kommt auch dann nicht dran, wenn sie ausdruecklich
    genannt wird - sonst waere "pausiert" eine Beschriftung ohne Wirkung.
    """
    offen = store.offener_lauf()
    if offen is not None:
        return int(offen["lauf_id"]), False

    kampagnen = aktive_kampagnen(store)
    if nur:
        gewuenscht = set(nur)
        kampagnen = [cid for cid in kampagnen if cid in gewuenscht]
    return store.starte_lauf(kampagnen, ziel_je_gruppe=ziel_je_gruppe), True


def _gruppen(pfad: Path) -> dict:
    with SqliteStore(pfad) as gruppen_store:
        return {g.group_id: g for g in gruppen_store.load_groups()}


def fuehre_lauf_aus(
    config: AppConfig,
    *,
    ausfuehren: Callable[[str, str, str], Schrittergebnis],
    max_schritte: int = 0,
    trocken: bool = False,
) -> lauf.Lauffortschritt:
    """Arbeitet den Lauf ab, bis nichts mehr offen ist.

    ``ausfuehren`` bekommt (Gruppen-URL, Gruppen-ID, Text) und liefert ein
    ``Schrittergebnis``. Der **Browserkontext gehoert dem Aufrufer**: Er
    oeffnet ihn einmal fuer den ganzen Lauf und bindet ihn in diese Funktion
    ein. Dadurch steht in diesem Modul keine Zeile Playwright, und der Test
    reicht eine Funktion herein, die zaehlt statt zu kommentieren.

    ``max_schritte`` begrenzt einen Lauf (0 = ohne Grenze); ``trocken`` zeigt
    nur, was geschaehe.
    """
    pfad = config.path("sqlite_path")
    aktiv, _pro_tag, abstand = kaltmodus.einstellungen(config)

    with MarketingStore(pfad) as store:
        lauf_id, neu = hole_oder_starte_lauf(store, ziel_je_gruppe=lauf.ZIEL_JE_GRUPPE)
        if neu:
            console.print(f"[cyan]Neuer Lauf {lauf_id}[/cyan]")
        else:
            console.print(f"[cyan]Lauf {lauf_id} wird fortgesetzt[/cyan]")

    gruppen = _gruppen(pfad)
    getan = 0

    while True:
        # 1. LESEN: Stand holen, naechsten Schritt bestimmen.
        with MarketingStore(pfad) as store:
            fortschritt = lauf.lies_fortschritt(store, lauf_id, gruppen)
            schritt = lauf.naechster_schritt(fortschritt)

            if schritt is None:
                # Kein Schritt heisst nicht "fertig": Vielleicht hat die
                # naechste Gruppe alle Fassungen verbraucht, ohne voll zu
                # werden. Dann ist sie erschoepft, und danach geht es weiter.
                if _erschoepfung_eintragen(store, fortschritt):
                    continue
                break

            vorschlag = store.vorschlag(
                schritt.campaign_id, schritt.group_id, schritt.texttyp, schritt.nummer
            )
            if vorschlag is None or not vorschlag.text.strip():
                store.setze_kommentar_erschoepft(
                    schritt.campaign_id, schritt.group_id, "kein Kommentartext vorhanden"
                )
                continue

            campaign = store.load_campaign(schritt.campaign_id)
            link = store.link_for(schritt.campaign_id, schritt.group_id)
            if campaign is None or link is None:
                break

            from fbgroups.marketing.beitrag import mit_link

            text = mit_link(
                campaign,
                link,
                vorschlag.text,
                config=config,
                ziel=lauf.ziel_zu_nummer(schritt.nummer),
            )
            wartezeit = _wartezeit(store, aktiv, abstand)

        gruppe = gruppen.get(schritt.group_id)
        if gruppe is None or not gruppe.url_canonical:
            with MarketingStore(pfad) as store:
                store.setze_kommentar_erschoepft(
                    schritt.campaign_id, schritt.group_id, "keine Gruppen-URL"
                )
            continue

        console.print(
            f"[bold]{schritt.gruppe_name}[/bold] - Kommentar "
            f"{schritt.kommentar_nr}/{schritt.kommentar_ziel} (Fassung {schritt.nummer})"
        )

        if trocken:
            console.print("[dim]  --dry-run: nichts wird abgesetzt[/dim]")
            break

        if wartezeit:
            console.print(f"[yellow]Abstandsregel: {wartezeit}[/yellow]")
            break

        # 2. HANDELN: Der Browser ist dran, die Datenbank ist zu.
        ergebnis = ausfuehren(gruppe.url_canonical, schritt.group_id, text)

        # 3. SCHREIBEN: Ausgang buchen - ueber denselben Weg wie die Arbeitsseite.
        with MarketingStore(pfad) as store:
            _buche(store, campaign, link, schritt, ergebnis)
            store.setze_lauf_status(lauf_id, LaufStatus.LAEUFT.value)

        getan += 1
        if max_schritte and getan >= max_schritte:
            console.print(f"[dim]Grenze von {max_schritte} Schritten erreicht.[/dim]")
            break

    # Abschluss: Der Zustand wird aus dem Stand abgeleitet, nicht behauptet.
    with MarketingStore(pfad) as store:
        fortschritt = lauf.lies_fortschritt(store, lauf_id, gruppen)
        _stand_fortschreiben(store, lauf_id, fortschritt)
        return lauf.lies_fortschritt(store, lauf_id, gruppen)


def _wartezeit(store: MarketingStore, aktiv: bool, abstand: int) -> str:
    """Der Kaltmodus gilt auch hier - er ist der Takt, nicht eine Sperre der Hand."""
    if not aktiv:
        return ""
    jetzt = datetime.now(UTC)
    letzter = store.letzter_versuch()
    if not letzter:
        return ""
    frei_ab = kaltmodus.naechster_zeitpunkt(
        datetime.fromisoformat(letzter),
        abstand_minuten=abstand,
        jetzt=jetzt,
        erledigt_heute=store.versuche_heute(jetzt.date().isoformat()),
    )
    return kaltmodus.wartezeit_text(frei_ab, jetzt=jetzt)


def _erschoepfung_eintragen(store: MarketingStore, fortschritt: lauf.Lauffortschritt) -> bool:
    """Traegt fuer die naechste haengende Gruppe die Erschoepfung ein.

    Returns: ob etwas eingetragen wurde - dann lohnt ein weiterer Durchgang.
    Ohne diesen Schritt bliebe der Lauf an einer Gruppe stehen, die keine
    Fassung mehr offen hat, aber ihr Ziel nie erreicht.
    """
    kampagne = fortschritt.naechste_kampagne
    if kampagne is None:
        return False
    gruppe = kampagne.naechste_gruppe
    if gruppe is None or not lauf.gruppe_ist_erschoepft(gruppe):
        return False
    store.setze_kommentar_erschoepft(
        gruppe.campaign_id,
        gruppe.group_id,
        f"nur {gruppe.veroeffentlicht} von {gruppe.ziel} Kommentaren moeglich",
    )
    console.print(
        f"[yellow]{gruppe.name}: erschoepft "
        f"({gruppe.veroeffentlicht}/{gruppe.ziel})[/yellow]"
    )
    return True


def _buche(store: MarketingStore, campaign, link, schritt: lauf.Schritt, ergebnis) -> None:
    """Den Ausgang eintragen - ueber ``arbeit.melde_vorschlag``, wie sonst auch."""
    from fbgroups.marketing.arbeit import Ergebnis, melde_vorschlag

    melde_vorschlag(
        store,
        campaign,
        link,
        schritt.texttyp,
        schritt.nummer,
        Ergebnis(
            erfolg=ergebnis.erfolg,
            fehler="" if ergebnis.erfolg else (ergebnis.fehler or "ohne Angabe"),
            post_url=ergebnis.post_url,
        ),
        ausgeloest_von="automatik",
        sitzung="auto",
    )
    if ergebnis.erfolg:
        console.print("[green]  veroeffentlicht[/green]")
    else:
        console.print(f"[red]  fehlgeschlagen: {ergebnis.fehler}[/red]")
    if ergebnis.erschoepft:
        store.setze_kommentar_erschoepft(
            schritt.campaign_id, schritt.group_id, ergebnis.fehler or "keine Beitraege mehr"
        )


def _stand_fortschreiben(
    store: MarketingStore, lauf_id: int, fortschritt: lauf.Lauffortschritt
) -> None:
    """Schreibt die abgeleiteten Zustaende zurueck - Kampagnen und Lauf.

    Hier steht Punkt 8: ``fertig`` wird **nicht** gesetzt, weil eine Gruppe
    durch ist, sondern weil ``Lauffortschritt.fertig`` es sagt - und das
    verlangt jede Gruppe jeder Kampagne. Die Bedingung steht an genau einer
    Stelle (``lauf.py``), damit sie nicht an zweien auseinanderlaufen kann.
    """
    for kampagne in fortschritt.kampagnen:
        neu = (
            KampagnenLaufStatus.FERTIG
            if kampagne.fertig
            else (
                KampagnenLaufStatus.LAEUFT
                if kampagne is fortschritt.naechste_kampagne
                else KampagnenLaufStatus.WARTET
            )
        )
        if neu is not kampagne.status:
            store.setze_lauf_kampagne_status(lauf_id, kampagne.campaign_id, neu.value)

        # Eine vollstaendig abgearbeitete Kampagne wird auch dauerhaft als
        # abgeschlossen vermerkt - sonst taucht sie im naechsten Lauf wieder
        # auf, obwohl es nichts mehr zu tun gibt.
        if kampagne.fertig:
            gespeichert = store.load_campaign(kampagne.campaign_id)
            if gespeichert is not None and gespeichert.status is CampaignStatus.ACTIVE:
                gespeichert.status = CampaignStatus.COMPLETED
                store.save_campaign(gespeichert)

    store.setze_lauf_status(
        lauf_id,
        LaufStatus.FERTIG.value if fortschritt.fertig else LaufStatus.ANGEHALTEN.value,
        meldung=lauf.abschlusstext(fortschritt),
    )


def browser_schritt(
    context, config: AppConfig, gruppen_url: str, group_id: str, text: str
) -> Schrittergebnis:
    """Ein Kommentar im Browser: Beitraege lesen, den besten waehlen, kommentieren.

    Gelesen werden nur Kennzahlen (Rueckmeldungen, Kommentarzahl) und die
    Beitrags-URL - nie ein Beitragstext, nie ein Name. Die Auswahl faellt auf
    den lebendigsten Beitrag, der noch keinen Kommentar von uns traegt;
    ``bisherige_post_urls`` haelt fest, welche das sind.

    ``limit=10`` statt der fuenf aus ``campaign auto``: Fuenf Kommentare
    brauchen fuenf **verschiedene** Beitraege, und schon der zweite Durchgang
    faende sonst nichts Neues mehr.
    """
    from fbgroups.automation.actions import comment_on_post, fetch_top_posts
    from fbgroups.models import GroupPost

    roh = fetch_top_posts(context, gruppen_url, group_id, limit=10)
    if not roh:
        return Schrittergebnis(
            erfolg=False, fehler="keine Beitraege zum Kommentieren gefunden", erschoepft=True
        )

    posts = [
        GroupPost(
            group_id=group_id,
            post_url=p["post_url"],
            interactions=p["interactions"],
            comments=p["comments"],
        )
        for p in roh
    ]
    return waehle_und_kommentiere(
        context, config, posts, group_id, text, kommentieren=comment_on_post
    )


def waehle_und_kommentiere(
    context, config: AppConfig, posts, group_id: str, text: str, *, kommentieren
) -> Schrittergebnis:
    """Den besten noch unkommentierten Beitrag nehmen.

    Eigene Funktion, weil hier die Auswahlregel steht - sie laesst sich damit
    ohne Browser pruefen, indem ``kommentieren`` durch eine Zaehlfunktion
    ersetzt wird.
    """
    pfad = config.path("sqlite_path")
    with SqliteStore(pfad) as gruppen_store:
        gruppen_store.upsert_group_posts(group_id, posts)
    with MarketingStore(pfad) as store:
        bisherige = store.bisherige_post_urls(group_id)

    offen = [p for p in posts if p.post_url not in bisherige]
    if not offen:
        return Schrittergebnis(
            erfolg=False,
            fehler="alle sichtbaren Beitraege sind bereits kommentiert",
            erschoepft=True,
        )

    bester = max(offen, key=lambda p: p.interactions + p.comments)
    erfolg = kommentieren(context, bester.post_url, text)
    return Schrittergebnis(
        erfolg=erfolg,
        fehler="" if erfolg else "Kommentarfeld nicht gefunden oder blockiert",
        post_url=bester.post_url,
    )


def fuehre_lauf_fern_aus(
    basis_url: str,
    *,
    ausfuehren: Callable[[str, str, str, list[str]], Schrittergebnis],
    max_schritte: int = 0,
    zeitlimit: float = 300.0,
    nur: list[str] | None = None,
) -> str:
    """Denselben Lauf fahren, aber **auf dem Bestand des Servers**.

    Der Unterschied zu ``fuehre_lauf_aus`` ist keine Feinheit, sondern der
    ganze Zweck: Dort liest und bucht die Automatik in der Datei, in der sie
    laeuft - auf dem Arbeitsrechner also in einer Kopie. Der Kommentar ging
    hinaus, gebucht wurde daneben, und der Server bot dieselbe Gruppe weiter
    als offen an. Am 31.08.2026 stand deshalb ein Beitrag lokal auf
    ``veroeffentlicht`` und auf dem Server auf ``offen``.

    Hier faellt das weg: Der Server sagt, was zu tun ist, und nimmt das
    Ergebnis entgegen. Dieser Rechner haelt **keinen** Stand - er steuert den
    Browser und meldet zurueck. Eine Wahrheit, und sie liegt dort, wo auch
    die Klicks gezaehlt werden.

    Returns: die Abschlussmeldung des Servers.
    """
    import httpx

    basis = basis_url.rstrip("/")
    getan = 0
    letzte_meldung = "Kein Lauf."

    # Ein Ursprung, den der Dienst als oertlich annimmt - ``_nur_lokal``
    # prueft neben der Adresse auch die Herkunft der Seite.
    kopf = {"Origin": basis, "Content-Type": "application/json"}

    with httpx.Client(timeout=zeitlimit, headers=kopf) as klient:
        while True:
            antwort = klient.post(
                f"{basis}/automatik/naechster", json={"kampagnen": list(nur or [])}
            )
            if antwort.status_code == 404:
                raise RuntimeError(
                    "Der Dienst haelt den Aufruf fuer nicht-oertlich. Laeuft der "
                    "SSH-Tunnel, und zeigt --server auf 127.0.0.1?"
                )
            antwort.raise_for_status()
            daten = antwort.json()

            if daten.get("schritt") is None:
                # ``weiter`` heisst: Der Server hat etwas geklaert (eine
                # erschoepfte Gruppe) und hat gleich den naechsten Schritt.
                if daten.get("weiter"):
                    continue
                letzte_meldung = daten.get("meldung", "Nichts mehr zu tun.")
                break

            s = daten["schritt"]
            console.print(
                f"[bold]{s['gruppe_name']}[/bold] - Kommentar "
                f"{s['kommentar_nr']}/{s['kommentar_ziel']} (Fassung {s['nummer']})"
            )
            if daten.get("fortschritt"):
                console.print(f"[dim]{daten['fortschritt'].splitlines()[2].strip()}[/dim]")

            ergebnis = ausfuehren(
                s["gruppen_url"], s["group_id"], s["text"], s["bisherige_post_urls"]
            )

            klient.post(
                f"{basis}/automatik/ergebnis",
                json={
                    "campaign_id": s["campaign_id"],
                    "group_id": s["group_id"],
                    "nummer": s["nummer"],
                    "erfolg": ergebnis.erfolg,
                    "fehler": ergebnis.fehler,
                    "post_url": ergebnis.post_url,
                    "erschoepft": ergebnis.erschoepft,
                },
            ).raise_for_status()

            if ergebnis.erfolg:
                console.print("[green]  veroeffentlicht (auf dem Server gebucht)[/green]")
            else:
                console.print(f"[red]  fehlgeschlagen: {ergebnis.fehler}[/red]")

            getan += 1
            if max_schritte and getan >= max_schritte:
                console.print(f"[dim]Grenze von {max_schritte} Schritten erreicht.[/dim]")
                letzte_meldung = f"{getan} Schritt(e) ausgefuehrt, Grenze erreicht."
                break

    return letzte_meldung


def browser_schritt_fern(
    context, gruppen_url: str, group_id: str, text: str, bisherige: list[str]
) -> Schrittergebnis:
    """Wie ``browser_schritt``, aber ohne jeden Datenbankzugriff.

    ``bisherige`` kommt vom Server mit; auf diesem Rechner steht kein Bestand,
    der befragt werden koennte - und genau das ist beabsichtigt.
    """
    from fbgroups.automation.actions import comment_on_post, fetch_top_posts

    roh = fetch_top_posts(context, gruppen_url, group_id, limit=10)
    if not roh:
        return Schrittergebnis(
            erfolg=False, fehler="keine Beitraege zum Kommentieren gefunden", erschoepft=True
        )

    schon = set(bisherige)
    offen = [p for p in roh if p["post_url"] not in schon]
    if not offen:
        return Schrittergebnis(
            erfolg=False,
            fehler="alle sichtbaren Beitraege sind bereits kommentiert",
            erschoepft=True,
        )

    bester = max(offen, key=lambda p: p["interactions"] + p["comments"])
    erfolg = comment_on_post(context, bester["post_url"], text)
    return Schrittergebnis(
        erfolg=erfolg,
        fehler="" if erfolg else "Kommentarfeld nicht gefunden oder blockiert",
        post_url=bester["post_url"],
    )


__all__ = [
    "Schrittergebnis",
    "aktive_kampagnen",
    "browser_schritt",
    "browser_schritt_fern",
    "fuehre_lauf_aus",
    "fuehre_lauf_fern_aus",
    "hole_oder_starte_lauf",
    "waehle_und_kommentiere",
]
