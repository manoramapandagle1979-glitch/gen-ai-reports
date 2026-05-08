"""OpenRouter API client with retry, rate-limit handling, and optional web search."""
import os
import json
import re
import time
import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# Primary model for report generation (reasoning model)
MODEL = "inclusionai/ring-2.6-1t:free"
RESEARCH_MODEL = "inclusionai/ring-2.6-1t:free"


def _extract_json(text: str) -> Optional[str]:
    """
    Extract JSON from model output.
    Handles: <think> blocks, ```json fences, truncated JSON (via json-repair),
    and bare JSON objects/arrays anywhere in the response.
    """
    # Remove <think>...</think> reasoning blocks
    text = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE)
    text = text.strip()

    # 1. Try extracting from a ```json ... ``` code fence (complete fence)
    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if m:
        candidate = m.group(1).strip()
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            pass

    # 2. Strip all fence markers and work with the raw text
    cleaned = re.sub(r"```(?:json)?", "", text).strip()

    # 3. Find the outermost JSON object or array (complete, parseable)
    for pattern in (r"(\{[\s\S]*\})", r"(\[[\s\S]*\])"):
        m = re.search(pattern, cleaned)
        if m:
            candidate = m.group(1)
            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError:
                pass

    # 4. Last resort: json-repair handles truncated / malformed JSON
    try:
        from json_repair import repair_json
        # Find the first { or [ and repair from there
        start = min(
            (cleaned.find(c) for c in ('{', '[') if cleaned.find(c) != -1),
            default=-1,
        )
        if start != -1:
            repaired_str = repair_json(cleaned[start:], skip_json_loads=False)
            repaired = json.loads(repaired_str)
            # Only accept non-trivial objects/arrays
            if isinstance(repaired, dict) and len(repaired) > 2:
                return repaired_str
            if isinstance(repaired, list) and len(repaired) > 1:
                return repaired_str
    except Exception:
        pass

    return None


def _api_key() -> str:
    key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY not set in pipeline/.env")
    return key


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://neographanalytics.com",
        "X-Title": "NeographAnalytics Data Pipeline",
    }


def call(
    user_prompt: str,
    system_prompt: str = "",
    temperature: float = 0.5,
    max_tokens: int = 6000,
    max_retries: int = 3,
    base_delay: float = 5.0,
    model: str = MODEL,
    web_search: bool = False,
) -> Optional[dict | list]:
    """
    Call OpenRouter and return parsed JSON.

    Args:
        web_search: When True, adds the OpenRouter web-search plugin so the
                    model can retrieve live information before answering.
    """
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})

    payload: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if web_search:
        payload["plugins"] = [{"id": "web"}]

    for attempt in range(max_retries):
        try:
            resp = requests.post(
                OPENROUTER_URL,
                headers=_headers(),
                json=payload,
                timeout=180,
            )

            if resp.status_code == 401:
                raise RuntimeError(
                    "OpenRouter API key rejected (401). "
                    "Get a valid key at https://openrouter.ai/keys and set "
                    "OPENROUTER_API_KEY in pipeline/.env"
                )

            if resp.status_code == 429:
                wait = base_delay * (2 ** attempt)
                logger.warning("Rate limited — waiting %.0fs (attempt %d)", wait, attempt + 1)
                time.sleep(wait)
                continue

            resp.raise_for_status()
            data = resp.json()
            content = _get_content(data["choices"][0])

            json_str = _extract_json(content)
            if not json_str:
                logger.warning(
                    "No JSON found in response (attempt %d). Preview: %s",
                    attempt + 1,
                    content[:600],
                )
                if attempt < max_retries - 1:
                    time.sleep(base_delay)
                continue

            return json.loads(json_str)

        except RuntimeError:
            raise  # auth errors — don't retry, surface immediately
        except json.JSONDecodeError as exc:
            logger.error("JSON parse error (attempt %d): %s", attempt + 1, exc)
            if attempt < max_retries - 1:
                time.sleep(base_delay)
        except requests.RequestException as exc:
            logger.error("HTTP error (attempt %d): %s", attempt + 1, exc)
            if attempt < max_retries - 1:
                time.sleep(base_delay * (attempt + 1))

    logger.error("All %d attempts failed", max_retries)
    return None


def call_text(
    user_prompt: str,
    system_prompt: str = "",
    temperature: float = 0.5,
    max_tokens: int = 2000,
    web_search: bool = False,
    model: str = RESEARCH_MODEL,
) -> Optional[str]:
    """
    Like call() but returns raw text instead of parsed JSON.
    Used for market intelligence gathering.
    """
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})

    payload: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if web_search:
        payload["plugins"] = [{"id": "web"}]

    try:
        resp = requests.post(
            OPENROUTER_URL,
            headers=_headers(),
            json=payload,
            timeout=120,
        )
        if resp.status_code == 401:
            raise RuntimeError(
                "OpenRouter API key rejected (401). "
                "Get a valid key at https://openrouter.ai/keys"
            )
        resp.raise_for_status()
        data = resp.json()
        content = _get_content(data["choices"][0])
        # Strip any inline <think> blocks
        content = re.sub(r"<think>[\s\S]*?</think>", "", content, flags=re.IGNORECASE)
        return content.strip() or None
    except RuntimeError:
        raise
    except Exception as exc:
        logger.error("call_text failed: %s", exc)
        return None


def _get_content(choice: dict) -> str:
    """
    Extract text from a response choice.
    Reasoning models (nemotron) may return content=null with all output in
    the 'reasoning' field when max_tokens is very small.
    """
    msg = choice.get("message", {})
    content = msg.get("content") or ""
    if not content:
        # Fall back to reasoning field for reasoning models
        content = msg.get("reasoning") or ""
    return content.strip()


def test_connection() -> bool:
    """Quick smoke-test: returns True if the API key is valid and model responds."""
    logger.info("Testing OpenRouter connection…")
    try:
        resp = requests.post(
            OPENROUTER_URL,
            headers=_headers(),
            json={
                "model": MODEL,
                "messages": [{"role": "user", "content": "Reply with the single word: READY"}],
                "max_tokens": 200,
                "temperature": 0,
            },
            timeout=30,
        )
        if resp.status_code == 401:
            logger.error(
                "API key invalid (401). Visit https://openrouter.ai/keys to get a valid key."
            )
            return False
        resp.raise_for_status()
        data = resp.json()
        content = _get_content(data["choices"][0])
        logger.info("OpenRouter connection OK. Model replied: %s", (content or "(no content)")[:80])
        return True  # 200 status means key is valid even if content is empty
    except Exception as exc:
        logger.error("Connection test failed: %s", exc)
        return False
