"""Prompt builders for the clip selection LLM call.

The LLM receives a `CampaignSpec` (duration window, format, language,
keywords, exclude_keywords) and a video transcription with segment-level
timestamps (typically from WhisperX on the Worker). It must propose
3-6 short, stand-alone clips.

Output MUST be strict JSON (shape documented in SYSTEM_PROMPT). We do
not require word-level timestamps to be in the prompt: segment-level is
enough for the LLM to pick meaningful spans.
"""
from __future__ import annotations

from typing import Optional


SYSTEM_PROMPT: str = """\
You are Clipper, an expert viral-clip selector.
Given a campaign spec and a video transcription with timestamps, propose
4 to 6 short segments (within the duration window) that would make great
standalone clips for the target platform. Each segment MUST be a distinct
non-overlapping portion of the video that can stand alone as a clip.

CRITICAL — Ranking (MUST follow):
1. Rank proposals from STRONGEST to WEAKEST by viral potential.
2. The "proposals" array MUST be sorted in that order (index 0 = best).
3. The "score" field MUST strictly match the rank (proposal[0].score >=
   proposal[1].score >= ...).
4. Each "reasoning" field MUST explicitly justify the rank position with
   concrete evidence: hook type, payoff, audience fit, keyword match, etc.

Rules:
- Every proposal MUST be inside the [duration_min, duration_max] window.
- Do NOT propose overlapping or duplicate segments (different
  start_time/end_time ranges, even partially).
- A "good" segment has a strong hook (curiosity, controversy, surprise,
  emotion, payoff, or a clear value proposition) early in the segment.
- Prefer segments whose speech ends cleanly (no mid-word cut).
- If exclude_keywords are listed, NO proposal may contain them.
- Score each proposal 0.0-1.0 reflecting your confidence. Highest score
  in the array MUST be the strongest clip; lowest MUST be the weakest.
- Output MUST be strict JSON of this exact shape:
    { "proposals": [
        { "rank":     <int 1..N>,           # position in sorted order (1 = best)
          "start_time": <float seconds>,
          "end_time":   <float seconds>,
          "score":      <float 0.0-1.0>,
          "reasoning":  "<short rationale citing hook type, payoff, fit>",
          "matched_keywords": [<string>, ...] } ],
      "notes": "<optional free text>" }
- DO NOT include any prose outside the JSON.
"""


def build_user_prompt(
    source_url: str,
    duration_seconds: float,
    transcription: dict,
    spec: dict,
) -> str:
    """Build the USER-side prompt from the asset + campaign spec.

    The transcription dict is expected to come from the transcribe
    job's `result.data` and usually has:
      { "text": "...full transcript...",
        "language": "en",
        "segments": [ {"start", "end", "text"}, ... ],
        "words":    [ {"word", "start", "end", "score"}, ... ] }
    Anything missing is silently skipped.
    """
    duration_min = float(spec.get("duration_min") or 20.0)
    duration_max = float(spec.get("duration_max") or 60.0)
    fmt = spec.get("format") or "9:16"
    language = spec.get("language") or "en"
    keywords = list(spec.get("keywords") or [])
    exclude = list(spec.get("exclude_keywords") or [])
    captions_required = bool(spec.get("captions_required"))

    text = transcription.get("text", "") or ""
    segs = transcription.get("segments") or transcription.get("chunks") or []
    words = transcription.get("words") or []

    # Compact segment view for the prompt (cap to keep tokens bounded).
    segments_text = "\n".join(
        f"[{float(s.get('start', 0)):.1f}s - {float(s.get('end', 0)):.1f}s] "
        f"{(s.get('text') or '').strip()}"
        for s in segs[:200]
    )

    spec_lines = [
        f"- duration: {duration_min:.0f}s - {duration_max:.0f}s",
        f"- format: {fmt}",
        f"- language: {language}",
        f"- captions_required: {captions_required}",
    ]
    if keywords:
        spec_lines.append(f"- keywords (prefer these topics): {', '.join(map(str, keywords))}")
    if exclude:
        spec_lines.append(f"- exclude_keywords (NEVER include): {', '.join(map(str, exclude))}")

    word_note = (
        f"\n\n(First 50 word timestamps available, {len(words)} total.)"
        if words else ""
    )

    return (
        "# Video metadata\n"
        f"- source_url: {source_url}\n"
        f"- duration: {float(duration_seconds or 0):.1f}s\n\n"
        "# Campaign spec\n"
        + "\n".join(spec_lines)
        + "\n\n# Transcription (segments)\n"
        + (segments_text if segments_text else (text[:5000] if text else "(empty)"))
        + word_note
        + "\n\n# Task\n"
        "Propose 3-6 candidate clips following the system instructions. "
        "Output JSON only.\n"
    )
