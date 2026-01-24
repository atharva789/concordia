import os
import textwrap
from typing import List, Dict

import requests

GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"


def _prompt_template(prompts: List[Dict[str, str]]) -> str:
    lines = [
        "You are a deduplication agent.",
        "Combine related user requests into a single multi-step prompt for Claude Code.",
        "Remove duplicates, keep all unique requirements, and output ONLY the merged prompt.",
        "",
        "User prompts:",
    ]
    for item in prompts:
        lines.append(f"- {item['user']}: {item['text'].strip()}")
    return "\n".join(lines)


def dedupe_with_gemini(prompts: List[Dict[str, str]], api_key: str) -> str:
    if not prompts:
        return ""
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": _prompt_template(prompts)}],
            }
        ]
    }
    resp = requests.post(
        GEMINI_ENDPOINT,
        params={"key": api_key},
        json=payload,
        timeout=60,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Gemini API error: {resp.status_code} {resp.text}")
    data = resp.json()
    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError("Gemini API returned no candidates")
    parts = candidates[0].get("content", {}).get("parts") or []
    if not parts:
        raise RuntimeError("Gemini API returned empty content")
    return "".join(part.get("text", "") for part in parts).strip()


def dedupe_fallback(prompts: List[Dict[str, str]]) -> str:
    lines = ["Combine these prompts:", ""]
    for item in prompts:
        lines.append(f"- {item['user']}: {item['text'].strip()}")
    return "\n".join(lines).strip()


def build_deduped_prompt(prompts: List[Dict[str, str]], api_key: str) -> str:
    if api_key:
        return dedupe_with_gemini(prompts, api_key)
    return dedupe_fallback(prompts)
