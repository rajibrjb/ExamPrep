from string import Template

import bleach
import markdown as markdown_lib

from app.llm_providers import generate_text
from app.settings import current_provider_config, get_all_settings

SYSTEM_PROMPT = "You are an expert, friendly technical tutor who writes clear Markdown explanations."

ALLOWED_TAGS = [
    "p", "br", "strong", "em", "ul", "ol", "li",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "code", "pre", "hr",
    "table", "thead", "tbody", "tr", "th", "td",
    "a",
]
ALLOWED_ATTRS = {"a": ["href", "title"]}


def build_prompt(template_str, question):
    options_lines = []
    correct_letters = []
    for opt in question["options"]:
        marker = " (correct)" if opt["is_correct"] else ""
        options_lines.append(f'{opt["option_letter"]}. {opt["option_text"]}{marker}')
        if opt["is_correct"]:
            correct_letters.append(opt["option_letter"])

    return Template(template_str).safe_substitute(
        question_text=question["question_text"],
        options_block="\n".join(options_lines),
        correct_letters=", ".join(correct_letters) or "(not yet confirmed)",
    )


def render_markdown(text):
    html = markdown_lib.markdown(text, extensions=["fenced_code", "tables", "nl2br"])
    return bleach.clean(html, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS, strip=True)


def explain_question(conn, question):
    """question: a dict as returned by db.get_question (includes `options`).
    Returns {html, raw, provider, model} -- caller decides whether/how to persist."""
    settings = get_all_settings(conn)
    provider, model, api_key = current_provider_config(settings)
    user_prompt = build_prompt(settings["explain_prompt_template"], question)
    raw_text = generate_text(provider, model, system=SYSTEM_PROMPT, user=user_prompt, api_key=api_key)
    return {
        "html": render_markdown(raw_text),
        "raw": raw_text,
        "provider": provider,
        "model": model,
    }
