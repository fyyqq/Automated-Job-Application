import os
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from fastapi import HTTPException

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def complete(prompt: str, max_tokens: int) -> str:
    if os.environ.get("LLM_PROVIDER") != "openrouter":
        raise HTTPException(status_code=501, detail="Only the openrouter provider is wired up")

    try:
        resp = httpx.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {os.environ.get('OPENROUTER_API_KEY', '')}"},
            json={
                "model": os.environ.get("OPENROUTER_MODEL", ""),
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": 0,
            },
            timeout=90,
        )
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"OpenRouter request failed: {type(e).__name__}")

    if resp.status_code == 429:
        raise HTTPException(status_code=429, detail=rate_limit_message(resp))
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=resp.text[:300])

    content = resp.json()["choices"][0]["message"].get("content")
    if not content:
        raise HTTPException(status_code=502, detail="Model returned empty content (reasoning may have used all max_tokens)")
    return content


def rate_limit_message(resp) -> str:
    # OpenRouter says when the daily free allowance resets, as epoch milliseconds in a header.
    try:
        reset_ms = int(resp.headers.get("X-RateLimit-Reset") or resp.json()["error"]["metadata"]["headers"]["X-RateLimit-Reset"])
        when = datetime.fromtimestamp(reset_ms / 1000, ZoneInfo("Asia/Kuala_Lumpur")).strftime("%Y-%m-%d %H:%M")
        return f"OpenRouter rate limit reached; the free allowance resets at {when} (Kuala Lumpur time)"
    except (KeyError, TypeError, ValueError):
        return "OpenRouter rate limit reached; try again later"
