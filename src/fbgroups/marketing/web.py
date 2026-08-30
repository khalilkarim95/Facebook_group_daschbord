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
from urllib.parse import quote, urlparse

from pydantic import BaseModel, Field

from fbgroups.config import AppConfig, load_config
from fbgroups.marketing.arbeit import (
    Ergebnis,
    Grund,
    Sperre,
    arbeitsreihenfolge,
    auswahlliste,
    hole_gruppenarbeit,
    melde_vorschlag,
    stelle_texte_bereit,
)
from fbgroups.marketing.arbeitsseite import render_gruppenarbeit, render_sperre
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
    CampaignGroup,
    CampaignStatus,
    EventType,
    JobStatus,
    MarketingStatus,
    PostStatus,
    QueueZustand,
    ReferralStatus,
    TextQuelle,
    Texttyp,
    TrackingEvent,
)
from fbgroups.marketing.referral import code_fuer_benutzer, lege_empfehlung_an, setze_status
from fbgroups.marketing.rewards import bewerte_benutzer, load_reward_rules
from fbgroups.marketing.selection import auswahl_der_kampagne, baue_plan, synchronisiere
from fbgroups.marketing.store import MarketingStore
from fbgroups.marketing.tracking import slug
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


class VorbereitenMeldung(BaseModel):
    """Welcher Vorbereitungsschritt ausgefuehrt werden soll.

    Ein Weg mit einem Feld statt fuenf Wegen: Die Schritte gehoeren zu einer
    Kette (Text -> Freigabe -> Warteschlange), sie werden nacheinander vom
    selben Knopf-Streifen ausgeloest, und jeder einzelne waere sonst ein
    weiterer Weg, der ``_nur_lokal`` und die Kampagnenpruefung wiederholt.
    """

    schritt: str = Field(pattern="^(text|text_neu|draft|approve|enqueue|reset|zurueckholen)$")
    #: Nur bei ``reset``: auch die gemessene Resonanz loeschen.
    auch_ereignisse: bool = False


class SammelZuordnenMeldung(BaseModel):
    """Mehrere angehakte Gruppen in einem Zug einer Kampagne zuordnen.

    Eine **Liste** und nicht ein Weg je Gruppe - dieselbe Ueberlegung wie bei
    ``POST /bearbeiten``: Zwoelf ausgewaehlte Zeilen sind ein Zug, keine zwoelf
    Klicks. Und nur so entstehen die Codes aus **einem** ``CodeAllocator``;
    zwoelf Aufrufe bauten zwoelf davon, jeder muesste den Bestand neu lesen,
    und zwei gleichzeitige koennten dieselbe Nummer zweimal ausgeben.
    """

    group_ids: list[str]


class LoeschMeldung(BaseModel):
    """Bestaetigung fuer das Loeschen einer Kampagne.

    ``bestaetigt`` ist Vorgabe **false**: Der erste Aufruf zeigt nur, was
    verlorenginge. Ein Loeschen nimmt ueber ``ON DELETE CASCADE`` jeden
    Tracking-Code dieser Kampagne mit - steht einer in einem veroeffentlichten
    Beitrag, fuehrt der Link dort danach ins Leere. Dieselbe Vorsicht wie bei
    ``SyncMeldung.dry_run``.
    """

    bestaetigt: bool = False


class ZuordnenMeldung(BaseModel):
    """Eine Gruppe einer Kampagne zuordnen - oder die Zuordnung entfernen.

    ``entfernen`` gibt es, weil eine Zuordnung aus Versehen entstehen kann und
    ein Tracking-Code, der **nie** in einem Beitrag stand, nichts bindet.
    Sobald einer veroeffentlicht wurde, weist der Weg das Entfernen ab: Der
    Link im Beitrag muss weiter ankommen.
    """

    campaign_id: str = Field(min_length=1, max_length=64)
    entfernen: bool = False


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


class GruppeMeldung(BaseModel):
    """Eine Gruppenkennung - und wofuer der Text gedacht ist.

    ``texttyp`` mit Vorgabe ``post``: Jeder Aufruf aus der Zeit vor den
    Kommentaren meint einen Beitrag und bleibt damit gueltig. Er steht schon
    hier und nicht erst in den abgeleiteten Meldungen, weil ihn **alle** vier
    Wege brauchen - ueberarbeiten, uebernehmen, von Hand schreiben,
    zuruecksetzen. Ohne ihn landete ein ueberarbeiteter Kommentar im Beitrag,
    und das faellt erst auf, wenn er in der Gruppe steht.
    """

    group_id: str = Field(min_length=1, max_length=200)
    texttyp: Texttyp = Texttyp.POST


class TextMeldung(GruppeMeldung):
    """Ein von Hand geschriebener Text - Beitrag oder Kommentar.

    Die Ausnahme von "der Text geht nur hinaus, nie zurueck" - und sie ist
    bewusst ein **eigener** Weg. Das Ergebnisformular bleibt textfrei; hier
    ist das Aendern die Handlung, nicht ein Nebeneffekt davon. 8000 Zeichen
    sind grosszuegig: Ein Facebook-Beitrag ist kuerzer, und eine Obergrenze
    schuetzt vor einem versehentlich eingefuegten Dokument.
    """

    text: str = Field(min_length=1, max_length=8000)


class VorschlagMeldung(GruppeMeldung):
    """Welche der fuenf Fassungen gemeint ist.

    ``nummer`` ist der Unterschied zwischen "der Text dieser Gruppe" und
    "dieser Text dieser Gruppe" - und damit die Zusicherung, dass ein
    Speichern die vier Nachbarn nicht anfasst. Sie faehrt bei **jedem** der
    drei Wege mit; ohne sie landete ein Text im falschen Vorschlag, und das
    faellt erst auf, wenn er in der Gruppe steht.

    Die Obergrenze ist grosszuegiger als ``MAX_VORSCHLAEGE``: Ein Vorrat, der
    einmal groesser war, hat Fassungen mit hoeherer Nummer hinterlassen, und
    die muessen weiter zu lesen und zu melden sein.
    """

    nummer: int = Field(default=1, ge=1, le=50)


class VorschlagText(VorschlagMeldung):
    """Der Text **einer** Fassung, von Hand geschrieben."""

    text: str = Field(min_length=1, max_length=8000)


class VorschlagErgebnis(VorschlagMeldung):
    """Was aus **einer** Fassung geworden ist.

    Bewusst ohne Textfeld. Was zurueckkommt, ist der Ausgang, die Fassung und
    der Grund - der Beitrag selbst hat den Server nur in eine Richtung
    verlassen, und ein manipuliertes Formular kann damit keinen anderen Text
    in eine Gruppe bringen als den, den der Server vorbereitet hat.
    """

    ausgang: str = Field(pattern="^(veroeffentlicht|fehlgeschlagen)$")
    fehler: str = Field(default="", max_length=300)


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


