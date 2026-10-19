import re

import cv2
import numpy as np
import pytesseract

# Header parts matched INDEPENDENTLY rather than as one rigid pattern -- every
# element of "Item N of M (Exam X, QN)" has been OCR-garbled at some point
# ("Item"->"ltem", ","->"." as in "(Exam B. Q6)", digits gaining/losing a digit),
# so requiring them all in one regex silently drops the whole header when any
# single character misreads.
EXAM_RE = re.compile(r"Exam\s+([A-Za-z0-9]{1,3})\b", re.IGNORECASE)
QN_RE = re.compile(r"\bQ\s*(\d{1,4})\s*\)", re.IGNORECASE)
ANSWER_RE = re.compile(r"Answer\s*[:.]?\s*([A-F]{1,4})\b", re.IGNORECASE)
# Looser than ANSWER_RE: just locates the answer line itself (to bound the question
# body) even when the letter after it is OCR-garbled and ANSWER_RE can't parse it.
ANSWER_LINE_RE = re.compile(r"\bAnswer\b", re.IGNORECASE)

# "Item N of M" prefix on the header line. Cross-checked against the "(Exam X, QN)"
# parenthetical: either number can gain/lose a digit under OCR (observed: "Q66"
# read as "Q686", and separately "Item 1 of 1336" read as "ltem 1 of 1333"), but
# they rarely both misread the *item* number, so agreement means high confidence
# and disagreement flags the question for review.
ITEM_PREFIX_RE = re.compile(r"\b(\d{1,4})\s+of\s+\d{3,4}\b")

# Unrevealed radio-button glyph that tesseract merges into an option line's text
# (e.g. "O A Configure an Amazon..."). Its presence both marks the line as an
# option (even when the glyph drags the line's left edge back to the question
# indent, defeating indent-based detection) and must be stripped from the text.
RADIO_PREFIX_RE = re.compile(r"^[O0Q©@®]\s+")
OPTION_START_RE = re.compile(r"^[O0Q©@®]\s+[A-Fa-f][.,:]?\s+")

# Player chrome that leaks into the body when there's no "Answer:" line to bound
# it (hidden-state captures): footer buttons, zoom indicator, scrollbar artifacts.
CHROME_RE = re.compile(
    r"Select the best choice|Review\s*All|Save Session|End Exam|Current Score|VCE Player",
    re.IGNORECASE,
)
JUNK_LINE_RE = re.compile(r"^[TI|l1\s.·_-]{1,10}$")

# Strips a garbled option-letter prefix (e.g. "A.", "B,", "©", "CD.") from an option
# line's OCR text. Tesseract frequently misreads the circled-letter glyph (C becomes
# "©", a letter is sometimes dropped or merged with its neighbour), so the exact
# label is not trustworthy -- only used here to clean the text, not to assign the letter.
LABEL_PREFIX_RE = re.compile(r"^(?:[A-Fa-f0-9]{1,2}|[^\w\s]{1,2})[.,:]?\s+")

# Row fragments within this many pixels of vertical offset are merged as one line
# (tesseract sometimes splits a single visual row into two "line" groups).
ROW_MERGE_TOLERANCE_PX = 6

# An option line's left edge sits at a consistent RATIO relative to the header
# line's indent -- ~2.2-2.3x across every capture resolution seen so far (e.g.
# header x0=16, option x0=37 on 2560-wide landscape captures; header x0=32,
# option x0=74 on 3024-wide Retina captures). Using a ratio of the header's own
# indent, rather than fixed pixel thresholds, makes this resolution-independent
# -- fixed pixel values broke silently when screenshots started coming from a
# different display/DPI than the one they were first calibrated against.
OPTION_INDENT_RATIO_MIN = 1.8
# Capped at 3.0x: wrapped option CONTINUATION lines indent deeper still (~3.7x,
# e.g. x0=118 vs header x0=32) and must NOT look like new options -- they're
# appended to the previous option instead (see parse_question).
OPTION_INDENT_RATIO_MAX = 3.0
DEFAULT_BASE_INDENT_PX = 16  # fallback if the header line itself wasn't found

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


# Radio/checkbox control glyphs render light-gray: V roughly 185-232 at low
# saturation. That window is unoccupied by anything else in the left margin --
# question text is near-black (V<160) and the pale watermark never drops below
# V~235 (its antialiasing blends toward white, i.e. upward). So "mid-gray
# pixels present AND no dark pixels" in the zone left of the option text is a
# reliable signature for "this line starts with a control = a new option".
GLYPH_V_MIN, GLYPH_V_MAX = 185, 232
GLYPH_S_MAX = 60
GLYPH_MIN_PIXELS = 15
GLYPH_MAX_DARK_PIXELS = 10


