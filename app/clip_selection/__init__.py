"""Clip selection agent (Steps 12-13 of architecture_flow.md).

Step 12: OpenClaw/MiniMax reviews new transcriptions.
Step 13: OpenClaw/MiniMax analyzes content + timestamps + rules and
         proposes CANDIDATOS which are validated against the campaign
         rules and persisted as `Candidate` rows.

Architecture:
  - `prompts`   build SYSTEM + USER prompts from (asset, transcription, spec)
  - `models`    Pydantic models for LLM output (ClipProposal, responses)
  - `llm_client` LLMClient interface + MockLLMClient + HttpLLMClient skeleton
  - `validator` validate ClipProposal against a NormalizedSpec
  - `agent`     orchestrate: prompt -> LLM -> parse -> validate -> persist
"""

from .agent import ClipSelectionAgent
from .llm_client import HttpLLMClient, LLMClient, MockLLMClient
from .models import (
    ClipProposal,
    ClipSelectionRequest,
    ClipSelectionResponse,
)
from .prompts import SYSTEM_PROMPT, build_user_prompt
from .validator import filter_valid, validate_proposal

__all__ = [
    "ClipSelectionAgent",
    "LLMClient",
    "MockLLMClient",
    "HttpLLMClient",
    "ClipProposal",
    "ClipSelectionRequest",
    "ClipSelectionResponse",
    "SYSTEM_PROMPT",
    "build_user_prompt",
    "validate_proposal",
    "filter_valid",
]