def play_store_url(tracking_code: str, config: AppConfig) -> str:
    """Die Play-Store-Adresse dieser App - mit dem Code im ``referrer``.

    ``referrer`` ist das einzige Feld, das eine Installation ueberlebt: Google
    reicht es nach dem Einrichten an die App weiter (Play Install Referrer),
    und die App liest es beim ersten Start. Ohne dieses Feld endet die
    Zuordnung am Store - die Landingpage-Adresse mit ``?ref=`` sieht ein
    Play-Store-Install nie.

    Der Code wird **prozentkodiert**. Er enthaelt heute nur Buchstaben, Ziffern
    und Bindestriche, aber die Kuerzel kommen aus der Konfiguration und koennen
    sich aendern; ein ungeschuetztes ``&`` darin zerlegte die Adresse.
    """
    paket = str(config.get("marketing", "store", "android_package", default="")).strip()
    vorlage = (
        str(config.get("marketing", "store", "play_url", default="")).strip()
        or "https://play.google.com/store/apps/details?id={package}&referrer={referrer}"
    )
    if not paket:
        return ""
    return vorlage.replace("{package}", quote(paket, safe="")).replace(
        "{referrer}", quote(tracking_code, safe="")
    )


def _ziel_gewaehlt(campaign: Campaign | None, config: AppConfig) -> str:
    """ "store" oder "landing" - die Kampagne entscheidet, sonst die Vorgabe.

    Leer an der Kampagne heisst ausdruecklich "die Vorgabe", nicht "landing":
    Sonst waere eine geaenderte Vorgabe fuer den Bestand wirkungslos, und das
    faellt erst auf, wenn keine Installation mehr zugeordnet wird.
    """
    if campaign is not None and campaign.ziel.strip():
        return campaign.ziel.strip().lower()
    return str(config.get("marketing", "ziel", default="landing")).strip().lower()


def _ziel_url(store: MarketingStore, tracking_code: str, config: AppConfig) -> tuple[str, bool]:
    """Wohin ein Klick fuehrt. Returns: (Adresse, ist_store).

    ``ist_store`` gehoert dazu, weil der Aufrufer daran entscheidet, ob ein
    ``store_visit`` mitgeschrieben wird - und weil es nicht aus der Adresse
    zurueckzulesen ist, ohne sie zu zerlegen.

    Faellt der Store aus (keine Package-ID eingetragen), wird auf die
    Landingpage ausgewichen **und das nicht als Store-Besuch gezaehlt**. Eine
    fehlende Kennung ist ein Einrichtungsfehler; ihn als Store-Besuch zu
    zaehlen machte ihn unsichtbar.
    """
    link = store.resolve_code(tracking_code)
    campaign = store.load_campaign(link.campaign_id) if link is not None else None

    if _ziel_gewaehlt(campaign, config) == "store":
        adresse = play_store_url(tracking_code, config)
        if adresse:
            return adresse, True

    if campaign is not None and campaign.landing_page:
        return campaign.landing_page, False
    return (str(config.get("marketing", "fallback_url", default="")) or "/"), False


def _kette_automatisch(
    store: MarketingStore,
    campaign: Campaign,
    gruppen: dict[str, Any],
    config: AppConfig,
) -> str:
    """Text, Freigabe, Warteschlange - in einem Zug. Returns: was dabei geschah.

    Der Weg fuer "Arbeiten" bei leerer Warteschlange. Die drei Schritte sind
    dieselben wie auf der Kommandozeile und in der Knopfreihe; hier laufen sie
    nur ohne Rueckfrage, weil keiner von ihnen etwas veroeffentlicht und keiner
    einen vorhandenen Text ueberschreibt.

    Der Bericht wird zurueckgegeben und nicht verschluckt: Bleibt die
    Warteschlange danach leer, ist er die einzige Auskunft darueber, woran es
    lag - "0 Texte gefuellt" bei einer Kampagne ohne Zuordnungen sagt etwas
    ganz anderes als "12 gefuellt, 0 freigegeben".

    Ein Schritt, der an der Zustandsmaschine scheitert, beendet die Kette
    nicht: Die drei sind unabhaengig voneinander, und ein ``approve``, das
    nichts findet, ist kein Grund, das ``enqueue`` ausfallen zu lassen.
    """
    from fbgroups.marketing.queue import UngueltigerUebergang

    # Die haeufigste Ursache zuerst und im Klartext. "0 Texte gefuellt,
    # freigegeben, eingereiht" ist zwar richtig, beantwortet aber nicht, woran
    # es liegt - und ohne Zuordnungen liegt es nie an der Kette.
    if not store.links_for_campaign(campaign.campaign_id):
        return (
            "Diese Kampagne hat keine Gruppen zugeordnet. Zuordnen in der "
            "Uebersicht (Spalte 'Kampagne') oder mit: fbgroups campaign sync "
            f"{campaign.campaign_id}"
        )

    benennung = {"text": "Texte", "approve": "freigegeben", "enqueue": "eingereiht"}
    teile: list[str] = []
    for schritt in ("text", "approve", "enqueue"):
        try:
            getan, hinweis = _vorbereiten(store, campaign, gruppen, schritt, False, config)
        except (UngueltigerUebergang, ValueError) as exc:
            getan, hinweis = 0, str(exc)
        # Die Zahl steht vorn, nicht der Satz: "Freigegeben." laesst offen, ob
        # es zwoelf waren oder keine - und genau das ist die Frage, wenn die
        # Warteschlange danach immer noch leer ist.
        teile.append(f"{getan} {benennung[schritt]}")
        store.audit("arbeiten_" + schritt, campaign.campaign_id, f"{getan}: {hinweis}")
    return " · ".join(teile)


