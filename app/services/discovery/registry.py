"""Provider registry — single place to declare active providers.

Add new Whop tenants or other providers here. The CRON in main.py iterates
over `all_providers()`.
"""
from __future__ import annotations

from app.services.discovery.base import CampaignProvider
from app.services.discovery.providers.whop import WhopProvider


# Whop tenants (sub-apps). Add more as you find new tenants.
_WHOP_TENANTS: list[str] = [
    "https://b4e0vdqv6zgqeqj4pfgm.apps.whop.com",  # Clipping Culture (primary)
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
