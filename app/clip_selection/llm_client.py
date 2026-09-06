"""LLM client interface for clip selection.

There are two implementations:
  - MockLLMClient: deterministic, rule-based proposals used in tests
    and as a no-LLM fallback. It walks the transcription segments and
    emits proposals that fit the duration window, sliding by 30s.
  - HttpLLMClient: real implementation that calls an LLM provider.
    Skeleton only — wiring the provider (openai / anthropic /
    minimax-portal) will depend on `.env` credentials. Raises
    NotImplementedError for now.

The agent takes any `LLMClient`, so swapping implementations is trivial.
"""
from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class LLMClient(ABC):
    """Abstract LLM client."""

    @abstractmethod
    def complete(
        self, system_prompt: str, user_prompt: str
    ) -> Tuple[str, Optional[str]]:
        """Call the LLM. Returns (raw_text, model_id_or_none)."""


class MockLLMClient(LLMClient):
    """Deterministic proposal extractor.

    Strategy:
      - Parse the duration window from the user prompt.
      - Parse segments of shape `[Xs - Ys] text`.
      - Slide a 30s window; collect covered text; emit one proposal per
        window that results in a clip whose length fits within
        `[duration_min, duration_max * 1.2]`.
    """

    _DUR_RE = re.compile(r"duration:\s*([\d.]+)s\s*-\s*([\d.]+)s")
    _SEG_RE = re.compile(r"\[(\d+(?:\.\d+)?)s\s*-\s*(\d+(?:\.\d+)?)s\]\s*(.*)")

    def complete(
        self, system_prompt: str, user_prompt: str
    ) -> Tuple[str, Optional[str]]:
        m_dur = self._DUR_RE.search(user_prompt)
        if not m_dur:
            return json.dumps({"proposals": [], "notes": "no duration window"}), "mock-0"
        try:
            dmin = float(m_dur.group(1))
            dmax = float(m_dur.group(2))
        except (TypeError, ValueError):
            return json.dumps({"proposals": [], "notes": "bad duration"}), "mock-0"

        raw_segs = self._SEG_RE.findall(user_prompt)
        if not raw_segs:
            # No segment-level transcript; nothing to propose.
            return json.dumps({"proposals": [], "notes": "no segments"}), "mock-0"
        segments = [
            (float(s[0]), float(s[1]), (s[2] or "").strip())
            for s in raw_segs
        ]

        step = 30.0
        proposals: list[dict] = []
        if segments:
            first_start = segments[0][0]
            last_end = segments[-1][1]
        else:
            first_start, last_end = 0.0, 0.0

        cursor = first_start
        while cursor < last_end:
            window_end = cursor + dmax
            covered = [s for s in segments if s[1] > cursor and s[0] < window_end]
            if covered:
                seg_start = covered[0][0]
                seg_end = covered[-1][1]
                clip_len = seg_end - seg_start
                if dmin <= clip_len <= dmax * 1.2:
                    full_text = " ".join(s[2] for s in covered if s[2])
                    confidence = 0.55 + (len(full_text) % 5) * 0.05
                    proposals.append({
                        "start_time": seg_start,
                        "end_time": seg_end,
                        "score": round(min(confidence, 0.95), 2),
                        "reasoning": (
                            f"auto-picked {clip_len:.1f}s segment "
                            f"({len(covered)} segs)"
                        ),
                        "matched_keywords": [],
                    })
                    cursor = seg_end + step
                    continue
            cursor += step

        proposals = proposals[:6]
        return (
            json.dumps({"proposals": proposals, "notes": "mock"}),
            "mock-0",
        )


class HttpLLMClient(LLMClient):
    """Real LLM client. Skeleton — wire up when API keys are in `.env`.

    TODO when credentials are available:
      - Choose provider (openai-compatible, anthropic, minimax-portal).
      - Implement `complete` with the proper HTTP call.
      - Add rate-limit / retry logic.
    """

    def __init__(self, provider_url: str, model: str, api_key: str):
        self.provider_url = provider_url
        self.model = model
        self.api_key = api_key

    def complete(
        self, system_prompt: str, user_prompt: str
    ) -> Tuple[str, Optional[str]]:
        raise NotImplementedError(
            "HttpLLMClient is a skeleton. Configure the provider (openai / "
            "anthropic / minimax-portal) in app/clip_selection/llm_client.py "
            "and implement the actual HTTP call here."
        )
