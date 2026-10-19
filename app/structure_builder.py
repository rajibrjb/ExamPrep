import re

from app.color_detect import classify_options
from app.config import config
from app.ocr_engine import extract_lines, parse_question
from app.ollama_client import OllamaError, extract_via_vision

# Common Tesseract misreads on this font/rendering -- deterministic character-level
# fixes are safer than trusting a generative model's paraphrase (see note below).
_TEXT_FIXES = [
    (re.compile(r"\$(\d)"), r"S\1"),   # "$3 bucket" -> "S3 bucket"
]


def _clean_text(text):
    for pattern, repl in _TEXT_FIXES:
        text = pattern.sub(repl, text)
    return text


def _normalize_letters(letters):
    return set(letter.upper() for letter in letters if letter)


def build_question(cropped_bgr, cleaned_bgr, image_path, force_llm=False):
    """Deterministically extract question/options/answer via OCR + pixel color
    detection -- these two signals are independently derived from the same image
    and agreed in every sample tested, so they're treated as authoritative.

    A local vision-LLM (Ollama) pass is also run, but only as an advisory note
    for the review UI: testing showed it can be non-deterministic (different
    answers across runs on the same image) and occasionally hallucinates extra
    options or paraphrases wording, so its output is never used to decide
    correctness or to replace OCR'd text -- only surfaced as a hint when it
    disagrees, so a human can take a second look.

    force_llm runs the advisory pass even when SKIP_LLM_ADVISORY is set (used by
    the single-question "Re-analyze" button, where its latency is acceptable).
    """
    lines = extract_lines(cleaned_bgr)
    parsed = parse_question(lines, cropped_bgr)
    ocr_options = classify_options(cropped_bgr, parsed["options"])

    color_correct = _normalize_letters(
        o["option_letter"] for o in ocr_options if o["color_detected"] == "green"
    )
    answer_text_correct = _normalize_letters(parsed["answer_letters"])

    reasons = []
    llm_note = None
    if force_llm or not config.skip_llm_advisory:
        try:
            ollama_result = extract_via_vision(image_path)
            llm_opts = ollama_result.get("options", [])
            llm_correct = _normalize_letters(ollama_result.get("correct_letters", []))
            if len(llm_opts) != len(ocr_options):
                llm_note = f"local vision model saw {len(llm_opts)} options (expected {len(ocr_options)})"
            elif llm_correct and llm_correct != color_correct:
                llm_note = f"local vision model suggested {sorted(llm_correct)} instead of {sorted(color_correct)}"
        except OllamaError as e:
            llm_note = f"local vision model call failed: {e}"

    if not color_correct and not answer_text_correct:
        confidence, needs_review = "low", True
        reasons.append("no answer signal (color or Answer: line) could be extracted")
        final_correct = set()
    elif color_correct == answer_text_correct:
        confidence, needs_review = "high", False
        final_correct = color_correct
    else:
        confidence, needs_review = "low", True
        reasons.append(
            f"color-detected answer {sorted(color_correct)} disagrees with "
            f"Answer: line {sorted(answer_text_correct)}"
        )
        final_correct = color_correct or answer_text_correct

    if parsed.get("header_mismatch"):
        confidence, needs_review = "low", True
        reasons.append(
            "item number disagreed between the 'Item N of M' and '(Exam, QN)' header "
            "readings -- verify the item number against the screenshot"
        )

    if llm_note:
        reasons.append(llm_note)

    options = []
    for opt in ocr_options:
        letter = opt["option_letter"]
        options.append({
            "option_letter": letter,
            "option_text": _clean_text(opt["option_text"]),
            "is_correct": letter in final_correct,
            "color_detected": opt["color_detected"],
            "source_signal": "color",
        })

    return {
        "exam_label": parsed["exam_label"],
        "item_number": parsed["item_number"],
        "question_text": _clean_text(parsed["question_text"]),
        "raw_ocr_text": parsed["raw_ocr_text"],
        "options": options,
        "confidence": confidence,
        "needs_review": needs_review,
        "review_reasons": reasons,
    }
