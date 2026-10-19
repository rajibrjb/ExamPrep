import hashlib
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

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
# Worker threads for parallel ingestion. OCR/color-detection release the GIL
# during their C-extension calls (tesseract, opencv), so threads parallelize
# real work rather than just waiting on I/O. 12 of the host's 16 cores --
# ingestion is compute-bound on tesseract over the ~6MP Retina captures.
MAX_WORKERS = 12


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


def _extract(file_path):
    """Pure extraction (no DB): preprocess, OCR, structure. Returns the
    build_question result dict."""
    basename = os.path.splitext(os.path.basename(file_path))[0]
    cleaned_path = os.path.join(config.cleaned_dir, f"{basename}.png")
    cropped, cleaned = load_and_prepare(file_path, cleaned_out_path=cleaned_path)
    return build_question(cropped, cleaned, file_path)


def _persist(conn, source_image, result, file_path, log):
    """Write one extraction result to the DB (new question / duplicate /
    conflict). Returns the outcome status string."""
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
            return "duplicate"

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
        return "conflict"

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
    return "processed"


def process_one(file_path, log=print):
    """Processes a single image end-to-end in its own DB connection/transaction,
    so it commits independently the moment it finishes -- a crash, container
    restart, or interrupted run never loses already-completed work (unlike an
    earlier version that held one connection/transaction open for an entire
    batch, silently rolling back hundreds of "successful" results when the
    process was killed mid-run)."""
    with get_conn() as conn:
        file_hash = _file_hash(file_path)
        source_image, created = get_or_create_source_image(conn, file_hash, file_path)

        if not created and source_image["status"] in ("processed", "duplicate"):
            return "skipped", source_image

        try:
            result = _extract(file_path)
            return _persist(conn, source_image, result, file_path, log), source_image
        except Exception as e:
            mark_source_image(conn, source_image["id"], "error", error_message=str(e))
            log(f"[error] {os.path.basename(file_path)}: {e}")
            return "error", source_image


def process_single(staging_path, log=print):
    """One-shot capture ingestion for the interactive capture loop: extract the
    freshly-captured screenshot, rename it to item_NNNN.png using the item
    number read from the image's own header, persist it, and return a
    verification summary the capture loop gates its next step on."""
    result = _extract(staging_path)

    item = result["item_number"]
    directory = os.path.dirname(staging_path)
    if item is not None:
        base_name = f"item_{item:04d}"
    else:
        # Header unreadable -- park under a content-derived name so the staging
        # filename can be reused by the next capture without orphaning this row.
        base_name = f"unparsed_{_file_hash(staging_path)[:10]}"
    final_path = os.path.join(directory, f"{base_name}.png")
    suffix = 2
    while os.path.exists(final_path) and os.path.abspath(final_path) != os.path.abspath(staging_path):
        final_path = os.path.join(directory, f"{base_name}_{suffix}.png")
        suffix += 1
    if os.path.abspath(final_path) != os.path.abspath(staging_path):
        os.rename(staging_path, final_path)

    with get_conn() as conn:
        file_hash = _file_hash(final_path)
        source_image, created = get_or_create_source_image(conn, file_hash, final_path)
        if not created and source_image["status"] in ("processed", "duplicate"):
            status = "skipped"
        else:
            try:
                status = _persist(conn, source_image, result, final_path, log)
            except Exception as e:
                mark_source_image(conn, source_image["id"], "error", error_message=str(e))
                log(f"[error] {os.path.basename(final_path)}: {e}")
                status = "error"

    correct_letters = sorted(
        o["option_letter"] for o in result["options"] if o["is_correct"]
    )
    return {
        "status": status,
        "file": os.path.basename(final_path),
        "exam_label": result["exam_label"],
        "item_number": item,
        "num_options": len(result["options"]),
        "correct_letters": correct_letters,
        "confidence": result["confidence"],
        "needs_review": result["needs_review"],
        "reasons": result["review_reasons"],
    }


def _run_parallel(file_paths, log):
    stats = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(process_one, fp, log): fp for fp in file_paths}
        for future in as_completed(futures):
            status, _ = future.result()
            stats[status] = stats.get(status, 0) + 1
    return stats


def process_folder(input_dir=None, log=print):
    """Scan input_dir for images; already-processed files (by content hash) are
    skipped, so this is safe to re-run after dropping in new screenshots.
    Processes up to MAX_WORKERS images concurrently, each committed to the
    database the moment it completes."""
    input_dir = input_dir or config.incoming_dir
    stats = _run_parallel(list(discover_images(input_dir)), log)
    log(f"done: {stats}")
    return stats


def retry_failed(log=print):
    """Re-run the pipeline on every source image currently marked 'error'."""
    with get_conn() as conn:
        errored = list_source_images(conn, status="error")
    stats = _run_parallel([row["file_path"] for row in errored], log)
    log(f"retry done: {stats}")
    return stats
