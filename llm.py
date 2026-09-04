"""Thin OpenRouter chat-completions client."""

import os
import time

import httpx

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def complete(
    prompt: str,
    model: str,
    system: str | None = None,
    max_tokens: int = 4000,
    max_retries: int = 3,
) -> str:
    api_key = os.environ["OPENROUTER_API_KEY"]

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    for attempt in range(max_retries + 1):
        response = httpx.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": model, "messages": messages, "max_tokens": max_tokens},
            timeout=60,
        )
        if response.status_code == 429 and attempt < max_retries:
            wait = float(response.headers.get("Retry-After", 5))
            time.sleep(wait)
            continue
        break

    response.raise_for_status()
    body = response.json()
    if "choices" not in body:
        # A 200 response can still carry a provider-level error body
        # (observed in practice, e.g. transiently around a rate-limit
        # window reset) instead of raising an HTTP error status.
        raise RuntimeError(f"Model {model} returned no choices: {body}")
    choice = body["choices"][0]
    content = choice["message"].get("content")
    if not content:
        # Reasoning models can burn the whole token budget on the
        # "reasoning" field and return empty content — surface that clearly
        # instead of crashing deep in a caller that assumes a string.
        raise RuntimeError(
            f"Model {model} returned empty content "
            f"(finish_reason={choice.get('finish_reason')!r}). "
            f"Raise max_tokens or use a non-reasoning model."
        )
    return content
