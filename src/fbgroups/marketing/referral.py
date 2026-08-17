"""Empfehlungen (Referrals) und ihr Missbrauchsschutz.

Ein Empfehlungscode gehoert zu genau einer Benutzerkennung, ein geworbener
Benutzer zu genau einem Werber. Die Regeln stehen bewusst an einer Stelle und
liefern immer eine **Begruendung** zurueck - eine abgelehnte Empfehlung ohne
nachvollziehbaren Grund waere im Streitfall wertlos.

Der Schutz ist absichtlich mild: Verdaechtiges wird auf ``review`` gesetzt,
nicht gesperrt. Ein falsch abgewiesener echter Nutzer kostet mehr als ein
zweifelhafter Fall, den jemand von Hand ansieht.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from fbgroups.config import AppConfig
from fbgroups.marketing.models import Referral, ReferralStatus
from fbgroups.marketing.store import MarketingStore

DEFAULT_PREFIX = "BTQ"
DEFAULT_LENGTH = 6

# Ohne 0/O und 1/I/L: Diese Codes werden vorgelesen und abgetippt.
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


@dataclass(frozen=True)
class Entscheidung:
    """Ergebnis einer Pruefung - angenommen oder nicht, immer mit Grund."""

    angenommen: bool
    grund: str
    status: ReferralStatus | None = None


def referral_prefix(config: AppConfig) -> str:
    return str(config.get("marketing", "referral", "prefix", default=DEFAULT_PREFIX)).upper()


def build_referral_code(config: AppConfig, vergeben: set[str]) -> str:
    """Erzeugt einen freien Code der Form ``BTQ-A8F4K2``.

    ``secrets`` statt ``random``: Ein erratbarer Empfehlungscode liesse sich
    fremden Konten zuschreiben.
    """
    prefix = referral_prefix(config)
    laenge = int(config.get("marketing", "referral", "length", default=DEFAULT_LENGTH))

    for _ in range(1000):
        kern = "".join(secrets.choice(ALPHABET) for _ in range(laenge))
        code = f"{prefix}-{kern}"
        if code not in vergeben:
            return code
    raise RuntimeError("Kein freier Empfehlungscode gefunden - Alphabet oder Laenge pruefen.")


def code_fuer_benutzer(store: MarketingStore, config: AppConfig, user_ref: str) -> str:
    """Liefert den Code eines Benutzers und legt ihn beim ersten Mal an."""
    vorhanden = store.referral_code_for(user_ref)
    if vorhanden:
        return vorhanden

    code = build_referral_code(config, store.referral_codes_vergeben())
    store.save_referral_code(code, user_ref)
    store.audit("referral_code_vergeben", user_ref, code)
    return code


def pruefe_empfehlung(
    store: MarketingStore,
    referral_code: str,
    referred_user_ref: str,
) -> Entscheidung:
    """Darf diese Empfehlung angelegt werden?

    Die vier Regeln aus der Anforderung, in dieser Reihenfolge:
    Code muss existieren, kein Selbst-Werben, kein zweiter Werber fuer
    denselben Benutzer, und der Geworbene muss eine Kennung haben - die gibt
    es erst nach der Registrierung.
    """
    if not referred_user_ref:
        return Entscheidung(False, "keine Benutzerkennung - Empfehlung erst nach Registrierung")

    referrer = store.user_for_referral_code(referral_code)
    if referrer is None:
        return Entscheidung(False, f"unbekannter Empfehlungscode: {referral_code}")

    if referrer == referred_user_ref:
        return Entscheidung(False, "Selbst-Empfehlung ist ausgeschlossen")

    bestehend = store.referral_for_referred(referred_user_ref)
    if bestehend is not None:
        if bestehend.referrer_user_ref == referrer:
            return Entscheidung(False, "diese Empfehlung gibt es bereits")
        return Entscheidung(
            False,
            f"bereits von {bestehend.referrer_user_ref} geworben - zweiter Werber abgewiesen",
            status=ReferralStatus.REVIEW,
        )

    return Entscheidung(True, "angenommen", status=ReferralStatus.REGISTERED)


def lege_empfehlung_an(
    store: MarketingStore,
    referral_code: str,
    referred_user_ref: str,
    campaign_id: str = "",
    group_id: str = "",
) -> tuple[Referral | None, Entscheidung]:
    """Prueft und speichert eine Empfehlung. Jeder Ausgang wird protokolliert."""
    entscheidung = pruefe_empfehlung(store, referral_code, referred_user_ref)

    if not entscheidung.angenommen:
        store.audit(
            "referral_abgelehnt", referred_user_ref, f"{referral_code}: {entscheidung.grund}"
        )
        return None, entscheidung

    referrer = store.user_for_referral_code(referral_code) or ""
    referral = Referral(
        referral_code=referral_code,
        referrer_user_ref=referrer,
        referred_user_ref=referred_user_ref,
        status=ReferralStatus.REGISTERED,
        campaign_id=campaign_id,
        group_id=group_id,
    )
    store.save_referral(referral)
    store.audit("referral_angelegt", referred_user_ref, f"{referral_code} von {referrer}")
    return referral, entscheidung


def setze_status(
    store: MarketingStore,
    referred_user_ref: str,
    status: ReferralStatus,
    note: str = "",
) -> Referral | None:
    """Hebt eine Empfehlung auf die naechste Stufe.

    Zurueck geht es nicht von selbst: ``qualified`` wird nicht wieder
    ``registered``, weil eine spaetere Meldung nachklappert. Herabstufen ist
    ein Fall fuer die Hand - ueber ``review``.
    """
    referral = store.referral_for_referred(referred_user_ref)
    if referral is None:
        return None

    reihenfolge = [
        ReferralStatus.PENDING,
        ReferralStatus.REGISTERED,
        ReferralStatus.QUALIFIED,
        ReferralStatus.CONVERTED,
    ]
    if (
        status in reihenfolge
        and referral.status in reihenfolge
        and reihenfolge.index(status) <= reihenfolge.index(referral.status)
    ):
        return referral

    referral.status = status
    referral.note = note or referral.note
    referral.updated_at = datetime.now(UTC)
    store.save_referral(referral)
    store.audit("referral_status", referred_user_ref, f"{status.value} {note}".strip())
    return referral
