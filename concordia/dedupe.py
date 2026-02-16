import os
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


def _summary_template(deduped_prompts: List[str]) -> str:
    lines = [
        "You are a session summarization agent for Concordia.",
        "Summarize the following deduped prompts into a practical project context document.",
        "Return Markdown only.",
        "Include these sections in order:",
        "## Session Goals",
        "## Implemented Or Requested Work",
        "## Open Questions Or Risks",
        "## Next Steps",
        "",
        "Deduped prompts:",
    ]
    for idx, prompt in enumerate(deduped_prompts, start=1):
        lines.append(f"### Prompt {idx}")
        lines.append(prompt.strip())
        lines.append("")
    return "\n".join(lines).strip()


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


def summarize_with_gemini(deduped_prompts: List[str], api_key: str) -> str:
    if not deduped_prompts:
        return ""
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": _summary_template(deduped_prompts)}],
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


def summarize_fallback(deduped_prompts: List[str]) -> str:
    lines = ["## Session Goals", "- Consolidate participant prompts into executable work.", ""]
    lines.append("## Implemented Or Requested Work")
    for idx, prompt in enumerate(deduped_prompts, start=1):
        lines.append(f"- Prompt {idx}: {prompt.strip()}")
    lines.append("")
    lines.append("## Open Questions Or Risks")
    lines.append("- No Gemini summary available; review prompt list directly.")
    lines.append("")
    lines.append("## Next Steps")
    lines.append("- Continue from the latest deduped prompt context.")
    return "\n".join(lines).strip()


def build_session_summary(deduped_prompts: List[str], api_key: str) -> str:
    if api_key:
        return summarize_with_gemini(deduped_prompts, api_key)
    return summarize_fallback(deduped_prompts)
