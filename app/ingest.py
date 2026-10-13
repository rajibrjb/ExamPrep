import hashlib
import os

from app.config import config
from app.db import (
    add_review_reason,
    get_conn,
    get_or_create_source_image,
    get_question_by_item,
    insert_conflict,
    insert_question,
    list_source_images,
    mark_source_image,
    mark_source_image_duplicate,
    replace_options,
)
from app.dedupe import classify_against_existing
from app.image_prep import load_and_prepare
from app.structure_builder import build_question

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}


def _file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def discover_images(input_dir):
    for name in sorted(os.listdir(input_dir)):
        ext = os.path.splitext(name)[1].lower()
        if ext in IMAGE_EXTENSIONS:
            yield os.path.join(input_dir, name)


def process_one(conn, file_path, log=print):
    file_hash = _file_hash(file_path)
    source_image, created = get_or_create_source_image(conn, file_hash, file_path)

    if not created and source_image["status"] in ("processed", "duplicate"):
        return "skipped", source_image

    try:
        basename = os.path.splitext(os.path.basename(file_path))[0]
        cleaned_path = os.path.join(config.cleaned_dir, f"{basename}.png")
        cropped, cleaned = load_and_prepare(file_path, cleaned_out_path=cleaned_path)

        result = build_question(cropped, cleaned, file_path)

        existing = get_question_by_item(conn, result["exam_label"], result["item_number"])

        if existing is not None:
            match_type, similarity = classify_against_existing(
                result["question_text"], result["options"],
                existing["question_text"], existing["options"],
            )
            if match_type == "duplicate":
                mark_source_image_duplicate(conn, source_image["id"], existing["id"])
                log(f"[duplicate] {os.path.basename(file_path)} -> "
                    f"{result['exam_label']} Q{result['item_number']} "
                    f"matches existing question #{existing['id']} (similarity={similarity:.2f})")
                return "duplicate", source_image

            insert_conflict(
                conn, existing["id"], source_image["id"],
                result["question_text"], result["options"], similarity,
            )
            add_review_reason(
                conn, existing["id"],
                f"conflicting re-extraction from a new screenshot (similarity={similarity:.2f}) "
                f"-- see Conflicts tab",
            )
            with conn.cursor() as cur:
                cur.execute("UPDATE questions SET needs_review = true WHERE id = %s", (existing["id"],))
            mark_source_image(conn, source_image["id"], "processed")
            log(f"[conflict] {os.path.basename(file_path)} -> "
                f"{result['exam_label']} Q{result['item_number']} "
                f"disagrees with existing question #{existing['id']} (similarity={similarity:.2f})")
            return "conflict", source_image

        question = insert_question(
            conn,
            source_image_id=source_image["id"],
            exam_label=result["exam_label"],
            item_number=result["item_number"],
            question_text=result["question_text"],
            raw_ocr_text=result["raw_ocr_text"],
            confidence=result["confidence"],
            needs_review=result["needs_review"],
        )
        replace_options(conn, question["id"], result["options"])

        if result["needs_review"]:
            for reason in result["review_reasons"]:
                add_review_reason(conn, question["id"], reason)

        mark_source_image(conn, source_image["id"], "processed")
        log(f"[ok] {os.path.basename(file_path)} -> "
            f"{result['exam_label']} Q{result['item_number']} "
            f"(confidence={result['confidence']})")
        return "processed", source_image
    except Exception as e:
        mark_source_image(conn, source_image["id"], "error", error_message=str(e))
        log(f"[error] {os.path.basename(file_path)}: {e}")
        return "error", source_image


def process_folder(input_dir=None, log=print):
    """Scan input_dir for images; already-processed files (by content hash) are
    skipped, so this is safe to re-run after dropping in new screenshots."""
    input_dir = input_dir or config.incoming_dir
    stats = {"processed": 0, "skipped": 0, "error": 0}
    with get_conn() as conn:
        for file_path in discover_images(input_dir):
            status, _ = process_one(conn, file_path, log=log)
            stats[status] = stats.get(status, 0) + 1
    log(f"done: {stats}")
    return stats


def retry_failed(log=print):
    """Re-run the pipeline on every source image currently marked 'error'."""
    stats = {"processed": 0, "skipped": 0, "error": 0}
    with get_conn() as conn:
        errored = list_source_images(conn, status="error")
        for source_image in errored:
            status, _ = process_one(conn, source_image["file_path"], log=log)
            stats[status] = stats.get(status, 0) + 1
    log(f"retry done: {stats}")
    return stats
