"""Praemien.

Die Regeln stehen in ``config/rewards.yaml`` - Schwellenwerte sind eine
fachliche Entscheidung und duerfen keine Codeaenderung kosten. Aus
"3 qualifizierte Empfehlungen -> 7 Premium-Tage" wird "5 -> 10", indem eine
Zahl in der Datei geaendert wird.

Vergeben wird erst, wenn die Empfehlung den geforderten Status **erreicht
hat**; eine Praemie je Regel und Benutzer, durch einen eindeutigen Schluessel
in der Datenbank abgesichert. Geld ist bewusst kein Praemientyp - was
ausgezahlt wird, entscheidet niemand nebenbei in einer YAML-Datei.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from fbgroups.marketing.models import (
    Referral,
    ReferralStatus,
    Reward,
    RewardRule,
    RewardStatus,
    RewardType,
)
from fbgroups.marketing.store import MarketingStore

REWARDS_DATEI = "rewards.yaml"

# Reihenfolge der Stufen: "qualified" zaehlt auch, was schon "converted" ist -
# sonst verloere jemand seine Praemie, indem er weiterkommt.
_RANG = {
    ReferralStatus.PENDING: 0,
    ReferralStatus.REGISTERED: 1,
    ReferralStatus.QUALIFIED: 2,
    ReferralStatus.CONVERTED: 3,
}


def load_reward_rules(root: Path) -> list[RewardRule]:
    """Liest die Praemienregeln. Fehlt die Datei, gibt es keine Praemien."""
    pfad = Path(root) / "config" / REWARDS_DATEI
    if not pfad.exists():
        return []

    with pfad.open("r", encoding="utf-8") as fh:
        daten: dict[str, Any] = yaml.safe_load(fh) or {}

    regeln = []
    for eintrag in daten.get("rewards", []) or []:
        regeln.append(
            RewardRule(
                rule_id=str(eintrag["id"]),
                label_de=str(eintrag.get("label_de", "")),
                threshold=int(eintrag.get("threshold", 1)),
                metric=str(eintrag.get("metric", "qualified")),
                reward_type=RewardType(str(eintrag.get("type", "custom"))),
                value=str(eintrag.get("value", "")),
                active=bool(eintrag.get("active", True)),
            )
        )
    return sorted(regeln, key=lambda r: r.threshold)


def zaehle_empfehlungen(referrals: list[Referral], metric: str) -> int:
    """Wie viele Empfehlungen erfuellen die geforderte Stufe?

    Gezaehlt wird "mindestens diese Stufe": Wer bereits zum Kunden geworden
    ist, zaehlt selbstverstaendlich auch als qualifiziert.
    """
    try:
        mindestens = _RANG[ReferralStatus(metric)]
    except ValueError:
        return 0
    return sum(1 for r in referrals if _RANG.get(r.status, -1) >= mindestens)


def bewerte_benutzer(
    store: MarketingStore,
    regeln: list[RewardRule],
    user_ref: str,
) -> list[Reward]:
    """Vergibt alle erreichten, noch nicht vergebenen Praemien.

    Returns: die neu vergebenen Praemien (leer, wenn nichts hinzukam).
    """
    referrals = store.referrals_of(user_ref)
    bereits = {r.rule_id for r in store.rewards_of(user_ref)}
    neu: list[Reward] = []

    for regel in regeln:
        if not regel.active or regel.rule_id in bereits:
            continue
        if zaehle_empfehlungen(referrals, regel.metric) < regel.threshold:
            continue

        reward = Reward(
            user_ref=user_ref,
            rule_id=regel.rule_id,
            reward_type=regel.reward_type,
            value=regel.value,
            status=RewardStatus.EARNED,
            note=regel.label_de,
        )
        if store.save_reward(reward):
            store.audit(
                "reward_vergeben",
                user_ref,
                f"{regel.rule_id}: {regel.reward_type.value} {regel.value}",
            )
            neu.append(reward)

    return neu


def fortschritt(
    store: MarketingStore,
    regeln: list[RewardRule],
    user_ref: str,
) -> list[tuple[RewardRule, int, RewardStatus]]:
    """Stand je Regel: erreichte Zahl und Status der Praemie."""
    referrals = store.referrals_of(user_ref)
    vergeben = {r.rule_id: r.status for r in store.rewards_of(user_ref)}

    stand = []
    for regel in regeln:
        erreicht = zaehle_empfehlungen(referrals, regel.metric)
        status = vergeben.get(regel.rule_id, RewardStatus.LOCKED)
        stand.append((regel, erreicht, status))
    return stand
