"""
tests/test_brief_in_prompt.py
=============================
Tests that `build_user_prompt` correctly injects the campaign brief when
present, and stays clean when absent.

Covers:
  - no brief → brief block absent
  - brief with text + sections + feature_urls → all three sections present
  - huge text → truncated with marker
  - many feature_urls → capped at 20, with "+N more" line
  - empty sections list → "(no sections detected)" placeholder
  - malformed brief (not a dict) → silent passthrough
"""

from __future__ import annotations

import pytest

from app.clip_selection.prompts import build_user_prompt


def _spec() -> dict:
    return {
        "duration_min": 20.0,
        "duration_max": 60.0,
        "format": "9:16",
        "language": "en",
        "keywords": ["viral", "emotional"],
        "exclude_keywords": ["nude", "violence"],
        "captions_required": True,
    }


def _transcription() -> dict:
    return {
        "text": "Hello world",
        "segments": [
            {"start": 0.0, "end": 5.0, "text": "Hello world"},
            {"start": 5.0, "end": 10.0, "text": "Second segment"},
        ],
    }


class TestNoBrief:
    def test_no_brief_returns_clean_prompt(self):
        prompt = build_user_prompt(
            source_url="https://youtube.com/watch?v=x",
            duration_seconds=120.0,
            transcription=_transcription(),
            spec=_spec(),
        )
        assert "Campaign brief" not in prompt

    def test_explicit_none_brief_returns_clean_prompt(self):
        prompt = build_user_prompt(
            source_url="https://youtube.com/watch?v=x",
            duration_seconds=120.0,
            transcription=_transcription(),
            spec=_spec(),
            campaign_brief=None,
        )
        assert "Campaign brief" not in prompt


class TestBriefInjection:
    BRIEF = {
        "text": "WHAT TO CLIP: romantic movie scenes.\nPAYOUT RULES: $5 per 1k.",
        "feature_urls": [
            "https://vt.tiktok.com/abc",
            "https://www.instagram.com/reels/audio/123",
        ],
        "sections": [
            {"title": "WHAT TO CLIP", "body": "romantic movie scenes"},
            {"title": "PAYOUT RULES", "body": "$5 per 1k views"},
        ],
        "page_count": 2,
        "mime_type": "application/pdf",
        "char_count": 80,
    }

    def test_brief_block_present(self):
        prompt = build_user_prompt(
            source_url="https://youtube.com/watch?v=x",
            duration_seconds=120.0,
            transcription=_transcription(),
            spec=_spec(),
            campaign_brief=self.BRIEF,
        )
        assert "Campaign brief" in prompt
        assert "2-page application/pdf" in prompt
        assert "WHAT TO CLIP" in prompt
        assert "PAYOUT RULES" in prompt

    def test_feature_urls_injected(self):
        prompt = build_user_prompt(
            source_url="https://youtube.com/watch?v=x",
            duration_seconds=120.0,
            transcription=_transcription(),
            spec=_spec(),
            campaign_brief=self.BRIEF,
        )
        assert "https://vt.tiktok.com/abc" in prompt
        assert "https://www.instagram.com/reels/audio/123" in prompt
        assert "Feature / source URLs" in prompt

    def test_sections_rendered(self):
        prompt = build_user_prompt(
            source_url="https://youtube.com/watch?v=x",
            duration_seconds=120.0,
            transcription=_transcription(),
            spec=_spec(),
            campaign_brief=self.BRIEF,
        )
        assert "## Sections detected" in prompt
        # Section title appears as a sub-heading.
        assert "### WHAT TO CLIP" in prompt
        assert "romantic movie scenes" in prompt

    def test_dont_invent_rules_instruction_present(self):
        prompt = build_user_prompt(
            source_url="https://youtube.com/watch?v=x",
            duration_seconds=120.0,
            transcription=_transcription(),
            spec=_spec(),
            campaign_brief=self.BRIEF,
        )
        # The injection must explicitly tell the LLM not to make up rules.
        assert "Do NOT invent rules" in prompt

    def test_existing_fields_still_present(self):
        prompt = build_user_prompt(
            source_url="https://youtube.com/watch?v=x",
            duration_seconds=120.0,
            transcription=_transcription(),
            spec=_spec(),
            campaign_brief=self.BRIEF,
        )
        # The previous spec / metadata / transcription blocks must still be there.
        assert "duration: 20s - 60s" in prompt
        assert "source_url: https://youtube.com/watch?v=x" in prompt
        assert "[0.0s - 5.0s] Hello world" in prompt


class TestBriefTextTruncation:
    def test_huge_text_is_truncated(self):
        big = "x" * 10_000
        brief = {
            "text": big,
            "feature_urls": [],
            "sections": [],
            "page_count": 50,
            "mime_type": "text/plain",
            "char_count": 10_000,
        }
        prompt = build_user_prompt(
            source_url="https://example.com/x",
            duration_seconds=10.0,
            transcription={"text": "", "segments": []},
            spec=_spec(),
            campaign_brief=brief,
        )
        assert "truncated at 4000" in prompt
        # The full 10000 chars should NOT be there — only the truncated excerpt.
        assert big not in prompt
        # But the first 4000 chars of the brief should be there.
        assert big[:4000] in prompt


class TestBriefUrlsCap:
    def test_many_urls_capped_at_20(self):
        urls = [f"https://example.com/{i}" for i in range(50)]
        brief = {
            "text": "short brief",
            "feature_urls": urls,
            "sections": [],
            "page_count": 1,
            "mime_type": "application/pdf",
            "char_count": 11,
        }
        prompt = build_user_prompt(
            source_url="https://example.com/x",
            duration_seconds=10.0,
            transcription={"text": "", "segments": []},
            spec=_spec(),
            campaign_brief=brief,
        )
        # First 20 must appear.
        for i in range(20):
            assert f"https://example.com/{i}" in prompt
        # 21st onwards must NOT appear.
        assert "https://example.com/20" not in prompt
        # "more" indicator with the right count.
        assert "... (30 more)" in prompt


class TestEdgeCases:
    def test_empty_brief_text_renders_no_brief_block(self):
        # A brief whose text is empty/whitespace is effectively no brief.
        # We still inject the block but with "(empty brief)" so the LLM knows.
        brief = {
            "text": "",
            "feature_urls": ["https://example.com/x"],
            "sections": [],
            "page_count": None,
            "mime_type": "unknown",
            "char_count": 0,
        }
        prompt = build_user_prompt(
            source_url="https://example.com/x",
            duration_seconds=10.0,
            transcription={"text": "", "segments": []},
            spec=_spec(),
            campaign_brief=brief,
        )
        assert "Campaign brief" in prompt
        assert "(empty brief)" in prompt

    def test_no_sections_placeholder(self):
        brief = {
            "text": "Plain brief with no detectable headings.",
            "feature_urls": [],
            "sections": [],
            "page_count": 1,
            "mime_type": "text/plain",
            "char_count": 40,
        }
        prompt = build_user_prompt(
            source_url="https://example.com/x",
            duration_seconds=10.0,
            transcription={"text": "", "segments": []},
            spec=_spec(),
            campaign_brief=brief,
        )
        assert "(no sections detected)" in prompt

    def test_malformed_brief_does_not_crash(self):
        # If campaign_brief is something weird (string, list, etc.), we
        # silently fall back to no-brief behavior instead of raising.
        prompt = build_user_prompt(
            source_url="https://example.com/x",
            duration_seconds=10.0,
            transcription={"text": "", "segments": []},
            spec=_spec(),
            campaign_brief="not a dict",  # type: ignore[arg-type]
        )
        assert "Campaign brief" not in prompt
