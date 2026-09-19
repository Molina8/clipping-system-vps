"""Minimal xAI Chat Completions client."""
from __future__ import annotations

import json
import os
from typing import Any

import httpx


def grok_chat_json(prompt: str, *, timeout: float = 60.0) -> dict[str, Any]:
    key = os.environ.get("XAI_API_KEY") or ""
    if not key:
        raise RuntimeError("XAI_API_KEY missing")
    base = os.environ.get("XAI_API_BASE", "https://api.x.ai/v1").rstrip("/")
    model = os.environ.get("XAI_MODEL", "grok-4-1-fast")
    payload = {
        "model": model,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": "Return only valid JSON. No markdown.",
            },
            {"role": "user", "content": prompt},
        ],
    }
    with httpx.Client(timeout=timeout) as client:
        r = client.post(
            f"{base}/chat/completions",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        r.raise_for_status()
        data = r.json()
    content = data["choices"][0]["message"]["content"]
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in content
        )
    return json.loads(content)
