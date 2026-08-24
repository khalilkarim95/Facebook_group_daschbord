"""Redirect-Dienst, Meldeschnittstelle und Uebersichtsseite.

``GET  /``           Uebersicht ueber den Bestand - localhost, oder lesend
                     ueber nginx mit Passwort (``UEBERSICHT_TOKEN``)
``GET  /r/{code}``   Klick zaehlen und zur Landingpage weiterleiten
``POST /events``     die Zielanwendung meldet, was danach passiert ist

Der Dienst spricht **nicht** mit facebook.com und veroeffentlicht nichts. Er
sieht nur die Leute, die einen unserer Links anklicken.

Datensparsamkeit ist eingebaut: Es werden weder IP-Adressen noch Kopfzeilen
gespeichert. Zur Erkennung doppelter Klicks entsteht daraus ein taeglich
wechselnder Pruefwert, der nicht zurueckrechenbar ist. Wer sich registriert,
erscheint nur als undurchsichtige Kennung aus der Zielanwendung.

FastAPI ist eine **optionale** Abhaengigkeit:

    pip install -e ".[web]"

Ohne sie laeuft alles Uebrige unveraendert weiter; nur ``fbgroups serve``
meldet dann, was fehlt.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlparse

from pydantic import BaseModel, Field

from fbgroups.config import AppConfig, load_config
from fbgroups.marketing.arbeit import Sperre, hole_auftrag, melde_ergebnis
from fbgroups.marketing.arbeitsseite import render_auftrag, render_sperre
from fbgroups.marketing.dashboard import (
    _beitrag_gesamtstand,
    regel_kurzfassung,
    render,
    sammle_daten,
    status_label,
)
from fbgroups.marketing.models import (
    EINMAL_JE_MENSCH,
    Campaign,
    CampaignStatus,
    EventType,
    JobStatus,
    MarketingStatus,
    PostStatus,
    QueueZustand,
    ReferralStatus,
    TrackingEvent,
)
from fbgroups.marketing.referral import code_fuer_benutzer, lege_empfehlung_an, setze_status
from fbgroups.marketing.rewards import bewerte_benutzer, load_reward_rules
from fbgroups.marketing.selection import auswahl_der_kampagne, baue_plan, synchronisiere
from fbgroups.marketing.store import MarketingStore
from fbgroups.marketing.tracking import slug
from fbgroups.marketing.veroeffentlicher import Ergebnis
from fbgroups.marketing.worker import lade_grenzen
from fbgroups.models import RecordStatus
from fbgroups.storage import SqliteStore

# Optionale Abhaengigkeit, aber auf Modulebene importiert: Wegen
# ``from __future__ import annotations`` sind Typangaben Zeichenketten, die
# FastAPI erst beim Bauen der Wege aufloest. Stuenden die Namen nur innerhalb
# von ``create_app``, waere ``Request`` dort unbekannt - der Dienst haette
# jeden Aufruf mit 422 beantwortet.
try:
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

    FASTAPI_VERFUEGBAR = True
except ImportError:  # pragma: no cover - haengt von der Installation ab
    FASTAPI_VERFUEGBAR = False

SALT_SCHLUESSEL = "visitor_salt"

# Absenderadressen, die als "derselbe Rechner" gelten. Der Dienst steht
# oeffentlich - die Tracking-Links zeigen auf ihn -, die Arbeitsliste darf aber
# nicht mit heraus. Es gibt keine Anmeldung, also entscheidet die Herkunft.
# Sie stammt aus der Verbindung selbst, nicht aus einer Kopfzeile, und ist
# deshalb nicht vorzutaeuschen. ``testclient`` vergibt Starlettes Testclient
# im selben Prozess - von aussen kann diese Adresse nicht auftreten.
LOKALE_ADRESSEN = {"127.0.0.1", "::1", "localhost", "testclient"}

# Welches Ereignis welchen Empfehlungsstand ergibt. Steht hier, damit die
# Zuordnung an einer Stelle nachlesbar ist.
_REFERRAL_STUFE = {
    EventType.REGISTRATION: ReferralStatus.REGISTERED,
    EventType.QUALIFIED: ReferralStatus.QUALIFIED,
    EventType.CONVERSION: ReferralStatus.CONVERTED,
}


class StandMeldung(BaseModel):
    """Ein Mensch traegt nach, was er bei Facebook getan hat.

    Bewusst genau zwei Felder: Der Weg pflegt den Arbeitsstand, sonst nichts.
    Weder Bewertung noch Klassifikation lassen sich darueber veraendern - die
    entstehen aus der Suche und gehoeren nicht in eine Handeingabe.
    """

    group_id: str = Field(max_length=128)
    status: MarketingStatus


class KampagneNeu(BaseModel):
    """Eine neue Kampagne - ohne dass dabei ein einziger Code entsteht.

    Anlegen und Zuordnen sind bewusst getrennt. Ein Tracking-Code ist
    endgueltig: Er steht spaeter in veroeffentlichten Beitraegen und wird nie
    zurueckgenommen. Ein Formular, das beim Speichern still 400 Codes vergibt,
    waere ein Knopf mit unumkehrbarer Wirkung. Die Kampagne beginnt deshalb als
    ``draft``, ohne Zuordnungen und mit ``auto_assign`` aus.
    """

    name: str = Field(min_length=1, max_length=120)
    campaign_id: str = Field(default="", max_length=64)
    description: str = Field(default="", max_length=500)
    audiences: list[str] = Field(default_factory=list, max_length=50)
    cities: list[str] = Field(default_factory=list, max_length=50)
    language: str = Field(default="", max_length=16)
    message_template: str = Field(default="", max_length=2000)
    landing_page: str = Field(default="", max_length=300)


class KampagneStatusMeldung(BaseModel):
    status: CampaignStatus


class QueueMeldung(BaseModel):
    """Welcher Zustand der Warteschlange gesetzt werden soll.

    Ein eigenes Modell statt eines freien Textes: ``pausiert`` und ``gestoppt``
    unterscheiden sich darin, was mit den eingereihten Jobs geschieht, und ein
    Tippfehler duerfte nicht als das eine oder das andere durchgehen.
    """

    zustand: QueueZustand


class SyncMeldung(BaseModel):
    """``dry_run`` ist die Vorgabe - gezeigt wird erst, gehandelt danach."""

    dry_run: bool = True


class AuswahlMeldung(BaseModel):
    """Die Auswahlregel einer Kampagne: **welche Gruppen** sie erfasst.

    Nicht zu verwechseln mit ``audiences``/``cities`` der Kampagne - die
    beschreiben, *wen* sie bewirbt. Beides war frueher dasselbe Feld; dadurch
    liess sich eine Kampagne nicht auf den ganzen Bestand weiten, ohne ihre
    fachliche Beschreibung zu verfaelschen.

    ``None`` heisst **unveraendert**, die leere Liste heisst **keine
    Einschraenkung**. Die Unterscheidung ist noetig, weil das Formular nur
    Zielgruppen und Staedte zeigt: Ohne sie loeschte jedes Speichern die auf
    der Kommandozeile gesetzte Kategorie- oder Statusregel gleich mit.

    ``min_score`` unter 0 hebt den Mindestscore auf - dieselbe Vereinbarung wie
    ``campaign target --min-score -1``. Ein Mindestscore unter 0 ist fachlich
    sinnlos und damit als "aufheben" eindeutig.
    """

    audiences: list[str] | None = Field(default=None, max_length=100)
    cities: list[str] | None = Field(default=None, max_length=100)
    categories: list[str] | None = Field(default=None, max_length=100)
    statuses: list[str] | None = Field(default=None, max_length=20)
    min_score: float | None = Field(default=None, ge=-1, le=100)
    include_unscored: bool | None = None
    auto_assign: bool | None = None


class BearbeitenMeldung(BaseModel):
    """Ob an diesen Gruppen gearbeitet wird - eine oder viele auf einmal.

    Eigene Meldung neben ``StandMeldung``, weil es zwei Fragen sind: Wo stehen
    wir mit dieser Gruppe (``marketing_status``), und arbeiten wir ueberhaupt
    an ihr. In einem Feld vermischt, loeschte das Ausschliessen die Angabe,
    dass man in der Gruppe bereits Mitglied ist - beim Wiederaufnehmen finge
    man von vorn an.

    Mengenweise, weil einzeln unbrauchbar: Beim ersten vollen Bestand waren
    144 von 413 Datensaetzen ohne verwertbare Daten. Das auszusortieren ist ein
    Zug, keine 144 Klicks.
    """

    group_ids: list[str] = Field(min_length=1, max_length=2000)
    bearbeiten: bool
    grund: str = Field(default="", max_length=200)


class BeitragMeldung(BaseModel):
    """Was beim Beitrag in einer Gruppe herauskam.

    Nur die beiden Ausgaenge, die ein Klick in der Uebersicht erzeugen kann.
    ``uebersprungen`` fehlt bewusst: Das ist ein Urteil ueber die Gruppe und
    gehoert an die Stelle, an der man sie ohnehin betrachtet - die
    Kommandozeile (``campaign posted --ueberspringen``). Ein Knopf mehr in der
    Zeile machte die haeufige Handlung schwerer zu treffen.
    """

    campaign_id: str = Field(min_length=1, max_length=64)
    group_id: str = Field(min_length=1, max_length=64)
    status: PostStatus
    grund: str = Field(default="", max_length=200)


class EventMeldung(BaseModel):
    """Was die Zielanwendung meldet.

    ``user_ref`` ist eine undurchsichtige Kennung aus der Zielanwendung. Namen,
    E-Mail-Adressen oder Telefonnummern gehoeren nicht hierher und werden auch
    nicht gespeichert, wenn sie faelschlich mitgeschickt wuerden - das Modell
    kennt schlicht keine solchen Felder.

    ``anon_ref`` ist die Kennung, unter der derselbe Mensch **vorher** anonym
    unterwegs war - die, die sich die Web-App im Browser gibt, bevor es ein
    Konto gibt. Sie ist der Grund, warum die Zuordnung den Uebergang zum
    angemeldeten Benutzer ueberlebt: Der erste Besuch traegt sie, die
    Registrierung traegt beide, alles danach nur noch ``user_ref``. Wer sie
    weglaesst, verliert die Gruppe genau an dieser Stelle.

    Vor der Anmeldung darf ``anon_ref`` auch allein stehen: Ein Download ohne
    Konto ist ein gueltiger Fall und wird ueber sie zugeordnet.
    """

    event_type: EventType
    user_ref: str = Field(default="", max_length=128)
    anon_ref: str = Field(default="", max_length=128)
    tracking_code: str = Field(default="", max_length=64)
    referral_code: str = Field(default="", max_length=64)
    occurred_at: datetime | None = None


def _events_token() -> str:
    """Gemeinsames Geheimnis fuer ``POST /events`` - aus der Umgebung.

    Leer heisst: keine Pruefung. Das ist der Entwicklungsfall und der Grund,
    warum die Tests ohne Einrichtung laufen; im Betrieb gehoert der Weg dann
    hinter einen Proxy, der nur den eigenen Rechner durchlaesst.

    Mit Schluessel ist der Weg von aussen benutzbar - und das ist der Punkt:
    Die Zielanwendung laeuft in einem Container und erreicht ``127.0.0.1`` des
    Wirts gar nicht. Die Alternative waere, den Dienst zusaetzlich an das
    Docker-Gateway zu binden; dessen Adresse wechselt aber, sobald das
    Compose-Netz neu entsteht, und offen waere er dann fuer jeden Container.

    Der Weg braucht den Schutz unabhaengig davon: Er schreibt Registrierungen,
    Empfehlungen und damit Praemien. Ohne Pruefung koennte jeder Praemien
    ausloesen.
    """
    return os.environ.get("EVENTS_TOKEN", "").strip()


def _uebersicht_token() -> str:
    """Geheimnis, mit dem nginx eine bestandene Passwortpruefung bezeugt.

    Die Uebersicht bleibt an ``127.0.0.1`` gebunden. Wer sie von aussen sehen
    will, kommt ueber einen nginx-Block mit ``auth_basic``, der diese Kopfzeile
    setzt - und der Dienst zeigt sie dann **schreibgeschuetzt**.

    Der Wert muss geheim sein, obwohl ``proxy_set_header`` eine vom Besucher
    mitgeschickte Kopfzeile ueberschreibt: Sonst haengt der Schutz daran, dass
    jeder kuenftige Block das Ueberschreiben nicht vergisst - auch der, den in
    zwei Jahren jemand anders schreibt. Ein Weg, der Auskunft gibt, bringt
    seinen Schutz besser selbst mit, als ihn von einer Datei nebenan zu borgen.

    Leer heisst: kein Zugang von aussen. Das ist die Vorgabe und der Fall, in
    dem die Tests ohne Einrichtung laufen.
    """
    return os.environ.get("UEBERSICHT_TOKEN", "").strip()


def _visitor_hash(store: MarketingStore, ip: str, user_agent: str) -> str:
    """Taeglich wechselnder Pruefwert statt gespeicherter IP-Adresse.

    Der Zufallsschluessel entsteht einmal und bleibt in der Datenbank. Durch
    das Tagesdatum im Wert laesst sich ein Besucher nicht ueber Tage hinweg
    verfolgen - genau so viel, wie das Aussortieren doppelter Klicks braucht.
    """
    salt = store.meta(SALT_SCHLUESSEL)
    if not salt:
        salt = secrets.token_hex(16)
        store.set_meta(SALT_SCHLUESSEL, salt)

    roh = f"{ip}|{user_agent}|{date.today().isoformat()}"
    return hmac.new(salt.encode(), roh.encode(), hashlib.sha256).hexdigest()[:16]


# User-Agents, die eine Linkvorschau erzeugen, statt dass ein Mensch klickt:
# Facebook selbst beim Posten, WhatsApp/Telegram/Slack & Co. beim Weiterleiten
# oder Anzeigen der Karte (Titel, Bild, Beschreibung). Kein Anspruch auf
# Vollstaendigkeit - neue Muster werden ergaenzt, sobald sie im nginx-Protokoll
# auffallen.
_VORSCHAU_USER_AGENTS = (
    "facebookexternalhit",
    "facebookcatalog",
    "whatsapp",
    "telegrambot",
    "slackbot",
    "slack-imgproxy",
    "discordbot",
    "linkedinbot",
    "twitterbot",
    "skypeuripreview",
    "vkshare",
)


def _ist_linkvorschau(user_agent: str) -> bool:
    """Erkennt einen automatischen Vorschau-Abruf statt eines Klicks.

    Ein frisch geposteter Link wird von der Plattform selbst abgerufen, um die
    Vorschaukarte zu bauen - oft von mehreren IPs und mehrfach ueber die Zeit
    verteilt. Ohne diese Erkennung zaehlte jeder dieser Abrufe als eigener
    Klick: Ein einzelner Livetest zeigte 25 Klicks fuer einen einzigen
    Menschen, alle mit demselben ``facebookexternalhit``-User-Agent.
    """
    ua = user_agent.lower()
    return any(muster in ua for muster in _VORSCHAU_USER_AGENTS)


def _ziel_url(store: MarketingStore, tracking_code: str, config: AppConfig) -> str:
    """Wohin ein Klick fuehrt: Landingpage der Kampagne, sonst Ausweichziel."""
    link = store.resolve_code(tracking_code)
    if link is not None:
        campaign = store.load_campaign(link.campaign_id)
        if campaign and campaign.landing_page:
            return campaign.landing_page
    return str(config.get("marketing", "fallback_url", default="")) or "/"


def create_app(config: AppConfig | None = None, db_path: Path | None = None) -> Any:
    """Baut die FastAPI-Anwendung.

    ``db_path`` erlaubt den Test gegen eine eigene Datenbank, ohne den
    Bestand anzufassen.
    """
    if not FASTAPI_VERFUEGBAR:  # pragma: no cover - haengt von der Installation ab
        raise RuntimeError('FastAPI fehlt. Installieren mit:  pip install -e ".[web]"')

    cfg = config or load_config()
    pfad = db_path or cfg.path("sqlite_path")

    app = FastAPI(
        title="fbgroups Tracking",
        description=(
            "Zaehlt Klicks auf Tracking-Links und nimmt Meldungen der "
            "Zielanwendung entgegen. Kein Zugriff auf facebook.com."
        ),
        version="1.0",
    )

    def _store() -> MarketingStore:
        return MarketingStore(pfad)

    def _pruefe_token(request: Request) -> None:
        """Prueft ``X-Events-Token`` - fuer die Wege, die die Zielanwendung ruft.

        Leerer Schluessel heisst: keine Pruefung. Das ist der Entwicklungsfall
        und der Grund, warum die Tests ohne Einrichtung laufen. Im Betrieb
        steht der Schluessel in ``/opt/fbgroups/app/.env``.

        401 statt 404: Anders als bei der Uebersicht ist hier nichts zu
        verbergen - die Gegenstelle ist eine Anwendung und soll den
        Unterschied zwischen "falscher Schluessel" und "Weg gibt es nicht"
        sehen koennen.
        """
        erwartet = _events_token()
        if not erwartet:
            return
        # Zeitkonstanter Vergleich: Ein frueher Abbruch bei der ersten
        # abweichenden Stelle verraet ueber viele Versuche den Schluessel.
        gesendet = request.headers.get("x-events-token", "")
        if not hmac.compare_digest(gesendet, erwartet):
            raise HTTPException(status_code=401, detail="Ungueltiger Schluessel")

    def _nur_lokal(request: Request) -> None:
        """Laesst nur Aufrufe vom selben Rechner durch - und keine fremde Seite.

        Zwei Pruefungen, weil sie zwei verschiedene Faelle abdecken:

        1. **Absenderadresse.** Sie stammt aus der Verbindung, nicht aus einer
           Kopfzeile, und ist deshalb nicht vorzutaeuschen. Der Dienst darf
           oeffentlich stehen, damit die Tracking-Links funktionieren; die
           Arbeitsliste bleibt trotzdem auf diesem Rechner.
        2. **Herkunft der Seite.** Ohne sie koennte jede beliebige Webseite,
           die du im Browser offen hast, an ``localhost`` schreiben - der
           Browser saesse ja auf demselben Rechner. Der Weg nimmt ausserdem
           nur JSON entgegen; ein einfaches Formular von aussen scheitert damit
           schon an der Vorabanfrage, die dieser Dienst nicht beantwortet.

        404 statt 403: Wer den Dienst oeffentlich stellt, soll nicht nebenbei
        verraten, dass es hier eine Arbeitsliste gibt.
        """
        absender = request.client.host if request.client else ""
        if absender not in LOKALE_ADRESSEN:
            raise HTTPException(status_code=404, detail="Not Found")

        herkunft = request.headers.get("origin", "")
        if herkunft and urlparse(herkunft).hostname not in LOKALE_ADRESSEN:
            raise HTTPException(status_code=404, detail="Not Found")

    def _pruefe_uebersicht(request: Request) -> bool:
        """Laesst die Uebersicht durch und sagt, ob sie schreibgeschuetzt ist.

        Zwei Zugaenge mit verschiedenen Rechten:

        * **vom selben Rechner** (SSH-Tunnel): die volle Seite, Aenderungen
          moeglich.
        * **ueber nginx mit Passwort**: dieselben Zahlen, aber nur lesend.

        Der Unterschied ist Absicht, kein Rest. Die schreibenden Wege vergeben
        Tracking-Codes, und ein vergebener Code wird nie zurueckgenommen - er
        steht spaeter in veroeffentlichten Beitraegen. Ein abhandengekommenes
        Passwort soll Zahlen zeigen koennen, aber nicht mit einem Klick 400
        Codes vergeben. Wer aendern will, baut den Tunnel auf; das ist ein
        Handgriff und keine taegliche Huerde.

        Rueckgabe: ``True``, wenn die Seite schreibgeschuetzt zu bauen ist.
        """
        absender = request.client.host if request.client else ""
        herkunft = request.headers.get("origin", "")
        lokal = absender in LOKALE_ADRESSEN and (
            not herkunft or urlparse(herkunft).hostname in LOKALE_ADRESSEN
        )
        if lokal:
            return False

        erwartet = _uebersicht_token()
        # Zeitkonstant, aus demselben Grund wie bei _pruefe_token.
        gesendet = request.headers.get("x-uebersicht-token", "")
        if erwartet and hmac.compare_digest(gesendet, erwartet):
            return True

        # 404 wie bisher: Wer den Dienst oeffentlich stellt, soll nicht
        # nebenbei verraten, dass es hier eine Arbeitsliste gibt.
        raise HTTPException(status_code=404, detail="Not Found")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):  # noqa: ANN202
        """Uebersicht ueber den Bestand - vom selben Rechner oder lesend.

        Ohne einen der beiden Zugaenge ist der Weg nicht vorhanden (404, nicht
        403): Wer den Dienst oeffentlich stellt, soll damit nicht nebenbei
        verraten, dass es hier ueberhaupt eine Arbeitsliste gibt.

        Die schreibenden Wege darunter pruefen weiterhin mit ``_nur_lokal``.
        Der Schreibschutz der Seite ist damit nicht die Absicherung, sondern
        ihre sichtbare Entsprechung.
        """
        nur_lesen = _pruefe_uebersicht(request)
        return HTMLResponse(render(sammle_daten(cfg, pfad), nur_lesen=nur_lesen))

    @app.get("/arbeit/{campaign_id}", response_class=HTMLResponse)
    def arbeit(campaign_id: str, request: Request):  # noqa: ANN202
        """Ein Beitrag, ein Bildschirm - die Arbeitsliste auf dem Server.

        Der Bestand lebt hier, aber ``campaign worker`` braucht Zwischenablage
        und Browser, die es auf einem Server nicht gibt. Beides auf den
        Arbeitsrechner zu holen hiesse, in eine zweite Datenbank zu schreiben.
        Also kommt die Arbeit dorthin, wo der Bestand steht: Der Server bereitet
        vor und zaehlt, der Browser des Menschen kopiert und oeffnet.

        ``_nur_lokal`` wie jeder schreibende Weg - der Aufruf **beginnt** einen
        Versuch (``processing`` plus Protokollzeile) und ist damit kein Lesen.
        """
        _nur_lokal(request)
        with SqliteStore(pfad) as gruppen_store:
            gruppen = {g.group_id: g for g in gruppen_store.load_groups()}
        with _store() as store:
            campaign = store.load_campaign(campaign_id)
            if campaign is None:
                raise HTTPException(status_code=404, detail="Unbekannte Kampagne")
            ergebnis = hole_auftrag(
                store,
                campaign,
                gruppen,
                lade_grenzen(cfg),
                ausgeloest_von="uebersicht",
                sitzung="browser",
            )
        if isinstance(ergebnis, Sperre):
            return HTMLResponse(render_sperre(ergebnis, campaign_id))
        return HTMLResponse(render_auftrag(ergebnis, campaign_id))

    @app.post("/arbeit/{campaign_id}/ergebnis")
    async def arbeit_ergebnis(campaign_id: str, request: Request):  # noqa: ANN202
        """Traegt den Ausgang ein und schickt weiter zur naechsten Gruppe.

        Ein Formular statt JSON: Wer hier arbeitet, hat gerade in einem anderen
        Reiter einen Beitrag abgesetzt und kommt mit einem Klick zurueck. Die
        Weiterleitung nach dem POST ist Absicht (303) - ein Neuladen soll den
        Ausgang nicht ein zweites Mal melden.

        Der Text kommt **nicht** aus dem Formular zurueck. Was zurueckkommt,
        ist der Ausgang und die ``versuch_id``; der Beitrag selbst hat den
        Server nur in eine Richtung verlassen.
        """
        _nur_lokal(request)
        # ``request.form()`` verlangt ``python-multipart``. Das Formular hier
        # traegt vier kurze Textfelder und keine Datei - dafuer genuegt
        # ``parse_qsl`` aus der Standardbibliothek. Ein Paket mehr waere fuer
        # vier Felder zu viel, und ``[web]`` soll klein bleiben.
        rumpf = (await request.body()).decode("utf-8", errors="replace")
        formular = dict(parse_qsl(rumpf, keep_blank_values=True))
        ausgang = formular.get("ausgang", "")
        group_id = formular.get("group_id", "")
        fehler = formular.get("fehler", "").strip()
        try:
            versuch_id = int(formular.get("versuch_id", "0"))
        except ValueError:
            versuch_id = 0

        if not group_id or not versuch_id:
            raise HTTPException(status_code=400, detail="Unvollstaendige Meldung")

        ergebnis = {
            "veroeffentlicht": Ergebnis(erfolg=True),
            "fehlgeschlagen": Ergebnis(erfolg=False, fehler=fehler or "ohne Angabe"),
            "uebersprungen": Ergebnis(erfolg=False, uebersprungen=True),
            "schluss": Ergebnis(erfolg=False, abbrechen=True),
        }.get(ausgang)
        if ergebnis is None:
            raise HTTPException(status_code=400, detail=f"Unbekannter Ausgang: {ausgang}")

        with _store() as store:
            if store.load_campaign(campaign_id) is None:
                raise HTTPException(status_code=404, detail="Unbekannte Kampagne")
            melde_ergebnis(store, campaign_id, group_id, versuch_id, ergebnis)
            store.audit("beitrag_" + ausgang, f"{campaign_id}/{group_id}", fehler)

        # Nach "Schluss" zurueck zur Uebersicht: Der naechste Auftrag laege
        # sonst sofort wieder auf dem Bildschirm, und "Schluss" haette nichts
        # bewirkt.
        ziel = "/" if ausgang == "schluss" else f"/arbeit/{campaign_id}"
        return RedirectResponse(ziel, status_code=303)

    @app.post("/stand")
    def stand_setzen(meldung: StandMeldung, request: Request):  # noqa: ANN202
        """Traegt den Arbeitsstand einer Gruppe ein - derselbe Weg wie die CLI.

        Facebook meldet nicht, dass eine Beitrittsanfrage gestellt wurde, und
        dieser Dienst fragt dort nichts ab. Der Stand kann deshalb nur von dem
        Menschen kommen, der die Anfrage geschickt hat; hier braucht es dafuer
        einen Klick statt eines Befehls.

        Anders als der Sammelbefehl ``marketing beitritt`` darf dieser Weg auch
        zurueckstufen: Es ist eine einzelne, ausdrueckliche Angabe zu einer
        einzelnen Gruppe - genau die Handarbeit, auf die der Sammelbefehl
        verweist.
        """
        _nur_lokal(request)

        with SqliteStore(pfad) as gruppen_store:
            bekannt = {g.group_id for g in gruppen_store.load_groups()}
        if meldung.group_id not in bekannt:
            raise HTTPException(status_code=404, detail="Unbekannte Gruppe")

        jetzt = datetime.now(UTC)
        with _store() as store:
            eintrag = store.load_marketing(meldung.group_id)
            eintrag.marketing_status = meldung.status
            # Zeitpunkte nur ergaenzen, nie ueberschreiben: Wann die Anfrage
            # gestellt wurde, weiss nur der erste Eintrag - ein spaeterer
            # Korrekturklick darf das Datum nicht auf heute ziehen.
            if meldung.status is MarketingStatus.JOIN_REQUESTED and not eintrag.join_requested_at:
                eintrag.join_requested_at = jetzt
            if meldung.status is MarketingStatus.CONTACTED and not eintrag.last_contacted_at:
                eintrag.last_contacted_at = jetzt
            eintrag.updated_at = jetzt
            store.save_marketing(eintrag)
            store.audit("stand_gesetzt", meldung.group_id, meldung.status.value)

        return JSONResponse(
            {"status": meldung.status.value, "label": status_label(meldung.status.value)}
        )

    @app.post("/beitrag")
    def beitrag_eintragen(meldung: BeitragMeldung, request: Request):  # noqa: ANN202
        """Traegt ein, dass ein Beitrag steht - oder warum nicht.

        Wie ``/stand`` ein Weg fuer den Menschen, der es gerade getan hat:
        Dieser Dienst sieht Facebook nicht und kann nicht wissen, ob ein
        Beitrag veroeffentlicht wurde. Er glaubt, was der Mensch ihm sagt.

        Nur von diesem Rechner - die Uebersicht von aussen ist schreibgeschuetzt.
        """
        _nur_lokal(request)

        if meldung.status not in (PostStatus.VEROEFFENTLICHT, PostStatus.FEHLGESCHLAGEN):
            raise HTTPException(status_code=422, detail="Nur veroeffentlicht oder fehlgeschlagen")

        with _store() as store:
            link = store.set_post_status(
                meldung.campaign_id, meldung.group_id, meldung.status, meldung.grund
            )
            if link is None:
                raise HTTPException(status_code=404, detail="Unbekannte Zuordnung")

            store.audit(
                "beitrag_" + meldung.status.value, meldung.group_id, link.tracking_code
            )
            # Der Gesamtstand der Gruppe aus derselben Funktion wie die
            # Uebersicht - eine zweite Fassung im JavaScript koennte abweichen,
            # und die Zeile zeigte dann etwas anderes als der Filter.
            alle = store.links_for_group(meldung.group_id)
            gesamtstand = _beitrag_gesamtstand(
                [{"status": eintrag.post_status.value} for eintrag in alle]
            )

        return JSONResponse(
            {
                "status": link.post_status.value,
                "fehler": link.post_error,
                "versuche": link.post_attempts,
                "gesamtstand": gesamtstand,
            }
        )

    @app.post("/kampagnen")
    def kampagne_anlegen(meldung: KampagneNeu, request: Request):  # noqa: ANN202
        """Legt eine Kampagne an - als Entwurf, ohne Zuordnungen.

        Die Auswahlregel startet als Abbild der Beschreibung: Wer eine Kampagne
        fuer syrische Zielgruppen in Berlin anlegt, erfasst zunaechst genau
        diese Gruppen. Eine leere Regel hiesse "keine Einschraenkung" und damit
        der gesamte Bestand - das mag richtig sein, ist aber eine Entscheidung
        und keine Vorgabe fuer ein frisch ausgefuelltes Formular.
        """
        _nur_lokal(request)

        kennung = slug(meldung.campaign_id) or slug(meldung.name)
        if not kennung:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Aus diesem Namen entsteht keine Kennung - bitte eine angeben "
                    "(Buchstaben a-z und Ziffern)."
                ),
            )

        unbekannt = [a for a in meldung.audiences if a not in cfg.audiences]
        unbekannt += [c for c in meldung.cities if c not in cfg.cities]
        if unbekannt:
            raise HTTPException(
                status_code=422,
                detail=f"Unbekannt in der Konfiguration: {', '.join(unbekannt)}",
            )

        with _store() as store:
            if store.load_campaign(kennung) is not None:
                raise HTTPException(
                    status_code=409, detail=f"Es gibt bereits eine Kampagne '{kennung}'."
                )
            store.save_campaign(
                Campaign(
                    campaign_id=kennung,
                    name=meldung.name,
                    description=meldung.description,
                    audiences=list(meldung.audiences),
                    cities=list(meldung.cities),
                    language=meldung.language,
                    message_template=meldung.message_template,
                    landing_page=meldung.landing_page,
                    target_audiences=list(meldung.audiences),
                    target_cities=list(meldung.cities),
                )
            )
            store.audit("kampagne_angelegt", kennung, meldung.name)

        return JSONResponse(
            {"campaign_id": kennung, "status": CampaignStatus.DRAFT.value}, status_code=201
        )

    @app.post("/kampagnen/{campaign_id}/status")
    def kampagne_status(  # noqa: ANN202
        campaign_id: str, meldung: KampagneStatusMeldung, request: Request
    ):
        """Setzt den Status. Vergebene Codes bleiben in jedem Fall gueltig.

        Eine pausierte oder beendete Kampagne nimmt keine neuen Gruppen mehr
        auf - aber ihre Links funktionieren weiter. Sie stehen in Beitraegen,
        die niemand zurueckholt.
        """
        _nur_lokal(request)
        with _store() as store:
            campaign = store.load_campaign(campaign_id)
            if campaign is None:
                raise HTTPException(status_code=404, detail="Unbekannte Kampagne")
            campaign.status = meldung.status
            store.save_campaign(campaign)
            store.audit("kampagne_status", campaign_id, meldung.status.value)
        return JSONResponse({"campaign_id": campaign_id, "status": meldung.status.value})

    @app.post("/kampagnen/{campaign_id}/queue")
    def kampagne_queue(  # noqa: ANN202
        campaign_id: str, meldung: QueueMeldung, request: Request
    ):
        """Haelt die Warteschlange an, laesst sie weiterlaufen oder raeumt sie.

        Wirkt **waehrend** ein Arbeiter laeuft: Er liest den Zustand vor jedem
        Beitrag und nach jeder Wartezeit frisch aus der Datenbank. Genau dafuer
        gibt es diesen Weg - wer einen Lauf anhalten will, sitzt selten vor dem
        Fenster, in dem er gestartet wurde.

        ``gestoppt`` raeumt zusaetzlich die Warteschlange: Alles, was noch
        nicht angefangen wurde, geht auf ``approved`` zurueck. Die Antwort
        nennt deshalb, wie viele Jobs das betraf - "gestoppt" allein liesse
        offen, ob gerade 3 oder 300 Beitraege zurueckgestellt wurden.
        """
        _nur_lokal(request)
        with _store() as store:
            if store.load_campaign(campaign_id) is None:
                raise HTTPException(status_code=404, detail="Unbekannte Kampagne")
            zurueckgestellt = store.set_queue_zustand(campaign_id, meldung.zustand)
            store.audit("queue_zustand", campaign_id, meldung.zustand.value)
            zaehler = store.job_counts(campaign_id)
        return JSONResponse(
            {
                "campaign_id": campaign_id,
                "zustand": meldung.zustand.value,
                "zurueckgestellt": zurueckgestellt,
                "eingereiht": zaehler.get(JobStatus.QUEUED.value, 0),
            }
        )

    @app.post("/kampagnen/{campaign_id}/auswahl")
    def kampagne_auswahl(  # noqa: ANN202
        campaign_id: str, meldung: AuswahlMeldung, request: Request
    ):
        """Setzt die Auswahlregel - und rechnet sofort vor, was sie bedeutet.

        Speichern vergibt **keinen** Code. Die Regel sagt nur, welche Gruppen
        in Frage kommen; die Codes entstehen erst durch "Zuordnen", und dort
        wird noch einmal gefragt. Ein Tracking-Code ist endgueltig - er steht
        spaeter in veroeffentlichten Beitraegen.

        Die Antwort enthaelt denselben Plan, den auch ``sync`` ausfuehren
        wuerde (``selection.baue_plan``). Eine zweite Zaehlung koennte davon
        abweichen, und der Mensch bestaetigte dann eine Zahl und bekaeme eine
        andere.
        """
        _nur_lokal(request)

        kategorien = {k.id for k in cfg.categories}
        zustaende = {s.value for s in RecordStatus}
        unbekannt = [a for a in (meldung.audiences or []) if a not in cfg.audiences]
        unbekannt += [c for c in (meldung.cities or []) if c not in cfg.cities]
        unbekannt += [k for k in (meldung.categories or []) if k not in kategorien]
        unbekannt += [s for s in (meldung.statuses or []) if s not in zustaende]
        if unbekannt:
            raise HTTPException(
                status_code=422,
                detail=f"Unbekannt in der Konfiguration: {', '.join(unbekannt)}",
            )

        with SqliteStore(pfad) as gruppen_store:
            groups = gruppen_store.load_groups()

        with _store() as store:
            campaign = store.load_campaign(campaign_id)
            if campaign is None:
                raise HTTPException(status_code=404, detail="Unbekannte Kampagne")

            # None laesst das Feld stehen - das Formular schickt nur, was es
            # auch zeigt.
            if meldung.audiences is not None:
                campaign.target_audiences = list(meldung.audiences)
            if meldung.cities is not None:
                campaign.target_cities = list(meldung.cities)
            if meldung.categories is not None:
                campaign.target_categories = list(meldung.categories)
            if meldung.statuses is not None:
                campaign.target_statuses = list(meldung.statuses)
            if meldung.min_score is not None:
                campaign.target_min_score = None if meldung.min_score < 0 else meldung.min_score
            if meldung.include_unscored is not None:
                campaign.target_include_unscored = meldung.include_unscored
            if meldung.auto_assign is not None:
                campaign.auto_assign = meldung.auto_assign

            campaign.updated_at = datetime.now(UTC)
            store.save_campaign(campaign)

            regel = auswahl_der_kampagne(campaign, cfg)
            store.audit("kampagne_auswahl", campaign_id, regel.beschreibung())

            plan = baue_plan(
                groups,
                campaign,
                cfg,
                vorhandene_gruppen=store.assigned_group_ids(campaign_id),
                vergebene_codes=store.assigned_codes(),
                auswahl=regel,
            )

        return JSONResponse(
            {
                "campaign_id": campaign_id,
                "beschreibung": regel.beschreibung(),
                "kurz": regel_kurzfassung(regel),
                "passend": plan.anzahl_neu + plan.bereits_zugeordnet,
                "bestand": len(groups),
                "neu": plan.anzahl_neu,
                "bereits_zugeordnet": plan.bereits_zugeordnet,
                "nicht_mehr_passend": len(plan.nicht_mehr_passend),
                "auto_assign": campaign.auto_assign,
                "regel": {
                    "audiences": campaign.target_audiences,
                    "cities": campaign.target_cities,
                    "categories": campaign.target_categories,
                    "statuses": campaign.target_statuses,
                    "min_score": campaign.target_min_score,
                    "include_unscored": campaign.target_include_unscored,
                    "auto_assign": campaign.auto_assign,
                },
            }
        )

    @app.post("/kampagnen/{campaign_id}/sync")
    def kampagne_sync(campaign_id: str, meldung: SyncMeldung, request: Request):  # noqa: ANN202
        """Wendet die Auswahlregel an - erst als Vorschau, dann im Ernstfall.

        Beide Wege lesen denselben Plan aus ``selection.baue_plan``. Eine
        zweite Zaehlung koennte von der Ausfuehrung abweichen und damit eine
        falsche Zahl versprechen - bei einer unumkehrbaren Vergabe der
        schlechteste Fehler.
        """
        _nur_lokal(request)
        with SqliteStore(pfad) as gruppen_store:
            groups = gruppen_store.load_groups()

        with _store() as store:
            campaign = store.load_campaign(campaign_id)
            if campaign is None:
                raise HTTPException(status_code=404, detail="Unbekannte Kampagne")

            if meldung.dry_run:
                plan = baue_plan(
                    groups,
                    campaign,
                    cfg,
                    vorhandene_gruppen=store.assigned_group_ids(campaign_id),
                    vergebene_codes=store.assigned_codes(),
                )
            else:
                plan = synchronisiere(store, groups, campaign, cfg)

        return JSONResponse(
            {
                "campaign_id": campaign_id,
                "dry_run": meldung.dry_run,
                "neu": plan.anzahl_neu,
                "bereits_zugeordnet": plan.bereits_zugeordnet,
                "nicht_mehr_passend": len(plan.nicht_mehr_passend),
                "beispiele": [link.tracking_code for _g, link in plan.neu[:5]],
            }
        )

    @app.post("/bearbeiten")
    def bearbeiten_setzen(meldung: BearbeitenMeldung, request: Request):  # noqa: ANN202
        """Nimmt Gruppen in die Arbeitsliste auf oder schliesst sie aus.

        Der Tracking-Code bleibt dabei unangetastet gueltig. Ein Ausschluss ist
        eine Entscheidung ueber die eigene Arbeit, kein Widerruf des Codes -
        der steht moeglicherweise schon in einem veroeffentlichten Beitrag, und
        ein Klick darauf muss weiter gezaehlt werden und ankommen.
        """
        _nur_lokal(request)

        with SqliteStore(pfad) as gruppen_store:
            bekannt = {g.group_id for g in gruppen_store.load_groups()}
        fremd = [gid for gid in meldung.group_ids if gid not in bekannt]
        if fremd:
            raise HTTPException(status_code=404, detail=f"Unbekannte Gruppe: {fremd[0]}")

        jetzt = datetime.now(UTC)
        with _store() as store:
            for group_id in meldung.group_ids:
                eintrag = store.load_marketing(group_id)
                eintrag.bearbeiten = meldung.bearbeiten
                # Der Grund gehoert zum Ausschluss. Bei der Wiederaufnahme
                # faellt er weg - sonst stuende bei einer bearbeiteten Gruppe
                # eine Begruendung, warum sie nicht bearbeitet wird.
                eintrag.ausschlussgrund = "" if meldung.bearbeiten else meldung.grund
                eintrag.updated_at = jetzt
                store.save_marketing(eintrag)

            # Eine Zeile je Vorgang, nicht je Gruppe: Ein Sammelausschluss ist
            # eine Entscheidung. 144 Protokollzeilen daraus zu machen, machte
            # das Protokoll unlesbar, ohne mehr zu sagen.
            store.audit(
                "bearbeiten_gesetzt",
                f"{len(meldung.group_ids)} Gruppen",
                "aufgenommen" if meldung.bearbeiten else f"ausgeschlossen: {meldung.grund}",
            )

        return JSONResponse(
            {"anzahl": len(meldung.group_ids), "bearbeiten": meldung.bearbeiten,
             "grund": "" if meldung.bearbeiten else meldung.grund}
        )

    @app.post("/ki/test")
    def ki_testen(request: Request):  # noqa: ANN202
        """Schickt eine sehr kurze echte Anfrage an den eingestellten Anbieter.

        Hinter ``_nur_lokal`` wie jeder schreibende Weg: Der Aufruf erzeugt
        wirklich etwas - bei Ollama Sekunden Rechenzeit, bei Anthropic Geld.
        Ein Weg, den jeder von aussen ausloesen koennte, waere bei einem
        lokalen Modell eine Einladung, den Rechner lahmzulegen.

        Antwortet immer mit 200 und einem ``ok``-Feld, nie mit einem
        Fehlercode: Der Aufruf hat seine Auskunft gegeben, auch wenn die
        Auskunft "laeuft nicht" lautet. Ein 500 saehe aus wie ein Fehler des
        Dienstes und nicht wie ein abgeschaltetes Ollama.
        """
        _nur_lokal(request)
        from fbgroups.marketing.ki import teste as ki_teste

        geklappt, text = ki_teste(cfg)
        return JSONResponse({"ok": geklappt, "text": text})

    @app.get("/r/{tracking_code}")
    def redirect(tracking_code: str, request: Request):  # noqa: ANN202
        """Zaehlt den Klick und leitet weiter.

        Ein unbekannter Code wird nicht stillschweigend weitergeleitet: Er
        deutet auf einen Tippfehler oder einen veralteten Beitrag hin, und ein
        stiller Umweg wuerde das verschleiern.
        """
        with _store() as store:
            link = store.resolve_code(tracking_code)
            if link is None:
                store.audit("klick_unbekannter_code", tracking_code)
                raise HTTPException(status_code=404, detail="Unbekannter Tracking-Code")

            user_agent = request.headers.get("user-agent", "")
            if not _ist_linkvorschau(user_agent):
                besucher = _visitor_hash(
                    store,
                    request.client.host if request.client else "",
                    user_agent,
                )
                if not store.klick_bereits_gezaehlt(tracking_code, besucher):
                    store.record_event(
                        TrackingEvent(
                            tracking_code=tracking_code,
                            campaign_id=link.campaign_id,
                            group_id=link.group_id,
                            event_type=EventType.CLICK,
                            visitor_hash=besucher,
                            source="redirect",
                        )
                    )
            ziel = _ziel_url(store, tracking_code, cfg)

        # 302, nicht 301: Ein dauerhaft gemerkter Umzug wuerde spaetere Klicks
        # am Zaehler vorbeifuehren.
        trenner = "&" if "?" in ziel else "?"
        return RedirectResponse(url=f"{ziel}{trenner}ref={tracking_code}", status_code=302)

    @app.post("/events")
    def melde_ereignis(meldung: EventMeldung, request: Request):  # noqa: ANN202
        """Nimmt ein Ereignis der Zielanwendung entgegen.

        **Jede Stufe steht fuer sich.** Kein Ereignis setzt ein anderes
        voraus, keines erzeugt ein anderes mit: Eine Registrierung ohne
        Download ist gueltig, ein Download ohne Registrierung ebenso. Das
        Einzige, was sie verbindet, ist die Zuordnung - die Frage, welcher
        Facebook-Gruppe dieses Ereignis zu verdanken ist.

        Zugeordnet wird in dieser Reihenfolge: mitgeschickter Tracking-Code,
        sonst der erste bekannte Code dieses Menschen ueber alle seine
        Kennungen hinweg. Findet sich keiner, bleibt das Ereignis **ohne**
        Zuordnung - es einer beliebigen Gruppe zuzuschlagen waere eine
        erfundene Zahl, und erfundene Zahlen sind schlimmer als fehlende.

        Bei ``registration`` mit ``referral_code`` entsteht zugleich die
        Empfehlung - mit allen Pruefungen. Wird sie abgewiesen, ist das
        Ereignis trotzdem gueltig: Der Mensch hat sich ja registriert.

        Ist ``EVENTS_TOKEN`` gesetzt, muss die Kopfzeile ``X-Events-Token``
        stimmen. 401 statt 404: Anders als bei der Uebersicht ist hier nichts
        zu verbergen - die Gegenstelle ist eine Anwendung, und sie soll den
        Unterschied zwischen "falscher Schluessel" und "Weg gibt es nicht"
        sehen koennen.
        """
        _pruefe_token(request)

        with _store() as store:
            # Unter welcher Kennung dieses Ereignis steht. Vor der Anmeldung
            # gibt es nur die anonyme; sie ist dann die einzige Spur, die den
            # Menschen mit seinem ersten Besuch verbindet.
            kennung = meldung.user_ref or meldung.anon_ref

            # Beide Kennungen in einer Meldung heisst: derselbe Mensch, neuer
            # Name. Das muss VOR der Zuordnung geschehen - sonst sucht die
            # Erbschaft im naechsten Schritt noch in der falschen Haelfte.
            if meldung.user_ref and meldung.anon_ref:
                store.verknuepfe_kennung(meldung.anon_ref, meldung.user_ref)

            campaign_id = group_id = ""
            tracking_code = ""

            if meldung.tracking_code:
                link = store.resolve_code(meldung.tracking_code)
                if link is not None:
                    campaign_id, group_id = link.campaign_id, link.group_id
                    tracking_code = meldung.tracking_code
                else:
                    # Ein Code, den es nicht gibt (Tippfehler, alter Beitrag,
                    # abgeschnittene URL). Ihn zu speichern erfaende eine
                    # Spalte in jeder Auswertung je Code; verworfen wird er
                    # zugunsten der Erbschaft, die den Menschen kennt.
                    store.audit("ereignis_unbekannter_code", meldung.tracking_code,
                                meldung.event_type.value)

            if not tracking_code and kennung:
                # Ohne Code die erste bekannte Zuordnung dieses Menschen erben -
                # ueber alle seine Kennungen hinweg. Sonst blieben spaete
                # Stufen ohne Gruppe, und genau die sind die interessanten.
                campaign_id, group_id, tracking_code = store.erste_zuordnung(kennung)

            # Ein Ereignis, das je Mensch nur einmal zaehlt (Download), wird
            # beim zweiten Mal nicht gespeichert. Die Meldung ist trotzdem
            # angekommen - die Antwort sagt beides.
            if meldung.event_type in EINMAL_JE_MENSCH and kennung and (
                store.ereignis_bereits_gezaehlt(meldung.event_type, kennung)
            ):
                return JSONResponse(
                    {
                        "gespeichert": meldung.event_type.value,
                        "gezaehlt": False,
                        "grund": "bereits gezaehlt",
                        "tracking_code": tracking_code,
                    }
                )

            store.record_event(
                TrackingEvent(
                    tracking_code=tracking_code,
                    campaign_id=campaign_id,
                    group_id=group_id,
                    user_ref=kennung,
                    event_type=meldung.event_type,
                    occurred_at=meldung.occurred_at or datetime.now(UTC),
                    source="api",
                )
            )

            # ``tracking_code`` in der Antwort ist kein Geheimnis - er steht in
            # veroeffentlichten Facebook-Beitraegen. Er ist der Beleg: Die
            # meldende Anwendung sieht, welcher Gruppe ihr Ereignis
            # zugeschlagen wurde, und kann das protokollieren, statt es
            # spaeter aus zwei Datenbanken zusammensuchen zu muessen.
            antwort: dict[str, Any] = {
                "gespeichert": meldung.event_type.value,
                "gezaehlt": True,
                "tracking_code": tracking_code,
            }

            if meldung.event_type is EventType.REGISTRATION and meldung.user_ref:
                # Jeder Registrierte bekommt seinen eigenen Empfehlungscode.
                antwort["referral_code"] = code_fuer_benutzer(store, cfg, meldung.user_ref)

                if meldung.referral_code:
                    _referral, entscheidung = lege_empfehlung_an(
                        store,
                        meldung.referral_code,
                        meldung.user_ref,
                        campaign_id,
                        group_id,
                    )
                    antwort["referral"] = entscheidung.grund
                    if not entscheidung.angenommen and entscheidung.status is not None:
                        antwort["referral_status"] = entscheidung.status.value

            stufe = _REFERRAL_STUFE.get(meldung.event_type)
            if stufe is not None and meldung.user_ref:
                referral = setze_status(store, meldung.user_ref, stufe)
                if referral is not None:
                    # Der Werber kann durch diese Stufe eine Praemie erreichen.
                    neu = bewerte_benutzer(
                        store, load_reward_rules(cfg.root), referral.referrer_user_ref
                    )
                    if neu:
                        antwort["rewards_neu"] = [r.rule_id for r in neu]

            return JSONResponse(antwort)

    @app.get("/referral/{user_ref}")
    def referral_stand(user_ref: str, request: Request) -> dict[str, Any]:
        """Empfehlungsstand eines Benutzers - fuer die Anzeige in der App.

        Hinter demselben Schluessel wie ``POST /events``. Bisher trug diesen
        Weg allein nginx, der ihn nicht nach aussen durchlaesst: Wer den Block
        um ein ``location /`` erweitert, gaebe damit unbeabsichtigt Auskunft
        ueber die Empfehlungen jedes Benutzers, dessen Kennung jemand raet.
        Ein Weg, der Auskunft ueber Menschen gibt, soll seinen Schutz selbst
        mitbringen und ihn nicht von einer Datei nebenan borgen.
        """
        _pruefe_token(request)
        with _store() as store:
            referrals = store.referrals_of(user_ref)
            return {
                "user_ref": user_ref,
                "referral_code": code_fuer_benutzer(store, cfg, user_ref),
                "referrals": {
                    status.value: sum(1 for r in referrals if r.status is status)
                    for status in ReferralStatus
                },
                "rewards": [
                    {"rule_id": r.rule_id, "type": r.reward_type.value,
                     "value": r.value, "status": r.status.value}
                    for r in store.rewards_of(user_ref)
                ],
            }

    return app
