"""Provider registry — single place to declare active providers.

Add new Whop tenants or other providers here. The CRON in main.py iterates
over `all_providers()`.
"""
from __future__ import annotations

from app.services.discovery.base import CampaignProvider
from app.services.discovery.providers.whop import WhopProvider


# Whop tenants (sub-apps). Add more as you find new tenants.
# 2026-09-17 (pipeline v2): primary tenant is Content Rewards.
# Whop exposes the campaign list JSON API at:
#   {tenant}/api/campaign/campaigns/discover?limit=N&sortBy=trending
# For host-based tenants (`whop.com/<slug>/`) the API actually lives on
# `<slug>.com/api/campaign/campaigns/discover` (subdomain split). The
# WhopProvider normalizes the URL — see `_discover_via_api`.
_WHOP_TENANTS: list[str] = [
    "https://contentrewards.com",                    # Content Rewards (primary, 2026-09-17)
    # Add additional tenants here when known.
]


_PROVIDERS: list[CampaignProvider] = [WhopProvider(t) for t in _WHOP_TENANTS]


def all_providers() -> list[CampaignProvider]:
    """Snapshot of registered providers (safe to iterate)."""
    return list(_PROVIDERS)


def get_provider(name: str) -> CampaignProvider | None:
    """Find a provider by its `name` attribute."""
    for p in _PROVIDERS:
        if p.name == name:
            return p
    return None
