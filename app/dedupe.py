import difflib
import re

from app.config import config

_WHITESPACE_RE = re.compile(r"\s+")


def _normalize(text):
    return _WHITESPACE_RE.sub(" ", (text or "").strip().lower())


def question_similarity(text_a, text_b):
    return difflib.SequenceMatcher(None, _normalize(text_a), _normalize(text_b)).ratio()


def classify_against_existing(new_question_text, new_options, existing_question_text, existing_options):
    """Compare a freshly-extracted question against the one already stored under
    the same (exam_label, item_number). Returns ("duplicate" | "conflict", similarity).

    Same item number is a strong identity signal (the VCE player assigns it per
    question slot), so two screenshots sharing it are presumed to be the *same*
    question re-captured -- unless the content disagrees enough that a human
    should look, e.g. a mis-OCR'd item number collided with an unrelated question,
    or OCR drifted badly enough that we can't trust the "duplicate" call.
    """
    similarity = question_similarity(new_question_text, existing_question_text)
    new_correct = {o["option_letter"] for o in new_options if o["is_correct"]}
    existing_correct = {o["option_letter"] for o in existing_options if o["is_correct"]}

    is_duplicate = (
        similarity >= config.dedupe_similarity_threshold
        and new_correct == existing_correct
    )
    return ("duplicate" if is_duplicate else "conflict"), similarity
