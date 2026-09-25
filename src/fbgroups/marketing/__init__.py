"""Marketing-Erweiterung: Kampagnen, Zuordnung, Texte und Kommentarautomatik.

Baut auf dem vorhandenen Gruppenbestand auf und aendert an ihm nichts.
Seit dem 25.09.2026 ohne Tracking: Kein Code und keine Zaehladresse verlaesst
mehr den Rechner.
"""

from fbgroups.marketing.models import (
    Campaign,
    CampaignGroup,
    CampaignParticipation,
    CampaignStatus,
    ContactStatus,
    GroupMarketing,
    MarketingStatus,
    PermissionStatus,
)
from fbgroups.marketing.store import (
    MarketingStore,
    UnknownCampaignError,
    UnknownGroupError,
)
from fbgroups.marketing.tracking import code_prefix, next_tracking_code

__all__ = [
    "Campaign",
    "CampaignGroup",
    "CampaignParticipation",
    "CampaignStatus",
    "ContactStatus",
    "GroupMarketing",
    "MarketingStatus",
    "MarketingStore",
    "PermissionStatus",
    "UnknownCampaignError",
    "UnknownGroupError",
    "code_prefix",
    "next_tracking_code",
]
