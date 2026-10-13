DEFAULT_EXPLAIN_PROMPT = """\
You are a patient AWS certification tutor. Explain the following exam question \
to someone who is completely new to AWS, as if they've never seen the relevant \
service before.

Question:
$question_text

Options:
$options_block

The correct answer is: $correct_letters

Write your explanation in Markdown with headings and bullet points, covering:
1. What the question is really asking, in plain language.
2. Why the correct option is right.
3. Why each other option is wrong.
4. Any AWS concept worth remembering for the exam -- note if anything about this \
topic may have changed since your training data, since AWS services evolve.

Keep it beginner-friendly but accurate.
"""

# provider -> {model setting key: default model, api_key setting key: default (blank)}
PROVIDERS = {
    "ollama": {"model_key": "ollama_model", "default_model": "qwen3.8:27b-mlx", "needs_key": False},
    "openai": {"model_key": "openai_model", "default_model": "gpt-4o-mini", "needs_key": True},
    "anthropic": {"model_key": "anthropic_model", "default_model": "claude-sonnet-5", "needs_key": True},
    "gemini": {"model_key": "gemini_model", "default_model": "gemini-2.0-flash", "needs_key": True},
    "openrouter": {"model_key": "openrouter_model", "default_model": "openai/gpt-4o-mini", "needs_key": True},
}

DEFAULTS = {"llm_provider": "ollama", "explain_prompt_template": DEFAULT_EXPLAIN_PROMPT}
for _provider, _meta in PROVIDERS.items():
    DEFAULTS[_meta["model_key"]] = _meta["default_model"]
    if _meta["needs_key"]:
        DEFAULTS[f"{_provider}_api_key"] = ""


def get_all_settings(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT key, value FROM settings")
        stored = dict(cur.fetchall())
    merged = dict(DEFAULTS)
    merged.update({k: v for k, v in stored.items() if v is not None})
    return merged


def set_settings(conn, updates):
    with conn.cursor() as cur:
        for key, value in updates.items():
            cur.execute(
                """
                INSERT INTO settings (key, value, updated_at) VALUES (%s, %s, now())
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
                """,
                (key, value),
            )


def current_provider_config(settings):
    """The active provider's model + api key, resolved from `settings`."""
    provider = settings.get("llm_provider", "ollama")
    meta = PROVIDERS.get(provider, PROVIDERS["ollama"])
    model = settings.get(meta["model_key"]) or meta["default_model"]
    api_key = settings.get(f"{provider}_api_key", "") if meta["needs_key"] else None
    return provider, model, api_key
