"""Campaign Engine: parser for free-form `source_instructions`.

Architecture_flow.md Step 3: OpenClaw/Mini­Max takes the user's natural-
language instructions and extracts structured rules.

This is a STUB: rule-based keyword extraction. Real implementation will
delegate to the Mini­Max LLM for richer extraction (semantic understanding
of "vertical shorts for tech tutorials" -> duration_max=60, format=9:16, etc.).
"""
from __future__ import annotations

import re

from app.campaign_engine.models import CampaignHints


# --- Regex helpers ---------------------------------------------------------

# Single value: "30 seconds", "2 minutes", "1.5m"
_DURATION_SINGLE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(seconds?|secs?|s|minutes?|mins?|m)\b",
    re.IGNORECASE,
)

# Range: "20-45 seconds", "1 to 2 minutes", "30–90 secs"
_DURATION_RANGE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:to|-|–|a)\s*(\d+(?:\.\d+)?)"
    r"\s*(seconds?|secs?|s|minutes?|mins?|m)\b",
    re.IGNORECASE,
)

_FORMAT_RE = re.compile(
    r"\b(9:16|1:1|16:9|9\s*:\s*16|vertical|horizontal|square)\b",
    re.IGNORECASE,
)

# Language: "in spanish", "in english", or just the word
_LANG_RE = re.compile(
    r"\b(?:in\s+)?(english|spanish|french|german|italian|"
    r"portuguese|japanese|chinese|korean)\b",
    re.IGNORECASE,
)

_CAPTION_RE = re.compile(
    r"\b(?:with|need|require|include|add)\s+subtitles?\b"
    r"|\b(?:captions?|subtitles?)\s+(?:required|needed|on)\b",
    re.IGNORECASE,
)

_WATERMARK_RE = re.compile(
    r"https?://\S+\.(?:png|jpg|jpeg|svg)\b", re.IGNORECASE
)

# Keywords: stop at first comma, period or end. Non-greedy on the chunk
# (we want the first meaningful phrase, not the whole sentence).
_KEYWORD_RE = re.compile(
    r"\b(?:about|on|regarding|keywords?\s*[:=]?)\s+"
    r"([^.]+)(?:\.|$)",
    re.IGNORECASE,
)

_EXCLUDE_RE = re.compile(
    r"\b(?:exclude|avoid|no|skip|without)\s+"
    r"([^,.\n]+?)(?:[.,\n]|$)",
    re.IGNORECASE,
)


# --- Helpers ---------------------------------------------------------------

def _parse_duration_to_seconds(value: float, unit: str) -> float:
    u = unit.lower()
    if u.startswith("s"):
        return float(value)
    if u.startswith("m"):
        return float(value) * 60.0
    return float(value)


def _format_alias(raw: str) -> str:
    raw = raw.lower().replace(" ", "")
    if "vertical" in raw:
        return "9:16"
    if "horizontal" in raw:
        return "16:9"
    if "square" in raw:
        return "1:1"
    return raw


def _collect_chunks(matches, split_by_comma: bool = True) -> list[str]:
    """Dedupe fragments from regex matches, preserving order."""
    seen: list[str] = []
    for m in matches:
        parts = m.split(",") if split_by_comma else [m]
        for p in parts:
            p = p.strip()
            if p and p not in seen:
                seen.append(p)
    return seen


# --- Public API -------------------------------------------------------------

def parse_instructions(text: str) -> CampaignHints:
    """Extract structured hints from a free-form instruction string.

    Real impl: call Mini­Max with a prompt that returns JSON matching
    CampaignHints. For now: a fast, rule-based regex pass.
    """
    if not text:
        return CampaignHints()

    hints = CampaignHints()

    # --- Duration (ranges first, then singles) ----------------------------
    durations: list[float] = []
    for m in _DURATION_RANGE_RE.finditer(text):
        durations.append(
            _parse_duration_to_seconds(m.group(1), m.group(3))
        )
        durations.append(
            _parse_duration_to_seconds(m.group(2), m.group(3))
        )
    for m in _DURATION_SINGLE_RE.finditer(text):
        durations.append(
            _parse_duration_to_seconds(m.group(1), m.group(2))
        )
    if durations:
        hints.duration_min = min(durations)
        hints.duration_max = max(durations)

    # --- Format -----------------------------------------------------------
    fmt_match = _FORMAT_RE.search(text)
    if fmt_match:
        hints.format = _format_alias(fmt_match.group(1))

    # --- Language (strip optional "in " prefix) ----------------------------
    lang_match = _LANG_RE.search(text)
    if lang_match:
        hints.language = lang_match.group(1).lower().strip()

    # --- Captions ---------------------------------------------------------
    if _CAPTION_RE.search(text):
        hints.captions_required = True

    # --- Watermark --------------------------------------------------------
    wm_match = _WATERMARK_RE.search(text)
    if wm_match:
        hints.watermark_url = wm_match.group(0)

    # --- Keywords / Exclude (collect all matches, dedupe) -----------------
    hints.keywords = _collect_chunks(_KEYWORD_RE.findall(text))
    hints.exclude_keywords = _collect_chunks(_EXCLUDE_RE.findall(text))

    # --- Leftover notes --------------------------------------------------
    notes: list[str] = []
    if not (hints.duration_min or hints.format or hints.captions_required):
        notes.append("no explicit duration/format/captions found in instructions")
    if not hints.keywords:
        notes.append("no explicit keywords found")
    if notes:
        hints.extra_notes = notes

    return hints
