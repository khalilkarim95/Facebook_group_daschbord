"""Redirect-Dienst und Meldeschnittstelle.

Zwei Aufgaben, mehr nicht:

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
import secrets
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from fbgroups.config import AppConfig, load_config
from fbgroups.marketing.models import EventType, ReferralStatus, TrackingEvent
from fbgroups.marketing.referral import code_fuer_benutzer, lege_empfehlung_an, setze_status
from fbgroups.marketing.rewards import bewerte_benutzer, load_reward_rules
from fbgroups.marketing.store import MarketingStore

# Optionale Abhaengigkeit, aber auf Modulebene importiert: Wegen
# ``from __future__ import annotations`` sind Typangaben Zeichenketten, die
# FastAPI erst beim Bauen der Wege aufloest. Stuenden die Namen nur innerhalb
# von ``create_app``, waere ``Request`` dort unbekannt - der Dienst haette
# jeden Aufruf mit 422 beantwortet.
try:
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import JSONResponse, RedirectResponse

    FASTAPI_VERFUEGBAR = True
except ImportError:  # pragma: no cover - haengt von der Installation ab
    FASTAPI_VERFUEGBAR = False

SALT_SCHLUESSEL = "visitor_salt"

# Welches Ereignis welchen Empfehlungsstand ergibt. Steht hier, damit die
# Zuordnung an einer Stelle nachlesbar ist.
_REFERRAL_STUFE = {
    EventType.REGISTRATION: ReferralStatus.REGISTERED,
    EventType.QUALIFIED: ReferralStatus.QUALIFIED,
    EventType.CONVERSION: ReferralStatus.CONVERTED,
}


class EventMeldung(BaseModel):
    """Was die Zielanwendung meldet.

    ``user_ref`` ist eine undurchsichtige Kennung aus der Zielanwendung. Namen,
    E-Mail-Adressen oder Telefonnummern gehoeren nicht hierher und werden auch
    nicht gespeichert, wenn sie faelschlich mitgeschickt wuerden - das Modell
    kennt schlicht keine solchen Felder.
    """

    event_type: EventType
    user_ref: str = Field(default="", max_length=128)
    tracking_code: str = Field(default="", max_length=64)
    referral_code: str = Field(default="", max_length=64)
    occurred_at: datetime | None = None


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

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

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

            besucher = _visitor_hash(
                store,
                request.client.host if request.client else "",
                request.headers.get("user-agent", ""),
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
    def melde_ereignis(meldung: EventMeldung):  # noqa: ANN202
        """Nimmt ein Ereignis der Zielanwendung entgegen.

        Bei ``registration`` mit ``referral_code`` entsteht zugleich die
        Empfehlung - mit allen Pruefungen. Wird sie abgewiesen, ist das
        Ereignis trotzdem gueltig: Der Mensch hat sich ja registriert.
        """
        with _store() as store:
            campaign_id = group_id = ""
            tracking_code = meldung.tracking_code

            if tracking_code:
                link = store.resolve_code(tracking_code)
                if link is not None:
                    campaign_id, group_id = link.campaign_id, link.group_id
            elif meldung.user_ref:
                # Ohne Code die erste bekannte Zuordnung des Benutzers erben -
                # sonst blieben spaete Stufen ohne Gruppe, und genau die sind
                # die interessanten.
                campaign_id, group_id, tracking_code = store.erste_zuordnung(meldung.user_ref)

            store.record_event(
                TrackingEvent(
                    tracking_code=tracking_code,
                    campaign_id=campaign_id,
                    group_id=group_id,
                    user_ref=meldung.user_ref,
                    event_type=meldung.event_type,
                    occurred_at=meldung.occurred_at or datetime.now(UTC),
                    source="api",
                )
            )

            antwort: dict[str, Any] = {"gespeichert": meldung.event_type.value}

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
    def referral_stand(user_ref: str) -> dict[str, Any]:
        """Empfehlungsstand eines Benutzers - fuer die Anzeige in der App."""
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
