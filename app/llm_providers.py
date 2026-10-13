import requests

from app.config import config

TIMEOUT_SECONDS = 180


class LLMError(RuntimeError):
    pass


def _ollama(model, system, user, api_key=None):
    prompt = f"{system}\n\n{user}" if system else user
    try:
        resp = requests.post(
            f"{config.ollama_base_url}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        raise LLMError(f"Ollama request failed: {e}") from e
    return resp.json().get("response", "")


def _openai(model, system, user, api_key):
    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            timeout=TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        raise LLMError(f"OpenAI request failed: {e}") from e
    return resp.json()["choices"][0]["message"]["content"]


def _anthropic(model, system, user, api_key):
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 4096,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            timeout=TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        raise LLMError(f"Anthropic request failed: {e}") from e
    return resp.json()["content"][0]["text"]


def _gemini(model, system, user, api_key):
    try:
        resp = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            params={"key": api_key},
            json={
                "system_instruction": {"parts": [{"text": system}]},
                "contents": [{"parts": [{"text": user}]}],
            },
            timeout=TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        raise LLMError(f"Gemini request failed: {e}") from e
    return resp.json()["candidates"][0]["content"]["parts"][0]["text"]


def _openrouter(model, system, user, api_key):
    try:
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            timeout=TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        raise LLMError(f"OpenRouter request failed: {e}") from e
    return resp.json()["choices"][0]["message"]["content"]


_DISPATCH = {
    "ollama": _ollama,
    "openai": _openai,
    "anthropic": _anthropic,
    "gemini": _gemini,
    "openrouter": _openrouter,
}


def generate_text(provider, model, system, user, api_key=None):
    fn = _DISPATCH.get(provider)
    if fn is None:
        raise LLMError(f"Unknown LLM provider: {provider}")
    if provider != "ollama" and not api_key:
        raise LLMError(f"{provider} is selected but no API key is configured (see Settings)")
    return fn(model, system, user, api_key)
