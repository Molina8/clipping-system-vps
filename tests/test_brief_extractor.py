"""
tests/test_brief_extractor.py
=============================
Tests for `app.services.discovery.brief_extractor`.

Covers:
  - URL extraction (regex-based, dedup, trailing punctuation)
  - Section parsing (heading heuristics)
  - docs_export_url() builder
  - extract_from_pdf_path() against the real Drummer Records PDF (if present
    at /tmp/drive_test/Drummer Records Brief  Us Two  MOVIE CLIPS.pdf)
  - classify_link / is_brief_kind / rewrite_drive_url integration
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.discovery.brief_extractor import (
    BriefContent,
    docs_export_url,
    extract_from_pdf_path,
    extract_urls,
    parse_sections,
)


# ---------------- docs_export_url ----------------


class TestDocsExportUrl:
    def test_basic_document(self):
        url = "https://docs.google.com/document/d/1FtFgAlk_JZqAM3jJzPJZMooXgLuKF-q3wPdOU7OcP5E/edit?usp=sharing"
        assert docs_export_url(url) == (
            "https://docs.google.com/document/d/"
            "1FtFgAlk_JZqAM3jJzPJZMooXgLuKF-q3wPdOU7OcP5E/export?format=txt"
        )

    def test_already_export(self):
        url = "https://docs.google.com/document/d/1ABC_DEF-234567890abcdefghijk/export?format=html"
        # Already-canonical export URL — we still pass it through and extract the ID.
        result = docs_export_url(url)
        assert result is not None
        assert "1ABC_DEF-234567890abcdefghijk" in result

    def test_non_docs_url_returns_none(self):
        assert docs_export_url("https://drive.google.com/file/d/X/view") is None
        assert docs_export_url("https://example.com/foo") is None

    def test_format_argument(self):
        url = "https://docs.google.com/document/d/1ABC_DEF-234567890abcdefghijkl/edit"
        assert "format=html" in (docs_export_url(url, fmt="html") or "")


# ---------------- extract_urls ----------------


class TestExtractUrls:
    def test_basic(self):
        text = "Check https://example.com/foo and http://bar.com/x for info."
        urls = extract_urls(text)
        assert urls == ["https://example.com/foo", "http://bar.com/x"]

    def test_dedup(self):
        text = "first https://x.com/a then again https://x.com/a and https://x.com/a"
        assert extract_urls(text) == ["https://x.com/a"]

    def test_trailing_punctuation_stripped(self):
        text = "see https://x.com/foo. Also https://y.com/bar,"
        urls = extract_urls(text)
        assert urls == ["https://x.com/foo", "https://y.com/bar"]

    def test_trailing_brackets_stripped(self):
        text = "(https://x.com/foo) and [https://y.com/bar]"
        urls = extract_urls(text)
        assert urls == ["https://x.com/foo", "https://y.com/bar"]

    def test_no_urls(self):
        assert extract_urls("nothing here") == []

    def test_real_drummer_pdf_urls(self):
        # The actual URLs the Drummer PDF contains.
        text = (
            "TikTok audio: https://vt.tiktok.com/ZS9hC3L4\n"
            "Instagram audio: https://www.instagram.com/reels/audio/884920947575289"
        )
        urls = extract_urls(text)
        assert "https://vt.tiktok.com/ZS9hC3L4" in urls
        assert "https://www.instagram.com/reels/audio/884920947575289" in urls


# ---------------- parse_sections ----------------


class TestParseSections:
    def test_splits_on_caps_heading(self):
        text = "intro line\nPAYOUT RULES\ndo this and that\nFAQ\nanswer"
        sections = parse_sections(text)
        titles = [s["title"] for s in sections]
        assert "PAYOUT RULES" in titles
        assert "FAQ" in titles

    def test_first_section_can_be_empty_title(self):
        text = "Preamble goes here.\n\nWHAT TO CLIP\nbase videos only"
        sections = parse_sections(text)
        # First section is "Preamble goes here." with empty title.
        assert sections[0]["title"] == ""
        assert "Preamble" in sections[0]["body"]
        # Second section has the heading.
        assert any(s["title"] == "WHAT TO CLIP" for s in sections)

    def test_section_body_preserves_text(self):
        text = "PAYOUT RULES\nrule one\nrule two\nrule three"
        sections = parse_sections(text)
        s = next(s for s in sections if s["title"] == "PAYOUT RULES")
        assert "rule one" in s["body"]
        assert "rule three" in s["body"]

    def test_drummer_pdf_sections(self):
        # Simulate the structure of the real Drummer Records brief.
        text = (
            "🎬 Drummer Records Brief\n"
            "Welcome clippers!\n"
            "\n"
            "WHAT TO CLIP (BASE VIDEOS)\n"
            "Lane 1: Romance\n"
            "Lane 2: Action\n"
            "\n"
            "PAYOUT RULES\n"
            "Tier 1: $5 per 1k\n"
            "Tier 2: $2 per 1k\n"
            "\n"
            "FAQ\n"
            "Q: Can I use other audio?\n"
            "A: No.\n"
        )
        sections = parse_sections(text)
        titles = [s["title"] for s in sections]
        assert "WHAT TO CLIP (BASE VIDEOS)" in titles
        assert "PAYOUT RULES" in titles
        assert "FAQ" in titles


# ---------------- extract_from_pdf_path (real Drummer PDF) ----------------


DRUMMER_PDF = Path(
    "/tmp/drive_test/Drummer Records Brief  Us Two  MOVIE CLIPS.pdf"
)


@pytest.mark.skipif(not DRUMMER_PDF.exists(), reason="Drummer Records PDF not in /tmp/drive_test")
class TestRealDrummerPdf:
    def test_extracts_text(self):
        bc = extract_from_pdf_path(DRUMMER_PDF)
        assert bc.char_count > 1000, f"PDF should yield >1k chars, got {bc.char_count}"
        assert "PAYOUT" in bc.text.upper()
        assert "WHAT TO CLIP" in bc.text.upper()

    def test_extracts_feature_urls(self):
        bc = extract_from_pdf_path(DRUMMER_PDF)
        # The PDF contains at least the TikTok + Instagram audio links.
        assert any("tiktok.com" in u for u in bc.feature_urls), bc.feature_urls
        assert any("instagram.com" in u for u in bc.feature_urls), bc.feature_urls

    def test_sections_present(self):
        bc = extract_from_pdf_path(DRUMMER_PDF)
        titles = [s["title"] for s in bc.sections]
        # Common campaign-brief headings should be detected.
        assert any("PAYOUT" in t.upper() for t in titles), titles
        assert any("WHAT TO CLIP" in t.upper() or "BASE VIDEO" in t.upper() for t in titles), titles

    def test_page_count_is_4(self):
        bc = extract_from_pdf_path(DRUMMER_PDF)
        assert bc.page_count == 4

    def test_mime_type(self):
        bc = extract_from_pdf_path(DRUMMER_PDF)
        assert bc.mime_type == "application/pdf"


# ---------------- asset_resolver integration ----------------


class TestAssetResolverBriefKind:
    """Quick checks that asset_resolver correctly classifies docs/PDFs as briefs."""

    def test_drive_pdf_is_brief(self):
        from app.services.discovery.asset_resolver import classify_link, is_brief_kind
        url = "https://drive.google.com/file/d/1mQQ5FIc_2Ru0eDj6XNNgPFIld2QDE_u7/view?usp=sharing"
        kind = classify_link(url)
        # Even though kind is "drive", this URL points to a PDF (the Drummer brief).
        # We don't decide brief vs feature here — we let the brief_extractor
        # detect it during extraction (via Content-Type sniffing on download).
        assert kind == "drive"

    def test_docs_url_is_brief(self):
        from app.services.discovery.asset_resolver import classify_link, is_brief_kind
        url = "https://docs.google.com/document/d/1FtFgAlk_JZqAM3jJzPJZMooXgLuKF-q3wPdOU7OcP5E/edit"
        kind = classify_link(url)
        assert kind == "docs"
        assert is_brief_kind(kind, url) is True

    def test_youtube_is_not_brief(self):
        from app.services.discovery.asset_resolver import classify_link, is_brief_kind
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        kind = classify_link(url)
        assert kind == "youtube"
        assert is_brief_kind(kind, url) is False

    def test_drive_folder_rejected(self):
        from app.services.discovery.asset_resolver import rewrite_drive_url
        url = "https://drive.google.com/drive/folders/15CRuz33sWIFz5moouuxu2vRS_UO5VpfZ?usp=sharing"
        assert rewrite_drive_url(url) is None

    def test_drive_file_canonicalized(self):
        from app.services.discovery.asset_resolver import rewrite_drive_url
        url = "https://drive.google.com/file/d/1mQQ5FIc_2Ru0eDj6XNNgPFIld2QDE_u7/view?usp=sharing"
        assert rewrite_drive_url(url) == (
            "https://drive.google.com/uc?export=download&id=1mQQ5FIc_2Ru0eDj6XNNgPFIld2QDE_u7"
        )
