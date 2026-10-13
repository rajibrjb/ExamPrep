import re

import cv2
import pytesseract

#  Anchored only on the "(Exam X, QN)" parenthetical, not the "Item N of M" prefix
# before it -- that prefix is OCR'd inconsistently ("Item" misread as "ltern"/"Itern"
# depending on the tesseract build) while the parenthetical reads reliably.
HEADER_RE = re.compile(
    r"\(Exam\s+([A-Za-z0-9]+)\s*,?\s*Q\s*(\d+)\)",
    re.IGNORECASE,
)
ANSWER_RE = re.compile(r"Answer\s*[:.]?\s*([A-F]{1,4})\b", re.IGNORECASE)
# Looser than ANSWER_RE: just locates the answer line itself (to bound the question
# body) even when the letter after it is OCR-garbled and ANSWER_RE can't parse it.
ANSWER_LINE_RE = re.compile(r"\bAnswer\b", re.IGNORECASE)

# Strips a garbled option-letter prefix (e.g. "A.", "B,", "©", "CD.") from an option
# line's OCR text. Tesseract frequently misreads the circled-letter glyph (C becomes
# "©", a letter is sometimes dropped or merged with its neighbour), so the exact
# label is not trustworthy -- only used here to clean the text, not to assign the letter.
LABEL_PREFIX_RE = re.compile(r"^(?:[A-Fa-f0-9]{1,2}|[^\w\s]{1,2})[.,:]?\s+")

# Row fragments within this many pixels of vertical offset are merged as one line
# (tesseract sometimes splits a single visual row into two "line" groups).
ROW_MERGE_TOLERANCE_PX = 6

# How far (in px) an option line's left edge is indented relative to the question/
# header text -- calibrated against the SPOTO VCE player screenshots (options sit
# at x0 ~37-38 while question/header/answer text sits at x0 ~11-17).
OPTION_INDENT_MIN_PX = 25
OPTION_INDENT_MAX_PX = 65

NOISE_WORDS = {"spoto", "ahamed", "amran"}


def _words_to_lines(data):
    """Group pytesseract.image_to_data output into lines of (text, bbox)."""
    lines = {}
    n = len(data["text"])
    for i in range(n):
        text = data["text"][i].strip()
        if not text:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        if key not in lines:
            lines[key] = {"words": [], "x0": x, "y0": y, "x1": x + w, "y1": y + h}
        line = lines[key]
        line["words"].append(text)
        line["x0"] = min(line["x0"], x)
        line["y0"] = min(line["y0"], y)
        line["x1"] = max(line["x1"], x + w)
        line["y1"] = max(line["y1"], y + h)

    ordered = sorted(lines.values(), key=lambda l: (l["y0"], l["x0"]))
    result = []
    for line in ordered:
        text = " ".join(w for w in line["words"] if w.lower() not in NOISE_WORDS)
        text = text.strip()
        if text:
            result.append({
                "text": text,
                "x0": line["x0"], "y0": line["y0"], "x1": line["x1"], "y1": line["y1"],
            })
    return result


def _merge_same_row_fragments(lines):
    merged = []
    for line in lines:
        if merged and abs(line["y0"] - merged[-1]["y0"]) <= ROW_MERGE_TOLERANCE_PX:
            prev = merged[-1]
            prev["text"] = f'{prev["text"]} {line["text"]}'
            prev["x1"] = max(prev["x1"], line["x1"])
            prev["y1"] = max(prev["y1"], line["y1"])
        else:
            merged.append(dict(line))
    return merged


def extract_lines(cleaned_image_bgr):
    rgb = cv2.cvtColor(cleaned_image_bgr, cv2.COLOR_BGR2RGB)
    data = pytesseract.image_to_data(rgb, output_type=pytesseract.Output.DICT)
    lines = _words_to_lines(data)
    return _merge_same_row_fragments(lines)


def parse_question(lines):
    """Classify OCR'd lines into header / question body / options / answer.

    Option lines are identified by their left-indent position, not by trying to
    read the (often garbled) A/B/C/D label glyph -- letters are assigned by the
    order options appear in, which is far more reliable than OCR'ing the label.
    """
    exam_label = None
    item_number = None
    header_idx = None
    answer_idx = None
    answer_letters = []

    for idx, line in enumerate(lines):
        match = HEADER_RE.search(line["text"])
        if match and header_idx is None:
            header_idx = idx
            exam_label = match.group(1)
            item_number = int(match.group(2))
            continue
        if ANSWER_LINE_RE.search(line["text"]):
            answer_idx = idx
            match = ANSWER_RE.search(line["text"])
            if match:
                answer_letters = list(match.group(1).upper())
            break  # nothing after the answer line is relevant, parseable or not

    body_start = (header_idx + 1) if header_idx is not None else 0
    body_end = answer_idx if answer_idx is not None else len(lines)
    body_lines = lines[body_start:body_end]

    question_parts = []
    options = []
    for line in body_lines:
        if OPTION_INDENT_MIN_PX <= line["x0"] <= OPTION_INDENT_MAX_PX:
            text = LABEL_PREFIX_RE.sub("", line["text"], count=1).strip()
            options.append({
                "option_letter": chr(ord("A") + len(options)),
                "option_text": text,
                "bbox": (line["x0"], line["y0"], line["x1"], line["y1"]),
            })
        else:
            question_parts.append(line["text"])

    return {
        "exam_label": exam_label,
        "item_number": item_number,
        "question_text": " ".join(question_parts).strip(),
        "options": options,
        "answer_letters": answer_letters,
        "raw_ocr_text": "\n".join(l["text"] for l in lines),
    }