def _vorbereiten(
    store: MarketingStore,
    campaign: Campaign,
    gruppen: dict[str, Any],
    schritt: str,
    auch_ereignisse: bool,
    config: AppConfig,
) -> tuple[int, str]:
    """Fuehrt einen Vorbereitungsschritt aus. Returns: (betroffen, Hinweis).

    Ausserhalb von ``create_app``, damit die Kette ohne laufenden Dienst
    pruefbar ist - dieselbe Ueberlegung wie bei ``arbeit.py``.

    Jeder Schritt geht durch **dieselben** Wege wie die Kommandozeile
    (``set_post_text``, ``set_job_status``, ``setze_kampagne_zurueck``). Eine
    zweite Fassung der Regeln fuer die Oberflaeche waere eine zweite Wahrheit
    ueber denselben Ablauf.
    """
    from fbgroups.marketing.queue import UngueltigerUebergang

    campaign_id = campaign.campaign_id
    links = store.links_for_campaign(campaign_id)

    if schritt == "reset":
        zahlen = store.setze_kampagne_zurueck(campaign_id, auch_ereignisse=auch_ereignisse)
        hinweis = f"{zahlen['versuche']} Versuche geloescht"
        if auch_ereignisse:
            hinweis += f", {zahlen['ereignisse']} Ereignisse geloescht"
        return zahlen["zuordnungen"], hinweis

    if schritt in ("text", "text_neu"):
        from fbgroups.marketing import vorlagen

        # Die eigene Vorlage der Kampagne ist optional. Ohne sie kommt der Text
        # aus dem Vorrat in textvorlagen.yaml - der Normalfall, seit die
        # Abwechslung von dort und nicht aus einem Sprachmodell kommt.
        eigene = campaign.message_template.strip()
        if eigene and vorlagen.PLATZHALTER_LINK not in eigene:
            return 0, (
                "Die eigene Vorlage der Kampagne enthaelt kein {link} - "
                "die Gruppen bekaemen nie einen Klick gutgeschrieben."
            )

        # "text_neu" schreibt auch dort, wo schon etwas steht. Der Weg fuer
        # geaenderte Vorlagen; er verwirft Handarbeit, deshalb ein eigener
        # Schritt und nicht eine stillere Vorgabe.
        neu_schreiben = schritt == "text_neu"

        gefuellt = 0
        kommentare = 0
        beruehrte = 0
        gruende: list[str] = []
        for link in links:
            # Was veroeffentlicht ist oder gerade abgesetzt wird, bleibt
            # unangetastet - der Text steht dort schon in der Gruppe.
            if link.job_status in (JobStatus.PUBLISHED, JobStatus.PROCESSING):
                continue
            group = gruppen.get(link.group_id)
            if group is None:
                gruende.append(f"{link.group_id}: nicht im Bestand")
                continue

            try:
                # Dieselbe Stelle, die auch die Arbeitsseite aufruft. Eine
                # zweite Fassung der Fuellregeln waere eine zweite Wahrheit
                # darueber, welche Vorlage eine Gruppe bekommt.
                entstanden = stelle_texte_bereit(
                    store, campaign, group, config, ueberschreiben=neu_schreiben
                )
            except vorlagen.VorlageFehlt as exc:
                if str(exc) not in gruende:
                    gruende.append(str(exc))
                continue

            gefuellt += entstanden.get(Texttyp.POST, 0)
            kommentare += entstanden.get(Texttyp.KOMMENTAR, 0)
            beruehrte += 1

        teile = [f"{gefuellt} Beitragsfassungen", f"{kommentare} Kommentarfassungen"]
        teile.append(f"in {beruehrte} Gruppen")
        if gruende:
            teile.append("; ".join(gruende[:3]))
        # Gezaehlt werden die **Fassungen**, nicht die Gruppen: Seit eine
        # Gruppe fuenf davon bekommt, ist "12 Texte" bei zwoelf Gruppen eine
        # andere Auskunft als bei zwei. Der Rueckgabewert steuert ausserdem,
        # ob die Werkbank die Seite neu laedt - und neu zu laden lohnt sich
        # genau dann, wenn etwas entstanden ist.
        return gefuellt + kommentare, ", ".join(teile) + "."

    if schritt == "approve":
        # Ohne Text weist ``pruefe_uebergang`` ohnehin ab - solche Zuordnungen
        # hier zu uebergehen spart eine Fehlermeldung je Gruppe.
        betroffen = [
            link
            for link in links
            if link.post_text.strip()
            and link.job_status
            in (JobStatus.DRAFT, JobStatus.AI_GENERATED, JobStatus.PENDING_REVIEW)
        ]
        fertig = 0
        for link in betroffen:
            try:
                if link.job_status is not JobStatus.PENDING_REVIEW:
                    store.set_job_status(campaign_id, link.group_id, JobStatus.PENDING_REVIEW)
                store.set_job_status(
                    campaign_id, link.group_id, JobStatus.APPROVED, akteur="uebersicht"
                )
                fertig += 1
            except UngueltigerUebergang:
                continue
        ohne_text = sum(
            1
            for link in links
            if not link.post_text.strip()
            and link.job_status not in (JobStatus.PUBLISHED, JobStatus.PROCESSING)
        )
        hinweis = "Freigegeben."
        if ohne_text:
            hinweis = f"{ohne_text} ohne Text uebersprungen - erst Text erzeugen."
        return fertig, hinweis

    if schritt == "enqueue":
        from fbgroups.marketing.selection import nach_prioritaet

        freigegeben = [link for link in links if link.job_status is JobStatus.APPROVED]
        geordnet = nach_prioritaet(
            [gruppen[link.group_id] for link in freigegeben if link.group_id in gruppen],
            config,
        )
        reihenfolge = {g.group_id: i for i, g in enumerate(geordnet)}
        freigegeben.sort(key=lambda link: reihenfolge.get(link.group_id, len(reihenfolge)))
        fertig = 0
        for link in freigegeben:
            try:
                store.set_job_status(campaign_id, link.group_id, JobStatus.QUEUED)
                fertig += 1
            except UngueltigerUebergang:
                continue
        return fertig, "Nach Score eingereiht - die besten zuerst."

    if schritt == "zurueckholen":
        # Die Gegenrichtung zu "Passt nicht" auf der Arbeitsseite.
        #
        # Sie fehlte, und das war eine Sackgasse mit Ansage: Ein
        # ``cancelled`` mit Text faellt durch **jede** Zeile dieser Kette -
        # die Textschritte nehmen nur Textlose, ``approve`` nur draft,
        # ai_generated und pending_review, ``enqueue`` nur approved. Wer sich
        # verklickt hatte, sah vier Knoepfe, von denen keiner etwas tat, und
        # nichts sagte ihm warum. Der einzige Weg zurueck war ``campaign
        # reset`` auf der Kommandozeile - also ausgerechnet ein Befehl, um
        # eine Fehlbedienung der Oberflaeche zu heilen.
        #
        # Ziel ist ``draft``, und nicht, weil es bequem waere: Es ist laut
        # ``UEBERGAENGE`` der einzige erlaubte Ausgang aus ``cancelled``. Von
        # dort geht es mit Text ueber "Freigeben" weiter und ohne Text ueber
        # "Text von der KI" - beide Wege stehen danach offen.
        beiseite = [link for link in links if link.job_status is JobStatus.CANCELLED]
        fertig = 0
        for link in beiseite:
            try:
                store.set_job_status(campaign_id, link.group_id, JobStatus.DRAFT)
                fertig += 1
            except UngueltigerUebergang:
                continue
        if not fertig:
            return 0, "Nichts beiseitegelegt - hier ist kein 'Passt nicht' zurueckzunehmen."
        # Der Text bleibt stehen. "Passt nicht" ist ein Urteil ueber die
        # Gruppe, nicht ueber den Text; ihn beilaeufig zu loeschen naehme ein
        # zweites Urteil vorweg, das niemand gefaellt hat.
        mit_text = sum(1 for link in beiseite if link.post_text.strip())
        hinweis = f"{fertig} zurueckgeholt - Stand: Entwurf."
        if mit_text:
            hinweis += (
                f" {mit_text} davon haben noch ihren Text und koennen gleich"
                " freigegeben werden; ein neuer Text entsteht im Textfeld"
                " auf der Arbeitsseite."
            )
        return fertig, hinweis

    return 0, f"Unbekannter Schritt: {schritt}"


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
    def arbeit(  # noqa: ANN202
        campaign_id: str, request: Request, gruppe: int = 1, group_id: str = ""
    ):
        """Eine Gruppe, zwei Spalten, zehn Fassungen - die Arbeitsseite.

        Der Bestand lebt hier, Zwischenablage und Browser aber auf dem
        Arbeitsrechner. Die Arbeit dorthin zu holen hiesse, in eine zweite
        Datenbank zu schreiben. Also kommt die Arbeit dorthin, wo der Bestand
        steht: Der Server bereitet vor und zaehlt, der Browser des Menschen
        schreibt, kopiert und oeffnet.

        **Der Aufruf beginnt nichts.** Er setzt keinen Stand und schreibt
        keine Protokollzeile - das tut erst eine gemeldete Veroeffentlichung.
        Vorher war das anders und musste es auch sein: Die Seite nahm einen
        Beitrag aus der Warteschlange und
        haette ihn bei einem geschlossenen Reiter verloren. Eine Seite, die
        nichts herausnimmt, kann auch nichts verlieren.

        Er steht trotzdem hinter ``_nur_lokal``: Von hier aus wird
        veroeffentlicht, und die Knoepfe dafuer stehen auf dieser Seite.

        ``?gruppe=N`` blaettert, ``?group_id=...`` springt eine bestimmte
        Gruppe an. Die Kennung geht vor: Wer eine bestimmte Gruppe meint,
        meint sie auch dann noch, wenn sich die Rangfolge zwischendurch
        geaendert hat.
        """
        _nur_lokal(request)
        with SqliteStore(pfad) as gruppen_store:
            gruppen = {g.group_id: g for g in gruppen_store.load_groups()}
        with _store() as store:
            campaign = store.load_campaign(campaign_id)
            if campaign is None:
                raise HTTPException(status_code=404, detail="Unbekannte Kampagne")

            reihe = arbeitsreihenfolge(store, campaign_id, gruppen)
            if not reihe:
                # Der einzige verbliebene Grund, die Seite zu verschliessen.
                # Pausiert und gestoppt halten nur noch das Veroeffentlichen
                # an - Texte vorbereiten geht weiter.
                bericht = _kette_automatisch(store, campaign, gruppen, cfg)
                reihe = arbeitsreihenfolge(store, campaign_id, gruppen)
                if not reihe:
                    return HTMLResponse(
                        render_sperre(Sperre(Grund.KEINE_GRUPPEN), campaign_id, bericht)
                    )

            # Hinter das Ende geblaettert: zurueck auf die erste Gruppe statt
            # auf eine Fehlerseite. Die Liste wird kuerzer, waehrend man
            # darin liest - eine ausgeschlossene Gruppe verschwindet daraus.
            if not group_id and not 1 <= gruppe <= len(reihe):
                return RedirectResponse(f"/arbeit/{campaign_id}", status_code=303)

            stand = hole_gruppenarbeit(
                store,
                campaign,
                gruppen,
                cfg,
                nummer=gruppe,
                group_id=group_id,
                reihe=reihe,
            )
            if stand is None:
                return RedirectResponse(f"/arbeit/{campaign_id}", status_code=303)

            # Fehlen die Fassungen dieser Gruppe, entstehen sie beim Oeffnen -
            # ohne Rueckfrage, wie die Vorbereitungskette. Zulaessig ist das,
            # weil nichts davon veroeffentlicht und nichts einen vorhandenen
            # Text ueberschreibt; das Schlimmste, was ein ueberfluessiger Lauf
            # anrichtet, sind fuenf Zeilen in einer Tabelle. Eine Knopfreihe
            # zu drueckten, bevor ueberhaupt ein Text dasteht, waere kein
            # Entschluss, sondern eine Wegstrecke.
            if not stand.posts or not stand.kommentare:
                gruppe_datensatz = gruppen.get(stand.link.group_id)
                if gruppe_datensatz is not None:
                    from fbgroups.marketing.vorlagen import VorlageFehlt

                    try:
                        stelle_texte_bereit(store, campaign, gruppe_datensatz, cfg)
                    except VorlageFehlt:
                        # Eine Luecke in textvorlagen.yaml haelt die Seite
                        # nicht an: Der Mensch kann hier selbst schreiben, und
                        # ``config-check`` nennt die Luecke beim Namen.
                        pass
                    else:
                        stand = hole_gruppenarbeit(
                            store,
                            campaign,
                            gruppen,
                            cfg,
                            nummer=stand.nummer,
                            reihe=reihe,
                        )
                        if stand is None:  # pragma: no cover - Reihe unveraendert
                            return RedirectResponse(f"/arbeit/{campaign_id}", status_code=303)

            eintraege = auswahlliste(store, campaign_id, reihe, gruppen)
            
            from datetime import UTC, datetime

            from fbgroups.marketing import kaltmodus as km
            jetzt = datetime.now(UTC)
            aktiv, pro_tag, abstand = km.einstellungen(cfg)
            kalt_text = ""
            kalt_limit_erreicht = False
            if aktiv:
                heute = store.versuche_heute(jetzt.date().isoformat())
                roh = store.letzter_versuch()
                portion = km.tagesportion(reihe, erledigt_heute=heute, grenze=pro_tag)
                letzter = datetime.fromisoformat(roh) if roh else None
                frei_ab = km.naechster_zeitpunkt(
                    letzter, abstand_minuten=abstand, jetzt=jetzt, erledigt_heute=heute
                )
                warte = km.wartezeit_text(frei_ab, jetzt=jetzt)
                
                kalt_text = f"Heute: {portion.erledigt} von {portion.grenze}"
                if warte:
                    kalt_text += f" &middot; Nächster: {warte}"
                if portion.erledigt >= portion.grenze:
                    kalt_limit_erreicht = True

        return HTMLResponse(
            render_gruppenarbeit(
                stand,
                campaign_id,
                eintraege,
                cfg,
                kalt_text=kalt_text,
                kalt_limit_erreicht=kalt_limit_erreicht,
            )
        )

    def _vorschlag_oder_404(  # noqa: ANN202
        store: MarketingStore, campaign_id: str, group_id: str
    ):
        """Kampagne und Zuordnung nachschlagen - fuer alle drei Vorschlagswege.

        Dreimal dasselbe zu schreiben hiesse, dass die dritte Kopie irgendwann
        die laxere ist. Ein Vorschlag ohne Zuordnung hat keinen Tracking-Code
        und damit keinen Link - er duerfte gar nicht entstehen.
        """
        campaign = store.load_campaign(campaign_id)
        if campaign is None:
            raise HTTPException(status_code=404, detail="Unbekannte Kampagne")
        link = store.link_for(campaign_id, group_id)
        if link is None:
            raise HTTPException(status_code=404, detail="Keine Zuordnung")
        return campaign, link

    @app.post("/arbeit/{campaign_id}/vorschlag/text")
    def vorschlag_text(  # noqa: ANN202
        campaign_id: str, meldung: VorschlagText, request: Request
    ):
        """Speichert **genau eine** Fassung - nicht die vier daneben.

        Die einzige Stelle, an der ein Text zum Server zurueckwandert, und sie
        ist ein eigener Weg statt eines Feldes im Ergebnisformular. Der
        Unterschied ist die ganze Begruendung: Das Formular, das einen
        Ausgang meldet, traegt weiterhin nur Kennung, Zweck, Nummer und Grund;
        ein Textfeld darin waere ein Kanal, den niemand geoeffnet haben wollte.

        Geprueft wird mit **derselben** ``pruefe_platzhalter`` wie jeder
        andere Text: genau ein ``{link}``, keine ausgeschriebene Adresse, kein
        codeaehnliches Muster. Wer hier eine Adresse hineinschriebe, haette
        einen Beitrag, der richtig aussieht und dessen Gruppe nie einen Klick
        gutgeschrieben bekommt - der Fehler, den niemand bemerkt.

        Zurueck kommt der gespeicherte Text **und** die angezeigte Fassung mit
        eingesetztem Link. Der Browser rechnet das eine nicht in das andere
        um: Die Ersetzung geschieht in ``beitrag.mit_link`` und nirgends
        sonst.
        """
        _nur_lokal(request)
        from fbgroups.marketing.beitrag import mit_link
        from fbgroups.marketing.vorlagen import UngueltigerText, pruefe_platzhalter

        text = meldung.text.strip()
        try:
            pruefe_platzhalter(text)
        except UngueltigerText as exc:
            return JSONResponse({"ok": False, "meldung": str(exc)})

        with _store() as store:
            campaign, link = _vorschlag_oder_404(store, campaign_id, meldung.group_id)
            vorschlag = store.setze_vorschlag_text(
                campaign_id,
                meldung.group_id,
                meldung.texttyp,
                meldung.nummer,
                text,
                TextQuelle.HAND,
            )
            store.audit(
                "vorschlag_von_hand",
                f"{campaign_id}/{meldung.group_id}",
                f"{meldung.texttyp.value} {meldung.nummer}",
            )
            return JSONResponse(
                {
                    "ok": True,
                    "nummer": vorschlag.nummer,
                    "text": vorschlag.text,
                    "angezeigt": mit_link(campaign, link, vorschlag.text, config=cfg),
                    "stand": vorschlag.status.value,
                }
            )

    @app.post("/arbeit/{campaign_id}/vorschlag/zuruecksetzen")
    def vorschlag_zuruecksetzen(  # noqa: ANN202
        campaign_id: str, meldung: VorschlagMeldung, request: Request
    ):
        """Holt den erzeugten Text **dieser** Fassung zurueck.

        Genau dafuer steht ``generated_text`` neben ``text``. Ohne ihn waere
        jede Ueberarbeitung endgueltig, und der einzige Weg zurueck fuehrte
        ueber die Kommandozeile.
        """
        _nur_lokal(request)
        from fbgroups.marketing.beitrag import mit_link

        with _store() as store:
            campaign, link = _vorschlag_oder_404(store, campaign_id, meldung.group_id)
            vorhanden = store.vorschlag(
                campaign_id, meldung.group_id, meldung.texttyp, meldung.nummer
            )
            if vorhanden is None or not vorhanden.generated_text.strip():
                return JSONResponse(
                    {
                        "ok": False,
                        "meldung": "Fuer diese Fassung wurde nie ein Text erzeugt.",
                    }
                )
            vorschlag = store.vorschlag_zuruecksetzen(
                campaign_id, meldung.group_id, meldung.texttyp, meldung.nummer
            )
            store.audit(
                "vorschlag_zurueckgesetzt",
                f"{campaign_id}/{meldung.group_id}",
                f"{meldung.texttyp.value} {meldung.nummer}",
            )
            return JSONResponse(
                {
                    "ok": True,
                    "nummer": vorschlag.nummer,
                    "text": vorschlag.text,
                    "angezeigt": mit_link(campaign, link, vorschlag.text, config=cfg),
                    "stand": vorschlag.status.value,
                }
            )

    @app.post("/arbeit/{campaign_id}/vorschlag/ergebnis")
    def vorschlag_ergebnis(  # noqa: ANN202
        campaign_id: str, meldung: VorschlagErgebnis, request: Request
    ):
        """Traegt den Ausgang **einer** Fassung ein - und schaltet nichts weiter.

        Der Kern der gruppenweisen Arbeitsweise. Frueher endete dieser Weg in
        einer 303 auf dieselbe Adresse, und die holte den naechsten Beitrag:
        Wer veroeffentlichte, bekam damit die naechste Gruppe, ob er wollte
        oder nicht. Jetzt antwortet er mit dem neuen Stand **dieser** Fassung,
        und der Browser aendert genau die eine Stelle, um die es geht - die
        Gruppe bleibt, der gewaehlte Vorschlag bleibt, die andere Spalte
        bleibt unberuehrt.

        Kein Formular, sondern JSON, und das ist die Folge davon: Ein
        Formular fuehrt zu einer neuen Seite; hier soll gerade keine neue
        Seite entstehen.

        Die Regeln liegen unveraendert in ``arbeit.melde_vorschlag`` - eine
        zweite Fassung fuer den Dienst waere eine zweite Zaehlweise fuer
        dieselben Beitraege.
        """
        _nur_lokal(request)
        with _store() as store:
            campaign, link = _vorschlag_oder_404(store, campaign_id, meldung.group_id)
            erfolg = meldung.ausgang == "veroeffentlicht"
            ergebnis = melde_vorschlag(
                store,
                campaign,
                link,
                meldung.texttyp,
                meldung.nummer,
                Ergebnis(erfolg=erfolg, fehler=meldung.fehler.strip()),
                ausgeloest_von="arbeitsseite",
                sitzung="browser",
            )
            if isinstance(ergebnis, Sperre):
                return JSONResponse({"ok": False, "meldung": ergebnis.grund})

            store.audit(
                "vorschlag_" + meldung.ausgang,
                f"{campaign_id}/{meldung.group_id}",
                f"{meldung.texttyp.value} {meldung.nummer}: {meldung.fehler}",
            )
            return JSONResponse(
                {
                    "ok": True,
                    "nummer": ergebnis.nummer,
                    "stand": ergebnis.status.value,
                    "fehler": ergebnis.fehler,
                }
            )

    @app.post("/arbeit/{campaign_id}/vorschlag/auto")
    def vorschlag_auto(  # noqa: ANN202
        campaign_id: str, meldung: VorschlagMeldung, request: Request
    ):
        """Fuehrt den Post oder Kommentar direkt per Browser-Automatisierung aus."""
        _nur_lokal(request)

        # 1. READ-Phase: Vorbereitungen treffen (Datenbank ist nur kurz offen)
        with _store() as store:
            campaign, link = _vorschlag_oder_404(store, campaign_id, meldung.group_id)

            from fbgroups.marketing.models import QueueZustand

            zustand = store.queue_zustand(campaign_id)
            if zustand is QueueZustand.PAUSIERT:
                return JSONResponse({"ok": False, "meldung": "Kampagne ist pausiert."})
            if zustand is QueueZustand.GESTOPPT:
                return JSONResponse({"ok": False, "meldung": "Kampagne ist gestoppt."})

            from fbgroups.marketing import kaltmodus

            aktiv, pro_tag, abstand = kaltmodus.einstellungen(cfg)
            if aktiv:
                jetzt = datetime.now(UTC)
                heute = store.versuche_heute(jetzt.date().isoformat())
                if heute >= pro_tag:
                    return JSONResponse(
                        {"ok": False, "meldung": f"Tageslimit von {pro_tag} erreicht (Kaltmodus)."}
                    )

                # Prüfen, ob der Abstand eingehalten ist
                letzter_versuch = store.letzter_versuch()
                letzter_dt = datetime.fromisoformat(letzter_versuch) if letzter_versuch else None
                naechster = kaltmodus.naechster_zeitpunkt(
                    letzter_dt, abstand_minuten=abstand, jetzt=jetzt, erledigt_heute=heute
                )
                if naechster:
                    wartezeit = kaltmodus.wartezeit_text(naechster, jetzt=jetzt)
                    return JSONResponse(
                        {
                            "ok": False,
                            "meldung": f"Abstandsregel aktiv, {wartezeit} warten (Kaltmodus).",
                        }
                    )

            vorschlag = store.vorschlag(
                campaign_id, meldung.group_id, meldung.texttyp, meldung.nummer
            )
            if not vorschlag or not vorschlag.text.strip():
                return JSONResponse({"ok": False, "meldung": "Fassung oder Text nicht gefunden"})

            from fbgroups.marketing.beitrag import mit_link

            text = mit_link(campaign, link, vorschlag.text, config=cfg)

        with SqliteStore(pfad) as gruppen_store:
            group = next(
                (g for g in gruppen_store.load_groups() if g.group_id == meldung.group_id), None
            )
        if not group or not group.url_canonical:
            return JSONResponse({"ok": False, "meldung": "Keine URL fuer Gruppe gefunden"})

        # 2. BROWSER-Phase (Datenbank geschlossen, kann Minuten dauern)
        from rich.console import Console

        from fbgroups.automation.actions import comment_on_post, fetch_top_posts, post_to_group
        from fbgroups.automation.browser import get_browser_context
        from fbgroups.models import GroupPost

        console = Console()

        erfolg = False
        fehler_text = "Element nicht gefunden/blockiert"
        used_post_url = ""
        try:
            with get_browser_context(cfg, headless=False) as context:
                if meldung.texttyp == Texttyp.POST:
                    erfolg = post_to_group(context, group.url_canonical, text)
                else:
                    console.print("[cyan]Fetching posts for commenting...[/cyan]")
                    raw_posts = fetch_top_posts(context, group.url_canonical, group.group_id)
                    if raw_posts:
                        posts = [
                            GroupPost(
                                group_id=group.group_id,
                                post_url=p["post_url"],
                                interactions=p["interactions"],
                                comments=p["comments"],
                            )
                            for p in raw_posts
                        ]
                        with SqliteStore(pfad) as gruppen_store:
                            gruppen_store.upsert_group_posts(group.group_id, posts)

                        with _store() as store:
                            bisherige = store.bisherige_post_urls(group.group_id)

                        offene_posts = [p for p in posts if p.post_url not in bisherige]
                        if offene_posts:
                            best_post = max(offene_posts, key=lambda p: p.interactions + p.comments)
                            used_post_url = best_post.post_url
                            erfolg = comment_on_post(context, used_post_url, text)
                        else:
                            fehler_text = "Alle aktuellen Beiträge wurden bereits kommentiert."
                    else:
                        fehler_text = "Keine passenden Beiträge zum Kommentieren gefunden."
        except Exception as exc:
            erfolg = False
            fehler_text = str(exc).split("\n")[0][:100]

        # 3. WRITE-Phase: Ergebnis eintragen
        with _store() as store:
            ergebnis = melde_vorschlag(
                store,
                campaign,
                link,
                meldung.texttyp,
                meldung.nummer,
                Ergebnis(
                    erfolg=erfolg, fehler="" if erfolg else fehler_text, post_url=used_post_url
                ),
                ausgeloest_von="auto",
                sitzung="browser",
            )

            if isinstance(ergebnis, Sperre):
                # Wenn in der Zwischenzeit pausiert wurde, aber es auf FB rauskam:
                if erfolg:
                    return JSONResponse(
                        {
                            "ok": True,
                            "nummer": meldung.nummer,
                            "stand": "veroeffentlicht",
                            "fehler": "",
                            "meldung": f"Gepostet, DB aber blockiert: {ergebnis.grund}",
                        }
                    )
                return JSONResponse({"ok": False, "meldung": ergebnis.grund})

            if erfolg:
                return JSONResponse(
                    {
                        "ok": True,
                        "nummer": ergebnis.nummer,
                        "stand": ergebnis.status.value,
                        "fehler": "",
                        "post_url": used_post_url,
                        "meldung": "Automatisch veroeffentlicht!",
                    }
                )
            else:
                return JSONResponse(
                    {
                        "ok": False,
                        "nummer": ergebnis.nummer,
                        "stand": ergebnis.status.value,
                        "fehler": ergebnis.fehler,
                        "meldung": f"Fehlgeschlagen: {ergebnis.fehler}",
                    }
                )

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

            store.audit("beitrag_" + meldung.status.value, meldung.group_id, link.tracking_code)
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

    @app.post("/kampagnen/{campaign_id}/vorbereiten")
    def kampagne_vorbereiten(  # noqa: ANN202
        campaign_id: str, meldung: VorbereitenMeldung, request: Request
    ):
        """Die Vorbereitungskette aus der Uebersicht statt aus dem Terminal.

        Dieselben Schritte wie auf der Kommandozeile und **dieselben Regeln**:
        ``text`` schreibt die Vorlage nur dort, wo keine steht; ``draft`` laesst
        das Modell nur fuer textlose Zuordnungen schreiben; ``approve`` uebergeht
        Zuordnungen ohne Text, weil ``pruefe_uebergang`` sie ohnehin abwiese;
        ``enqueue`` reiht nach Score ein. Wer hier klickt, bekommt nichts
        anderes als wer dort tippt - es gibt nur einen Weg durch die
        Zustandsmaschine.

        ``reset`` loescht bewusst **nicht** die Ereignisse, solange nicht
        ``auch_ereignisse`` gesetzt ist: Gemessene Resonanz ist das Einzige,
        was sich nicht wiederherstellen laesst.
        """
        _nur_lokal(request)
        with SqliteStore(pfad) as gruppen_store:
            gruppen = {g.group_id: g for g in gruppen_store.load_groups()}

        with _store() as store:
            campaign = store.load_campaign(campaign_id)
            if campaign is None:
                raise HTTPException(status_code=404, detail="Unbekannte Kampagne")

            getan, hinweis = _vorbereiten(
                store, campaign, gruppen, meldung.schritt, meldung.auch_ereignisse, cfg
            )
            store.audit("vorbereiten_" + meldung.schritt, campaign_id, str(getan))
            zaehler = store.job_counts(campaign_id)

        return JSONResponse(
            {
                "campaign_id": campaign_id,
                "schritt": meldung.schritt,
                "betroffen": getan,
                "hinweis": hinweis,
                "eingereiht": zaehler.get(JobStatus.QUEUED.value, 0),
                "freigegeben": zaehler.get(JobStatus.APPROVED.value, 0),
            }
        )

    @app.post("/gruppen/{group_id}/kampagne")
    def gruppe_zuordnen(  # noqa: ANN202
        group_id: str, meldung: ZuordnenMeldung, request: Request
    ):
        """Ordnet **eine** Gruppe einer Kampagne zu - oder loest die Zuordnung.

        Die Regel einer Kampagne (``campaign sync``) bleibt der Weg fuer den
        Bestand; dies ist der Griff fuer den Einzelfall, der sonst nur ueber
        eine Regelaenderung ginge. Der Code entsteht ueber denselben
        ``CodeAllocator`` wie dort - eine zweite Vergabestelle koennte eine
        Nummer ein zweites Mal ausgeben.

        Eine bestehende Zuordnung wird **nicht** angetastet: Ihr Code steht
        moeglicherweise schon in einem Beitrag. Entfernen geht nur, solange
        nichts veroeffentlicht wurde.
        """
        _nur_lokal(request)
        from fbgroups.marketing.tracking import CodeAllocator, tracking_url

        with SqliteStore(pfad) as gruppen_store:
            gruppe = next((g for g in gruppen_store.load_groups() if g.group_id == group_id), None)
        if gruppe is None:
            raise HTTPException(status_code=404, detail="Unbekannte Gruppe")

        with _store() as store:
            campaign = store.load_campaign(meldung.campaign_id)
            if campaign is None:
                raise HTTPException(status_code=404, detail="Unbekannte Kampagne")

            vorhanden = store.link_for(meldung.campaign_id, group_id)

            if meldung.entfernen:
                if vorhanden is None:
                    return JSONResponse({"group_id": group_id, "zugeordnet": False})
                if vorhanden.posted_at is not None:
                    # Der Code steht in einem veroeffentlichten Beitrag. Ein
                    # Klick darauf muss ankommen und gezaehlt werden - auch
                    # dann, wenn die Gruppe nicht mehr bearbeitet wird. Dafuer
                    # gibt es "Ausschliessen", das den Code gueltig laesst.
                    raise HTTPException(
                        status_code=409,
                        detail="Zu dieser Zuordnung wurde bereits veroeffentlicht. "
                        "Der Tracking-Code bleibt gueltig - stattdessen ausschliessen.",
                    )
                store.remove_link(meldung.campaign_id, group_id)
                store.audit("zuordnung_entfernt", f"{meldung.campaign_id}/{group_id}")
                return JSONResponse({"group_id": group_id, "zugeordnet": False})

            if vorhanden is not None:
                return JSONResponse(
                    {
                        "group_id": group_id,
                        "zugeordnet": True,
                        "code": vorhanden.tracking_code,
                        "hinweis": "War bereits zugeordnet.",
                    }
                )

            allocator = CodeAllocator(cfg, store.assigned_codes())
            code = allocator.next_for(gruppe)
            store.add_link(
                CampaignGroup(
                    campaign_id=meldung.campaign_id,
                    group_id=group_id,
                    tracking_code=code,
                    tracking_url=tracking_url(code, cfg),
                )
            )
            store.audit("zuordnung_einzeln", f"{meldung.campaign_id}/{group_id}", code)

        return JSONResponse(
            {
                "group_id": group_id,
                "zugeordnet": True,
                "code": code,
                "kampagne": meldung.campaign_id,
            }
        )

    @app.post("/kampagnen/{campaign_id}/gruppen")
    def kampagne_gruppen_zuordnen(  # noqa: ANN202
        campaign_id: str, meldung: SammelZuordnenMeldung, request: Request
    ):
        """Ordnet die angehakten Gruppen einer Kampagne zu.

        Der dritte Weg neben Regel und Einzelfall, und er schliesst eine
        Luecke: ``campaign sync`` beschreibt die Auswahl als **Regel** - wer
        aber genau diese zwoelf Gruppen meint, muesste sie erst als Regel
        formulieren, und eine Regel, die zwoelf Gruppen trifft und keine
        dreizehnte, ist meist gar nicht formulierbar.

        **Es wird nur hinzugefuegt.** Eine bestehende Zuordnung bleibt
        unangetastet und zaehlt als ``schon_zugeordnet``; ihr Code steht
        moeglicherweise in einem veroeffentlichten Beitrag. Unbekannte
        Kennungen brechen den Zug nicht ab - sie werden gezaehlt und genannt.

        Die Reihenfolge kommt aus ``selection.vergabereihenfolge``, nicht aus
        der Reihenfolge der Haken: Sonst bekaeme dieselbe Gruppe eine andere
        Nummer, je nachdem, in welcher Sortierung die Tabelle gerade stand.
        """
        _nur_lokal(request)
        from fbgroups.marketing.selection import vergabereihenfolge
        from fbgroups.marketing.tracking import CodeAllocator, tracking_url

        gewuenscht = list(dict.fromkeys(meldung.group_ids))  # Reihenfolge egal, Dubletten weg
        with SqliteStore(pfad) as gruppen_store:
            bekannt = {g.group_id: g for g in gruppen_store.load_groups()}

        gefunden = [bekannt[gid] for gid in gewuenscht if gid in bekannt]
        unbekannt = [gid for gid in gewuenscht if gid not in bekannt]

        with _store() as store:
            campaign = store.load_campaign(campaign_id)
            if campaign is None:
                raise HTTPException(status_code=404, detail="Unbekannte Kampagne")

            schon = [g for g in gefunden if store.link_for(campaign_id, g.group_id) is not None]
            offen = sorted(
                (g for g in gefunden if store.link_for(campaign_id, g.group_id) is None),
                key=vergabereihenfolge,
            )

            # Ein Allocator fuer den ganzen Zug - er zaehlt je Kuerzelpaar
            # weiter, statt den Bestand je Gruppe neu zu befragen.
            allocator = CodeAllocator(cfg, store.assigned_codes())
            codes: dict[str, str] = {}
            for gruppe in offen:
                code = allocator.next_for(gruppe)
                store.add_link(
                    CampaignGroup(
                        campaign_id=campaign_id,
                        group_id=gruppe.group_id,
                        tracking_code=code,
                        tracking_url=tracking_url(code, cfg),
                    )
                )
                codes[gruppe.group_id] = code
            if codes:
                store.audit("zuordnung_sammel", campaign_id, f"{len(codes)} Gruppen")

        return JSONResponse(
            {
                "campaign_id": campaign_id,
                "neu": len(codes),
                "schon_zugeordnet": len(schon),
                "unbekannt": unbekannt,
                "codes": codes,
            }
        )

    @app.post("/kampagnen/{campaign_id}/loeschen")
    def kampagne_loeschen(  # noqa: ANN202
        campaign_id: str, meldung: LoeschMeldung, request: Request
    ):
        """Loescht eine Kampagne - erst nach ausdruecklicher Bestaetigung.

        Ohne ``bestaetigt`` antwortet der Weg mit dem, was verlorenginge, und
        aendert nichts. Der Grund steht in ``store.delete_campaign``: Die
        Tracking-Codes gehen mit, und ein Code in einem veroeffentlichten
        Beitrag laesst sich nicht zurueckholen.

        Die Ereignisse bleiben - eine Auswertung von gestern behaelt ihre
        Zahlen. Was fehlt, ist danach der Weg vom Code zurueck zur Gruppe.
        """
        _nur_lokal(request)
        with _store() as store:
            campaign = store.load_campaign(campaign_id)
            if campaign is None:
                raise HTTPException(status_code=404, detail="Unbekannte Kampagne")

            verlust = store.was_geht_verloren(campaign_id)
            if not meldung.bestaetigt:
                return JSONResponse(
                    {
                        "campaign_id": campaign_id,
                        "name": campaign.name,
                        "geloescht": False,
                        **verlust,
                    }
                )

            store.delete_campaign(campaign_id)

        return JSONResponse(
            {"campaign_id": campaign_id, "name": campaign.name, "geloescht": True, **verlust}
        )

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
            {
                "anzahl": len(meldung.group_ids),
                "bearbeiten": meldung.bearbeiten,
                "grund": "" if meldung.bearbeiten else meldung.grund,
            }
        )

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
            ziel, ist_store = _ziel_url(store, tracking_code, cfg)

            if ist_store and not _ist_linkvorschau(user_agent):
                # Eine eigene Stufe, und sie heisst mit Bedacht nicht
                # "Installation": Gemessen ist, dass wir diesen Menschen zum
                # Play Store geschickt haben. Ob er dort installiert, meldet
                # uns niemand - der Beweis kommt erst als ``activation`` aus
                # der App selbst.
                store.record_event(
                    TrackingEvent(
                        tracking_code=tracking_code,
                        campaign_id=link.campaign_id,
                        group_id=link.group_id,
                        event_type=EventType.STORE_VISIT,
                        visitor_hash=_visitor_hash(
                            store,
                            request.client.host if request.client else "",
                            user_agent,
                        ),
                        source="redirect",
                    )
                )

        # 302, nicht 301: Ein dauerhaft gemerkter Umzug wuerde spaetere Klicks
        # am Zaehler vorbeifuehren.
        if ist_store:
            # Die Play-Adresse traegt den Code bereits im ``referrer`` - das
            # ist das Feld, das die Installation ueberlebt. Ein zweites ``ref``
            # daneben brauchte niemand und Google reichte es nicht weiter.
            return RedirectResponse(url=ziel, status_code=302)
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
                    store.audit(
                        "ereignis_unbekannter_code", meldung.tracking_code, meldung.event_type.value
                    )

            if not tracking_code and kennung:
                # Ohne Code die erste bekannte Zuordnung dieses Menschen erben -
                # ueber alle seine Kennungen hinweg. Sonst blieben spaete
                # Stufen ohne Gruppe, und genau die sind die interessanten.
                campaign_id, group_id, tracking_code = store.erste_zuordnung(kennung)

            # Ein Ereignis, das je Mensch nur einmal zaehlt (Download), wird
            # beim zweiten Mal nicht gespeichert. Die Meldung ist trotzdem
            # angekommen - die Antwort sagt beides.
            if (
                meldung.event_type in EINMAL_JE_MENSCH
                and kennung
                and (store.ereignis_bereits_gezaehlt(meldung.event_type, kennung))
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
                    {
                        "rule_id": r.rule_id,
                        "type": r.reward_type.value,
                        "value": r.value,
                        "status": r.status.value,
                    }
                    for r in store.rewards_of(user_ref)
                ],
            }

    return app
