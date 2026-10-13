import base64
import json

import requests

from app.config import config

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "question_text": {"type": "string"},
        "options": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "letter": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["letter", "text"],
            },
        },
        "correct_letters": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["question_text", "options", "correct_letters"],
}

PROMPT = (
    "This is a screenshot of a multiple-choice exam question. Some option lines are "
    "colored green (the correct answer) and others are colored red (incorrect). There "
    "is also an 'Answer:' line stating the correct letter. Ignore the faint repeating "
    "watermark text in the background. Extract the question text, every lettered "
    "option's text (A, B, C, D, ...), and which letter(s) are marked correct (green / "
    "stated in the Answer line). Respond with JSON only."
)


class OllamaError(RuntimeError):
    pass


def extract_via_vision(image_path):
    with open(image_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode("ascii")

    try:
        resp = requests.post(
            f"{config.ollama_base_url}/api/generate",
            json={
                "model": config.ollama_model,
                "prompt": PROMPT,
                "images": [image_b64],
                "format": RESPONSE_SCHEMA,
                "stream": False,
            },
            timeout=config.ollama_timeout_seconds,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        raise OllamaError(f"Ollama request failed: {e}") from e

    body = resp.json()
    raw = body.get("response", "")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise OllamaError(f"Ollama returned non-JSON response: {raw[:200]!r}") from e
