"""Validate LLM-sourced ClipProposals against a NormalizedSpec.

Per architecture_flow.md Step 14: OpenClaw validates candidates against
campaign rules before promoting them to RENDER jobs. This module holds
that pure rule logic (no DB, no I/O) so it is unit-testable on its own.
"""
from __future__ import annotations

import logging
from typing import List, Tuple

from app.campaign_engine.models import NormalizedSpec

from .models import ClipProposal

logger = logging.getLogger(__name__)


def validate_proposal(
    proposal: ClipProposal,
    spec: NormalizedSpec,
    transcription_text: str,
) -> Tuple[bool, str]:
    """Validate a single proposal. Returns (is_valid, reason).

    Rules:
      - Duration within `[duration_min * 0.9, duration_max * 1.1]`
        (small tolerance for boundaries).
      - None of the spec's `exclude_keywords` appear (case-insensitive)
        in the segment's covered text.
    """
    duration = float(proposal.end_time) - float(proposal.start_time)
    if duration < float(spec.duration_min) * 0.9:
        return (
            False,
            f"too short: {duration:.1f}s < {float(spec.duration_min):.1f}s",
        )
    if duration > float(spec.duration_max) * 1.1:
        return (
            False,
            f"too long: {duration:.1f}s > {float(spec.duration_max):.1f}s",
        )

    lower = (transcription_text or "").lower()
    for kw in spec.exclude_keywords or []:
        kw_l = str(kw).strip().lower()
        if kw_l and kw_l in lower:
            return False, f"contains excluded keyword: {kw}"

    return True, "ok"


def filter_valid(
    proposals: List[ClipProposal],
    spec: NormalizedSpec,
    transcription_text: str,
) -> Tuple[List[ClipProposal], List[Tuple[ClipProposal, str]]]:
    """Split proposals into (valid, rejected-with-reason)."""
    valid: List[ClipProposal] = []
    rejected: List[Tuple[ClipProposal, str]] = []
    for p in proposals:
        ok, reason = validate_proposal(p, spec, transcription_text)
        if ok:
            valid.append(p)
        else:
            rejected.append((p, reason))
    return valid, rejected
