"""Tests for the campaign_engine (parser + normalizer).

Architecture_flow.md Steps 3 + 13: OpenClaw/Mini­Max extracts structured
rules from free-form `source_instructions` and normalizes them per source.
"""


def test_parser_extracts_duration_in_seconds():
    from app.campaign_engine.parser import parse_instructions
    h = parse_instructions("Make 30-90 second clips")
    assert h.duration_min == 30.0
    assert h.duration_max == 90.0


def test_parser_extracts_duration_in_minutes():
    from app.campaign_engine.parser import parse_instructions
    h = parse_instructions("clips of 1 to 2 minutes")
    assert h.duration_min == 60.0
    assert h.duration_max == 120.0


def test_parser_extracts_format_vertical():
    from app.campaign_engine.parser import parse_instructions
    h = parse_instructions("vertical shorts for instagram")
    assert h.format == "9:16"


def test_parser_extracts_format_explicit():
    from app.campaign_engine.parser import parse_instructions
    h = parse_instructions("create 1:1 square clips for IG")
    assert h.format == "1:1"


def test_parser_extracts_captions_required():
    from app.campaign_engine.parser import parse_instructions
    h = parse_instructions("videos with subtitles for spanish")
    assert h.captions_required is True


def test_parser_extracts_language():
    from app.campaign_engine.parser import parse_instructions
    h = parse_instructions("videos in spanish about tech")
    assert h.language == "spanish"


def test_parser_extracts_watermark_url():
    from app.campaign_engine.parser import parse_instructions
    h = parse_instructions("apply https://example.com/logo.png as watermark")
    assert h.watermark_url == "https://example.com/logo.png"


def test_parser_extracts_keywords():
    from app.campaign_engine.parser import parse_instructions
    h = parse_instructions("about AI, machine learning, robotics.")
    assert "AI" in h.keywords
    assert "machine learning" in h.keywords
    assert "robotics" in h.keywords


def test_parser_extracts_exclude_keywords():
    from app.campaign_engine.parser import parse_instructions
    h = parse_instructions("exclude politics, no religion")
    assert "politics" in h.exclude_keywords
    assert "religion" in h.exclude_keywords


def test_parser_handles_empty():
    from app.campaign_engine.parser import parse_instructions
    h = parse_instructions("")
    assert h.duration_min is None
    assert h.duration_max is None
    assert h.format is None


def test_parser_handles_none():
    from app.campaign_engine.parser import parse_instructions
    h = parse_instructions(None)  # type: ignore
    assert h.duration_min is None


def test_normalizer_youtube_defaults():
    from app.campaign_engine.parser import parse_instructions
    from app.campaign_engine.normalizer import normalize
    h = parse_instructions("")  # no hints
    spec = normalize(h, "youtube")
    assert spec.source_provider == "youtube"
    assert spec.format == "9:16"
    assert spec.language == "en"
    assert spec.captions_required is True  # youtube default
    assert spec.duration_min == 30.0
    assert spec.duration_max == 60.0


def test_normalizer_hints_win_over_defaults():
    from app.campaign_engine.parser import parse_instructions
    from app.campaign_engine.normalizer import normalize
    h = parse_instructions("vertical shorts in spanish, 10-20 seconds")
    spec = normalize(h, "youtube")
    # hints win
    assert spec.duration_min == 10.0
    assert spec.duration_max == 20.0
    assert spec.language == "spanish"
    # defaults fill the rest
    assert spec.format == "9:16"
    assert spec.captions_required is True


def test_normalizer_tiktok_defaults():
    from app.campaign_engine.normalizer import normalize
    from app.campaign_engine.models import CampaignHints
    spec = normalize(CampaignHints(), "tiktok")
    assert spec.format == "9:16"
    assert spec.duration_max == 180.0


def test_normalizer_instagram_defaults():
    from app.campaign_engine.normalizer import normalize
    from app.campaign_engine.models import CampaignHints
    spec = normalize(CampaignHints(), "instagram")
    assert spec.format == "1:1"
    assert spec.duration_min == 3.0


def test_normalizer_manual_uses_spanish():
    from app.campaign_engine.normalizer import normalize
    from app.campaign_engine.models import CampaignHints
    spec = normalize(CampaignHints(), "manual")
    assert spec.language == "es"


def test_normalizer_unknown_provider_uses_other_defaults():
    from app.campaign_engine.normalizer import normalize
    from app.campaign_engine.models import CampaignHints
    spec = normalize(CampaignHints(), "spotify")  # not in defaults
    assert spec.source_provider == "spotify"
    # should fall back to "other" defaults
    assert spec.format == "9:16"


def test_normalizer_enforces_max_gt_min():
    from app.campaign_engine.normalizer import normalize
    from app.campaign_engine.models import CampaignHints
    h = CampaignHints(duration_min=50.0, duration_max=30.0)  # max < min
    spec = normalize(h, "youtube")
    assert spec.duration_max > spec.duration_min


def test_parser_full_workflow():
    """End-to-end: free-form text -> CampaignHints -> NormalizedSpec."""
    from app.campaign_engine.parser import parse_instructions
    from app.campaign_engine.normalizer import normalize

    text = (
        "Make vertical shorts 20-45 seconds about AI, in spanish, "
        "with subtitles and watermark https://logo.com/wm.png"
    )
    hints = parse_instructions(text)
    spec = normalize(hints, "youtube")

    assert spec.duration_min == 20.0
    assert spec.duration_max == 45.0
    assert spec.format == "9:16"
    assert spec.language == "spanish"
    assert spec.captions_required is True
    assert spec.watermark_url == "https://logo.com/wm.png"
    assert "AI" in spec.keywords
