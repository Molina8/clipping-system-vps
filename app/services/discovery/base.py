"""CampaignProvider abstract base class — multi-provider discovery contract."""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.services.discovery.models import DiscoveredCampaign


class CampaignProvider(ABC):
    """Pluggable discovery source. Multiple providers can coexist.

    Subclass this and implement `discover()` and `fetch_detail()`.
    Register the instance in `app.services.discovery.registry`.
    """
    name: str  # stable identifier, e.g. "whop"

    @abstractmethod
    def discover(self, *, limit: int = 50) -> list[DiscoveredCampaign]:
        """Fetch the list of campaigns from the source.

        Should be idempotent and side-effect-free: re-running yields
        the same set of campaigns (modulo what's new on the source).

        MUST NOT raise on transient errors — wrap in try/except, log, return [].
        """

    @abstractmethod
    def fetch_detail(self, campaign: DiscoveredCampaign) -> DiscoveredCampaign | None:
        """Optional: enrich a campaign with detail-page data
        (requirements, asset links). Returns None if not applicable.
        """