def _has_control_glyph(cropped_bgr, line, base_indent):
    """True if a radio/checkbox control sits in the left margin of this line's
    row in the original (uncleaned) image. Catches option rows whose control +
    letter were swallowed by OCR, which shifts the line's x0 unpredictably and
    defeats indent-based detection (observed with checkbox multi-select
    questions: five options OCR'd at x0 74/76/120/99/121)."""
    if cropped_bgr is None:
        return False
    zone_x0 = int(base_indent * 0.6)
    zone_x1 = int(base_indent * 2.1)
    zone_y0 = max(0, line["y0"] - 4)
    zone_y1 = min(cropped_bgr.shape[0], line["y1"] + 4)
    zone = cropped_bgr[zone_y0:zone_y1, zone_x0:zone_x1]
    if zone.size == 0:
        return False
    hsv = cv2.cvtColor(zone, cv2.COLOR_BGR2HSV)
    _, s, v = cv2.split(hsv)
    dark = np.count_nonzero(v < 160)
    midgray = np.count_nonzero((v >= GLYPH_V_MIN) & (v <= GLYPH_V_MAX) & (s < GLYPH_S_MAX))
    return midgray >= GLYPH_MIN_PIXELS and dark < GLYPH_MAX_DARK_PIXELS


def parse_question(lines, cropped_bgr=None):
    """Classify OCR'd lines into header / question body / options / answer.

    Option lines are identified by their left-indent position, by a merged
    radio-glyph prefix in the text, or by a control glyph detected in the
    original image's left margin -- never by trusting the OCR'd A/B/C/D label
    character; letters are assigned by order of appearance.
    """
    exam_label = None
    item_number = None
    header_mismatch = False
    header_idx = None
    answer_idx = None
    answer_letters = []

    for idx, line in enumerate(lines):
        if header_idx is None:
            prefix_match = ITEM_PREFIX_RE.search(line["text"])
            exam_match = EXAM_RE.search(line["text"])
            qn_match = QN_RE.search(line["text"])
            if prefix_match or (exam_match and qn_match):
                header_idx = idx
                exam_label = exam_match.group(1).upper() if exam_match else None
                qn_number = int(qn_match.group(1)) if qn_match else None
                prefix_number = int(prefix_match.group(1)) if prefix_match else None
                # The deck contains multiple exams (A, B, ...): "Item N of M" is
                # the GLOBAL position while "QN" restarts per exam, so N == QN
                # only holds within Exam A. Prefer the global N (bounded on both
                # sides, so a stray OCR digit is less likely); the equality
                # cross-check is only meaningful -- and only flags -- in Exam A.
                item_number = prefix_number if prefix_number is not None else qn_number
                if (prefix_number is not None and qn_number is not None
                        and exam_label == "A" and prefix_number != qn_number):
                    header_mismatch = True
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

    base_indent = lines[header_idx]["x0"] if header_idx is not None else DEFAULT_BASE_INDENT_PX
    if base_indent <= 0:
        base_indent = DEFAULT_BASE_INDENT_PX
    option_indent_min = base_indent * OPTION_INDENT_RATIO_MIN
    option_indent_max = base_indent * OPTION_INDENT_RATIO_MAX

    question_parts = []
    options = []
    for line in body_lines:
        text = line["text"]
        if CHROME_RE.search(text) or JUNK_LINE_RE.match(text):
            continue

        # A new option starts either at the option indent band, or at base indent
        # with a merged radio-button glyph prefix ("O A Configure...") -- the
        # latter happens on hidden-state captures where the glyph drags the
        # line's left edge back to the question indent.
        is_new_option = (
            option_indent_min <= line["x0"] <= option_indent_max
            or OPTION_START_RE.match(text)
            or _has_control_glyph(cropped_bgr, line, base_indent)
        )

        if is_new_option:
            text = RADIO_PREFIX_RE.sub("", text, count=1)
            text = LABEL_PREFIX_RE.sub("", text, count=1).strip()
            options.append({
                "option_letter": chr(ord("A") + len(options)),
                "option_text": text,
                "bbox": (line["x0"], line["y0"], line["x1"], line["y1"]),
            })
        elif options:
            # Options are always the last section before the answer line, so any
            # non-option line after them is a wrapped continuation of the previous
            # option (continuations indent deeper than the option band, ~3.7x the
            # header indent) -- never question text.
            prev = options[-1]
            prev["option_text"] = f'{prev["option_text"]} {text.strip()}'
            x0, y0, x1, y1 = prev["bbox"]
            prev["bbox"] = (x0, y0, max(x1, line["x1"]), max(y1, line["y1"]))
        else:
            question_parts.append(text)

    # Tesseract sometimes groups stray control-glyph fragments from SEVERAL rows
    # into one "line", inflating an option's bbox down over its neighbours --
    # which then poisons per-option color sampling (a green option reads red
    # because the bbox swallowed the red rows below it). Clamp every option's
    # bbox to end above the next option's top.
    for i in range(len(options) - 1):
        x0, y0, x1, y1 = options[i]["bbox"]
        next_y0 = options[i + 1]["bbox"][1]
        if y1 >= next_y0:
            options[i]["bbox"] = (x0, y0, x1, max(y0 + 1, next_y0 - 2))

    return {
        "exam_label": exam_label,
        "item_number": item_number,
        "header_mismatch": header_mismatch,
        "question_text": " ".join(question_parts).strip(),
        "options": options,
        "answer_letters": answer_letters,
        "raw_ocr_text": "\n".join(l["text"] for l in lines),
    }
