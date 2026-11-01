import os

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

    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=resp.text[:300])

    content = resp.json()["choices"][0]["message"].get("content")
    if not content:
        raise HTTPException(status_code=502, detail="Model returned empty content (reasoning may have used all max_tokens)")
    return content
