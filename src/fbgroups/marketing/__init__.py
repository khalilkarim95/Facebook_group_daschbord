"""Marketing-Erweiterung: Kampagnen, Zuordnung und Tracking-Codes.

Baut auf dem vorhandenen Gruppenbestand auf und aendert an ihm nichts. Es
werden keine Beitraege veroeffentlicht, keine Nachrichten verschickt und keine
Gruppen automatisiert - das Modul verwaltet ausschliesslich die eigene
Vorbereitung und die Zuordnung von Links zu Gruppen.
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
from fbgroups.marketing.tracking import (
    app_base_url,
    code_prefix,
    next_tracking_code,
    tracking_url,
)

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
    "app_base_url",
    "code_prefix",
    "next_tracking_code",
    "tracking_url",
]
